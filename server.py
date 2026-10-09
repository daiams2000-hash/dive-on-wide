#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dive on Wide — Lokaler KI-Arbeitsplatz mit Ollama-Orchestrierung
=====================================================================
Ein einziger Python-Server (nur Standardbibliothek, KEINE pip-Installation nötig):
  - Serviert das Frontend (frontend/index.html)
  - Proxied Chat-Anfragen (mit Streaming) an die lokale Ollama-Instanz
  - Persistiert Chats, Agenten, Prompts, Skills, Templates, Wissen & Artefakte in SQLite
  - Führt Skill-Pipelines (verkettete System-Prompts) im Hintergrund aus → Inbox-Benachrichtigung

Start:  python3 server.py        (dann http://localhost:3000 öffnen)
Config: .env im selben Ordner (OLLAMA_BASE_URL, DEFAULT_MODEL, PORT, ...)
"""

import base64
import collections
import glob
import http.client
import json
import os
import posixpath
import re
import secrets
from html import unescape as html_unescape
import shutil
import socket
import socketserver
import sqlite3
import struct
import tempfile
import subprocess
import sys
import platform
import plattform  # noqa: E402  Windows-Eigenheiten (Ausgabe, localhost)
from plattform import ausgabe_absichern  # noqa: E402,F401
plattform.localhost_ipv4_zuerst()
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ----------------------------------------------------------------------------
# Konfiguration (.env)
# ----------------------------------------------------------------------------

def load_env():
    env = {
        "PORT": "3000",
        "OLLAMA_BASE_URL": "http://localhost:11434",
        "DEFAULT_MODEL": "qwen2.5-coder:14b",
        "STORAGE_DIR": os.path.join(BASE_DIR, "storage"),
        "NUM_CTX": "16384",
        "TEMPERATURE": "0.7",
    }
    path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    # Die Umgebung schlägt die Datei. Ohne das startet `PORT=3099 python3
    # server.py` stillschweigend auf 3000 — beim Frischklon-Test am 18.09.2026
    # lief es damit gegen die schon laufende Instanz und starb mit „Address
    # already in use". Docker, systemd, ein zweiter Start zum Ausprobieren und
    # jede CI setzen Variablen, keine .env. Übernommen wird nur, was Dive on Wide
    # ohnehin kennt (Vorgaben plus .env) — der Rest der Umgebung geht uns
    # nichts an.
    for k in list(env) + ["HOST"]:
        wert = os.environ.get(k)
        if wert not in (None, ""):
            env[k] = wert
    return env

ENV = load_env()

# Die Version setzt werkzeuge/freigeben.sh (ersetzt diese Zeichenkette überall).
# Mit Bindestrich (1.0.0-rc.1) ist es eine Vorab-Version für Tester, ohne eine stabile.
DOWOS_VERSION = "0.5.0"


def dowos_kanal(version=None):
    return "vorab" if "-" in (version or DOWOS_VERSION) else "stabil"
STORAGE_DIR = ENV["STORAGE_DIR"]
ARTIFACTS_DIR = os.path.join(STORAGE_DIR, "artifacts")
# (Workspaces der Code-Sandbox liegen unter storage/workspaces/ — siehe Code-Sandbox)
DB_PATH = os.path.join(STORAGE_DIR, "dowos.db")
os.makedirs(ARTIFACTS_DIR, exist_ok=True)


# ----------------------------------------------------------------------------
# Datenbank (SQLite)
# ----------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, title TEXT, agent_id TEXT, created_at REAL, updated_at REAL);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, model TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY, name TEXT, description TEXT, system_prompt TEXT,
  model TEXT, emoji TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS prompts (
  id TEXT PRIMARY KEY, name TEXT, description TEXT, content TEXT, category TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS skills (
  id TEXT PRIMARY KEY, name TEXT, trigger_word TEXT, description TEXT,
  steps TEXT, model TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS templates (
  id TEXT PRIMARY KEY, title TEXT, description TEXT, kind TEXT,
  fields TEXT, base_prompt TEXT, emoji TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS notifications (
  id TEXT PRIMARY KEY, title TEXT, body TEXT, kind TEXT, read INTEGER DEFAULT 0,
  artifact_id TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, filename TEXT, title TEXT, session_id TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS knowledge (
  id TEXT PRIMARY KEY, name TEXT, content TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS pipelines (
  id TEXT PRIMARY KEY, name TEXT, description TEXT, steps TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, kind TEXT, title TEXT, status TEXT, progress TEXT,
  result TEXT, artifact_id TEXT, session_id TEXT, created_at REAL, updated_at REAL,
  cancel INTEGER DEFAULT 0, pending TEXT DEFAULT '', decision TEXT DEFAULT '',
  autoconfirm INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS zeitplan (
  id TEXT PRIMARY KEY, name TEXT, art TEXT, was TEXT, ziel TEXT, eingabe TEXT,
  uhrzeit TEXT, intervall_min INTEGER DEFAULT 0, wochentage TEXT DEFAULT '',
  aktiv INTEGER DEFAULT 1, letzter_lauf REAL DEFAULT 0, letzter_status TEXT DEFAULT '',
  naechster_lauf REAL DEFAULT 0, created_at REAL, zustellen TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS tokens (
  id TEXT PRIMARY KEY, name TEXT, token TEXT UNIQUE, rolle TEXT, created_at REAL);
"""

# Migrationen für bestehende Datenbanken (ältere Dive-on-Wide-Versionen)
MIGRATIONS = [
    "ALTER TABLE knowledge ADD COLUMN folder TEXT DEFAULT ''",
    "ALTER TABLE knowledge ADD COLUMN updated_at REAL DEFAULT 0",
    "ALTER TABLE runs ADD COLUMN cancel INTEGER DEFAULT 0",
    "ALTER TABLE runs ADD COLUMN pending TEXT DEFAULT ''",
    "ALTER TABLE runs ADD COLUMN decision TEXT DEFAULT ''",
    "ALTER TABLE runs ADD COLUMN autoconfirm INTEGER DEFAULT 0",
    "ALTER TABLE zeitplan ADD COLUMN zustellen TEXT DEFAULT ''",
    "ALTER TABLE zeitplan ADD COLUMN modell TEXT DEFAULT ''",
    "ALTER TABLE zeitplan ADD COLUMN aufbauen_auf TEXT DEFAULT ''",
    "ALTER TABLE messages ADD COLUMN wissen TEXT DEFAULT ''",
    "ALTER TABLE zeitplan ADD COLUMN letzter_lauf_id TEXT DEFAULT ''",
]

_db_init_done = False
_db_init_lock = threading.Lock()

def db():
    """Datenbankverbindung mit WAL und Wartezeit.

    Dive on Wide schreibt aus vielen Hintergrund-Threads gleichzeitig (Läufe, Skills,
    Second Brain). Ohne WAL-Modus und busy_timeout käme es dabei früher oder
    später zu „database is locked“. Beides wird einmalig gesetzt und gilt dann
    für die Datei.
    """
    global _db_init_done
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    if not _db_init_done:
        with _db_init_lock:
            if not _db_init_done:
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                except sqlite3.Error:
                    pass          # z. B. auf Netzlaufwerken nicht verfügbar
                _db_init_done = True
    return conn

# Begrenzt, wie viele modellintensive Hintergrundläufe gleichzeitig arbeiten.
# Ohne Bremse würden zehn parallele Pipelines einen lokalen Ollama-Server (und
# damit den Rechner) lahmlegen. Ein wartender Lauf blockiert nichts anderes.
_run_slots = threading.Semaphore(int(ENV.get("MAX_PARALLEL_RUNS", "3")))

def background(fn, *args, **kwargs):
    """Startet einen Hintergrundlauf mit Parallelitäts-Bremse."""
    def wrapper():
        with _run_slots:
            fn(*args, **kwargs)
    t = threading.Thread(target=wrapper, daemon=True)
    t.start()
    return t

def rows(cur):
    return [dict(r) for r in cur.fetchall()]

def now():
    return time.time()

def nid():
    return uuid.uuid4().hex[:12]

# ----------------------------------------------------------------------------
# Der Fluss — ein einziger Ereignis- und Artefaktkanal für ALLE Module.
# Jedes System (Skill, Pipeline, Research, Sandbox, Brain, Browser) mündet hier
# hinein. Dadurch verhalten sich alle Teile gleich: Ergebnis wird zum Artefakt,
# Artefakt meldet sich in der Inbox, Inbox verweist zurück aufs Artefakt.
# ----------------------------------------------------------------------------

INBOX_GELESEN_TAGE = 30
INBOX_HOECHSTENS = 5000
_inbox_zaehler = [0]


def inbox_aufraeumen(conn):
    """Die Inbox wuchs ohne Grenze: im Dauertest 3 Eintraege je Arbeitsrunde,
    3 157 nach sechs Stunden (27.09.2026). Ungelesenes bleibt IMMER. Gelesenes
    geht nach 30 Tagen, und ueber 5 000 Eintraegen zuerst das aelteste Gelesene.
    Artefakte, auf die ein Eintrag zeigt, bleiben unberuehrt."""
    conn.execute("DELETE FROM notifications WHERE read=1 AND created_at < ?",
                 (now() - INBOX_GELESEN_TAGE * 86400,))
    zuviel = conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] - INBOX_HOECHSTENS
    if zuviel > 0:
        conn.execute("DELETE FROM notifications WHERE id IN (SELECT id FROM notifications "
                     "WHERE read=1 ORDER BY created_at LIMIT ?)", (zuviel,))


def emit(title, body, kind="system", artifact_id=None, read=0):
    """Meldet ein Ereignis in die Inbox."""
    conn = db()
    conn.execute("INSERT INTO notifications VALUES(?,?,?,?,?,?,?)",
                 (nid(), title, body, kind, read, artifact_id, now()))
    _inbox_zaehler[0] += 1
    if _inbox_zaehler[0] % 50 == 1:     # beim ersten Eintrag nach dem Start und dann alle 50
        inbox_aufraeumen(conn)
    conn.commit()
    conn.close()

def safe_filename(name, fallback="datei.md"):
    """Macht aus beliebigem Nutzertext einen harmlosen Dateinamen.

    Es wird nur der letzte Pfadbestandteil verwendet, Trennzeichen und führende
    Punkte fallen weg — damit ist weder ein Ausbruch aus dem Ordner noch eine
    versteckte Datei möglich.
    """
    name = str(name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^\w.\-]+", "-", name).lstrip(".-")
    name = re.sub(r"-{2,}", "-", name)
    return name[:120] or fallback

def save_artifact(filename_hint, content, title=None, session_id=None):
    """Speichert Inhalt als Artefakt und gibt (id, filename) zurück."""
    art_id = nid()
    stem, ext = os.path.splitext(filename_hint)
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", stem).strip("-").lower() or "artefakt"
    filename = "%s-%s%s" % (safe, art_id, ext or ".md")
    with open(os.path.join(ARTIFACTS_DIR, filename), "w", encoding="utf-8") as f:
        f.write(content)
    conn = db()
    conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)",
                 (art_id, filename, title or filename, session_id, now()))
    conn.commit()
    conn.close()
    return art_id, filename

def deliver(name, content, filename_hint, done_title, done_body, kind="skill"):
    """Ergebnis eines Hintergrundlaufs: Artefakt schreiben + Inbox melden."""
    art_id, _fn = save_artifact(filename_hint, content, title=name)
    emit(done_title, done_body, kind, art_id)
    return art_id

def fail(title, err):
    """Einheitliche Fehlermeldung für alle Hintergrundläufe."""
    emit(title, "Fehler: %s" % err, "error")

# --- Läufe: Live-Status für jeden Hintergrundprozess -------------------------
# Damit sieht man im Chat, was gerade passiert, statt nur am Ende eine Meldung
# zu bekommen. Jeder Lauf (Skill, Pipeline, Research, Coding-Agent) meldet hier
# seinen Fortschritt; das Frontend fragt ihn ab und zeigt eine Live-Karte.

# Wer die laufende Anfrage stellt (je Anfrage-Faden gesetzt in Handler._auth).
_anfrage = threading.local()
# Laeufe, die ein Gast-Schluessel gestartet hat. Darin wird kein Code ausgefuehrt
# und kein Browser gesteuert — Befund 10 in docs/SICHERHEIT.md (27.09.2026).
GAST_LAEUFE = set()
GAST_VERBOT = ("Code ausführen und den Browser steuern darf nur der Besitzer dieser Instanz — "
               "dieser Lauf wurde mit einem Gast-Schlüssel gestartet.")


def run_begin(kind, title, session_id=None):
    run_id = nid()
    if getattr(_anfrage, "rolle", None) == "gast":
        GAST_LAEUFE.add(run_id)
    conn = db()
    conn.execute("INSERT INTO runs(id,kind,title,status,progress,result,"
                 "artifact_id,session_id,created_at,updated_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (run_id, kind, title, "running", json.dumps([]), "", None,
                  session_id or "", now(), now()))
    conn.commit()
    conn.close()
    return run_id

class RunCancelled(Exception):
    """Der Nutzer hat den Lauf abgebrochen — kein Fehler, sondern eine Entscheidung."""

class FreigabeAusgeblieben(RunCancelled):
    """Eine Rückfrage blieb unbeantwortet — abgebrochen, aber NICHT vom Nutzer.

    Am 22.09.2026 endete ein Werkbank-Lauf mit „Vom Nutzer abgebrochen", ohne
    dass jemand etwas abgebrochen hätte: Das Projekt brachte eigene
    Einstellungen mit, Dive on Wide fragte nach Vertrauen, und die Frage stand fünf
    Minuten unbeantwortet im Leeren. Abzubrechen ist richtig — die falsche
    Meldung schickt den Betreiber aber an die falsche Stelle. Erbt von
    RunCancelled, damit alle bestehenden Abbruchpfade unverändert greifen."""

    def __init__(self, frage="", sekunden=0):
        super().__init__(frage)
        self.frage = frage
        self.sekunden = sekunden

    def text(self):
        return ("Die Rückfrage blieb %d Minuten unbeantwortet, deshalb wurde der Lauf "
                "sicherheitshalber beendet — niemand hat ihn abgebrochen.%s\n\n"
                "Abhilfe: die Frage in der Oberfläche beantworten, oder den Lauf ohne "
                "Aufsicht starten (dann wird nicht gefragt und Ungefragtes bleibt aus)."
                % (max(1, self.sekunden // 60),
                   "\n\nGefragt war: " + self.frage[:300] if self.frage else ""))

def run_step(run_id, text, state="done"):
    """Hängt einen Fortschrittsschritt an (state: done | active | error).

    Prüft dabei das Abbruch-Flag: Hat der Nutzer den Lauf abgebrochen, fliegt
    RunCancelled — jede lange Schleife (Pipeline, Browser, Research, Coding)
    meldet hier regelmäßig ihren Fortschritt und endet damit von selbst an der
    nächsten Schrittgrenze."""
    if not run_id:
        return
    conn = db()
    r = conn.execute("SELECT progress, cancel FROM runs WHERE id=?",
                     (run_id,)).fetchone()
    if r and r["cancel"]:
        conn.close()
        raise RunCancelled()
    steps = json.loads(r["progress"]) if r and r["progress"] else []
    steps.append({"text": text, "state": state, "at": now()})
    conn.execute("UPDATE runs SET progress=?, updated_at=? WHERE id=?",
                 (json.dumps(steps, ensure_ascii=False), now(), run_id))
    conn.commit()
    conn.close()

def run_finish(run_id, status, result="", artifact_id=None):
    if not run_id:
        return
    conn = db()
    conn.execute("UPDATE runs SET status=?, result=?, artifact_id=?, updated_at=? "
                 "WHERE id=?", (status, result, artifact_id, now(), run_id))
    conn.commit()
    conn.close()

def run_cancel(run_id):
    """Setzt das Abbruch-Flag. Der Lauf endet an der nächsten Schrittgrenze."""
    conn = db()
    r = conn.execute("UPDATE runs SET cancel=1, updated_at=? "
                     "WHERE id=? AND status='running'", (now(), run_id))
    conn.commit()
    betroffen = r.rowcount
    conn.close()
    return betroffen > 0

def run_abgebrochen(run_id, was, session_id=None, grund=None):
    """Einheitlicher Abschluss für abgebrochene Läufe (Gegenstück zu fail()).

    `grund` kommt von FreigabeAusgeblieben: Ein Lauf, der an einer
    unbeantworteten Rückfrage endet, darf nicht als Nutzerentscheidung
    dastehen."""
    text = grund.text() if isinstance(grund, FreigabeAusgeblieben) else "Vom Nutzer abgebrochen."
    run_finish(run_id, "cancelled", text)
    if isinstance(grund, FreigabeAusgeblieben):
        emit("%s beendet: Rückfrage unbeantwortet ⏳" % was, text, "system")
    else:
        emit("%s abgebrochen ⏹" % was,
             "Der Lauf wurde auf deinen Wunsch beendet.", "system")
    if session_id:
        post_to_session(session_id, "⏹ %s abgebrochen." % was)

def post_to_session(session_id, content, model=""):
    """Schreibt ein Ergebnis als Assistenten-Nachricht in einen Chat."""
    if not session_id:
        return
    conn = db()
    conn.execute("INSERT INTO messages(id,session_id,role,content,model,created_at) VALUES(?,?,?,?,?,?)",
                 (nid(), session_id, "assistant", content, model, now()))
    conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now(), session_id))
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    conn = db()
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    if r:
        return r["value"]
    return ENV.get(key, default)

def set_setting(key, value):
    conn = db()
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
    conn.close()

# ----------------------------------------------------------------------------
# Zugänge — Zugangsschlüssel für Besitzer und Alpha-Tester
#
# Dive on Wide lauscht im ganzen LAN. Vom eigenen Rechner aus (localhost) bleibt
# alles schlüsselfrei — easy to use, ./start.sh genügt weiterhin. Jedes andere
# Gerät braucht einen Zugangsschlüssel. Der Besitzer legt unter Einstellungen →
# Zugänge pro Tester einen eigenen Schlüssel an und kann ihn einzeln
# widerrufen. Gäste dürfen alles benutzen, aber nicht: Einstellungen ändern,
# Zugänge verwalten, Backups ziehen.
# ----------------------------------------------------------------------------

def ensure_owner_token():
    """Legt beim ersten Start den Besitzer-Schlüssel an (idempotent)."""
    conn = db()
    r = conn.execute("SELECT token FROM tokens WHERE rolle='besitzer'").fetchone()
    if r:
        token = r["token"]
    else:
        token = "dow_" + secrets.token_urlsafe(18)
        conn.execute("INSERT INTO tokens VALUES(?,?,?,?,?)",
                     (nid(), "Besitzer", token, "besitzer", now()))
        conn.commit()
    conn.close()
    return token

def token_role(token):
    """Liefert die Rolle zu einem Schlüssel — oder None, wenn er ungültig ist."""
    return token_info(token)[0]

def token_info(token):
    """(Rolle, Name) zu einem Schlüssel.

    Der Name landet im Ausgangsbuch: „Claude Code" ist als Empfänger nachlesbar,
    „irgendein Schlüssel" wäre es nicht."""
    if not token:
        return None, ""
    conn = db()
    r = conn.execute("SELECT rolle, name FROM tokens WHERE token=?", (token,)).fetchone()
    conn.close()
    return (r["rolle"], r["name"] or "") if r else (None, "")

# ----------------------------------------------------------------------------
# Seed-Daten (werden nur beim allerersten Start angelegt)
# ----------------------------------------------------------------------------

# Ohne diesen Satz füllt das Basismodell die Lücke selbst: Beim Frischklon-
# Durchgang am 22.09.2026 stellte sich Dive on Wide dem ersten Nutzer als
# "entwickelt von OpenAI" vor. Ein Modell, das seine Herkunft nicht kennt,
# rät — also wird sie ihm gesagt.
HERKUNFT = (
    "Dive on Wide ist eine eigenständige Anwendung, die auf dem Rechner des Nutzers "
    "läuft — sie stammt nicht von OpenAI, Anthropic oder Google. Das "
    "Sprachmodell, mit dem du antwortest, läuft lokal über Ollama. Wenn du "
    "nicht weißt, wer dich entwickelt hat, sage das — erfinde keinen "
    "Hersteller.")

SEED_AGENTS = [
    ("Starter-Assistent", "Ein vielseitiger Assistent für den Einstieg.",
     "Du bist Dive on Wide, ein lokaler KI-Arbeitsplatz. " + HERKUNFT + " Antworte präzise, "
     "hilfreich und auf Deutsch. Formatiere Antworten mit Markdown.", "", "🤖"),
    ("Code-Experte", "Spezialist für Programmierung, Reviews und Debugging.",
     "Du bist ein Senior Software Engineer in Dive on Wide. Liefere sauberen, kommentierten Code, "
     "erkläre Entscheidungen kurz und weise auf Risiken hin. Antworte auf Deutsch.", "", "💻"),
    ("Marketing-Stratege", "Kampagnen, Copywriting und Positionierung.",
     "Du bist ein erfahrener Marketing-Stratege in Dive on Wide. Denke in Zielgruppen, Botschaften "
     "und Conversion. Liefere konkrete, umsetzbare Vorschläge auf Deutsch.", "", "📈"),
    # --- Reale Umsetzung der Rollen aus dem Recherche-Verlauf ---
    ("Recherche-Analyst", "Verdichtet Quellen zu belastbaren Erkenntnissen und trennt "
     "Gesichertes von Vermutung.",
     "Du bist Recherche-Analyst in Dive on Wide. Arbeite quellenkritisch: Trenne strikt zwischen "
     "(a) belegten Fakten mit Quelle, (b) plausiblen Schlüssen und (c) Spekulation — und "
     "kennzeichne jede Aussage entsprechend. Wenn dir Wissen fehlt, sage das ausdrücklich, "
     "statt zu raten. Erfinde niemals Quellen, Produktnamen, Bibliotheken oder Zahlen. "
     "Antworte strukturiert auf Deutsch.", "", "🔍"),
    ("Kritiker", "Prüft Ergebnisse gnadenlos auf Fehler, Lücken und Halluzinationen.",
     "Du bist der Kritiker in Dive on Wide. Deine Aufgabe ist es, den vorgelegten Inhalt "
     "systematisch zu zerlegen: Welche Behauptungen sind unbelegt? Welche Namen, Pakete "
     "oder APIs könnten erfunden sein? Wo fehlt Logik, wo widerspricht sich der Text? "
     "Liste jeden Befund mit Schweregrad (kritisch/mittel/gering) und einem konkreten "
     "Korrekturvorschlag. Sei streng, aber sachlich und konstruktiv.", "", "🧪"),
    ("Coding-Agent", "Schreibt lauffähigen Code und testet ihn selbstständig in der "
     "Code-Sandbox.",
     "Du bist der Coding-Agent von Dive on Wide. Schreibe sauberen, vollständigen, "
     "eigenständig lauffähigen Code mit kurzen Kommentaren. Erkläre nach dem Codeblock "
     "in 2-3 Sätzen die Funktionsweise. Nutze nur real existierende Bibliotheken. "
     "Antworte auf Deutsch.", "", "🧪"),
    ("Synthese-Agent", "Verdichtet Ergebnisse anderer Agenten zu strukturierten "
     "Dokumenten.",
     "Du bist der Synthese-Agent von Dive on Wide. Führe Inhalte zusammen, strukturiere sie "
     "mit klaren Überschriften, entferne Redundanz und schließe mit einer prägnanten "
     "Zusammenfassung plus nächsten Schritten. Sauberes Markdown, deutsch.", "", "📝"),
    ("Sprach-Synthesizer", "Bringt Rohmaterial in klare, publikationsreife Form.",
     "Du bist der Sprach-Synthesizer in Dive on Wide. Du erhältst Rohmaterial und formst daraus "
     "einen klaren, gut lesbaren Text in sauberem Markdown: sinnvolle Überschriften, "
     "kurze Absätze, keine Füllwörter, kein Marketing-Sprech. Inhalte niemals hinzuerfinden "
     "— nur ordnen, verdichten und verständlich machen. Antworte auf Deutsch.", "", "✍️"),

    # --- Qualitätssicherung ---
    ("Test-Ingenieur", "Schreibt Testfälle und deckt Randfälle auf, an die niemand denkt.",
     "Du bist Test-Ingenieur in Dive on Wide. Zu jedem Stück Code oder jeder Spezifikation "
     "entwirfst du Testfälle: Normalfall, Randfälle, Fehlerfälle, Grenzwerte, "
     "Nebenläufigkeit. Denke besonders an das, was Entwickler übersehen — leere "
     "Eingaben, Sonderzeichen, sehr große Werte, gleichzeitige Zugriffe. Liefere "
     "lauffähigen Testcode plus eine kurze Begründung je Fall. Antworte auf Deutsch.",
     "", "🧷"),
    ("Sicherheits-Prüfer", "Sucht Schwachstellen: Injection, Pfad-Ausbrüche, Datenlecks.",
     "Du bist Sicherheits-Prüfer in Dive on Wide. Untersuche Code und Konzepte auf "
     "Schwachstellen: Injection (SQL, Befehle, Prompts), Pfad-Ausbrüche, unsichere "
     "Deserialisierung, offene Netzwerkbindungen, SSRF, Rechteausweitung, Geheimnisse "
     "im Klartext. Bewerte jeden Befund mit Schweregrad (kritisch/hoch/mittel/gering), "
     "beschreibe den Angriffsweg konkret und nenne die Gegenmaßnahme. Erfinde keine "
     "Lücken — wenn etwas sicher aussieht, sage das. Antworte auf Deutsch.", "", "🛡️"),
    ("Daten-Analyst", "Rechnet mit Zahlen, statt über sie zu reden.",
     "Du bist Daten-Analyst in Dive on Wide. Du arbeitest mit Tabellen, Kennzahlen und "
     "Messwerten. Rechne Schritt für Schritt und zeige den Rechenweg. Nenne immer die "
     "Grundgesamtheit und den Zeitraum. Unterscheide klar zwischen Korrelation und "
     "Ursache. Wenn Daten fehlen, um eine Frage zu beantworten, sage genau welche — "
     "statt zu schätzen. Antworte auf Deutsch mit Tabellen, wo sie helfen.", "", "📊"),

    # --- Sprache & Vermittlung ---
    ("Lektor", "Korrigiert Rechtschreibung, Grammatik und Stil — ohne den Ton zu verbiegen.",
     "Du bist Lektor in Dive on Wide. Korrigiere Rechtschreibung, Grammatik, Zeichensetzung "
     "und Stil. Erhalte dabei die Stimme des Autors — du glättest, du übernimmst nicht. "
     "Liefere zuerst die korrigierte Fassung, danach eine kurze Liste der wichtigsten "
     "Änderungen mit Begründung. Antworte auf Deutsch.", "", "📖"),
    ("Didaktik-Coach", "Erklärt so, dass es hängen bleibt — vom Vorwissen aus aufgebaut.",
     "Du bist Didaktik-Coach in Dive on Wide. Erkläre Themen vom Vorwissen des Lernenden aus: "
     "erst das Warum, dann eine tragfähige Analogie, dann die Sache selbst, zum Schluss "
     "eine Übung mit Lösung. Warne vor typischen Denkfehlern. Wenn du das Vorwissen "
     "nicht kennst, frage zuerst danach. Antworte auf Deutsch.", "", "🎓"),
    ("Anforderungs-Interviewer", "Stellt die Rückfragen, die ein Projekt vor dem Scheitern "
     "bewahren.",
     "Du bist Anforderungs-Interviewer in Dive on Wide. Deine Aufgabe ist NICHT, sofort eine "
     "Lösung zu liefern, sondern die Aufgabe zu klären. Stelle höchstens fünf gezielte "
     "Fragen auf einmal, priorisiert nach Risiko: Was ist das eigentliche Ziel? Wer "
     "nutzt es? Was passiert bei Fehlern? Woran misst sich Erfolg? Fasse am Ende die "
     "geklärten Anforderungen und die verbleibenden Unklarheiten zusammen. "
     "Antworte auf Deutsch.", "", "🎤"),

    # --- Planung & Entscheidung ---
    ("Projekt-Planer", "Zerlegt Vorhaben in Schritte mit Aufwand, Reihenfolge und Risiken.",
     "Du bist Projekt-Planer in Dive on Wide. Zerlege Vorhaben in konkrete Arbeitspakete mit "
     "Abhängigkeiten, grobem Aufwand und Risiken. Markiere den kritischen Pfad und "
     "benenne, was zuerst geklärt werden muss, weil alles andere daran hängt. Plane "
     "realistisch statt optimistisch und baue Puffer ein. Antworte auf Deutsch mit "
     "einer übersichtlichen Tabelle.", "", "🗺️"),
    ("Advocatus Diaboli", "Argumentiert konsequent gegen den Vorschlag, um ihn zu härten.",
     "Du bist Advocatus Diaboli in Dive on Wide. Deine Rolle ist es, den vorgelegten Plan "
     "oder Vorschlag mit den stärksten verfügbaren Argumenten anzugreifen: Was spricht "
     "dagegen? Wo ist die Annahme falsch? Was ist die unbequeme Alternative? Wie sieht "
     "das teuerste Scheitern aus? Bleibe sachlich und fair — du suchst Schwächen, nicht "
     "Streit. Schließe mit dem Punkt, der dich selbst am meisten überzeugt. "
     "Antworte auf Deutsch.", "", "⚖️"),
]

SEED_PROMPTS = [
    ("Meeting-Notiz", "Strukturiert Meeting-Notizen.",
     "Strukturiere die folgenden Meeting-Notizen: Fasse Beschlüsse, offene Punkte und "
     "Verantwortlichkeiten (mit Deadlines) als übersichtliche Markdown-Tabelle zusammen.\n\n"
     "Notizen:\n", "Schreiben"),
    ("Höfliche Absage", "Formuliert eine professionelle, höfliche Absage.",
     "Formuliere eine höfliche, professionelle Absage-E-Mail auf Basis folgender Situation. "
     "Bleibe wertschätzend und lasse die Tür für zukünftige Zusammenarbeit offen:\n\n", "Schreiben"),
    ("ELI5", "Erklärt ein Thema so einfach wie möglich.",
     "Erkläre das folgende Thema so, dass es ein interessierter Laie sofort versteht. "
     "Nutze eine Alltagsanalogie und maximal 200 Wörter:\n\n", "Lernen"),
]

SEED_SKILLS = [
    ("Zusammenfassen", "zusammenfassen", "Fasst lange Texte in klare Stichpunkte zusammen.",
     [{"name": "Kernaussagen extrahieren",
       "system_prompt": "Extrahiere aus dem folgenden Text alle Kernaussagen als prägnante "
                        "Stichpunkte. Verwende Markdown."},
      {"name": "Verdichten & ordnen",
       "system_prompt": "Ordne die folgenden Stichpunkte thematisch, entferne Dopplungen und "
                        "erstelle eine finale, klar strukturierte Zusammenfassung mit "
                        "Überschriften und einem 3-Satz-Fazit am Ende."}]),
    ("Image-Prompt Architekt", "imageprompt",
     "Wandelt vage Bildideen in hochoptimierte englische Prompts für moderne Bildmodelle "
     "(Midjourney, Flux, DALL·E) um.",
     [{"name": "Idee analysieren",
       "system_prompt": "Analysiere die folgende Bildidee: Identifiziere Motiv, Stimmung, Stil, "
                        "Komposition, Licht und fehlende Details. Ergänze sinnvolle Annahmen. "
                        "Antworte strukturiert auf Deutsch."},
      {"name": "Prompt bauen",
       "system_prompt": "Erstelle aus der folgenden Analyse einen hochoptimierten ENGLISCHEN "
                        "Bild-Prompt für moderne Bildmodelle. Format: 1) Haupt-Prompt (eine Zeile, "
                        "kommaseparierte Deskriptoren), 2) Negative-Prompt, 3) Parameter-Empfehlung "
                        "(Aspect Ratio, Stilstärke). Gib nur diese drei Blöcke aus."}]),
    ("Kampagnenkonzept", "kampagne",
     "Erstellt aus einem kurzen Briefing ein vollständiges, umsetzbares Kampagnenkonzept "
     "inklusive Kernidee, Botschaften nach Segment und Budget-Mix.",
     [{"name": "Briefing strukturieren",
       "system_prompt": "Strukturiere das folgende Briefing: Ziel, Zielgruppe(n), Angebot, USP, "
                        "Budgetrahmen, Zeitraum. Markiere fehlende Infos mit sinnvollen Annahmen."},
      {"name": "Konzept entwickeln",
       "system_prompt": "Entwickle aus dem strukturierten Briefing ein vollständiges "
                        "Kampagnenkonzept: Kernidee (Big Idea), Botschaften je Segment, "
                        "Kanal-Mix mit Budgetverteilung in %, Content-Formate, KPI-Set und "
                        "grober Zeitplan. Markdown, deutsch, umsetzbar."}]),
    # --- Reale Umsetzung der Skill-Konzepte aus dem Recherche-Verlauf ---
    ("Quellen-Fusion", "fusion",
     "Führt mehrere Textquellen zu einem Bild zusammen: Übereinstimmungen, Widersprüche "
     "und Lücken werden sichtbar getrennt.",
     [{"name": "Aussagen extrahieren",
       "system_prompt": "Extrahiere aus dem folgenden Material alle Einzelaussagen und "
                        "ordne jeder ihre Herkunft zu. Nummeriere sie durch."},
      {"name": "Abgleichen",
       "system_prompt": "Vergleiche die Aussagen: Was bestätigen mehrere Quellen, was "
                        "widerspricht sich, was steht nur an einer Stelle? Gliedere in "
                        "ÜBEREINSTIMMEND / WIDERSPRÜCHLICH / EINZELQUELLE / LÜCKEN."},
      {"name": "Gesamtbild",
       "system_prompt": "Formuliere daraus ein belastbares Gesamtbild. Kennzeichne den "
                        "Sicherheitsgrad jeder Kernaussage (gesichert / wahrscheinlich / "
                        "unsicher) und nenne offene Fragen."}]),
    ("Fakten-Check", "faktencheck",
     "Prüft einen Text auf erfundene Fakten, Pakete und Quellen — genau die Fehlerklasse, "
     "die lokale Modelle ohne Web-Zugriff produzieren.",
     [{"name": "Behauptungen isolieren",
       "system_prompt": "Liste jede überprüfbare Behauptung aus dem Text einzeln auf: "
                        "Namen, Produkte, Bibliotheken, Zahlen, Zitate, APIs."},
      {"name": "Plausibilität bewerten",
       "system_prompt": "Bewerte jede Behauptung: Existiert das mit hoher Wahrscheinlichkeit "
                        "wirklich, oder klingt es nur plausibel? Achte besonders auf "
                        "Paketnamen und Bibliotheken, die es vermutlich nicht gibt. "
                        "Ampel: 🟢 gesichert, 🟡 unklar, 🔴 vermutlich erfunden — mit "
                        "kurzer Begründung. Empfiehl am Ende, was per Web-Recherche "
                        "verifiziert werden muss."}]),
    ("Testfälle", "tests",
     "Erzeugt aus Code oder einer Beschreibung eine vollständige Testfall-Sammlung "
     "inklusive Randfällen und lauffähigem Testcode.",
     [{"name": "Verhalten erfassen",
       "system_prompt": "Beschreibe präzise, was der folgende Code oder die folgende "
                        "Anforderung leisten soll: Eingaben, Ausgaben, Seiteneffekte, "
                        "Fehlerfälle. Nummeriere die Verhaltensweisen."},
      {"name": "Randfälle finden",
       "system_prompt": "Finde zu jedem Verhalten die Fälle, die üblicherweise "
                        "übersehen werden: leere und riesige Eingaben, Sonderzeichen, "
                        "Null-Werte, Grenzwerte, doppelte Aufrufe, gleichzeitige "
                        "Zugriffe, fehlende Rechte. Als Liste mit Risikoeinschätzung."},
      {"name": "Testcode schreiben",
       "system_prompt": "Schreibe daraus lauffähigen Testcode (Python/unittest, sofern "
                        "nichts anderes erkennbar ist). Ein Test je Fall, sprechende "
                        "Namen, Kommentar mit dem geprüften Verhalten."}]),
    ("Sicherheits-Audit", "security",
     "Prüft Code auf Schwachstellen und liefert Angriffsweg plus Gegenmaßnahme je Befund.",
     [{"name": "Angriffsfläche kartieren",
       "system_prompt": "Bestimme die Angriffsfläche des folgenden Codes: Wo kommen "
                        "fremde Daten herein, was wird ausgeführt, was wird gelesen "
                        "oder geschrieben, was geht nach außen?"},
      {"name": "Schwachstellen suchen",
       "system_prompt": "Suche entlang dieser Angriffsfläche nach Schwachstellen: "
                        "Injection, Pfad-Ausbruch, SSRF, unsichere Ausführung, "
                        "Datenlecks, fehlende Grenzen. Je Befund: Schweregrad, "
                        "konkreter Angriffsweg, betroffene Stelle."},
      {"name": "Gegenmaßnahmen",
       "system_prompt": "Formuliere je Befund die konkrete Gegenmaßnahme mit "
                        "Codebeispiel und ordne nach Dringlichkeit. Nenne am Ende, "
                        "was bereits gut abgesichert ist."}]),
    ("Lektorat", "lektorat",
     "Korrigiert Text und erklärt die wichtigsten Änderungen.",
     [{"name": "Korrigieren",
       "system_prompt": "Korrigiere Rechtschreibung, Grammatik, Zeichensetzung und "
                        "Stil des folgenden Textes. Erhalte Stimme und Aussage. "
                        "Gib nur den korrigierten Text aus."},
      {"name": "Änderungen erklären",
       "system_prompt": "Liste die wichtigsten Änderungen am Text mit kurzer "
                        "Begründung auf und nenne wiederkehrende Muster, auf die der "
                        "Autor künftig achten sollte."}]),
    ("Entscheidungshilfe", "entscheidung",
     "Beleuchtet eine Entscheidung von beiden Seiten und gibt eine begründete Empfehlung.",
     [{"name": "Optionen klären",
       "system_prompt": "Arbeite heraus, welche Optionen tatsächlich zur Wahl stehen, "
                        "welche Kriterien entscheidend sind und welche Informationen "
                        "fehlen. Ergänze eine oft übersehene Option."},
      {"name": "Gegenargumente",
       "system_prompt": "Argumentiere für jede Option so stark wie möglich — und "
                        "anschließend so scharf wie möglich dagegen. Benenne das "
                        "jeweils schlimmste realistische Ergebnis."},
      {"name": "Empfehlung",
       "system_prompt": "Gib eine klare, begründete Empfehlung: Was spricht dafür, "
                        "unter welchen Bedingungen wäre sie falsch, woran erkennt man "
                        "früh, dass man umsteuern sollte?"}]),
    ("Code-Erklärer", "codeerklaerer",
     "Erklärt fremden Code Zeile für Zeile und deckt Fehler, Sicherheitsrisiken und "
     "Verbesserungsmöglichkeiten auf.",
     [{"name": "Verstehen",
       "system_prompt": "Erkläre den folgenden Code: Zweck, Ablauf, Datenfluss, "
                        "verwendete Konzepte. Verständlich, aber technisch präzise."},
      {"name": "Prüfen",
       "system_prompt": "Prüfe denselben Code auf Bugs, Sicherheitsrisiken, "
                        "Fehlerbehandlung und Performance. Liefere konkrete "
                        "Verbesserungen mit Codebeispiel."}]),
]

SEED_TEMPLATES = [
    ("Ad-Copy-Optimierer", "Analysiert Anzeigentexte für Meta, Google und LinkedIn Ads und "
     "liefert konkrete Verbesserungshebel plus 3 Varianten.", "agent",
     [{"id": "ad_text", "type": "textarea", "label": "Aktueller Anzeigentext"},
      {"id": "platform", "type": "text", "label": "Plattform (Meta / Google / LinkedIn)"},
      {"id": "goal", "type": "text", "label": "Kampagnenziel"}],
     "Analysiere den folgenden Anzeigentext für {{platform}} mit dem Ziel '{{goal}}':\n\n"
     "{{ad_text}}\n\nLiefere: 1) Schwachstellen-Analyse (Hook, Nutzen, CTA), 2) konkrete "
     "Verbesserungshebel, 3) drei optimierte Varianten (kurz/mittel/lang).", "🎯"),
    ("Blog-Artikel-Optimierer", "Analysiert einen Blogartikel auf Suchintention, Struktur, "
     "Snippet-Chancen und Conversion-Elemente.", "agent",
     [{"id": "raw_text", "type": "textarea", "label": "Artikel-Text oder URL-Inhalt"},
      {"id": "focus_keyword", "type": "text", "label": "Fokus-Keyword"}],
     "Optimiere den folgenden Text auf das Keyword '{{focus_keyword}}'. Achte auf Lesbarkeit, "
     "SEO-Struktur (H2/H3), Suchintention, Featured-Snippet-Chancen und Conversion-Elemente:\n\n"
     "{{raw_text}}", "📝"),
    ("Case-Study aufbereiten", "Erzeugt aus den Angaben eines erfolgreichen Kundenprojekts "
     "eine professionelle Case Study im Problem-Lösung-Ergebnis-Format.", "skill",
     [{"id": "project", "type": "textarea", "label": "Projekt-Beschreibung & Ergebnisse"},
      {"id": "customer", "type": "text", "label": "Kunde / Branche"}],
     "Erstelle aus folgenden Angaben eine professionelle Case Study "
     "(Problem → Lösung → Ergebnis, mit Zwischenüberschriften und Zitat-Platzhalter). "
     "Kunde/Branche: {{customer}}\n\nProjekt:\n{{project}}", "📊"),
    ("First-Principles", "Führt Nutzer Schritt für Schritt durch das First-Principles Thinking: "
     "zerlegt ein Problem oder eine bestehende Lösung in Grundwahrheiten.", "skill",
     [{"id": "problem", "type": "textarea", "label": "Problem oder bestehende Lösung"}],
     "Wende First-Principles Thinking auf folgendes Problem an: Zerlege es in Grundwahrheiten, "
     "hinterfrage jede Annahme und baue daraus mindestens zwei neuartige Lösungsansätze auf:\n\n"
     "{{problem}}", "🧠"),
    ("ICP-Builder", "Erstellt aus Produkt- oder Nischenangaben ein vollständiges ICP- und "
     "Messaging-Framework für den DACH-Markt.", "skill",
     [{"id": "product", "type": "textarea", "label": "Produkt / Nische"},
      {"id": "price", "type": "text", "label": "Preismodell (optional)"}],
     "Erstelle für folgendes Produkt ein vollständiges ICP-Framework (DACH-Markt): "
     "Firmografie, Buying Center, Pains, Gains, Trigger-Events, Einwände plus passendes "
     "Messaging je Persona. Preismodell: {{price}}\n\nProdukt:\n{{product}}", "🎯"),
    ("Image-Prompt Architekt", "Wandelt vage Bildideen (Text oder Referenzbild) in "
     "hochoptimierte englische Prompts für moderne Bildmodelle um.", "skill",
     [{"id": "idea", "type": "textarea", "label": "Deine Bildidee"}],
     "Wandle folgende Bildidee in einen hochoptimierten ENGLISCHEN Bild-Prompt um "
     "(Haupt-Prompt, Negative-Prompt, Parameter-Empfehlung):\n\n{{idea}}", "🎨"),
    ("Kampagnenkonzept", "Erstellt aus einem kurzen Briefing ein vollständiges, umsetzbares "
     "Kampagnenkonzept inklusive Kernidee.", "skill",
     [{"id": "briefing", "type": "textarea", "label": "Kurz-Briefing"}],
     "Entwickle aus folgendem Briefing ein vollständiges Kampagnenkonzept: Kernidee, "
     "Botschaften je Segment, Kanal-Mix mit Budgetverteilung, KPI-Set, Zeitplan:\n\n{{briefing}}", "🚀"),
    ("Landingpage-Optimierer", "Analysiert eine Landingpage anhand ihres Inhalts und liefert "
     "priorisierte, konkrete Conversion-Optimierungshebel.", "agent",
     [{"id": "page_text", "type": "textarea", "label": "Landingpage-Text / Inhalt"},
      {"id": "goal", "type": "text", "label": "Conversion-Ziel"}],
     "Analysiere die folgende Landingpage mit Conversion-Ziel '{{goal}}'. Liefere priorisierte "
     "Optimierungshebel (Above-the-fold, Nutzenargumentation, Social Proof, CTA, Einwände) "
     "mit konkreten Textvorschlägen:\n\n{{page_text}}", "🖥️"),
    ("Marketing-E-Mail Optimierer", "Analysiert Newsletter, Kampagnen-Mails und Cold-Mails: "
     "Betreff, Preview-Text, Struktur, CTA. Liefert konkrete Verbesserungen.", "agent",
     [{"id": "email_text", "type": "textarea", "label": "E-Mail-Text"},
      {"id": "audience", "type": "text", "label": "Zielgruppe"}],
     "Analysiere folgende Marketing-E-Mail für die Zielgruppe '{{audience}}': Betreff, "
     "Preview-Text, Aufbau, Tonalität, CTA. Liefere konkrete Verbesserungen plus eine "
     "komplett überarbeitete Version:\n\n{{email_text}}", "✉️"),
]

SEED_PIPELINES = [{
    "name": "Web-Research Thinking",
    "description": "Recherche mit echtem Browser und Denkschritten dazwischen: "
                   "planen → im Browser nachsehen → nachdenken → Lücken schließen → "
                   "kritisch prüfen → Bericht. Ohne Browser weicht sie automatisch "
                   "auf die eingebaute Web-Recherche aus.",
    "steps": [
        {"type": "agent", "agent_name": "Recherche-Analyst",
         "instruction": "Zerlege dieses Thema in die drei wichtigsten Teilfragen und "
                        "benenne, welche Art von Quelle jede Frage beantworten kann. "
                        "Markiere, was du bereits sicher weißt und was nicht:"},
        {"type": "browser", "agent_name": "",
         "instruction": "Suche im Web nach Antworten auf die wichtigste offene "
                        "Teilfrage. Öffne die zwei aussagekräftigsten Treffer und "
                        "lies sie tatsächlich, statt nur die Suchergebnisse zu nehmen:"},
        {"type": "think", "agent_name": "",
         "instruction": "Ordne den bisherigen Stand und benenne die eine Frage, die "
                        "jetzt am dringendsten geklärt werden muss:"},
        {"type": "browser", "agent_name": "",
         "instruction": "Kläre gezielt die offen gebliebene Frage aus dem "
                        "Zwischenstand. Suche bewusst nach Quellen, die der bisherigen "
                        "Antwort widersprechen könnten:"},
        {"type": "agent", "agent_name": "Kritiker",
         "instruction": "Prüfe die gesammelten Erkenntnisse: Was ist wirklich durch "
                        "besuchte Seiten belegt, was hat das Modell ergänzt, welche "
                        "Quelle ist zweifelhaft?",},
        {"type": "agent", "agent_name": "Synthese-Agent",
         "instruction": "Schreibe daraus einen Rechercheberichtsentwurf: Antwort auf "
                        "die Ausgangsfrage, Belege mit Quellen-URLs, offene Punkte, "
                        "Einschätzung der Verlässlichkeit:"},
    ],
}, {
    "name": "Qualitäts-Kette",
    "description": "Code entsteht, wird getestet, sicherheitsgeprüft und dokumentiert — "
                   "der komplette Weg von der Anforderung zum belastbaren Ergebnis.",
    "steps": [
        {"type": "code", "agent_name": "",
         "instruction": "Setze diese Anforderung als lauffähigen Code um und teste ihn:",
         "iterations": 3, "workspace": "qualitaet"},
        {"type": "agent", "agent_name": "Test-Ingenieur",
         "instruction": "Entwirf zu diesem Code eine vollständige Testfall-Sammlung "
                        "inklusive der Randfälle, die hier fehlen:"},
        {"type": "agent", "agent_name": "Sicherheits-Prüfer",
         "instruction": "Prüfe Code und Tests auf Schwachstellen und nenne je Befund "
                        "Angriffsweg und Gegenmaßnahme:"},
        {"type": "agent", "agent_name": "Synthese-Agent",
         "instruction": "Fasse Code, Tests und Sicherheitsbefunde zu einer sauberen "
                        "Übergabe-Dokumentation zusammen:"},
    ],
}, {
    "name": "Entscheidung schärfen",
    "description": "Anforderungen klären, recherchieren, Gegenposition einnehmen und "
                   "zu einer begründeten Empfehlung kommen.",
    "steps": [
        {"type": "agent", "agent_name": "Anforderungs-Interviewer",
         "instruction": "Kläre, worum es bei dieser Entscheidung wirklich geht, und "
                        "benenne die offenen Fragen:"},
        {"type": "web", "agent_name": "",
         "instruction": "Recherchiere Fakten und Erfahrungswerte zu dieser Frage:"},
        {"type": "agent", "agent_name": "Advocatus Diaboli",
         "instruction": "Greife die naheliegende Antwort mit den stärksten Argumenten an:"},
        {"type": "agent", "agent_name": "Projekt-Planer",
         "instruction": "Leite daraus eine begründete Empfehlung mit konkretem "
                        "Vorgehen, Meilensteinen und Abbruchkriterien ab:"},
    ],
}, {
    "name": "Recherche → Konzept",
    "description": "Gemischter Workflow: erst Web-Recherche, dann Deep Research, dann "
                   "Konzeption durch den Strategen, geprüft vom Kritiker.",
    "steps": [
        {"type": "web", "agent_name": "",
         "instruction": "Recherchiere den aktuellen Stand zu diesem Thema:"},
        {"type": "research", "agent_name": "",
         "instruction": "Vertiefe die offenen Fragen in mehreren Runden:"},
        {"type": "agent", "agent_name": "Marketing-Stratege",
         "instruction": "Entwickle auf Basis der Recherche ein tragfähiges Konzept "
                        "mit Zielgruppe, Nutzenversprechen und Umsetzungsidee:"},
        {"type": "agent", "agent_name": "Kritiker",
         "instruction": "Prüfe Konzept und Rechercheteil auf unbelegte Annahmen, "
                        "erfundene Fakten und Schwachstellen:"},
    ],
}, {
    "name": "Software-Werkstatt",
    "description": "Vom Wunsch zur Spezifikation: Analyst klärt Anforderungen, "
                   "Code-Experte entwirft die Architektur, Kritiker sucht Schwachstellen, "
                   "Synthesizer schreibt das finale Dokument.",
    "steps": [
        {"agent_name": "Recherche-Analyst",
         "instruction": "Kläre die Anforderungen hinter diesem Wunsch: Was soll das System "
                        "wirklich leisten, für wen, unter welchen Randbedingungen? "
                        "Markiere Annahmen ausdrücklich als solche:"},
        {"agent_name": "Code-Experte",
         "instruction": "Entwirf auf dieser Basis eine konkrete technische Architektur: "
                        "Komponenten, Datenfluss, Technologiewahl mit Begründung, "
                        "Umsetzungsschritte. Nutze nur real existierende Technologien:"},
        {"agent_name": "Kritiker",
         "instruction": "Zerlege diesen Entwurf: unbelegte Annahmen, erfundene oder "
                        "unsichere Technologien, fehlende Fehlerbehandlung, Komplexität, "
                        "die dem Ziel schadet. Mit Korrekturvorschlägen:"},
        {"agent_name": "Sprach-Synthesizer",
         "instruction": "Erstelle aus Entwurf und Kritik ein finales, sauberes "
                        "Umsetzungsdokument: Zusammenfassung, Architektur, offene Punkte, "
                        "nächste Schritte:"},
    ],
}, {
    "name": "Konzept-Werkstatt",
    "description": "Drei Agenten arbeiten nacheinander ein Konzept aus: Stratege entwirft, "
                   "Code-Experte prüft Machbarkeit, Starter-Assistent finalisiert.",
    "steps": [
        {"agent_name": "Marketing-Stratege",
         "instruction": "Entwickle aus folgender Idee ein erstes Konzept mit Zielgruppe, "
                        "Nutzenversprechen und Grobstruktur:"},
        {"agent_name": "Code-Experte",
         "instruction": "Prüfe das folgende Konzept auf technische Machbarkeit, Risiken und "
                        "Aufwand. Ergänze eine Umsetzungs-Roadmap:"},
        {"agent_name": "Starter-Assistent",
         "instruction": "Verdichte alles zu einem finalen, sauber strukturierten "
                        "Konzeptdokument mit Zusammenfassung, nächsten Schritten und offenen "
                        "Fragen:"},
    ],
}, {
    "name": "Code-Fabrik",
    "description": "Coding-Agent entwirft, Code-Experte reviewt, Synthese-Agent "
                   "dokumentiert — kompletter Code-Workflow in einem Lauf.",
    "steps": [
        {"type": "agent", "agent_name": "Coding-Agent",
         "instruction": "Entwirf lauffähigen Code für folgende Anforderung "
                        "(vollständiger Codeblock plus kurze Erklärung):"},
        {"type": "agent", "agent_name": "Code-Experte",
         "instruction": "Reviewe den folgenden Code: Fehler, Risiken, Verbesserungen. "
                        "Gib danach die korrigierte Endfassung aus:"},
        {"type": "agent", "agent_name": "Synthese-Agent",
         "instruction": "Erstelle aus Code und Review eine saubere Dokumentation mit "
                        "Verwendung, Beispielen und Hinweisen:"},
    ],
}]

def init_db():
    """Legt Schema an und ergänzt fehlende Inhalte — auch bei bestehenden Datenbanken.

    Alle Seeds sind idempotent (Prüfung per Name): Ein Update bringt neue Agenten,
    Skills, Templates und Pipelines mit, ohne vorhandene Daten anzufassen. Zusätzlich
    werden Pipeline-Schritte geheilt, deren Agent-Verweis ins Leere zeigt.
    """
    conn = db()
    conn.executescript(SCHEMA)
    for mig in MIGRATIONS:
        try:
            conn.execute(mig)
        except sqlite3.OperationalError:
            pass  # Spalte existiert bereits

    fresh = not conn.execute("SELECT 1 FROM agents LIMIT 1").fetchone()

    for name, desc, sp, model, emoji in SEED_AGENTS:
        if not conn.execute("SELECT 1 FROM agents WHERE name=?", (name,)).fetchone():
            conn.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?)",
                         (nid(), name, desc, sp, model, emoji, now()))
    for name, desc, content, cat in SEED_PROMPTS:
        if not conn.execute("SELECT 1 FROM prompts WHERE name=?", (name,)).fetchone():
            conn.execute("INSERT INTO prompts VALUES(?,?,?,?,?,?)",
                         (nid(), name, desc, content, cat, now()))
    for name, trig, desc, steps in SEED_SKILLS + skills_paket.PAKET:
        if not conn.execute("SELECT 1 FROM skills WHERE name=?", (name,)).fetchone():
            conn.execute("INSERT INTO skills VALUES(?,?,?,?,?,?,?)",
                         (nid(), name, trig, desc,
                          json.dumps(steps, ensure_ascii=False), "", now()))
    for title, desc, kind, fields, base, emoji in SEED_TEMPLATES:
        if not conn.execute("SELECT 1 FROM templates WHERE title=?", (title,)).fetchone():
            conn.execute("INSERT INTO templates VALUES(?,?,?,?,?,?,?,?)",
                         (nid(), title, desc, kind,
                          json.dumps(fields, ensure_ascii=False), base, emoji, now()))

    # Pipelines: Agenten-Namen zur Laufzeit auf IDs auflösen
    amap = {r["name"]: r["id"] for r in
            conn.execute("SELECT id,name FROM agents").fetchall()}
    for pl in SEED_PIPELINES:
        if conn.execute("SELECT 1 FROM pipelines WHERE name=?", (pl["name"],)).fetchone():
            continue
        steps = [{"type": s.get("type", "agent"),
                  "ref_id": amap.get(s.get("agent_name", ""), ""),
                  "agent_id": amap.get(s.get("agent_name", ""), ""),
                  "label": s.get("agent_name", ""),
                  "instruction": s["instruction"]} for s in pl["steps"]]
        conn.execute("INSERT INTO pipelines VALUES(?,?,?,?,?)",
                     (nid(), pl["name"], pl["description"],
                      json.dumps(steps, ensure_ascii=False), now()))

    # Heilung: Schritte, deren Agent-Verweis leer ist oder ins Leere zeigt,
    # anhand des gespeicherten Namens neu verknüpfen.
    valid_ids = set(amap.values())
    for row in conn.execute("SELECT id, steps FROM pipelines").fetchall():
        try:
            steps = json.loads(row["steps"] or "[]")
        except Exception:
            continue
        changed = False
        for st in steps:
            if st.get("type", "agent") != "agent":
                continue
            ref = st.get("ref_id") or st.get("agent_id") or ""
            if ref in valid_ids:
                st.setdefault("ref_id", ref)
                continue
            fixed = amap.get(st.get("label", ""), "")
            if fixed:
                st["ref_id"] = st["agent_id"] = fixed
                changed = True
        if changed:
            conn.execute("UPDATE pipelines SET steps=? WHERE id=?",
                         (json.dumps(steps, ensure_ascii=False), row["id"]))

    if fresh:
        conn.execute("INSERT INTO notifications VALUES(?,?,?,?,?,?,?)",
                     (nid(), "Willkommen bei Dive on Wide \U0001F44B",
                      "Dein lokaler KI-Arbeitsplatz ist bereit. Pr\u00fcfe unter "
                      "Einstellungen die Verbindung zu Ollama und starte deinen "
                      "ersten Chat.", "system", 0, None, now()))
    conn.commit()
    conn.close()

# ============================================================================
# LLM-Anbindung — MULTI-PROVIDER (Ollama nativ + alles OpenAI-kompatible)
#
# Jeder Provider ist {id, name, type, base_url, api_key}. type "ollama" spricht
# Ollamas natives Protokoll (/api/chat, /api/tags, images, Modell-Verwaltung),
# type "openai" spricht /v1/chat/completions + /v1/models und deckt damit auf
# einen Schlag mlx_vlm, vLLM, LM Studio, llama.cpp-Server, OpenAI, OpenRouter …
# ab. Modelle werden intern als "providerid@@modelname" referenziert; ein reiner
# Modellname (ohne @@) läuft über den Standard-Provider (rückwärtskompatibel).
# ============================================================================

MODEL_SEP = "@@"

import gguf_dienst  # noqa: E402
import chatkontext  # noqa: E402
import skills_paket  # noqa: E402
import schwarm  # noqa: E402


def _ollama_entladen():
    """Vor dem Start eines Dateimodells: lokale Ollama-Modelle aus dem Speicher nehmen (laden bei Bedarf neu)."""
    for prov in get_providers():
        if (prov.get("type") or "ollama") != "ollama" or not _ist_lokal(prov.get("base_url") or ""):
            continue
        try:
            for m in ollama_json("/api/ps", timeout=5, base=prov.get("base_url")).get("models", []):
                ollama_json("/api/generate", {"model": m.get("name"), "keep_alive": 0}, timeout=30,
                            base=prov.get("base_url"))
        except Exception:
            continue


def _ist_lokal(url):
    return bool(re.match(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:|/|$)", url or ""))


GGUF = gguf_dienst.Dienst(os.path.join(STORAGE_DIR, "logs"), vor_start=_ollama_entladen)


def _gguf_platz(prov):
    """Vor einer Anfrage an ein lokales Ollama: ein unbenutztes Dateimodell beenden — beide passen nicht zugleich."""
    if GGUF.lage() and _ist_lokal(prov.get("base_url") or get_setting("OLLAMA_BASE_URL", "http://localhost:11434")):
        GGUF.platz_machen()

def get_providers():
    """Alle konfigurierten LLM-Provider. Ohne Konfiguration: ein Ollama-Standard."""
    roh = get_setting("LLM_PROVIDERS", "")
    if roh:
        try:
            provs = json.loads(roh)
            if isinstance(provs, list) and provs:
                return provs
        except Exception:
            pass
    return [{"id": "ollama", "name": "Ollama (lokal)", "type": "ollama",
             "base_url": get_setting("OLLAMA_BASE_URL", "http://localhost:11434"),
             "api_key": ""}]


def alle_provider():
    """Die konfigurierten Anbieter plus den Netzwerk-Anbieter, falls er lebt."""
    provs = list(get_providers())
    if _mesh_zustand.get("knoten") and not any(
            p.get("id") == MESH_PROVIDER_ID for p in provs):
        provs.append(mesh_provider())
    # Das verteilte Modell ist ein ganz gewoehnliches Modell, sobald es laeuft
    # — es soll ueberall waehlbar sein, nicht nur in der Netzansicht.
    if (_verteiltes["prozess"] is not None
            and _verteiltes["prozess"].poll() is None
            and not any(p.get("id") == VERTEILT_PROVIDER_ID for p in provs)):
        provs.append(verteilt_provider())
    provs += schueler_provider()
    return provs


SCHUELER_PROVIDER_PREFIX = "schueler-"


def schueler_provider():
    """Bereitgestellte Schüler (Training → „Als Modell bereitstellen“) als Anbieter.

    Wie das verteilte Modell nie gespeichert: Es gibt sie, solange ihr
    mlx_lm-Server läuft. `nutzlast` nennt in jeder Anfrage den Adapter — ohne
    ihn antwortet mlx_lm server mit dem nackten Grundmodell."""
    if not os.path.exists(os.path.join(TRAINING_DIR, "dienste.json")):
        return []
    return [{"id": SCHUELER_PROVIDER_PREFIX + d["id"], "name": "Schüler: %s" % d["name"], "type": "openai",
             "base_url": "http://127.0.0.1:%d" % d["port"], "api_key": "",
             "nutzlast": {"adapters": d["adapter"]}, "modell": d["modell"]}
            for d in training.Werkbank(TRAINING_DIR).dienste()]

MESH_PROVIDER_ID = "mesh"


def mesh_provider():
    """Der virtuelle Anbieter „Netzwerk".

    Damit werden die Modelle anderer Geraete ueberall dort waehlbar, wo Dive on Wide
    ohnehin Modelle anbietet: Chat, Agenten, Skills, Pipelines, Orchestrator.
    Ohne diesen Anbieter waere das Mesh eine Insel neben Dive on Wide statt sein
    Unterbau — und genau das war der Zweck der Uebung.

    Er existiert nur, solange das Netz laeuft, und wird nie gespeichert: Ein
    Anbieter, den es beim naechsten Start nicht gibt, hat in der Konfiguration
    nichts verloren."""
    return {"id": MESH_PROVIDER_ID, "name": "Netzwerk (andere Geräte)",
            "type": "mesh", "base_url": "", "api_key": ""}


def save_providers(provs):
    set_setting("LLM_PROVIDERS", json.dumps(provs, ensure_ascii=False))

def default_provider():
    return get_providers()[0]

def get_provider(pid):
    # Bewusst ueber alle_provider(): Sonst liesse sich ein Modell zwar
    # auswaehlen, aber nicht mehr aufloesen, sobald es im Netz liegt.
    for p in alle_provider():
        if p.get("id") == pid:
            return p
    return None

class UnbekannterProvider(RuntimeError):
    """Das Modell nennt einen Anbieter, den es gerade nicht gibt."""


def parse_model_ref(model):
    """'providerid@@modelname' → (provider_dict, modelname).

    Ein reiner Name laeuft ueber den Standard-Anbieter — das ist die
    Rueckwaertsvertraeglichkeit und soll so bleiben.

    Nennt der Name aber ausdruecklich einen Anbieter, den es nicht gibt, wird
    das gemeldet statt still auf den Standard zurueckzufallen. Frueher landete
    „mesh@@ollama@@qwen3:32b" bei ausgeschaltetem Netz bei Ollama, das dann mit
    „Connection refused" scheiterte — eine Meldung, aus der niemand schliessen
    kann, dass in Wahrheit das Netzwerk nicht laeuft."""
    model = model or ""
    if MODEL_SEP in model:
        pid, name = model.split(MODEL_SEP, 1)
        prov = get_provider(pid)
        if prov:
            return prov, name
        if pid == MESH_PROVIDER_ID:
            raise UnbekannterProvider(
                "Das Modell %r liegt auf einem anderen Gerät, aber das "
                "Netzwerk läuft nicht. Starte es unter Netzwerk, oder wähle "
                "ein Modell von diesem Rechner." % name)
        raise UnbekannterProvider(
            "Der Anbieter %r ist nicht (mehr) eingerichtet. Wähle unter "
            "Einstellungen → Modelle & Provider ein anderes Modell." % pid)
    return default_provider(), model

def ollama_url(path, base=None):
    base = (base or get_setting("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
    return base + path

def ist_zeitablauf(e):
    """Erkennt einen Zeitablauf, egal wie urllib ihn gerade verpackt.

    Beim Lesen kommt socket.timeout durch, beim Verbinden steckt er als
    `reason` in einer URLError. Beides muss gleich behandelt werden."""
    if isinstance(e, (socket.timeout, TimeoutError)):
        return True
    if isinstance(e, urllib.error.URLError) and isinstance(
            getattr(e, "reason", None), (socket.timeout, TimeoutError)):
        return True
    return "timed out" in str(e).lower()

class ModellFehler(RuntimeError):
    """Ein gescheiterter Modellaufruf — mit lesbarem Grund statt HTTP-Nummer.

    Am 17.09.2026 scheiterte eine Orchestrator-Pipeline nach 6 Minuten mit
    genau einer Zeile: „HTTP Error 500: Internal Server Error“. Der Grund stand
    nur in Ollamas eigenem Protokoll: das 27-B-Modell passte nicht mehr in den
    Metal-Speicher (panic: Insufficient Memory, danach „runtime OOM detected“).
    Eine nackte 500 ist für den Besitzer eine Sackgasse — deshalb übersetzt
    Dive on Wide sie hier in Ursache und Abhilfe. `speicher` sagt dem Aufrufer, dass
    ein Ausweichen auf ein kleineres Modell Sinn hat."""

    def __init__(self, text, speicher=False, modell="", code=0):
        super().__init__(text)
        self.speicher = speicher
        self.modell = modell
        self.code = code

_SPEICHER_WORTE = ("insufficient memory", "out of memory", "oom",
                   "not enough memory", "requires more system memory",
                   "kiogpucommandbuffer", "failed to allocate",
                   "unable to allocate", "speicher reicht")

def ist_speichernot(text):
    """Ist das ein Speicherproblem? Ollama formuliert es in vielen Varianten."""
    t = (text or "").lower()
    return any(w in t for w in _SPEICHER_WORTE)

# Wo Ollama sein Protokoll hinschreibt — je Betriebssystem ein anderer Ort.
# Nicht gefunden ist kein Fehler: dann fehlt nur die Zusatzzeile.
_OLLAMA_LOGS = ("~/.ollama/logs/server*.log", "~/Library/Logs/Ollama/server*.log",
                "%LOCALAPPDATA%/Ollama/server*.log", "/var/log/ollama*.log")
_LOG_MARKER = ("panic:", "level=error", "oom detected", "insufficient memory",
               "out of memory", "error loading model")

def ollama_log_grund(sekunden=600):
    """Die letzte Fehlerzeile aus Ollamas Protokoll, wenn sie frisch ist.

    Bei einem abgestürzten Runner antwortet Ollama nur mit einer leeren 500 —
    der eigentliche Satz („panic: mlx: [METAL] … Insufficient Memory“) steht im
    Protokoll. Gelesen wird nur das Ende der Datei; die wächst auf Megabyte."""
    jetzt = time.time()
    for muster in _OLLAMA_LOGS:
        muster = os.path.expandvars(os.path.expanduser(muster))
        for pfad in sorted(glob.glob(muster)):
            try:
                if jetzt - os.path.getmtime(pfad) > sekunden:
                    continue
                with open(pfad, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    f.seek(max(0, f.tell() - 200000))
                    zeilen = f.read().decode("utf-8", "replace").splitlines()
            except OSError:
                continue
            for z in reversed(zeilen[-600:]):
                if any(m in z.lower() for m in _LOG_MARKER):
                    return z.strip()[:300]
    return ""

def _fehler_koerper(e):
    """Was der Server im Fehlerfall selbst geschrieben hat (Ollama: {"error": …})."""
    if not isinstance(e, urllib.error.HTTPError):
        return ""
    try:
        roh = e.read().decode("utf-8", "replace")[:800]
    except Exception:
        return ""
    try:
        d = json.loads(roh)
        if isinstance(d, dict) and d.get("error"):
            return str(d["error"])[:600]
    except ValueError:
        pass
    return roh.strip()

def modellfehler_aus_http(e, name="", pfad=""):
    """HTTP-Fehler eines Ollama-Servers → ModellFehler mit Ursache und Abhilfe."""
    code = int(getattr(e, "code", 0) or 0)
    koerper = _fehler_koerper(e)
    protokoll = ollama_log_grund() if (code >= 500 or ist_speichernot(koerper)) else ""
    speicher = ist_speichernot(koerper) or ist_speichernot(protokoll)
    wer = "„%s“ " % name if name else ""
    if code == 404 and "model" in (koerper or "").lower():
        return ModellFehler(
            "Das Modell %sist auf diesem Ollama-Server nicht installiert. "
            "Hol es mit „ollama pull %s“ oder wähle unter Einstellungen → "
            "Modelle ein vorhandenes." % (wer, name or "<modell>"),
            modell=name, code=code)
    if speicher:
        ersatz = ausweichmodell(name)
        return ModellFehler(
            "Das Modell %spasste nicht in den Speicher — Ollama hat den Lauf "
            "abgebrochen (Speichernot).%s Abhilfe: ein kleineres Modell "
            "einstellen%s, den Kontext verkleinern (Einstellungen → NUM_CTX, "
            "aktuell %s) oder nichts anderes gleichzeitig rechnen lassen.%s"
            % (wer,
               " Ollama gibt nach so einem Abbruch alle geladenen Modelle frei, "
               "der nächste Versuch startet also mit leerem Speicher.",
               " (z. B. „%s“)" % ersatz if ersatz else "",
               get_setting("NUM_CTX", "16384"),
               "\n\nOllama-Protokoll: %s" % protokoll if protokoll else ""),
            speicher=True, modell=name, code=code)
    grund = koerper or "(Ollama hat keinen Grund mitgeteilt.)"
    return ModellFehler(
        "Der Modellaufruf %sist am Ollama-Server gescheitert (HTTP %d%s). "
        "Grund laut Server: %s%s"
        % (wer, code, ", %s" % pfad if pfad else "", grund,
           "\n\nOllama-Protokoll: %s" % protokoll if protokoll else ""),
        modell=name, code=code)

def ausweichmodell(ausser=""):
    """Ein kleineres, lauffähiges Modell für den zweiten Versuch.

    Wahl: das kleinste installierte Modell ab 2 GB — darunter liegen Winzlinge
    (0,5 B), die einen Plan nicht zustande bringen. Ein ausdrücklich gesetztes
    AUSWEICH_MODELL hat Vorrang."""
    gesetzt = get_setting("AUSWEICH_MODELL", "")
    if gesetzt:
        return gesetzt
    try:
        d = ollama_json("/api/tags", timeout=5)
    except Exception:
        return ""
    aus = (ausser or "").split("@@")[-1]
    kandidaten = [((m.get("size") or 0), m.get("name") or "")
                  for m in (d.get("models") or [])
                  if (m.get("name") or "") != aus and (m.get("size") or 0) > 0]
    gross = sorted(k for k in kandidaten if k[0] >= 2 * 1024 ** 3)
    return (gross or sorted(kandidaten) or [(0, "")])[0][1]

def modell_mit_ausweich(modell, aufruf, run_id=None, zweck="Der Schritt"):
    """Einen Modellaufruf ausführen und bei Speichernot einmal ausweichen.

    `aufruf(modell)` wird höchstens zweimal gerufen. Zurück kommt
    (Ergebnis, tatsächlich benutztes Modell) — der Aufrufer kann damit
    weiterarbeiten und es im Schrittprotokoll sichtbar machen. Der Ausweg wird
    nie stillschweigend genommen: er steht als Schritt im Protokoll."""
    try:
        return aufruf(modell), modell
    except ModellFehler as e:
        if not e.speicher:
            raise
        entladen = ollama_alle_entladen()
        if entladen:
            # Ein zweites geladenes Modell war das Problem — gleiches Modell,
            # jetzt mit freiem Speicher.
            if run_id:
                run_step(run_id, "Speichernot: %s entladen, zweiter Versuch mit „%s“"
                         % (", ".join(entladen)[:60], modell))
            try:
                return aufruf(modell), modell
            except ModellFehler as e2:
                if not e2.speicher:
                    raise
        ersatz = ausweichmodell(modell)
        if not ersatz or ersatz == modell:
            raise
        if run_id:
            run_step(run_id, "„%s“ passt nicht in den Speicher — %s läuft mit „%s“"
                     % (modell, zweck, ersatz))
        return aufruf(ersatz), ersatz

def ollama_json(path, payload=None, timeout=600, base=None):
    """Nicht-streamende Anfrage an einen Ollama-Server (nativ)."""
    url = ollama_url(path, base)
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise modellfehler_aus_http(e, (payload or {}).get("model", ""), path) from e
    except (ConnectionResetError, ConnectionAbortedError, http.client.RemoteDisconnected) as e:   # Windows: WinError 10053
        # Kleine Hardware (8-GB-VM, 05.10.2026): Linux beendete den Modellprozess wegen Speichernot; Dive on Wide sah nur
        # „Remote end closed connection“. Mitten in einer Antwort ist das fast immer der OOM-Killer.
        if path not in ("/api/chat", "/api/generate"):
            raise
        name = (payload or {}).get("model", "")
        raise ModellFehler(
            "Ollama hat die Verbindung ohne Antwort beendet — fast immer wurde das Modell „%s“ wegen Speichernot "
            "beendet. Abhilfe: ein kleineres Modell oder den Kontext verkleinern (Einstellungen → NUM_CTX, aktuell %s)."
            % (name, get_setting("NUM_CTX", "16384")), speicher=True, modell=name) from e

def _openai_headers(prov):
    h = {"Content-Type": "application/json"}
    if prov.get("api_key"):
        h["Authorization"] = "Bearer " + prov["api_key"]
        if "anthropic.com" in (prov.get("base_url") or ""):
            # Anthropics OpenAI-Zugang nimmt Bearer; die Modellliste (/v1/models) will den eigenen Kopf.
            h["x-api-key"] = prov["api_key"]
            h["anthropic-version"] = "2023-06-01"
    return h

def _openai_nachrichten(messages):
    """Ollama-Form (images: [base64]) → OpenAI-Form (content-Teile mit data-URL)."""
    aus = []
    for m in messages:
        if m.get("images"):
            teile = [{"type": "text", "text": m.get("content", "")}] + [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b}} for b in m["images"]]
            aus.append({"role": m["role"], "content": teile})
        else:
            aus.append({k: v for k, v in m.items() if k != "images"})
    return aus

def openai_url(prov, pfad):
    """Adresse einer OpenAI-Schnittstelle. Viele Anbieter nennen ihre Basis mit
    „/v1" (https://api.openai.com/v1), der Anbieter des verteilten Modells auch —
    daraus wurde /v1/v1/chat/completions, und jede Anfrage lief ins Leere."""
    basis = (prov.get("base_url") or "").rstrip("/")
    if basis.endswith("/v1"):
        basis = basis[:-3]
    # Gemini: https://generativelanguage.googleapis.com/v1beta/openai — die Version steht schon VOR „/openai“.
    if re.search(r"/v\d+[a-z0-9]*/openai$", basis) and pfad.startswith("/v1/"):
        return basis + pfad[3:]
    return basis + pfad

def _openai_chat_once(prov, model, messages, temperature,
                      json_mode=False, timeout=600):
    messages = _openai_nachrichten(messages)
    url = openai_url(prov, "/v1/chat/completions")
    payload = {"model": model, "messages": messages, "stream": False,
               "temperature": temperature}
    payload.update(prov.get("nutzlast") or {})
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=_openai_headers(prov))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    wahl = (data.get("choices") or [{}])[0]
    inhalt = wahl.get("message", {}).get("content", "")
    return werkbank.Abgeschnitten(inhalt) if wahl.get("finish_reason") == "length" else inhalt

def llm_chat_once(model, messages, temperature=None,
                  json_mode=False, no_think=False, timeout=600):
    """Eine nicht-streamende Chat-Completion über den passenden Provider.

    Zwei Schalter für Aufgaben, bei denen eine knappe Maschinenantwort zählt
    und nicht ein schöner Text — beide aus einem gescheiterten Nutzertest des
    Computer-Use gelernt und am echten Fall nachgemessen (gemma4:12b, Planner,
    3024x1964-Bildschirm, je 3 Läufe):

        Ist-Zustand (nichts davon)   26-140 s   618-3292 Ausgabe-Token
        nur json_mode                39-81 s    40-47 Token
        nur no_think                  2-4 s     40-59 Token
        beides zusammen               2 s       42-46 Token

    no_think ist der eigentliche Hebel: „Thinking“-Modelle wie gemma4 denken
    vor der Antwort seitenweise laut, und genau das kostet die Zeit — nicht die
    Antwort selbst. json_mode kommt oben drauf und macht die Antwort sicher
    auswertbar. `think:false` stört Modelle ohne diese Fähigkeit nicht
    (nachgemessen an qwen2.5-coder), darf also immer mitgeschickt werden.

    ACHTUNG: Keinen num_predict-Deckel setzen — ein abgeschnittenes JSON ist
    unbrauchbar (gemessen: Deckel 250 → leere Antwort)."""
    prov, name = parse_model_ref(model)
    temp = float(temperature if temperature is not None
                 else get_setting("TEMPERATURE", "0.7"))
    if prov.get("type") == "mesh":
        return _mesh_chat_once(name, messages, timeout)
    if prov.get("gguf"):
        with GGUF.benutzt(prov) as echt:
            return _openai_chat_once(echt, name, messages, temp, json_mode, timeout)
    if prov.get("type") == "openai":
        return _openai_chat_once(prov, name, messages, temp, json_mode, timeout)
    _gguf_platz(prov)
    payload = {"model": name, "messages": messages, "stream": False,
               "options": {"num_ctx": int(get_setting("NUM_CTX", "16384")),
                           "temperature": temp}}
    if json_mode:
        payload["format"] = "json"
    if no_think:
        payload["think"] = False
    data = ollama_json("/api/chat", payload, timeout=timeout,
                       base=prov.get("base_url"))
    inhalt = data.get("message", {}).get("content", "")
    # An der Kontextgrenze endet die Antwort ohne Fehler, nur kürzer. Wer das
    # nicht weiß (die Werkbank), hält sie für kaputtes JSON und rät falsch.
    voll = (data.get("prompt_eval_count") or 0) + (data.get("eval_count") or 0) >= payload["options"]["num_ctx"] - 1
    if data.get("done_reason") == "length" or voll:
        return werkbank.Abgeschnitten(inhalt)
    return inhalt

_faehigkeit_cache = {}

def modell_faehigkeiten(model):
    """Was kann dieses Modell? (vision, thinking, tools …) — laut Ollama selbst.

    Ollama beantwortet das unter /api/show exakt; raten anhand des Namens wäre
    unzuverlässig. Ergebnis wird gemerkt, die Antwort ändert sich praktisch nie."""
    prov, name = parse_model_ref(model)
    if prov.get("type") == "openai":
        return []                      # OpenAI-kompatible Server sagen es nicht
    schluessel = (prov.get("base_url") or "", name)
    if schluessel in _faehigkeit_cache:
        return _faehigkeit_cache[schluessel]
    try:
        d = ollama_json("/api/show", {"model": name}, timeout=10,
                        base=prov.get("base_url"))
        koennen = [str(c).lower() for c in (d.get("capabilities") or [])]
    except Exception:
        koennen = []
    _faehigkeit_cache[schluessel] = koennen
    return koennen

# Rückwärtskompatibler Name — alle 33 Aufrufer nutzen weiter ollama_chat_once,
# routen aber jetzt über den Multi-Provider-Router.
ollama_chat_once = llm_chat_once

LEER_HINWEIS = {
    "de": "⚠️ Das Modell hat nur nachgedacht, bis sein Kontext voll war — eine Antwort kam nicht mehr. Abhilfe: "
          "Kontext vergrößern (Einstellungen → Kontextfenster) oder „Denken im Chat“ abschalten.",
    "en": "⚠️ The model only thought until its context was full — no answer came. Fix: increase the context "
          "(Settings → context window) or switch off “thinking in chat”.",
}


def chat_denken(num_ctx):
    """Soll ein denkendes Modell im Chat denken? „auto“: nur, wenn der Kontext dafür Platz hat (07.10.2026: mit
    4 096 Token dachte Qwen 3.5 4B den Kontext voll und lieferte leere Antworten)."""
    wahl = get_setting("CHAT_DENKEN", "auto")
    if wahl == "aus":
        return False
    if wahl == "an":
        return None              # das Modell entscheidet
    return None if num_ctx >= 8192 else False


def llm_stream(model, messages, temperature=None, sprache="de"):
    """Generator: liefert Antwort-Textstücke, egal welcher Provider dahintersteht.

    Vereinheitlicht Ollama-NDJSON und OpenAI-SSE zu einem Strom von Text-Chunks —
    der HTTP-Endpunkt normalisiert das dann auf das bestehende Browser-Protokoll."""
    prov, name = parse_model_ref(model)
    temp = float(temperature if temperature is not None
                 else get_setting("TEMPERATURE", "0.7"))
    if prov.get("type") == "mesh":
        # Ein Auftrag im Netz kommt als Ganzes zurueck — es gibt nichts zu
        # stroemen. Statt eine Stueckelung vorzutaeuschen, kommt die Antwort
        # in einem Stueck. Die Oberflaeche kommt damit klar.
        yield _mesh_chat_once(name, messages, 300)
        return
    if prov.get("gguf"):
        with GGUF.benutzt(prov) as echt:
            yield from _openai_stream(echt, name, messages, temp)
        return
    if prov.get("type") == "openai":
        yield from _openai_stream(prov, name, messages, temp)
    else:
        _gguf_platz(prov)
        num_ctx = int(get_setting("NUM_CTX", "16384"))
        payload = {"model": name, "messages": messages, "stream": True,
                   "options": {"num_ctx": num_ctx, "temperature": temp}}
        if chat_denken(num_ctx) is False:
            payload["think"] = False
        req = urllib.request.Request(
            ollama_url("/api/chat", prov.get("base_url")),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        geliefert, gedacht = False, False
        with urllib.request.urlopen(req, timeout=600) as resp:
            for line in resp:
                line = line.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                gedacht = gedacht or bool(d.get("message", {}).get("thinking"))
                stueck = d.get("message", {}).get("content")
                if stueck:
                    geliefert = True
                    yield stueck
        # Nie eine stumme leere Antwort: Wer nichts bekommt, erfährt warum.
        if not geliefert and gedacht:
            yield LEER_HINWEIS["en" if sprache == "en" else "de"]

def _openai_stream(prov, name, messages, temp):
    url = openai_url(prov, "/v1/chat/completions")
    payload = {"model": name, "messages": messages, "stream": True,
               "temperature": temp}
    payload.update(prov.get("nutzlast") or {})
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=_openai_headers(prov))
    with urllib.request.urlopen(req, timeout=600) as resp:
        for line in resp:
            line = line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            daten = line[5:].strip()
            if daten == "[DONE]":
                break
            try:
                d = json.loads(daten)
                stueck = (d.get("choices") or [{}])[0].get("delta", {}).get("content")
                if stueck:
                    yield stueck
            except Exception:
                continue


def llm_list_models(ohne_netz=False):
    """Sammelt Modelle von ALLEN Providern. Namen als 'providerid@@modell'.

    `ohne_netz`: nur, was auf DIESEM Gerät läuft. Das braucht der eigene Knoten für
    sein Angebot — ohne diesen Schalter rief die Liste sich über Netz → Modellkarte →
    eigene Modelle selbst auf, bis zur Rekursionsgrenze. Auf dem Mac fiel das nicht
    auf; unter Windows kostet jede Stufe 2 s, der Testlauf hing über eine Stunde
    (Windows-VM, 29.09.2026)."""
    out = []
    for prov in alle_provider():
        if prov.get("type") == "mesh":
            if ohne_netz:
                continue
            for name, e in sorted(_mesh_netz_modelle().items()):
                out.append({"name": MESH_PROVIDER_ID + MODEL_SEP + name,
                            "label": name.split(MODEL_SEP)[-1],
                            "provider": prov.get("name", "Netzwerk"),
                            "provider_id": MESH_PROVIDER_ID,
                            "geraete": e})
            continue
        if prov.get("id") == VERTEILT_PROVIDER_ID:
            # Nicht nachfragen, sondern wissen: Dive on Wide hat dieses Modell selbst
            # gestartet und kennt seinen Namen. Nachzufragen ginge auch, aber
            # llama.cpp antwortet auf /v1/models im Ollama-Format
            # ({"models": …} statt {"data": …}) — die Liste bliebe leer, und
            # das verteilte Modell waere unsichtbar, obwohl es laeuft. Genau
            # das ist passiert.
            lage = mesh_verteilt_lage()
            if lage["laeuft"]:
                stufen = len((lage.get("plan") or {}).get("knoten", []))
                out.append({
                    "name": VERTEILT_PROVIDER_ID + MODEL_SEP + lage["modell"],
                    "label": "%s (verteilt über %d Geräte)"
                             % (os.path.basename(lage["modell"]), stufen),
                    "provider": prov.get("name", "Verteiltes Modell"),
                    "provider_id": VERTEILT_PROVIDER_ID,
                    "geraete": stufen})
            continue
        pid = prov.get("id", "")
        pname = prov.get("name", pid)
        if pid.startswith(SCHUELER_PROVIDER_PREFIX) and prov.get("modell"):
            # Nicht nachfragen: mlx_lm server listet unter /v1/models seinen
            # Hugging-Face-Zwischenspeicher, nicht das geladene Modell.
            out.append({"name": pid + MODEL_SEP + prov["modell"],
                        "label": "%s + Adapter" % os.path.basename(prov["modell"]),
                        "provider": pname, "provider_id": pid})
            continue
        if prov.get("gguf"):
            out.append({"name": pid + MODEL_SEP + gguf_dienst.modellname(prov["gguf"]["pfad"]),
                        "label": gguf_dienst.modellname(prov["gguf"]["pfad"]), "provider": pname, "provider_id": pid,
                        "size": prov["gguf"].get("groesse")})
            continue
        try:
            if prov.get("type") == "openai":
                extern = _anbieter_extern(prov)
                for m in _openai_models(prov):
                    out.append({"name": pid + MODEL_SEP + m, "label": m,
                                "provider": pname, "provider_id": pid, "extern": extern})
            else:
                data = ollama_json("/api/tags", timeout=6, base=prov.get("base_url"))
                for m in data.get("models", []):
                    mn = m.get("name")
                    out.append({"name": pid + MODEL_SEP + mn, "label": mn,
                                "provider": pname, "provider_id": pid,
                                "size": m.get("size"),
                                "family": (m.get("details") or {}).get("family", "")})
        except Exception:
            continue
    return out

def _openai_models(prov):
    url = openai_url(prov, "/v1/models")
    req = urllib.request.Request(url, headers=_openai_headers(prov))
    with urllib.request.urlopen(req, timeout=6) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [m.get("id") for m in data.get("data", []) if m.get("id")]

# ----------------------------------------------------------------------------
# Skill-Pipeline (läuft im Hintergrund-Thread → Inbox-Benachrichtigung)
# ----------------------------------------------------------------------------

def run_skill_pipeline(skill, user_input, model, session_id=None, run_id=None):
    try:
        current = user_input
        steps = json.loads(skill["steps"] or "[]")
        for i, step in enumerate(steps):
            run_step(run_id, "Schritt %d/%d — %s" % (i + 1, len(steps),
                     step.get("name", "verarbeite …")), "active")
            current = ollama_chat_once(model, [
                {"role": "system", "content": step.get("system_prompt", "")},
                {"role": "user", "content": current},
            ])
        art_id = deliver("Skill: %s" % skill["name"],
                         "# Skill-Ergebnis: %s\n\n%s\n" % (skill["name"], current),
                         skill["name"] + ".md",
                         "Skill „%s“ abgeschlossen ✅" % skill["name"],
                         "Das Ergebnis wurde als Artefakt gespeichert und liegt in "
                         "Dokumente & Artefakte.")
        if session_id:
            post_to_session(session_id, "**⚡ Skill „%s“ abgeschlossen**\n\n%s"
                            % (skill["name"], current))
        run_finish(run_id, "done", current, art_id)
    except RunCancelled as _abbruch:
        run_abgebrochen(run_id, "Skill „%s“" % skill["name"], session_id, grund=_abbruch)
    except Exception as e:
        fail("Skill „%s“ fehlgeschlagen ⚠️" % skill["name"], e)
        run_finish(run_id, "error", str(e))
        if session_id:
            post_to_session(session_id, "⚠️ Skill „%s“ fehlgeschlagen: %s"
                            % (skill["name"], e))

# ----------------------------------------------------------------------------
# WebBridge — abhängigkeitsfreie Web-Recherche (Suche + Seitenabruf, stdlib)
# ----------------------------------------------------------------------------

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

def check_url(url):
    """Erlaubt nur http/https und blockt interne Adressen.

    Ohne diese Prüfung könnte über /api/web/fetch mit file:// jede lokale Datei
    gelesen und mit http://127.0.0.1:… jeder interne Dienst angesprochen werden —
    und Dive on Wide lauscht auf allen Netzwerkschnittstellen.
    Wer bewusst lokale Adressen abrufen will (eigenes Wiki im LAN), setzt
    ALLOW_LOCAL_FETCH=1.
    """
    import ipaddress
    import socket
    parts = urllib.parse.urlparse(url or "")
    if parts.scheme not in ("http", "https"):
        raise ValueError("Nur http/https-Adressen sind erlaubt (war: %r)."
                         % (parts.scheme or "ohne Schema"))
    if not parts.hostname:
        raise ValueError("Adresse ohne Hostnamen.")
    if get_setting("ALLOW_LOCAL_FETCH", "0") == "1":
        return url
    try:
        infos = socket.getaddrinfo(parts.hostname, None)
    except socket.gaierror as e:
        raise ValueError("Adresse nicht auflösbar: %s" % e)
    for info in infos:
        _adresse_pruefen(info[4][0])
    return url

def _adresse_pruefen(adresse):
    """Wirft ValueError, wenn die IP-Adresse ins eigene Netz oder auf den Rechner zeigt."""
    import ipaddress
    ip = ipaddress.ip_address(str(adresse).split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped:   # ::ffff:127.0.0.1 ist 127.0.0.1
        ip = ip.ipv4_mapped
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified
            or ip.is_reserved or ip.is_multicast):
        raise ValueError(
            "Interne Adresse blockiert (%s). Für den Zugriff auf lokale "
            "Dienste ALLOW_LOCAL_FETCH=1 setzen." % ip)

def _verbinden_geprueft(adresse, *a, **k):
    """socket.create_connection, aber die Gegenstelle wird geprüft, bevor ein
    einziges Byte HTTP oder TLS hinausgeht.

    check_url löst den Namen auf und prüft; danach löst urllib ihn NOCH EINMAL
    auf. Ein Angreifer mit eigenem DNS und sehr kurzer Gültigkeit liefert beim
    ersten Mal eine öffentliche, beim zweiten Mal eine interne Adresse
    („DNS-Rebinding"). Hier wird die Adresse geprüft, mit der tatsächlich
    verbunden ist — dazwischen liegt keine weitere Auflösung mehr. Die
    TLS-Prüfung bleibt unberührt: Sie läuft danach wie immer gegen den Namen."""
    sock = socket.create_connection(adresse, *a, **k)
    try:
        _adresse_pruefen(sock.getpeername()[0])
    except ValueError:
        sock.close()
        raise
    return sock

class _GepruefteHttp(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_GepruefteHttpVerbindung, req)

class _GepruefteHttps(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_GepruefteHttpsVerbindung, req, context=self._context)

class _GepruefteHttpVerbindung(http.client.HTTPConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _verbinden_geprueft

class _GepruefteHttpsVerbindung(http.client.HTTPSConnection):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._create_connection = _verbinden_geprueft

class _GeprueftesFolgen(urllib.request.HTTPRedirectHandler):
    """Prueft das Weiterleitungsziel, BEVOR ihm gefolgt wird.

    urllib folgt Weiterleitungen selbst. Eine Pruefung, die erst hinterher auf
    `geturl()` schaut, kommt zu spaet: Die Anfrage an die interne Adresse ist
    dann laengst gestellt. Nachgewiesen am 23.09.2026 mit zwei kleinen Servern
    — der „interne Dienst" verzeichnete den Treffer, waehrend der Aufrufer nur
    die Daten nicht zu sehen bekam. Bei einem Dienst, der auf ein GET hin
    handelt (Router-Oberflaeche, Metadatendienst einer Cloud, ein Loesch-Link),
    ist der Schaden damit schon geschehen.

    Die Zieladresse stammt aus der Antwort einer fremden Seite. Sie ist also
    genau so wenig vertrauenswuerdig wie die erste."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl)


def _oeffner():
    """Ein Öffner, der interne Adressen auch ueber Weiterleitungen und einen
    DNS-Wechsel nicht anfasst.

    Mit einem eingestellten Proxy verbindet urllib zum Proxy, nicht zum Ziel,
    und der Proxy löst selbst auf; die Prüfung beim Verbinden träfe dann den
    Proxy (oft lokal) und gäbe keinen Schutz. Dort bleibt es bei check_url und
    der Weiterleitungsprüfung. Ebenso mit ALLOW_LOCAL_FETCH=1."""
    griffe = [_GeprueftesFolgen(), ausgang.Protokoll(STORAGE_DIR)]
    if get_setting("ALLOW_LOCAL_FETCH", "0") != "1" and not urllib.request.getproxies():
        griffe += [_GepruefteHttp(), _GepruefteHttps()]
    return urllib.request.build_opener(*griffe)


def _http_get(url, timeout=10, max_bytes=600_000):
    check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept-Language": "de,en;q=0.8"})
    with _oeffner().open(req, timeout=timeout) as r:
        if r.geturl() != url:      # Gürtel und Hosenträger
            check_url(r.geturl())
        return r.read(max_bytes).decode("utf-8", errors="replace")

def _strip_html(html):
    html = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    html = html_unescape(html)          # &amp; &#8211; &nbsp; … alle Entities
    return re.sub(r"\s+", " ", html).strip()

def parse_mojeek(html, max_results=5):
    """Zerlegt eine Mojeek-Ergebnisseite in [{title,url,snippet}] (ohne Netz)."""
    results = []
    for m in re.finditer(
            r'<a class="title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        url, title = m.group(1), _strip_html(m.group(2))
        if url.startswith("//"):
            url = "https:" + url
        results.append({"title": title, "url": url, "snippet": ""})
        if len(results) >= max_results:
            break
    snips = [_strip_html(s) for s in
             re.findall(r'<p class="s">(.*?)</p>', html, re.S)]
    for i, s in enumerate(snips[:len(results)]):
        results[i]["snippet"] = s
    return results

def parse_ddg_lite(html, max_results=5):
    """Zerlegt die schlanke DuckDuckGo-Seite in [{title,url,snippet}] (ohne Netz).

    Diese Quelle ist der schlüssellose Standard, seit Mojeek Anfragen ohne
    Browser mit 403 abweist — gemessen am 16.09.2026, als die Werkbank bei
    jeder Suche „(keine Treffer)“ bekam und in eine Schleife lief.
    """
    results = []
    for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*class=[\'"]result-link[\'"][^>]*>(.*?)</a>',
                         html, re.S):
        url, title = html_unescape(m.group(1)), _strip_html(m.group(2))
        if url.startswith("//"):
            url = "https:" + url
        if not url.startswith("http"):
            continue
        if urllib.parse.urlparse(url).netloc.endswith("duckduckgo.com"):
            continue          # Anzeigen (y.js?ad_domain=…) und Hilfeseiten, keine Treffer
        results.append({"title": title, "url": url, "snippet": ""})
        if len(results) >= max_results:
            break
    snips = [_strip_html(x) for x in
             re.findall(r'<td[^>]+class=[\'"]result-snippet[\'"][^>]*>(.*?)</td>', html, re.S)]
    for i, snip in enumerate(snips[:len(results)]):
        results[i]["snippet"] = snip
    return results


def parse_ddg_instant(raw, query, max_results=5):
    """DuckDuckGo Instant-Answer-JSON → [{title,url,snippet}] (Wissens-Fallback)."""
    results = []
    try:
        data = json.loads(raw)
    except ValueError:
        return results
    if data.get("AbstractText"):
        results.append({"title": data.get("Heading") or query,
                        "url": data.get("AbstractURL", ""),
                        "snippet": data["AbstractText"]})
    for t in data.get("RelatedTopics", []):
        if len(results) >= max_results:
            break
        if isinstance(t, dict) and t.get("Text") and t.get("FirstURL"):
            results.append({"title": t["Text"][:90], "url": t["FirstURL"],
                            "snippet": t["Text"]})
    return results

def parse_wikipedia(raw, max_results=5):
    """Wikipedia-OpenSearch-JSON [q,[titel],[beschr],[urls]] → Treffer (ohne Netz)."""
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    titel = data[1] if len(data) > 1 else []
    beschr = data[2] if len(data) > 2 else []
    urls = data[3] if len(data) > 3 else []
    out = []
    for i, t in enumerate(titel[:max_results]):
        out.append({"title": t, "url": urls[i] if i < len(urls) else "",
                    "snippet": beschr[i] if i < len(beschr) else ""})
    return out

def _http_get_retry(url, timeout=10, versuche=2):
    """HTTP-GET mit kleiner Wiederholungs-Schleife gegen kurzzeitige Aussetzer.

    Genau diese „Aktualisierungs-Schleife" macht die Suche stabil: ein einzelner
    Timeout oder ein kurzer Rate-Limit-Ausrutscher eines Anbieters wirft die
    Recherche nicht mehr um."""
    letzter = None
    for i in range(max(1, versuche)):
        try:
            return _http_get(url, timeout=timeout)
        except Exception as e:
            letzter = e
            time.sleep(0.6 * (i + 1))
    raise letzter

# ============================================================================
# Pluggbare Web-Bridge (Architektur nach dem Zwei-Schritt-Prinzip)
#
#   Schritt 1 SUCHE:  strukturierte JSON-API statt HTML-Parsing
#   Schritt 2 LESEN:  sauberes Markdown statt roher HTML-Wüste
#
# Standard ist schlüssellos und läuft sofort (Mojeek → DDG → Wikipedia). Wer
# stabile Ergebnisse will, trägt in den Einstellungen ein besseres Backend ein:
#   Suche:  Tavily · Serper · Brave (API-Key)  oder  SearXNG (eigene Instanz)
#   Lesen:  Firecrawl (Docker/API)  oder  Jina Reader (API-Key)
# Fällt ein konfiguriertes Backend aus, greift automatisch der keyless-Standard.
# ============================================================================

def _http_get_direct(url, timeout=15, headers=None, max_bytes=1_200_000):
    """HTTP-GET OHNE SSRF-Prüfung — NUR für vom Besitzer bewusst eingetragene
    Backends (SearXNG/Firecrawl/Jina), die auch lokal (Docker) liegen dürfen.
    Für beliebige Web-Seiten bleibt _http_get mit check_url zuständig."""
    h = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes).decode("utf-8", "replace")

def _http_post_json(url, payload, headers=None, timeout=25, max_bytes=1_500_000):
    """POST mit JSON-Body → Text. Für Such-/Scrape-APIs (Tavily, Serper, Firecrawl)."""
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json", "User-Agent": UA,
         "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes).decode("utf-8", "replace")

# --- Parser der strukturierten Such-Backends (ohne Netz testbar) ------------

def parse_searxng(raw, max_results=5):
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    out = []
    for r in data.get("results", []):
        if r.get("url") and r.get("title"):
            c = r.get("content", "") or ""
            out.append({"title": r["title"], "url": r["url"],
                        "snippet": c[:300], "content": c})
        if len(out) >= max_results:
            break
    return out

def parse_tavily(raw, max_results=5):
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    out = []
    for r in data.get("results", []):
        if r.get("url"):
            inhalt = r.get("raw_content") or r.get("content") or ""
            out.append({"title": r.get("title") or r["url"], "url": r["url"],
                        "snippet": (r.get("content") or "")[:300], "content": inhalt})
        if len(out) >= max_results:
            break
    return out

def parse_serper(raw, max_results=5):
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    out = []
    for r in data.get("organic", []):
        if r.get("link"):
            out.append({"title": r.get("title") or r["link"], "url": r["link"],
                        "snippet": r.get("snippet", "")})
        if len(out) >= max_results:
            break
    return out

def parse_brave(raw, max_results=5):
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    out = []
    for r in (data.get("web", {}) or {}).get("results", []):
        if r.get("url"):
            out.append({"title": r.get("title") or r["url"], "url": r["url"],
                        "snippet": r.get("description", "")})
        if len(out) >= max_results:
            break
    return out

# --- Such-Backends (Schritt 1) ----------------------------------------------

def _http_post_form(url, felder, timeout=15, max_bytes=600_000):
    """Formular-POST wie ein Browser — manche Suchseiten antworten nur darauf."""
    check_url(url)
    daten = urllib.parse.urlencode(felder).encode("utf-8")
    req = urllib.request.Request(url, data=daten, headers={
        "User-Agent": UA, "Accept-Language": "de,en;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded"})
    with _oeffner().open(req, timeout=timeout) as r:
        if r.geturl() != url:
            check_url(r.geturl())
        return r.read(max_bytes).decode("utf-8", errors="replace")


class DdgSperre(RuntimeError):
    pass


# DuckDuckGo zeigt nach einigen Anfragen eine Bot-Prüfung („Select all squares
# containing a duck“). Dive on Wide löst so etwas nicht — es fragt 15 Minuten lang
# nicht mehr nach und sagt das, statt „keine Treffer“ zu melden.
_ddg_gesperrt = {"bis": 0.0}


def _such_ddg_lite(query, n):
    """Die schlanke DuckDuckGo-Seite — **als POST**.

    Gemessen am 16.09.2026: Ein GET auf dieselbe Adresse beantwortet DuckDuckGo
    mit HTTP 202 und einer Seite ohne Ergebnisse, egal mit welcher Kennung; der
    Formular-POST liefert zuverlässig die volle Trefferliste. Deshalb POST.
    """
    roh = _http_post_form("https://lite.duckduckgo.com/lite/", {"q": query})
    if "anomaly" in roh[:20000].lower() and "result-link" not in roh:
        raise DdgSperre("Bot-Prüfung (CAPTCHA) — vorübergehend gesperrt, wird nicht gelöst")
    return parse_ddg_lite(roh, n)

def _such_mojeek(q, n):
    return parse_mojeek(_http_get_retry("https://www.mojeek.com/search?q=" + q), n)

def _such_ddg(q, query, n):
    return parse_ddg_instant(_http_get_retry(
        "https://api.duckduckgo.com/?format=json&no_html=1&q=" + q), query, n)

def parse_wikipedia_volltext(raw, max_results=5, lang="de"):
    """Wikipedia-Volltextsuche (list=search) → [{title,url,snippet}] (ohne Netz)."""
    try:
        daten = json.loads(raw)
    except (ValueError, TypeError):
        return []
    aus = []
    for t in (daten.get("query", {}) or {}).get("search", [])[:max_results]:
        titel = str(t.get("title") or "")
        if not titel:
            continue
        aus.append({"title": titel,
                    "url": "https://%s.wikipedia.org/wiki/%s" % (lang, urllib.parse.quote(titel.replace(" ", "_"))),
                    "snippet": _strip_html(str(t.get("snippet") or ""))})
    return aus


def _such_wikipedia_volltext(q, n):
    """Volltextsuche statt Titelanfang — findet auch, was nicht am Wortanfang steht.

    Die Titel-Autovervollständigung (opensearch) liefert für Fachbegriffe wie
    „knowledge distillation large language models“ nichts; die Volltextsuche
    findet den passenden Artikel. Diese Quelle ist unbeschränkt und stabil und
    damit der letzte, verlässliche Anker der schlüssellosen Kette.
    """
    for lang in ("de", "en"):
        res = parse_wikipedia_volltext(_http_get_retry(
            "https://%s.wikipedia.org/w/api.php?action=query&list=search&srsearch=%s"
            "&srlimit=%d&format=json&formatversion=2" % (lang, q, n)), n, lang)
        if res:
            return res
    return []


def _such_wikipedia(q, n):
    for lang in ("de", "en"):
        res = parse_wikipedia(_http_get_retry(
            "https://%s.wikipedia.org/w/api.php?action=opensearch"
            "&limit=%d&namespace=0&format=json&search=%s" % (lang, n, q)), n)
        if res:
            return res
    return []

# --- Der Fächer: schlüssellose Quellen, die nicht sperren -------------------
#
# Am 17.09.2026 gemessen: DuckDuckGo drosselt nach wenigen Anfragen (HTTP 202
# mit leerer Seite) und zeigt einem echten Browser ein CAPTCHA; Mojeek antwortet
# ohne Browser mit 403. Eine einzelne freie Suchmaschine ist also nie garantiert.
# Diese sechs Quellen antworten ohne Schlüssel, ohne Drosselung und in
# Sekundenbruchteilen — und decken zusammen das ab, wofür Dive on Wide benutzt wird:
# Begriffe, Papers, Programmierfragen, Code, Diskussionen, Pakete.

def _json_holen(url, timeout=12):
    return json.loads(_http_get(url, timeout=timeout, max_bytes=400_000))


def _such_wikipedia_api(query, n, lang="de"):
    daten = _json_holen("https://%s.wikipedia.org/w/api.php?action=query&list=search&srsearch=%s"
                        "&srlimit=%d&format=json&formatversion=2"
                        % (lang, urllib.parse.quote_plus(query), n))
    return [{"title": t["title"],
             "url": "https://%s.wikipedia.org/wiki/%s" % (lang, urllib.parse.quote(t["title"].replace(" ", "_"))),
             "snippet": _strip_html(t.get("snippet") or ""), "quelle": "Wikipedia"}
            for t in (daten.get("query", {}) or {}).get("search", [])[:n]]


def _such_arxiv(query, n):
    roh = _http_get("http://export.arxiv.org/api/query?search_query=all:%s&max_results=%d"
                    % (urllib.parse.quote(query), n), timeout=15, max_bytes=400_000)
    aus = []
    for eintrag in re.findall(r"<entry>(.*?)</entry>", roh, re.S)[:n]:
        titel = re.search(r"<title>(.*?)</title>", eintrag, re.S)
        link = re.search(r'<id>(.*?)</id>', eintrag, re.S)
        kurz = re.search(r"<summary>(.*?)</summary>", eintrag, re.S)
        if titel and link:
            aus.append({"title": " ".join(html_unescape(titel.group(1)).split()),
                        "url": link.group(1).strip(),
                        "snippet": " ".join(html_unescape(kurz.group(1)).split())[:400] if kurz else "",
                        "quelle": "arXiv"})
    return aus


def _such_hackernews(query, n):
    daten = _json_holen("https://hn.algolia.com/api/v1/search?query=%s&hitsPerPage=%d"
                        % (urllib.parse.quote_plus(query), n))
    aus = []
    for t in daten.get("hits", [])[:n]:
        url = t.get("url") or ("https://news.ycombinator.com/item?id=%s" % t.get("objectID"))
        aus.append({"title": t.get("title") or t.get("story_title") or "(ohne Titel)", "url": url,
                    "snippet": ((t.get("story_text") or t.get("comment_text") or "")[:300] or
                                "%s Punkte, %s Kommentare" % (t.get("points"), t.get("num_comments"))),
                    "quelle": "Hacker News"})
    return aus


def _such_stackexchange(query, n, site="stackoverflow"):
    daten = _json_holen("https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=relevance"
                        "&q=%s&site=%s&pagesize=%d&filter=default"
                        % (urllib.parse.quote_plus(query), site, n))
    return [{"title": html_unescape(t.get("title") or ""), "url": t.get("link") or "",
             "snippet": "%s Stimmen, %s Antworten%s" % (t.get("score"), t.get("answer_count"),
                                                        ", beantwortet" if t.get("is_answered") else ""),
             "quelle": "Stack Overflow"}
            for t in daten.get("items", [])[:n] if t.get("link")]


def _such_github(query, n):
    daten = _json_holen("https://api.github.com/search/repositories?q=%s&per_page=%d"
                        % (urllib.parse.quote_plus(query), n))
    return [{"title": "%s (%s ★)" % (t.get("full_name"), t.get("stargazers_count")),
             "url": t.get("html_url") or "", "snippet": (t.get("description") or "")[:300],
             "quelle": "GitHub"}
            for t in daten.get("items", [])[:n] if t.get("html_url")]


def _such_pypi(query, n):
    name = re.sub(r"[^a-zA-Z0-9._-]", "", query.split()[0]) if query.split() else ""
    if not name:
        return []
    daten = _json_holen("https://pypi.org/pypi/%s/json" % urllib.parse.quote(name))
    info = daten.get("info") or {}
    return [{"title": "%s %s" % (info.get("name"), info.get("version")),
             "url": info.get("project_url") or info.get("package_url") or "",
             "snippet": (info.get("summary") or "")[:300], "quelle": "PyPI"}]


FAECHER = (("Wikipedia", _such_wikipedia_api), ("arXiv", _such_arxiv),
           ("Stack Overflow", _such_stackexchange), ("GitHub", _such_github),
           ("Hacker News", _such_hackernews), ("PyPI", _such_pypi))


def _faecher_gewichte(query):
    """Welche Quelle passt zur Frage? Einfache Regeln, kein Orakel."""
    f = query.lower()
    gewicht = {"Wikipedia": 2, "arXiv": 1, "Stack Overflow": 1, "GitHub": 1, "Hacker News": 1, "PyPI": 0}
    if any(w in f for w in ("fehler", "error", "exception", "traceback", "wie ", "how to", "python",
                            "javascript", "sql", "bash")):
        gewicht["Stack Overflow"] += 3
        gewicht["GitHub"] += 1
    if any(w in f for w in ("paper", "arxiv", "modell", "model", "transformer", "distillation",
                            "lora", "training", "benchmark")):
        gewicht["arXiv"] += 3
    if any(w in f for w in ("bibliothek", "library", "paket", "package", "pip", "install")):
        gewicht["PyPI"] += 3
        gewicht["GitHub"] += 1
    if any(w in f for w in ("repo", "github", "quelltext", "source code", "implementierung")):
        gewicht["GitHub"] += 3
    if any(w in f for w in ("news", "diskussion", "meinung", "erfahrung", "vergleich")):
        gewicht["Hacker News"] += 2
    if any(w in f for w in ("was ist", "wer ist", "definition", "geschichte", "bedeutet")):
        gewicht["Wikipedia"] += 3
    return gewicht


FUELLWORTE = frozenset("""was wie wer warum wieso wann wo und oder aber der die das den dem des ein eine
einen einem eines ich mir mich du dir dich wir uns ihr sie es man kann muss soll darf für von mit ohne
bei zum zur nach über unter auf aus ist sind war waren hat habe haben wird werden nimmt nehme macht
machen gibt geben bitte danke the and for how what why does with from this that into your you are was
were has have will can should would about behebe behebt beheben loese lösen fixe fixen mache machst
brauche braucht brauchen finde finden zeige zeigen erklaere erklären sage sagen weiss wissen bekomme
bekommen funktioniert geht welche welcher welches viel viele mehr besser gut schlecht bitte""".split())

# Wörter, die in fast jeder Frage stehen können und allein nichts finden.
SCHWACHE_WORTE = frozenset("""bibliothek library paket package tool werkzeug programm software
methode verfahren ansatz beispiel frage antwort problem fehler moeglichkeit""".split())


def _stichworte(frage):
    """Aus einer ganzen Frage die Wörter ziehen, mit denen man wirklich sucht.

    Fachbegriffe zuerst: Was Großbuchstaben im Wortinneren hat (LoRA, GPT,
    ModuleNotFoundError) oder Ziffern (K3, 3.11), ist fast immer das Gesuchte.
    Ohne diese Gewichtung landete „Welche Bibliothek nimmt man für LoRA
    Feintuning?" bei der Deutschen Nationalbibliothek (gemessen 17.09.2026).
    """
    roh = re.findall(r"[\w äöüÄÖÜß.+#-]*?([\wäöüÄÖÜß.+#-]{2,})", frage) or frage.split()
    bewertet = []
    for i, wort in enumerate(re.findall(r"[\wäöüÄÖÜß.+#_-]{2,}", frage)):
        klein = wort.lower().strip(".-_")
        if not klein or klein in FUELLWORTE:
            continue
        punkte = 1.0
        if re.search(r"[A-ZÄÖÜ]", wort[1:]):          # LoRA, ModuleNotFoundError, GPT
            punkte += 3
        elif wort[:1].isupper() and i > 0:            # Eigenname mitten im Satz
            punkte += 1.5
        if re.search(r"\d", wort):                    # K3, 3.11
            punkte += 1.5
        if klein in SCHWACHE_WORTE:
            punkte -= 1.5
        punkte += min(len(klein), 12) / 12.0
        bewertet.append((punkte, i, wort))
    # Nicht kürzen, nur sieben: Wer "Kimi K3" fragt, verliert sonst "Kimi",
    # weil "K3" technischer aussieht. Die Gewichtung dient dem Sortieren der
    # Treffer, nicht dem Wegwerfen von Suchwörtern.
    behalten = [(i, w) for _p, i, w in bewertet]
    behalten.sort()
    gewaehlt = [w for _i, w in behalten][:8]
    if not gewaehlt:
        gewaehlt = frage.split()[:6]
    schwer = sorted(bewertet, key=lambda x: -x[0])[:4]
    return " ".join(gewaehlt), [w.lower() for _p, _i, w in schwer]


def _ohne_pflichtwort(treffer, query):
    """Fehlt dem Treffer eine Versions- oder Typangabe der Frage (3.14, K3, RTX 4090)?

    Anwendungstest 29.09.2026: DuckDuckGo sperrte, und für „Python 3.14
    Neuerungen“ lieferten die Ausweichquellen TI-Nspire, die F-16 („Rafael
    Python 3“) und alte arXiv-Papers — als Quellen eines Rechercheberichts.
    Ein Wort passte, die Version nicht."""
    pflicht = [w.lower().strip(".-") for w in re.findall(r"[\wäöüÄÖÜß.+#-]*\d[\wäöüÄÖÜß.+#-]*", query or "")]
    # Jahreszahlen („erschienen 2025“) sind Umstand, nicht Gegenstand; von mehreren
    # Versionsangaben („RTX 4090 oder 3090“) genügt eine.
    pflicht = [w for w in pflicht if len(w) >= 2 and not re.fullmatch(r"(19|20)\d\d", w)]
    if not pflicht:
        return False
    text = " ".join(str(treffer.get(k) or "") for k in ("title", "snippet", "url")).lower()
    return not any(w in text or w.replace(".", "") in text for w in pflicht)


def _passung(treffer, worte):
    """Wie gut passt ein Treffer zur Frage? Anteil der Suchwörter, die vorkommen."""
    if not worte:
        return 0.0
    text = ((treffer.get("title") or "") + " " + (treffer.get("snippet") or "")).lower()
    return sum(1 for w in worte if w in text) / float(len(worte))


def _such_faecher(query, max_results):
    """Alle schlüssellosen Quellen gleichzeitig fragen und nach Passung mischen.

    Parallel, damit es eine halbe Sekunde dauert statt drei. Eine Quelle, die
    ausfällt, nimmt die anderen nicht mit — das ist der ganze Sinn des Fächers.
    An jede Quelle gehen die **Stichwörter**, nicht der ganze Fragesatz: „Wie
    behebe ich einen Python ModuleNotFoundError?" findet als Frage nichts, als
    „Python ModuleNotFoundError" alles.
    """
    stichworte, worte = _stichworte(query)
    gewicht = _faecher_gewichte(query)
    ergebnisse, sperre = {}, threading.Lock()

    def fragen(name, fn):
        try:
            treffer = fn(stichworte, max(2, min(4, max_results)))
        except Exception:
            treffer = []
        with sperre:
            ergebnisse[name] = treffer

    faeden = [threading.Thread(target=fragen, args=(name, fn), daemon=True)
              for name, fn in FAECHER if gewicht.get(name, 0) > 0]
    for f in faeden:
        f.start()
    for f in faeden:
        f.join(timeout=8)          # eine lahme Quelle hält den Fächer nicht auf
    # Bewerten statt bloß abwechseln: Gewicht der Quelle plus Passung zum Text.
    # Ohne die Passung füllte arXiv eine Frage nach einem Python-Fehler mit
    # Papers auf, nur weil Stack Overflow nichts hergab (gemessen 17.09.2026).
    bewertet, gesehen = [], set()
    for name, liste in ergebnisse.items():
        for rang, t in enumerate(liste or []):
            url = t.get("url")
            if not url or url in gesehen:
                continue
            gesehen.add(url)
            passung = _passung(t, worte)
            if _ohne_pflichtwort(t, query):
                continue
            if passung == 0 and gewicht.get(name, 0) < 3:
                continue          # weder passend noch erwartet: weglassen
            t["punkte"] = gewicht.get(name, 0) * 0.5 + passung * 3 - rang * 0.2
            bewertet.append(t)
    bewertet.sort(key=lambda t: -t["punkte"])
    for t in bewertet:
        t.pop("punkte", None)
    return bewertet[:max_results]


def _search_keyless(query, max_results):
    """Schlüsselloser Standard: DDG-Lite → Mojeek → DDG-Instant → Wikipedia.

    Die Reihenfolge ist Erfahrung, keine Meinung: Mojeek antwortet auf Anfragen
    ohne Browser inzwischen mit 403, und die Instant-Answers von DuckDuckGo sind
    für Fachbegriffe meist leer. Die schlanke DuckDuckGo-Seite liefert echte
    Ergebnislisten und steht deshalb vorn; die übrigen bleiben als Ausweichweg.
    """
    treffer, _gruende = _search_keyless_diagnose(query, max_results)
    return treffer


def _search_keyless_diagnose(query, max_results):
    """Wie _search_keyless, gibt aber zusätzlich zurück, woran es lag.

    Ohne diese Begründung antwortete die Websuche dem Agenten nur „(keine
    Treffer)“ — und ein kleines Modell probiert dann zwanzig Suchbegriffe
    durch, statt zu merken, dass die Quelle gesperrt ist (gemessen 16.09.2026).
    """
    q = urllib.parse.quote_plus(query)
    quellen = (("DuckDuckGo", lambda: _such_ddg_lite(query, max_results)),
               ("Mojeek", lambda: _such_mojeek(q, max_results)),
               ("DuckDuckGo-Kurzantwort", lambda: _such_ddg(q, query, max_results)),
               ("Fächer (Wikipedia, arXiv, Stack Overflow, GitHub, HN, PyPI)",
                lambda: _such_faecher(query, max_results)),
               ("Wikipedia-Volltext", lambda: _such_wikipedia_volltext(q, max_results)),
               ("Wikipedia-Titel", lambda: _such_wikipedia(q, max_results)))
    gruende = []
    for nr, (name, fn) in enumerate(quellen):
        if nr == 0 and time.time() < _ddg_gesperrt["bis"]:
            gruende.append("DuckDuckGo: Bot-Prüfung, pausiert bis %s" % time.strftime("%H:%M", time.localtime(_ddg_gesperrt["bis"])))
            continue
        try:
            res = fn()
            if nr >= 3:          # Ausweichquellen ohne eigene Rangfolge: Version muss stimmen
                res = [t for t in res if not _ohne_pflichtwort(t, query)]
            if res:
                return res, gruende
            gruende.append("%s: keine Treffer" % name)
        except DdgSperre as e:
            _ddg_gesperrt["bis"] = time.time() + 15 * 60
            gruende.append("DuckDuckGo: %s" % e)
        except Exception as e:
            gruende.append("%s: %s" % (name, str(e)[:80]))
    return [], gruende

def _search_tavily(query, n):
    key = get_setting("TAVILY_API_KEY", "").strip()
    if not key:
        return []
    raw = _http_post_json("https://api.tavily.com/search",
        {"api_key": key, "query": query, "max_results": n,
         "search_depth": "basic", "include_raw_content": False}, timeout=25)
    return parse_tavily(raw, n)

def _search_serper(query, n):
    key = get_setting("SERPER_API_KEY", "").strip()
    if not key:
        return []
    raw = _http_post_json("https://google.serper.dev/search",
        {"q": query, "num": n}, headers={"X-API-KEY": key}, timeout=20)
    return parse_serper(raw, n)

def _search_brave(query, n):
    key = get_setting("BRAVE_API_KEY", "").strip()
    if not key:
        return []
    raw = _http_get_direct(
        "https://api.search.brave.com/res/v1/web/search?count=%d&q=%s"
        % (n, urllib.parse.quote_plus(query)),
        headers={"X-Subscription-Token": key, "Accept": "application/json"}, timeout=20)
    return parse_brave(raw, n)

def _search_searxng(query, n):
    base = get_setting("SEARXNG_URL", "").strip().rstrip("/")
    if not base:
        return []
    raw = _http_get_direct(base + "/search?format=json&safesearch=0&q="
                           + urllib.parse.quote_plus(query), timeout=20)
    return parse_searxng(raw, n)

_SEARCH_BACKENDS = {"tavily": _search_tavily, "serper": _search_serper,
                    "brave": _search_brave, "searxng": _search_searxng,
                    "faecher": _such_faecher}

def web_search(query, max_results=5):
    """Websuche über das konfigurierte Backend, mit keyless-Fallback.

    SEARCH_BACKEND wählt die Quelle (auto|tavily|serper|brave|searxng). Ein
    konfiguriertes Backend, das ausfällt oder nichts liefert, fällt automatisch
    auf den schlüssellosen Standard zurück — die Recherche bricht nie ganz weg.
    Gibt [{title,url,snippet[,content]}] zurück."""
    backend = get_setting("SEARCH_BACKEND", "auto")
    fn = _SEARCH_BACKENDS.get(backend)
    if fn:
        try:
            res = fn(query, max_results)
            if res:
                return res
        except Exception:
            pass          # Backend versagte → keyless-Fallback
    return _search_keyless(query, max_results)

# --- Lese-Backends (Schritt 2): sauberes Markdown statt HTML-Wüste ----------

def _fetch_firecrawl(url):
    base = get_setting("FIRECRAWL_URL", "").strip().rstrip("/")
    key = get_setting("FIRECRAWL_API_KEY", "").strip()
    if not base:
        base = "https://api.firecrawl.dev" if key else ""
    if not base:
        return ""
    hdr = {"Authorization": "Bearer " + key} if key else {}
    raw = _http_post_json(base + "/v1/scrape",
        {"url": url, "formats": ["markdown"], "onlyMainContent": True},
        headers=hdr, timeout=45)
    d = json.loads(raw)
    return (d.get("data", {}) or {}).get("markdown", "") or ""

def _fetch_jina(url):
    key = get_setting("JINA_API_KEY", "").strip()
    hdr = {"Authorization": "Bearer " + key} if key else {}
    return _http_get_direct("https://r.jina.ai/" + url, timeout=30, headers=hdr)

def web_fetch(url, max_chars=6000):
    """Liest eine Seite als sauberen Text/Markdown — über das konfigurierte
    FETCH_BACKEND (auto|firecrawl|jina), sonst eingebautes HTML-Strippen.

    check_url gilt vor JEDEM Weg: Ein selbst betriebenes Firecrawl steht oft im
    eigenen Netz und riefe eine interne Adresse sonst stellvertretend ab."""
    check_url(url)
    fb = get_setting("FETCH_BACKEND", "auto")
    try:
        if fb == "firecrawl":
            md = _fetch_firecrawl(url)
            if md:
                return md[:max_chars]
        elif fb == "jina":
            md = _fetch_jina(url)
            if md:
                return md[:max_chars]
    except Exception:
        pass
    return _seite_zu_text(_http_get(url))[:max_chars]


def _seite_zu_text(html):
    """HTML → lesbarer Text mit Struktur, ohne fremde Bibliothek.

    Vorher ging alles durch einen groben Tag-Entferner: Navigation, Fußzeilen und
    Zustimmungsbanner landeten mitten im Text, und Überschriften verschwanden.
    Ein Modell, das daraus antworten soll, bekommt so vor allem Menüs zu lesen.
    Hier wird stattdessen der Hauptteil gesucht (main/article), das Beiwerk
    entfernt und die Gliederung als Markdown erhalten — dasselbe, was man sonst
    mit BeautifulSoup macht, nur mit der Standardbibliothek.
    """
    # 1) Beiwerk raus, bevor irgendetwas anderes passiert
    html = re.sub(r"(?is)<(script|style|noscript|svg|form|iframe|template)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<(nav|header|footer|aside)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<div[^>]*(?:class|id)=\"[^\"]*(cookie|consent|banner|sidebar|menu|advert|"
                  r"newsletter|paywall)[^\"]*\"[^>]*>.*?</div>", " ", html)
    # 2) Hauptteil bevorzugen, wenn die Seite einen ausweist
    for muster in (r"(?is)<main[^>]*>(.*?)</main>", r"(?is)<article[^>]*>(.*?)</article>",
                   r"(?is)<div[^>]*(?:id|class)=\"[^\"]*(?:content|mw-parser-output|post|entry)[^\"]*\"[^>]*>(.*?)</div>"):
        treffer = re.search(muster, html)
        if treffer and len(treffer.group(1)) > 500:
            html = treffer.group(1)
            break
    # 3) Gliederung erhalten: Überschriften und Absätze markieren
    for stufe in range(1, 7):
        html = re.sub(r"(?is)<h%d[^>]*>(.*?)</h%d>" % (stufe, stufe),
                      lambda m, s=stufe: "\n\n" + "#" * s + " " + m.group(1) + "\n", html)
    html = re.sub(r"(?is)<li[^>]*>", "\n- ", html)
    html = re.sub(r"(?is)</(p|div|tr|section|ul|ol|table|pre|blockquote)>", "\n", html)
    html = re.sub(r"(?is)<br[^>]*>", "\n", html)
    text = html_unescape(re.sub(r"(?s)<[^>]+>", " ", html))
    # 4) Aufräumen: Leerzeichenwüsten und Leerzeilenketten
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    zeilen = [z for z in text.split("\n") if z.strip() and len(z.strip()) > 1]
    return "\n".join(zeilen).strip()

def relevanz_score(treffer, anfrage):
    """Wie gut passt ein Suchtreffer zur Anfrage? 0.0 (gar nicht) bis 1.0 (voll).

    Schwaechere Suchmaschinen liefern zu Nischenfragen oft thematisch fremde
    Seiten. Ohne Pruefung landen die als vermeintliche 'Quellen' im Bericht —
    das ist schlimmer als keine Quelle. Gemessen wird, wie viele Kernbegriffe
    der Anfrage in Titel, Kurztext oder URL wirklich vorkommen."""
    kern = [w.lower() for w in re.findall(r"[A-Za-zÄÖÜäöüß0-9]{3,}", anfrage or "")
            if w.lower() not in _SUCH_STOP]
    if not kern:
        return 1.0
    heu = " ".join([treffer.get("title", ""), treffer.get("snippet", ""),
                    treffer.get("url", "")]).lower()
    # Umlaute tolerant vergleichen (URLs schreiben oft 'bruecke' statt 'brücke')
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        heu = heu.replace(a, b)
    def drin(w):
        w2 = w
        for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
            w2 = w2.replace(a, b)
        if w in heu or w2 in heu:
            return True
        # Wortformen (Plural/Fugen-s) nur bei langen, spezifischen Woertern
        return len(w) >= 8 and (w[:-2] in heu or w2[:-2] in heu)
    return sum(1 for w in kern if drin(w)) / float(len(kern))

def filtere_relevant(treffer, anfrage, mindest=0.6):
    """Sortiert Treffer nach Relevanz und wirft thematisch fremde weg."""
    bewertet = [(relevanz_score(r, anfrage), r) for r in treffer]
    bewertet.sort(key=lambda x: -x[0])
    gut = [r for s, r in bewertet if s >= mindest]
    # Lieber die zwei besten als gar nichts — aber nur, wenn sie etwas treffen.
    if not gut:
        gut = [r for s, r in bewertet[:2] if s > 0]
    return gut

def build_web_context(query, max_pages=3, thema=None):
    """Zwei-Schritt-Recherche → Quellen-Kontextblock fürs LLM.

    Schritt 1: Suche liefert Treffer (manche Backends wie Tavily/SearXNG liefern
    schon sauberen Inhalt mit). Schritt 2: fehlt der Inhalt, wird die Seite gezielt
    ausgelesen. Gibt (kontext, quellen) oder (None, grund)."""
    try:
        results = web_search(query, 8)
    except Exception as e:
        return None, "Websuche fehlgeschlagen: %s" % e
    # Thematisch fremde Treffer aussortieren — sie sind schlimmer als keine Quelle
    # Gegen das URSPRUENGLICHE Thema pruefen: driftet eine Suchvariante ab,
    # darf sie nicht auch noch ihren eigenen Massstab mitbringen.
    results = filtere_relevant(results, thema or query)
    # Eine Suchvariante kann das Thema verlieren („Sprachfeatures Leistungsverbesserungen“
    # statt „Python 3.14 …“); gemessen wird am Thema, nicht an der Variante.
    results = [r for r in results if not _ohne_pflichtwort(r, thema or query)]
    if not results:
        return None, "keine thematisch passenden Treffer"
    parts, sources = [], []
    for r in results:
        if len(parts) >= max_pages:
            break
        text = (r.get("content") or "").strip()      # vom Backend mitgeliefert?
        if len(text) < 200:                            # sonst gezielt auslesen
            try:
                text = web_fetch(r["url"], 3500).strip()
            except Exception:
                text = ""
        if len(text) < 120:                            # Cookie-Wall/leer überspringen
            continue
        parts.append("### Quelle: %s (%s)\n%s" % (r["title"], r["url"], text[:3500]))
        sources.append(r["url"])
    if not parts and results:      # Seiten nicht ladbar → wenigstens die Kurztexte
        parts = ["### Suchtreffer (Kurzbeschreibungen)\n" + "\n".join(
            "- %s (%s): %s" % (r["title"], r["url"], r.get("snippet", ""))
            for r in results)]
        sources = [r["url"] for r in results]
    if not parts:
        return None, "keine Suchergebnisse gefunden"
    ctx = ("Aktuelle Web-Rechercheergebnisse zur Anfrage „%s“:\n\n%s"
           % (query, "\n\n".join(parts)))
    return ctx, sources

_wb_cache = {"t": 0.0, "info": None}

def web_bridge_info(refresh=False):
    """Ehrlicher Statusbericht der Web-Bridge (aktives Backend + Testsuche)."""
    if not refresh and _wb_cache["info"] and time.time() - _wb_cache["t"] < 30:
        return _wb_cache["info"]
    backend = get_setting("SEARCH_BACKEND", "auto")
    fetch = get_setting("FETCH_BACKEND", "auto")
    konf = {
        "tavily": bool(get_setting("TAVILY_API_KEY", "").strip()),
        "serper": bool(get_setting("SERPER_API_KEY", "").strip()),
        "brave": bool(get_setting("BRAVE_API_KEY", "").strip()),
        "searxng": bool(get_setting("SEARXNG_URL", "").strip()),
        "firecrawl": bool(get_setting("FIRECRAWL_URL", "").strip()
                          or get_setting("FIRECRAWL_API_KEY", "").strip()),
        "jina": bool(get_setting("JINA_API_KEY", "").strip()),
    }
    info = {"backend": backend, "fetch": fetch, "konfiguriert": konf, "hinweise": []}
    if backend in _SEARCH_BACKENDS and not konf.get(backend):
        info["hinweise"].append(
            "Such-Backend '%s' gewählt, aber Key/URL fehlt — es läuft der "
            "schlüssellose Standard." % backend)
    if fetch == "firecrawl" and not konf["firecrawl"]:
        info["hinweise"].append("Lese-Backend 'Firecrawl' gewählt, aber URL/Key fehlt.")
    if fetch == "jina" and not konf["jina"]:
        info["hinweise"].append("Lese-Backend 'Jina' gewählt, aber API-Key fehlt "
                                "(r.jina.ai verlangt inzwischen einen Key).")
    try:
        treffer = web_search("Wikipedia", 3)
        info["test_treffer"] = len(treffer)
        info["ok"] = len(treffer) > 0
    except Exception as e:
        info["test_treffer"] = 0
        info["ok"] = False
        info["hinweise"].append("Testsuche fehlgeschlagen: %s" % e)
    info["aktive_quelle"] = (backend if (backend in _SEARCH_BACKENDS
                             and konf.get(backend)) else "keyless (Mojeek/DDG/Wikipedia)")
    _wb_cache["t"] = time.time()
    _wb_cache["info"] = info
    return info

# --- Erdung: das Modell darf NUR die Rechercheergebnisse verwenden -----------
# Das ist der Kern gegen Halluzinationen: im Web-Modus wird IMMER eine System-
# Anweisung eingeschleust — bei Treffern die Erdung, bei leerer Suche das klare
# Verbot, etwas zu erfinden. Nie mehr stilles Weglassen des Kontexts.

WEB_GROUNDING_PROMPT = (
    "WICHTIG — Recherche-Modus. Unten stehen aktuelle Web-Rechercheergebnisse. "
    "Beantworte die Frage AUSSCHLIESSLICH auf Grundlage dieser Ergebnisse. Erfinde "
    "KEINE Fakten, Zahlen, Namen, Daten, Ereignisse, Zitate oder Quellen, die nicht "
    "in den Ergebnissen stehen. Nenne die verwendeten Quellen-URLs. Wenn die "
    "Ergebnisse die Frage nicht (vollständig) beantworten, sage das ehrlich und "
    "rate NICHT.\n\n")

WEB_NO_RESULTS_PROMPT = (
    "WICHTIG — Recherche-Modus, aber die Live-Websuche lieferte KEINE verwertbaren "
    "Ergebnisse (%s). Sage dem Nutzer klar und knapp, dass du dazu gerade nichts "
    "Belastbares im Web finden konntest. Erfinde KEINE Fakten, Zahlen, Namen, "
    "Ereignisse oder Quellen und tu NICHT so, als hättest du recherchiert. Du "
    "darfst höchstens gesichertes Allgemeinwissen anbieten — kennzeichne es dann "
    "ausdrücklich als ungeprüft und nicht aus dem Web.")

# Frage- und Füllwörter, die eine Schlüsselwort-Suche nur verwässern.
_SUCH_STOP = set((
    "was ist sind war waren der die das dem den des ein eine einen einem eines "
    "und oder aber wie wo wann warum wer wem wen welche welcher welches "
    "mir mich dir dich uns ihr über um von zu zum zur für mit auf aus bei "
    "einer eines einem einen eine ein anderen andere weitere manche solche "
    "alles alle darüber dazu davon kannst kann könnt könnte du es sie ihn "
    "erzähl erzähle erkläre erklär gib gibt nenne zeig zeige sag sage mal bitte "
    # Meta-/Auftragswoerter: stehen in der Anfrage, gehoeren aber NICHT in die Suche
    "recherche recherchiere researche thema themas themen info infos information "
    "informationen suche such finde finden herausfinden bericht zusammenfassung "
    "uebersicht überblick uebersicht details hintergrund stand aktuelles neuigkeiten "
    "etwas neue neues neuen aktuelle aktuell heute jetzt gerade genau "
    "the a an of is are was what how why who where when tell me about please "
    "give show all more info information me you it").split())

def _llm_destillat(text, model=None):
    """Letzte Chance: das Modell eine knappe Suchanfrage formulieren lassen."""
    try:
        q = ollama_chat_once(model or get_setting("DEFAULT_MODEL"), [
            {"role": "system", "content":
             "Wandle die Nachricht in EINE knappe Websuchanfrage aus 2 bis 5 "
             "Schlüsselwörtern um. Keine Sätze, keine Satzzeichen, keine "
             "Anführungszeichen, nur die Suchbegriffe."},
            {"role": "user", "content": text[:600]}],
            temperature=0.1).strip().strip('"').splitlines()
        return (q[0].strip().strip('"') if q else "")[:70]
    except Exception:
        return ""

def such_kandidaten(text, model=None):
    """Erzeugt mehrere Suchanfrage-Varianten (beste zuerst) aus einer Nachricht.

    Kern der Lösung: Schlüsselwort-Engines finden zu ganzen Sätzen nichts. Die
    Eigennamen-Extraktion holt Begriffe wie „Kimi K3" oder „GPT 5.6" verlässlich
    heraus — die erste Variante mit Treffern gewinnt."""
    text = " ".join((text or "").split())
    if not text:
        return []
    kand = []
    # Eigennamen/Kürzel (Großschreibung + evtl. Ziffernzusatz), Füllwörter raus
    eigen = [e for e in re.findall(
        r"[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]+(?:[ -][A-Z0-9][A-Za-z0-9.+]*)*", text)
        if e.lower() not in _SUCH_STOP]
    # 1) Spezifische Eigennamen EINZELN und zuerst (z.B. „Kimi K3", „GPT 5.6") —
    #    kurze Anfragen treffen bei Schlüsselwort-Engines am zuverlässigsten.
    spezifisch = [e for e in eigen
                  if " " in e or "-" in e or any(c.isdigit() for c in e)]
    kand.extend(spezifisch[:2])
    # 2) die ersten Eigennamen zusammen (falls einzeln nichts bringt)
    if len(eigen) >= 2:
        kand.append(" ".join(dict.fromkeys(eigen[:2])))
    elif eigen:
        kand.append(eigen[0])
    # 3) KURZ: die 2-3 wichtigsten Begriffe — Suchmaschinen mögen knappe Anfragen.
    #    Substantive (im Deutschen grossgeschrieben) zuerst, danach der Rest.
    worte = [w for w in re.findall(r"[A-Za-zÄÖÜäöüß0-9.+#-]{2,}", text)
             if w.lower() not in _SUCH_STOP]
    haupt = [w for w in worte if w[:1].isupper()] or worte
    if haupt:
        # Laengere Woerter sind spezifischer und damit als Suchbegriff wertvoller.
        spezifisch = sorted(dict.fromkeys(haupt), key=lambda w: -len(w))[:3]
        # In der urspruenglichen Reihenfolge zusammensetzen (liest sich natuerlicher)
        kand.append(" ".join([w for w in dict.fromkeys(haupt) if w in spezifisch]))
    # 4) etwas breiter, falls die Kurzform nichts bringt
    if worte:
        kand.append(" ".join(worte[:5]))
    # 5) knappe Rohform
    kand.append(text[:60])
    # 6) Tippfehler-Toleranz: haeufige Vertauschungen im Deutschen probieren.
    #    (echter Fall: "Buerckentransport" statt "Brueckentransport")
    # Zwei Klassen von Tippfehlern, beide kommen real vor:
    #  - ausgelassener Konsonant am WORTANFANG ("bueckentransport" -> "bruecken…")
    #  - vertauschte Buchstaben mitten im Wort ("buercken" -> "bruecken")
    # Anfangsregeln duerfen nur am Wortanfang greifen, sonst wird aus "hamburg"
    # ploetzlich "hambrurg".
    anfang = [("bü", "brü"), ("bu", "bru"), ("gö", "grö"), ("go", "gro"),
              ("kü", "krü"), ("pü", "prü"), ("tü", "trü"), ("fü", "frü")]
    intern = [("ür", "rü"), ("rü", "ür"), ("ie", "ei"), ("ei", "ie"),
              ("ck", "k"), ("tt", "t"), ("nn", "n")]
    tausch = [(r"\b" + a, b) for a, b in anfang] + [(a, b) for a, b in intern]
    if kand:
        basis = kand[0]
        # Mehrere Korrekturen erzeugen statt nur einer: welche Regel passt, weiss
        # man vorher nicht ("buercken"->"bruecken" braucht ür/rü, "bueckentransport"
        # -> "brueckentransport" braucht bü/brü). Die Suche probiert beide.
        korrekturen = []
        for a, b in tausch:
            if not re.search(a, basis, flags=re.I):
                continue
            if True:
                variante = re.sub(a, b, basis, count=1, flags=re.I)
                if (variante.lower() != basis.lower()
                        and variante.lower() not in [k.lower() for k in korrekturen]):
                    korrekturen.append(variante)
            if len(korrekturen) >= 2:
                break
        for i, v in enumerate(korrekturen):
            kand.insert(1 + i, v)          # direkt hinter der besten Variante
    # 7) LLM-Destillat als zusätzliche Chance (nur bei längeren Eingaben)
    if len(text) > 55:
        d = _llm_destillat(text, model)
        if d:
            kand.append(d)
    # entdoppeln, Reihenfolge erhalten
    out = []
    for k in kand:
        k = k.strip()
        if k and all(k.lower() != o.lower() for o in out):
            out.append(k)
    return out[:7]

def korrigiere_anfrage(text, model=None):
    """Letzte Rettung: das Modell die Schreibweise der Suchbegriffe korrigieren lassen.

    Mechanische Regeln decken nicht jeden Tippfehler ab (echter Fall:
    'Bueckentransport' statt 'Brueckentransport' — ein fehlender Buchstabe mitten
    im Wort). Ein Sprachmodell erkennt so etwas zuverlaessig. Laeuft nur, wenn
    zuvor gar nichts gefunden wurde, kostet also im Normalfall keine Zeit."""
    try:
        korr = ollama_chat_once(model or get_setting("DEFAULT_MODEL"), [
            {"role": "system", "content":
             "Korrigiere Rechtschreib- und Tippfehler in der folgenden Suchanfrage. "
             "Gib NUR die korrigierten Suchbegriffe zurueck — keine Erklaerung, keine "
             "Anfuehrungszeichen, keinen ganzen Satz. Ist alles korrekt, gib den Text "
             "unveraendert zurueck."},
            {"role": "user", "content": text[:200]}], temperature=0.1)
        korr = " ".join((korr or "").split()).strip().strip('"')
        return korr[:120]
    except Exception:
        return ""

def suche_mit_varianten(text, model=None):
    """Websuche mit Anfrage-Destillation — DER eine Sucheinstieg fuer alles.

    Probiert mehrere Suchanfrage-Varianten (Eigennamen, Schluesselwoerter, Rohform,
    LLM-Destillat) und nimmt die erste mit echten Treffern. Damit scheitert eine
    natuerlichsprachige Frage nicht mehr an einer Schluesselwort-Engine — egal ob
    sie aus dem Chat, einer Pipeline oder Deep Research kommt.
    Gibt (kontext, quellen) oder (None, [])."""
    bester = (0.0, None, [])
    for such in such_kandidaten(text, model)[:3]:
        ctx, src = build_web_context(such, thema=text)
        if not ctx:
            continue
        quellen = src if isinstance(src, list) else []
        # Wie gut passen die gefundenen Seiten zum urspruenglichen Thema?
        try:
            gefunden = web_search(such, 6)
            gute = filtere_relevant(gefunden, text)
            guete = (sum(relevanz_score(r, text) for r in gute) / len(gute)) if gute else 0.0
        except Exception:
            guete = 0.5
        # Ein gefundener Kontext gewinnt immer gegen "gar nichts"; die Guete
        # entscheidet nur zwischen mehreren Varianten.
        if bester[1] is None or guete > bester[0]:
            bester = (guete, ctx, quellen)
        if guete >= 0.9:          # klarer Treffer — nicht weitersuchen
            break
    if bester[1]:
        return bester[1], bester[2]
    # Nichts gefunden -> Schreibweise korrigieren lassen und EINMAL neu versuchen.
    korr = korrigiere_anfrage(text, model)
    if korr and korr.lower() != " ".join(text.split()).lower():
        for such in such_kandidaten(korr, model)[:2]:
            # Wichtig: gegen die KORRIGIERTE Fassung pruefen, nicht gegen den Tippfehler
            ctx, src = build_web_context(such, thema=korr)
            if ctx:
                return ctx, (src if isinstance(src, list) else [])
    return None, []

def recherche_kontext(query, model=None):
    """Websuche -> einzuschleusende System-Nachrichten fuer den Web-Modus.

    Liefert IMMER mindestens eine Nachricht: bei Treffern die Erdung samt Quellen,
    sonst die Anti-Halluzinations-Anweisung. Gibt (messages, quellen)."""
    ctx, quellen = suche_mit_varianten(query, model)
    if ctx:
        return [{"role": "system", "content": WEB_GROUNDING_PROMPT + ctx}], quellen
    return [{"role": "system", "content": WEB_NO_RESULTS_PROMPT % "keine Treffer"}], []

# ----------------------------------------------------------------------------
# Browser-Use-Adapter (optional): echte Browser-Automation mit lokalem Ollama.
# Standardmäßig NICHT nötig — WebBridge (oben) funktioniert ohne Installation.
# Wer echte Klick-Automation will:  pip install browser-use  (lädt Chromium)
# ----------------------------------------------------------------------------

# --- Erkennung: Was ist auf diesem Rechner tatsächlich vorhanden? -----------

_bu_cache = {"t": 0.0, "info": None}

def modell_kann_sehen(name):
    """Kann dieses Ollama-Modell Bilder lesen? Gefragt, nicht geraten.

    browser-use schickt Bildschirmfotos mit. Kann das Modell keine Bilder,
    antwortet Ollama auf jeden Schritt mit HTTP 400 und der Agent arbeitet
    blind weiter — ohne Fehlermeldung, mit leerem Ergebnis. Deshalb fragt
    Dive on Wide vorher die Fähigkeiten ab und schaltet die Sicht entsprechend.
    """
    name = (name or "").split("@@")[-1].strip()
    if not name:
        return False
    try:
        basis = get_setting("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        roh = _http_post_json(basis + "/api/show", {"model": name}, timeout=10)
        return "vision" in (json.loads(roh).get("capabilities") or [])
    except Exception:
        return False


def browser_python():
    """Das Python der Browser-Umgebung.

    browser-use zieht ein paar hundert Megabyte nach. Dive on Wide selbst bleibt bei
    der Standardbibliothek und muss mit jedem Python startbar sein — auch mit
    einem „extern verwalteten“ Systempython, in das man nichts installieren
    darf. Deshalb liegt die Abhängigkeit in einer eigenen Umgebung, wie beim
    Training (TRAINING_PYTHON), und wird als Unterprozess gerufen.
    """
    eigen = get_setting("BROWSER_PYTHON", "").strip()
    kandidaten = [os.path.expanduser(eigen)] if eigen else []
    for wurzel in (os.path.dirname(BASE_DIR), BASE_DIR, os.path.expanduser("~/DowOS")):
        kandidaten.append(os.path.join(wurzel, "venv-browser", "bin", "python"))
        kandidaten.append(os.path.join(wurzel, "venv-browser", "Scripts", "python.exe"))
    for k in kandidaten:
        if k and os.path.exists(k):
            return k
    return ""


# Kindprozesse, mit denen Dive on Wide Text über Pipes austauscht, sprechen UTF-8 — auf
# Windows sonst Windows-1252, und ein Emoji auf einer Webseite beendete den Läufer
# (Windows-VM, 29.09.2026).
def _utf8_kind():
    return {"text": True, "encoding": "utf-8", "errors": "replace",
            "env": dict(os.environ, PYTHONIOENCODING="utf-8")}

def browser_laeufer_pfad():
    return os.path.join(BASE_DIR, "browser_laeufer.py")


def browser_info(refresh=False):
    """Prüft die Browser-Automation und liefert einen ehrlichen Statusbericht."""
    if not refresh and _bu_cache["info"] and time.time() - _bu_cache["t"] < 30:
        return _bu_cache["info"]
    modus = get_setting("BROWSER_MODUS", "eigenes")
    if modus not in ("eigenes", "chrome"):
        modus = "eigenes"
    info = {"browser_use": False, "version": "", "ollama_client": False, "chrome": "",
            "modus": modus, "python": browser_python(),
            "cdp_url": get_setting("BROWSER_CDP_URL", ""), "cdp_reachable": False,
            "profil_ordner": get_setting("BROWSER_PROFIL", ""),
            "headless": get_setting("BROWSER_HEADLESS", "0") == "1",
            "modell": (get_setting("BROWSER_MODELL", "") or get_setting("BROWSER_MODEL", "")
                       or get_setting("DEFAULT_MODEL", "")).split("@@")[-1],
            "steuerung": (get_setting("BROWSER_STEUERUNG", "dowos")
                          if get_setting("BROWSER_STEUERUNG", "dowos") in ("dowos", "browser_use") else "dowos"),
            "grounding_url": get_setting("GROUNDING_API_URL", ""),
            "hinweise": []}
    info["sicht"] = modell_kann_sehen(info["modell"])
    if info["steuerung"] == "browser_use" and info["modell"] and not info["sicht"]:
        info["hinweise"].append(
            "Steuerung „browser-use“ schickt Bildschirmfotos, aber „%s“ kann keine Bilder lesen. "
            "Entweder ein Modell mit Bildverständnis eintragen (z. B. gemma4:12b) oder die Steuerung "
            "von Dive on Wide nehmen — die arbeitet mit der Elementliste der Seite." % info["modell"])
    if not info["python"]:
        info["hinweise"].append(
            "Keine Browser-Umgebung gefunden. Einmal anlegen:  python3 -m venv ~/DowOS/venv-browser "
            "&& ~/DowOS/venv-browser/bin/pip install browser-use ollama   "
            "(oder den Pfad unter BROWSER_PYTHON eintragen)")
    else:
        try:
            fertig = subprocess.run([info["python"], browser_laeufer_pfad(), "--pruefen"],
                                    capture_output=True, timeout=60, **_utf8_kind())
            zeile = next((z for z in fertig.stdout.splitlines() if z.startswith("ERGEBNIS ")), "")
            if zeile:
                bericht = json.loads(zeile[9:])
                # Nur übernehmen, was die Umgebung wirklich weiß — Pfad und Modus
                # bestimmt Dive on Wide, sonst überschreibt ein Läufer seine eigene Adresse.
                for k in ("browser_use", "version", "ollama_client", "chrome"):
                    if k in bericht:
                        info[k] = bericht[k]
                info["hinweise"] += bericht.get("hinweise", [])
            else:
                info["hinweise"].append("Die Browser-Umgebung antwortet nicht: %s"
                                        % (fertig.stderr or fertig.stdout)[-200:])
        except Exception as e:
            info["hinweise"].append("Browser-Umgebung nicht prüfbar: %s" % e)
    if info["cdp_url"] and modus != "chrome":
        # Adresse eingetragen, aber anderer Modus: trotzdem prüfen und sagen,
        # wenn dort nichts antwortet — sonst sucht man den Fehler später.
        try:
            with urllib.request.urlopen(info["cdp_url"].rstrip("/") + "/json/version", timeout=2) as r:
                info["cdp_reachable"] = True
                info["cdp_browser"] = json.loads(r.read().decode("utf-8")).get("Browser", "")
        except Exception as e:
            info["hinweise"].append("Chrome ist unter %s nicht erreichbar (%s) — im Modus „mein Chrome“ "
                                    "wäre das nötig." % (info["cdp_url"], e))
    if modus == "chrome":
        if not info["cdp_url"]:
            info["hinweise"].append(
                "Modus „mein Chrome“: Es fehlt die Fernsteuerungsadresse. Chrome beenden und so neu "
                "starten:  \"%s\" --remote-debugging-port=9222   — dann BROWSER_CDP_URL auf "
                "http://127.0.0.1:9222 setzen."
                % (info["chrome"] or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
        else:
            try:
                with urllib.request.urlopen(info["cdp_url"].rstrip("/") + "/json/version", timeout=2) as r:
                    daten = json.loads(r.read().decode("utf-8"))
                info["cdp_reachable"] = True
                info["cdp_browser"] = daten.get("Browser", "")
            except Exception as e:
                info["hinweise"].append(
                    "Chrome ist unter %s nicht erreichbar (%s). So starten:  \"%s\" "
                    "--remote-debugging-port=9222"
                    % (info["cdp_url"], e, info["chrome"] or "google-chrome"))
    info["bereit"] = bool(info["browser_use"] and info["ollama_client"]
                          and (info["cdp_reachable"] if modus == "chrome" else info["chrome"]))
    _bu_cache["t"] = time.time()
    _bu_cache["info"] = info
    return info


def browseruse_available():
    return browser_info()["browser_use"]

# --- Ausführung eines Browser-Auftrags ---------------------------------------

BROWSER_SYSTEM_ZUSATZ = (
    "Du arbeitest für Dive on Wide. Fasse am Ende dein Ergebnis auf DEUTSCH zusammen und "
    "nenne die URLs aller Seiten, die du tatsächlich besucht hast."
)

_browser_sperre = threading.Lock()


def browser_run(task, model=None, max_steps=None, run_id=None):
    """Führt einen Browser-Auftrag aus und liefert (Bericht, besuchte URLs).

    Der Auftrag läuft als eigener Prozess in der Browser-Umgebung
    (browser_laeufer.py). Das hält die schwere Abhängigkeit aus dem Server
    heraus: Stürzt browser-use ab oder frisst es Speicher, trifft es nicht
    Dive on Wide. Fortschritt kommt Zeile für Zeile zurück, Abbruch beendet den
    Prozess.
    """
    info = browser_info(refresh=True)
    if not info["python"] or not info["browser_use"]:
        raise RuntimeError("Browser-Automation ist nicht eingerichtet. "
                           + " ".join(info["hinweise"])[:400])
    if info["modus"] == "chrome" and not info["cdp_reachable"]:
        raise RuntimeError("Modus „mein Chrome“, aber kein Chrome mit Fernsteuerung erreichbar. "
                           + " ".join(info["hinweise"])[:300])
    auftrag = {
        "aufgabe": task + "\n\n" + BROWSER_SYSTEM_ZUSATZ,
        "modell": (model or get_setting("BROWSER_MODELL", "") or get_setting("BROWSER_MODEL", "")
                   or get_setting("DEFAULT_MODEL", "")).split("@@")[-1],
        "ollama": get_setting("OLLAMA_BASE_URL", "http://localhost:11434"),
        "modus": info["modus"], "cdp_url": info["cdp_url"], "chrome": info["chrome"],
        "profil_ordner": info["profil_ordner"], "headless": info["headless"],
        "steuerung": info["steuerung"],
        "grounding_url": info["grounding_url"],
        "grounding_modell": get_setting("GROUNDING_MODEL", ""),
        "seiten_zeichen": int(get_setting("BROWSER_SEITEN_ZEICHEN", "6000")),
        "max_schritte": int(max_steps or get_setting("BROWSER_MAX_STEPS", "25")),
    }
    auftrag["sicht"] = modell_kann_sehen(auftrag["modell"])
    # Ein Browserauftrag zur Zeit: Modell und Browser zusammen füllen den
    # Speicher. Zwei gleichzeitig führten auf 24 GB zuverlässig in den
    # Metal-Speicherfehler (gemessen 16.09.2026) — dieselbe Regel wie beim
    # Training, nur hier.
    if not _browser_sperre.acquire(blocking=False):
        raise RuntimeError("Es läuft schon ein Browser-Auftrag. Modell und Browser teilen sich den "
                           "Speicher — zwei gleichzeitig bringen beide zum Absturz. Erst abwarten "
                           "oder den laufenden Auftrag abbrechen.")
    try:
        # Andere Modelle freigeben, wie vor dem Training. Gemessen am 16.09.2026:
        # Während eines Browserlaufs mit gemma4:12b lud Dive on Wide nebenbei das
        # 27-B-Standardmodell — 90 Sekunden später meldete Ollama einen
        # Speicherfehler und der Lauf war hin.
        entladen = ollama_alle_entladen()
        if entladen:
            run_step(run_id, "Speicher freigemacht: %s entladen" % ", ".join(entladen)[:80], "done")
        return _browser_run_intern(task, auftrag, info, run_id)
    finally:
        _browser_sperre.release()


def _browser_run_intern(task, auftrag, info, run_id):
    run_step(run_id, "Browser wird gestartet%s · Steuerung: %s …"
             % (" (übernimmt dein Chrome)" if info["modus"] == "chrome" else " (eigenes Profil)",
                "Dive on Wide (Elementliste)" if info["steuerung"] == "dowos"
                else "browser-use (Bildschirmfotos)"), "active")
    proc = subprocess.Popen([info["python"], browser_laeufer_pfad()], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1, **_utf8_kind())
    ergebnis, fehler = None, ""
    try:
        proc.stdin.write(json.dumps(auftrag, ensure_ascii=False))
        proc.stdin.close()
        for zeile in proc.stdout:
            zeile = zeile.strip()
            if zeile.startswith("SCHRITT "):
                run_step(run_id, "Browser-Schritt %s von höchstens %d"
                         % (zeile[8:], auftrag["max_schritte"]), "active")
            elif zeile.startswith("ERGEBNIS "):
                ergebnis = json.loads(zeile[9:])
            elif zeile.startswith("FEHLER "):
                fehler = zeile[7:]
    except RunCancelled:
        proc.kill()
        raise
    finally:
        try:
            proc.wait(timeout=20)
        except Exception:
            proc.kill()
    if ergebnis is None:
        rest = (proc.stderr.read() or "")[-300:] if proc.stderr else ""
        grund = fehler or ("Der Browser-Auftrag lieferte kein Ergebnis. " + rest)
        if "Insufficient Memory" in grund or "OOM" in grund:
            grund += ("  Hinweis: Der Speicher reichte nicht. Meist liegt es daran, dass währenddessen "
                      "ein zweites Modell geladen wurde — ein Chat oder das Dashboard mit einem großen "
                      "Standardmodell genügt. Während eines Browserlaufs nichts anderes rechnen lassen "
                      "oder ein kleineres Standardmodell einstellen.")
        raise RuntimeError(grund)
    return ergebnis.get("bericht") or "(Der Browser-Agent lieferte kein Ergebnis.)", ergebnis.get("urls") or []

def run_browser_task(task, model, session_id=None, run_id=None, max_steps=None):
    """Hintergrundlauf: Browser-Auftrag ausführen, ablegen, melden."""
    try:
        bericht, urls = browser_run(task, model, max_steps, run_id)
        quellen = ("\n\n### Besuchte Seiten\n" +
                   "\n".join("- %s" % u for u in dict.fromkeys(urls))) if urls else ""
        inhalt = ("# Browser-Auftrag\n\n**Aufgabe:** %s\n\n## Ergebnis\n\n%s%s\n"
                  % (task, bericht, quellen))
        art_id = deliver("Browser: %s" % task[:60], inhalt, "browser-auftrag.md",
                         "Browser-Auftrag abgeschlossen 🌐",
                         "Der Bericht liegt unter Dokumente & Artefakte.")
        if session_id:
            post_to_session(session_id, "**🌐 Browser-Auftrag abgeschlossen**\n\n%s%s"
                            % (bericht, quellen))
        run_finish(run_id, "done", bericht + quellen, art_id)
    except RunCancelled as _abbruch:
        run_abgebrochen(run_id, "Browser-Auftrag", session_id, grund=_abbruch)
    except Exception as e:
        fail("Browser-Auftrag fehlgeschlagen ⚠️", e)
        run_finish(run_id, "error", str(e))
        if session_id:
            post_to_session(session_id, "⚠️ Browser-Auftrag fehlgeschlagen: %s" % e)

# ----------------------------------------------------------------------------
# Computer-Use (Alpha) — Diagnose für den Grounding-Server
#
# Konzept: zwei lokale Modelle arbeiten zusammen. Der Planner (dein Ollama-
# Modell) entscheidet WAS zu tun ist; der Grounder (ein Vision-Modell wie
# nvidia/LocateAnything-3B hinter einer OpenAI-kompatiblen API, z. B.
# vllm-metal auf Apple Silicon) zeigt WO auf dem Bildschirm. Wie bei der
# Bild-API gilt: Ohne konfigurierten und erreichbaren Grounding-Server wird
# nichts vorgetäuscht — die Diagnose sagt ehrlich, was fehlt und wie man es
# nachholt.
# ----------------------------------------------------------------------------

import steuerung


def _schirm():
    """Die Rueckseite, auf der Computer-Use gerade arbeitet.

    EINE Stelle, an der das entschieden wird. Vorher stand `rueckseite()` an
    sieben Stellen — und dann muesste man an sieben Stellen daran denken, dass
    der Nutzer einen Container gewaehlt hat. Eine davon vergisst man, und der
    Agent fotografiert den Container, klickt aber auf den echten Schirm."""
    return steuerung.rueckseite(
        steuerung.schirm_waehlen(get_setting("COMPUTER_SCHIRM", "auto")))


_cu_cache = {"t": 0.0, "info": None}

def darf_bildschirm_lesen():
    """Prüft das Recht „Bildschirmaufnahme“ mit einem 1-Pixel-Foto.

    Ohne das Recht sagt macOS nicht Nein — screencapture liefert einfach eine
    leere Datei. Ein Pixel kostet nichts und ist der einzige ehrliche Test."""
    if ENV.get("DOWOS_FAKE_SCREENSHOT"):
        return True
    return _schirm().darf_lesen()

def verantwortliches_programm():
    """Welches Programm hängt eigentlich an unserer Bedienungshilfen-Freigabe?

    macOS vergibt dieses Recht nicht an ein einzelnes Kommandozeilen-Werkzeug,
    sondern an die Code-Signatur des *verantwortlichen* Prozesses weiter oben
    im Baum. Und das ist nicht immer die App, die man vor sich sieht:

        iTerm.app                    Signatur com.googlecode.iterm2
        └─ iTermServer-3.6.11        Signatur iTermServer   ← andere Identität!
           └─ zsh
              └─ python3 server.py   erbt von iTermServer, NICHT von iTerm.app

    Wer in den Systemeinstellungen iTerm.app freigibt, hat damit den Server
    nicht freigegeben — der Eintrag steht da und wirkt trotzdem nicht. Genau
    dieser Fall hat einen Nutzertest gekostet. Deshalb raten wir hier nicht
    „Terminal/iTerm“, sondern nennen den Pfad, der wirklich zählt.

    Gibt (pfad, ist_app_buendel) zurück, sonst (None, False)."""
    durchreichen = ("zsh", "bash", "sh", "dash", "login", "sudo", "env",
                    "nohup", "script", "tmux", "screen")
    pid = os.getpid()
    for _ in range(8):
        try:
            r = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                               capture_output=True, timeout=5)
            zeile = r.stdout.decode("utf-8", "replace").strip()
            if not zeile:
                return (None, False)
            ppid_txt, _, pfad = zeile.partition(" ")
            pid = int(ppid_txt.strip())
            pfad = pfad.strip()
        except Exception:
            return (None, False)
        if pid <= 1 or not pfad:
            return (None, False)
        name = os.path.basename(pfad).lower().lstrip("-")
        if name in durchreichen or name.startswith("python"):
            continue                      # nur eine Hülle, weiter nach oben
        return (pfad, ".app/Contents/MacOS/" in pfad)
    return (None, False)

def bedienungshilfen_hinweis():
    """Der Hinweistext — mit dem konkreten Programm, das eingetragen gehört."""
    pfad, ist_app = verantwortliches_programm()
    if not pfad:
        return ("Erteilen unter: Systemeinstellungen → Datenschutz & Sicherheit "
                "→ Bedienungshilfen, dort das Programm eintragen, in dem Dive on Wide "
                "läuft (Terminal/iTerm), danach dieses Programm neu starten.")
    if ist_app:
        app = pfad.split(".app/Contents/MacOS/")[0] + ".app"
        return ("Erteilen unter: Systemeinstellungen → Datenschutz & Sicherheit "
                "→ Bedienungshilfen, dort %s eintragen UND den Schalter "
                "einschalten, danach diese App vollständig beenden und neu "
                "starten." % app)
    # Kein App-Bündel: ein separat signierter Helfer (typisch: iTermServer).
    # Hier wäre der Hinweis auf die sichtbare App aktiv irreführend.
    return ("ACHTUNG: Dive on Wide läuft nicht direkt unter einer App, sondern unter "
            "„%s“. macOS vergibt das Recht pro Code-Signatur — eine Freigabe für "
            "die sichtbare App (z. B. iTerm.app) gilt für diesen Helfer NICHT. "
            "Drei Wege: (a) Dive on Wide aus Terminal.app starten und Terminal.app "
            "freigeben; (b) genau diese Datei in die Liste der Bedienungshilfen "
            "ziehen (bricht bei jedem Update, weil die Version im Namen steht); "
            "(c) bei iTerm den Sitzungsserver abschalten — "
            "defaults write com.googlecode.iterm2 RunJobsInServers -bool false, "
            "danach iTerm vollständig beenden und neu starten." % pfad)

def darf_maus_tastatur():
    """Prüft das Recht „Bedienungshilfen“ — cliclick sagt es selbst.

    `cliclick -V` schreibt eine Warnung auf stderr, wenn das Recht fehlt.
    Das ist billiger und sicherer als ein Probeklick, der ja etwas auslösen
    würde."""
    if ENV.get("DOWOS_FAKE_EXECUTOR"):
        return True
    return _schirm().darf_steuern()

def computer_info(refresh=False):
    """Prüft die Computer-Use-Voraussetzungen — ehrlicher Statusbericht."""
    if not refresh and _cu_cache["info"] and time.time() - _cu_cache["t"] < 30:
        return _cu_cache["info"]
    url = (get_setting("GROUNDING_API_URL", "") or "http://localhost:8600").rstrip("/")
    modell = get_setting("GROUNDING_MODEL", "") or "nvidia/LocateAnything-3B"
    info = {"url": url, "modell": modell, "api_erreichbar": False,
            "modell_geladen": False, "screenshot": False, "modelle": [],
            "planner_modell": get_setting("COMPUTER_PLANNER_MODEL", "")
                              or get_setting("DEFAULT_MODEL"),
            "hinweise": []}
    # 1) Grounding-Server erreichbar und richtiges Modell geladen?
    try:
        with urllib.request.urlopen(url + "/v1/models", timeout=3) as r:
            daten = json.loads(r.read().decode("utf-8"))
        info["api_erreichbar"] = True
        namen = [m.get("id", "") for m in daten.get("data", [])]
        info["modelle"] = namen
        info["modell_geladen"] = (not namen) or any(
            modell in n or n in modell for n in namen)
        if namen and not info["modell_geladen"]:
            info["hinweise"].append(
                "Der Grounding-Server läuft, aber mit anderem Modell (%s). "
                "Erwartet: %s" % (", ".join(namen[:3]), modell))
    except Exception as e:
        info["hinweise"].append(
            "Grounding-Server unter %s nicht erreichbar (%s). Start auf Apple "
            "Silicon: python3 -m mlx_vlm server --model %s --port 8600 — auf "
            "einem Linux/GPU-Rechner: vllm serve %s" % (url, e, modell, modell))
    # 2) Screenshot: nicht nur „Werkzeug da?“, sondern „darf es auch?“.
    # Das Werkzeug ist auf macOS immer vorhanden — entscheidend ist das
    # Recht „Bildschirmaufnahme“. Fehlt es, liefert screencapture eine leere
    # Datei, und das merkte man bisher erst mitten im Lauf.
    # Die Rueckseite weiss selbst, welche Werkzeuge sie braucht und wie man
    # nachhilft — IN DEN WORTEN DER JEWEILIGEN PLATTFORM. Vorher stand hier
    # macOS fest verdrahtet, und ein Linux-Nutzer bekam geduldig erklaert, er
    # moege `brew install cliclick` ausfuehren. Das ist schlimmer als keine
    # Meldung, weil es Zeit kostet, bevor es scheitert.
    rueck = steuerung.rueckseite(
        steuerung.schirm_waehlen(get_setting("COMPUTER_SCHIRM", "auto")))
    info["plattform"] = rueck.name
    info["plattform_text"] = rueck.beschreibung
    info["plattform_erprobt"] = rueck.name == "macos"
    # Klickt der Agent in einem Wegwerf-Behaelter oder auf dem echten Schirm
    # des Besitzers? Das ist die wichtigste Zeile dieser ganzen Diagnose.
    info["abgeschottet"] = rueck.name == "container"
    info["schirm_wunsch"] = get_setting("COMPUTER_SCHIRM", "auto")
    info["container_moeglich"] = bool(shutil.which("docker"))
    info["foto_werkzeug"] = rueck.foto_werkzeug()
    info["screenshot_werkzeug"] = bool(rueck.foto_werkzeug())
    info["screenshot"] = (True if ENV.get("DOWOS_FAKE_SCREENSHOT")
                          else info["screenshot_werkzeug"] and darf_bildschirm_lesen())
    if not info["screenshot"]:
        info["hinweise"].append(rueck.hinweis_foto())
    # „Zeig mir wo“ (Phase 2) braucht nur Screenshot + Grounder.
    info["bereit"] = bool(info["api_erreichbar"] and info["modell_geladen"]
                          and info["screenshot"])
    # 3) Steuerung (Phase 3): Maus/Tastatur brauchen einen Executor UND den
    # bewusst eingeschalteten Schalter. Standardmäßig ist die Steuerung AUS —
    # das System bekommt die Fähigkeit, den Rechner zu bedienen, erst wenn der
    # Besitzer sie in den Einstellungen freigibt.
    info["executor"] = ("fake" if ENV.get("DOWOS_FAKE_EXECUTOR")
                        else rueck.steuer_werkzeug())
    info["steuern_aktiviert"] = get_setting("COMPUTER_USE_ENABLED", "0") == "1"
    info["bestaetigung"] = get_setting("COMPUTER_CONFIRM", "1") == "1"
    # Installiert heißt nicht erlaubt: ohne das Recht „Bedienungshilfen“ nimmt
    # cliclick jeden Befehl klaglos an und tut nichts. Genau das sieht von
    # außen aus wie „das Feature funktioniert nicht“ — deshalb echt prüfen.
    info["bedienungshilfen"] = (True if info["executor"] == "fake"
                                else darf_maus_tastatur() if info["executor"]
                                else False)
    if not info["bedienungshilfen"] and info["executor"] != "fake":
        hinweis = rueck.hinweis_steuern()
        # Auf macOS kommt der lange Absatz ueber die Code-Signatur dazu: Dort
        # haengt das Recht nicht am Werkzeug, sondern am verantwortlichen
        # Prozess weiter oben im Baum, und das errät niemand von allein.
        if rueck.name == "macos" and rueck.steuer_werkzeug():
            hinweis += " " + bedienungshilfen_hinweis()
        info["hinweise"].append(hinweis)
    # 4) Kann das Planner-Modell überhaupt Bilder sehen? Ollama weiß es selbst.
    # Ein reines Textmodell (z. B. ein Coder-Modell) als Planner ist der
    # naheliegendste Fehlgriff — es lehnt den Screenshot schlicht ab.
    koennen = modell_faehigkeiten(info["planner_modell"])
    info["planner_faehigkeiten"] = koennen
    info["planner_sieht"] = (not koennen) or ("vision" in koennen)
    info["planner_denkt_laut"] = "thinking" in koennen
    if koennen and not info["planner_sieht"]:
        info["hinweise"].append(
            "Das Planner-Modell „%s“ kann keine Bilder verarbeiten — es sieht "
            "den Bildschirm gar nicht. Wähle unter Einstellungen → Computer-Use "
            "ein bildfähiges Modell (Vision-Modell)." % info["planner_modell"])
    info["steuern_bereit"] = bool(info["bereit"] and info["executor"]
                                  and info["bedienungshilfen"]
                                  and info["planner_sieht"]
                                  and info["steuern_aktiviert"])
    if info["bereit"] and info["executor"] and not info["steuern_aktiviert"]:
        info["hinweise"].append(
            "Steuerung ist aus Sicherheitsgründen deaktiviert. Zum Freischalten: "
            "Einstellungen → Computer-Use → „Steuerung erlauben“.")
    _cu_cache["t"] = time.time()
    _cu_cache["info"] = info
    return info

def screenshot_aufnehmen():
    """Nimmt ein Bildschirmfoto auf (macOS: screencapture, lautlos) → PNG-Bytes.

    DOWOS_FAKE_SCREENSHOT (.env) ersetzt den echten Bildschirm durch eine
    vorbereitete Datei — dadurch bleibt der Testlauf deterministisch und
    fotografiert nicht wirklich den Schirm des Entwicklers."""
    fake = ENV.get("DOWOS_FAKE_SCREENSHOT", "")
    if fake:
        with open(fake, "rb") as f:
            return f.read()
    return _schirm().foto()

def png_groesse(png):
    """Breite/Höhe aus dem PNG-Header (IHDR beginnt bei Byte 16)."""
    return struct.unpack(">II", png[16:24])

# LocateAnything antwortet mit 0-1000-normierten Koordinaten:
# <ref>beschreibung</ref><box><x1><y1><x2><y2></box>
BOX_RE = re.compile(r"<box><(\d+)><(\d+)><(\d+)><(\d+)></box>")

def parse_grounding_boxes(text, breite, hoehe):
    """Übersetzt die normierten <box>-Angaben in Bildpixel."""
    boxen = []
    for m in BOX_RE.finditer(text):
        x1, y1, x2, y2 = (int(g) for g in m.groups())
        boxen.append({"x1": round(x1 * breite / 1000),
                      "y1": round(y1 * hoehe / 1000),
                      "x2": round(x2 * breite / 1000),
                      "y2": round(y2 * hoehe / 1000)})
    return boxen

def grounding_anfrage(png, beschreibung):
    """Fragt den Grounding-Server, wo das beschriebene Element liegt."""
    url = (get_setting("GROUNDING_API_URL", "") or "http://localhost:8600").rstrip("/")
    modell = get_setting("GROUNDING_MODEL", "") or "nvidia/LocateAnything-3B"
    payload = {
        "model": modell,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,"
             + base64.b64encode(png).decode()}},
            {"type": "text", "text":
             "Locate the following UI element on this screenshot: %s. "
             "Answer with the bounding box coordinates." % beschreibung},
        ]}],
    }
    req = urllib.request.Request(url + "/v1/chat/completions",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(
            req, timeout=int(get_setting("GROUNDING_TIMEOUT", "180"))) as r:
        antwort = json.loads(r.read().decode("utf-8"))
    return antwort["choices"][0]["message"]["content"] or ""

def zeig_html(png, boxen, breite, hoehe, beschreibung, antwort_roh):
    """Markierter Screenshot als eigenständige HTML-Datei.

    Bewusst keine Bildbearbeitung (die bräuchte Bibliotheken): Die Markierung
    ist ein CSS-Overlay über dem eingebetteten Original — stdlib-rein und in
    jedem Browser exakt."""
    marker = "".join(
        '<div style="position:absolute;border:3px solid #e5484d;border-radius:4px;'
        'box-shadow:0 0 0 2px rgba(229,72,77,.35);left:%.2f%%;top:%.2f%%;'
        'width:%.2f%%;height:%.2f%%"></div>'
        % (b["x1"] * 100.0 / breite, b["y1"] * 100.0 / hoehe,
           (b["x2"] - b["x1"]) * 100.0 / breite,
           (b["y2"] - b["y1"]) * 100.0 / hoehe)
        for b in boxen)
    koords = "".join("<li>(%d, %d) – (%d, %d) · Mitte (%d, %d)</li>"
                     % (b["x1"], b["y1"], b["x2"], b["y2"],
                        (b["x1"] + b["x2"]) // 2, (b["y1"] + b["y2"]) // 2)
                     for b in boxen) or "<li>nichts gefunden</li>"
    return ("<!DOCTYPE html><html lang=\"de\"><head><meta charset=\"utf-8\">"
            "<title>Zeig mir wo</title></head>"
            "<body style=\"font-family:sans-serif;margin:20px\">"
            "<h2>\U0001F5B1 Zeig mir wo: %s</h2>"
            "<p>Screenshot %d×%d px · Koordinaten in Bildpixeln</p><ul>%s</ul>"
            "<div style=\"position:relative;display:inline-block;max-width:100%%\">"
            "<img src=\"data:image/png;base64,%s\" style=\"max-width:100%%;display:block\">"
            "%s</div>"
            "<p style=\"color:#888\">Rohantwort des Grounders: <code>%s</code></p>"
            "</body></html>"
            % (beschreibung, breite, hoehe, koords,
               base64.b64encode(png).decode(), marker, antwort_roh[:400]))

def run_computer_zeig(beschreibung, session_id=None, run_id=None):
    """Hintergrundlauf „Zeig mir wo“: Screenshot → Grounder → markiertes Artefakt.

    Phase 2 von Computer-Use: Es wird NICHTS geklickt oder getippt — nur
    gezeigt. So lässt sich die komplette Bildschirm-Strecke prüfen, bevor
    das System je eine Eingabe macht."""
    try:
        info = computer_info(refresh=True)
        if not info["bereit"]:
            raise RuntimeError("Computer-Use ist nicht bereit: "
                               + (" | ".join(info["hinweise"])
                                  or "Diagnose lieferte keine Hinweise."))
        run_step(run_id, "Bildschirmfoto wird aufgenommen …", "active")
        png = screenshot_aufnehmen()
        breite, hoehe = png_groesse(png)
        run_step(run_id, "Grounder sucht: %s" % beschreibung[:70], "active")
        antwort = grounding_anfrage(png, beschreibung)
        boxen = parse_grounding_boxes(antwort, breite, hoehe)
        html = zeig_html(png, boxen, breite, hoehe, beschreibung, antwort)
        art_id = deliver("Zeig mir wo: %s" % beschreibung[:50], html,
                         "zeig-mir-wo.html",
                         "„Zeig mir wo“ abgeschlossen \U0001F5B1",
                         "%s. Markierter Screenshot liegt unter Dokumente & "
                         "Artefakte." % ("%d Fundstelle(n)" % len(boxen)
                                         if boxen else "Nichts gefunden"))
        if boxen:
            ergebnis = ("Gefunden: %s\n" % beschreibung
                        + "\n".join("- (%d, %d)–(%d, %d), Mitte (%d, %d)"
                                    % (b["x1"], b["y1"], b["x2"], b["y2"],
                                       (b["x1"] + b["x2"]) // 2,
                                       (b["y1"] + b["y2"]) // 2)
                                    for b in boxen)
                        + "\nKoordinaten in Bildpixeln (Screenshot %d×%d)."
                        % (breite, hoehe))
        else:
            ergebnis = ("Der Grounder hat „%s“ nicht gefunden. Rohantwort: %s"
                        % (beschreibung, antwort[:300]))
        if session_id:
            post_to_session(session_id,
                            "**\U0001F5B1 Zeig mir wo — fertig**\n\n%s" % ergebnis)
        run_finish(run_id, "done", ergebnis, art_id)
    except RunCancelled as _abbruch:
        run_abgebrochen(run_id, "„Zeig mir wo“", session_id, grund=_abbruch)
    except Exception as e:
        fail("„Zeig mir wo“ fehlgeschlagen ⚠️", e)
        run_finish(run_id, "error", str(e))
        if session_id:
            post_to_session(session_id, "⚠️ „Zeig mir wo“ fehlgeschlagen: %s" % e)

# ----------------------------------------------------------------------------
# Computer-Use Phase 3 — der Steuerungs-Loop (Planner + Grounder + Executor)
#
# Planner (dein Ollama-Modell) entscheidet WAS zu tun ist und beschreibt das
# Ziel; Grounder (LocateAnything) sagt WO es liegt; Executor (cliclick) führt
# es aus. Vor jeder Aktion, die den Rechner verändert, liegt eine Bestätigungs-
# Schranke: ohne „Ausführen“ des Besitzers passiert nichts. Not-Aus ist der
# Abbrechen-Knopf (greift zwischen den Schritten und während des Wartens).
# ----------------------------------------------------------------------------

# Erlaubte Tasten für den Executor (Weißliste — keine beliebigen Tastencodes).
COMPUTER_TASTEN = {
    "return": "return", "enter": "return", "esc": "esc", "escape": "esc",
    "tab": "tab", "space": "space", "leer": "space", "delete": "delete",
    "backspace": "delete", "hoch": "arrow-up", "runter": "arrow-down",
    "links": "arrow-left", "rechts": "arrow-right", "up": "arrow-up",
    "down": "arrow-down", "left": "arrow-left", "right": "arrow-right",
}

def _cliclick(*args):
    """Schreibt die Aktion in eine Datei (Test-Fake) — oder gibt False zurück.

    DOWOS_FAKE_EXECUTOR (.env) lenkt jede Aktion in eine Logdatei um, statt
    Maus und Tastatur wirklich zu bewegen — so testet der Harness den Loop,
    ohne je den echten Bildschirm zu berühren. Das Protokollformat ist
    absichtlich das alte cliclick-Format geblieben: Es ist kurz, es ist
    eindeutig, und die vorhandenen Tests lesen es aus.

    Gibt True zurueck, wenn die Aktion abgelenkt wurde; sonst False, und der
    Aufrufer geht ueber die Plattform-Rueckseite."""
    fake = ENV.get("DOWOS_FAKE_EXECUTOR", "")
    if fake:
        with open(fake, "a", encoding="utf-8") as f:
            f.write(" ".join(args) + "\n")
        return True
    return False

def bildschirm_skala(png_breite):
    """Faktor Bildpixel → logische Punkte (cliclick rechnet in Punkten).

    Auf Retina ist der Screenshot doppelt so breit wie die logische Fläche;
    ohne Umrechnung klickte das System um Faktor 2 daneben."""
    if ENV.get("DOWOS_FAKE_EXECUTOR"):
        return 1.0
    return _schirm().skala(png_breite)

def aktion_ausfuehren(aktion, breite, hoehe, skala):
    """Führt eine bestätigte Aktion aus. Gibt einen Protokolltext zurück."""
    typ = aktion.get("aktion")
    if typ in ("klick", "doppelklick"):
        box = aktion.get("_box")
        if not box:
            return "übersprungen — Ziel nicht gefunden"
        px = (box["x1"] + box["x2"]) // 2
        py = (box["y1"] + box["y2"]) // 2
        lx, ly = round(px / skala), round(py / skala)
        if not _cliclick(("dc" if typ == "doppelklick" else "c") + ":%d,%d" % (lx, ly)):
            _schirm().klick(lx, ly, doppelt=(typ == "doppelklick"))
        return "%s bei (%d, %d) Punkten [Pixel (%d, %d)]" % (
            "Doppelklick" if typ == "doppelklick" else "Klick", lx, ly, px, py)
    if typ == "tippen":
        text = aktion.get("text", "")
        if not _cliclick("t:" + text):
            _schirm().tippen(text)
        return "getippt: %r" % text[:80]
    if typ == "taste":
        name = (aktion.get("taste") or "").lower().strip()
        code = COMPUTER_TASTEN.get(name)
        if not code:
            return "unbekannte Taste übersprungen: %r" % name
        if not _cliclick("kp:" + code):
            _schirm().taste(code)
        return "Taste gedrückt: %s" % name
    return "unbekannte Aktion übersprungen: %r" % typ

PLANNER_PROMPT = (
    "Du steuerst den Computer für Dive on Wide. Du siehst einen Screenshot und eine "
    "Aufgabe. Entscheide die EINE nächste Aktion. Antworte NUR mit gültigem "
    "JSON nach diesem Schema (deutsch):\n"
    '{"gedanke":"kurze Begründung","aktion":"klick|doppelklick|tippen|taste|fertig",'
    '"ziel":"genaue Beschreibung des anzuklickenden Elements (nur bei klick/'
    'doppelklick)","text":"einzugebender Text (nur bei tippen)","taste":"return|'
    'esc|tab|hoch|runter|links|rechts (nur bei taste)","fertig_text":"Ergebnis '
    'für den Nutzer (nur bei fertig)"}\n'
    "Regeln: Gib NIEMALS Passwörter, Kreditkartennummern oder Geheimnisse ein. "
    "Wähle „fertig“, sobald die Aufgabe erledigt ist oder du nicht weiterkommst. "
    "Eine Aktion pro Schritt. Kein Text außerhalb des JSON.")

class PlannerZuLangsam(RuntimeError):
    """Der Planner hat den Zeitrahmen für EINEN Schritt gesprengt."""

def planner_naechste_aktion(auftrag, verlauf, png, modell):
    """Fragt den Planner nach der nächsten Aktion — sieht den Screenshot.

    Drei Dinge, die hier bewusst so sind (alle aus einem gescheiterten
    Nutzertest gelernt, bei dem der Lauf 10 Minuten still stand und dann nur
    „timed out“ meldete):

    1. no_think + json_mode — der Planner soll entscheiden, nicht philosophieren.
       Das allein bringt den Schritt von 26-140 s auf rund 2 s (siehe die
       Messreihe in llm_chat_once).
    2. Ein eigener, kurzer Zeitrahmen statt der 600 s des Chats. Ein Lauf hat
       bis zu 15 Schritte; 600 s pro Schritt heißt im schlimmsten Fall 2,5 h.
    3. Eine Fehlermeldung, die sagt, welches Modell zu langsam war und was man
       dagegen tun kann — „timed out“ allein hilft niemandem weiter."""
    verlauf_txt = ("\n".join("- " + s for s in verlauf[-8:])
                   if verlauf else "(noch nichts geschehen)")
    nachricht = {
        "role": "user",
        "content": ("Aufgabe: %s\n\nBisher ausgeführt:\n%s\n\nWas ist die nächste "
                    "Aktion? Nur JSON." % (auftrag, verlauf_txt)),
        "images": [base64.b64encode(png).decode()],
    }
    frist = int(get_setting("COMPUTER_PLANNER_TIMEOUT", "180"))
    try:
        roh = ollama_chat_once(
            modell, [{"role": "system", "content": PLANNER_PROMPT}, nachricht],
            temperature=0.2, json_mode=True, no_think=True, timeout=frist)
    except Exception as e:
        if ist_zeitablauf(e):
            raise PlannerZuLangsam(
                "Das Planner-Modell „%s“ hat für einen einzelnen Schritt länger "
                "als %d s gebraucht. Ein Lauf besteht aus bis zu %s Schritten — "
                "so wird das nichts. Abhilfe: unter Einstellungen → Computer-Use "
                "ein kleineres bildfähiges Modell als Planner wählen (z. B. ein "
                "4B- statt 12B-Modell), oder COMPUTER_PLANNER_TIMEOUT erhöhen, "
                "wenn du bereit bist zu warten."
                % (modell, frist, get_setting("COMPUTER_MAX_STEPS", "15")))
        if getattr(e, "code", 0) == 400:
            raise RuntimeError(
                "Das Planner-Modell „%s“ nimmt keine Bilder an — es kann den "
                "Bildschirm also gar nicht sehen. Wähle unter Einstellungen → "
                "Computer-Use ein bildfähiges Modell (Vision-Modell)." % modell)
        raise
    aktion = extract_json_or_none(roh)
    if not isinstance(aktion, dict) or not aktion.get("aktion"):
        return {"aktion": "fertig",
                "fertig_text": "Planner-Antwort unlesbar: %s" % roh[:200]}
    return aktion

# Unterlauf -> Lauf, den der Mensch sieht (z. B. ein Roadmap-Paket -> die Roadmap). Freigabefragen eines Unterlaufs
# erscheinen auch dort, und eine Antwort dort gilt für beide. Windows-VM 07.10.2026: Unter Windows fragt die Werkbank
# vor jedem Befehl; die Frage hing am unsichtbaren Unterlauf, und die Roadmap stand still.
RUN_ELTERN = {}


def _freigabe_laeufe(run_id):
    ids, x = [run_id], run_id
    while x in RUN_ELTERN and RUN_ELTERN[x] not in ids:
        x = RUN_ELTERN[x]
        ids.append(x)
    return ids


def warte_auf_bestaetigung(run_id, aktion_text):
    """Bestätigungs-Schranke: hält den Lauf an, bis der Besitzer entscheidet.

    Gibt True (ausführen), False (ablehnen) oder wirft RunCancelled. Ist die
    Auto-Bestätigung für diesen Lauf gesetzt, kehrt sie sofort mit True zurück."""
    laeufe = _freigabe_laeufe(run_id)
    conn = db()
    if any((conn.execute("SELECT autoconfirm FROM runs WHERE id=?", (x,)).fetchone() or {"autoconfirm": 0})["autoconfirm"]
           for x in laeufe):
        conn.close()
        return True
    for x in laeufe:
        conn.execute("UPDATE runs SET pending=?, decision='', updated_at=? WHERE id=?",
                     (aktion_text, now(), x))
    conn.commit()
    conn.close()
    wartezeit = int(get_setting("COMPUTER_CONFIRM_TIMEOUT", "300"))
    # Sichtbar machen, worauf gewartet wird — sonst steht im Protokoll später
    # nur ein Abbruch ohne Grund.
    run_step(run_id, "Wartet auf Freigabe (%d s): %s" % (wartezeit, aktion_text[:120]),
             "active")
    frist = time.time() + wartezeit
    while time.time() < frist:
        conn = db()
        zeilen = [conn.execute("SELECT cancel, decision, autoconfirm FROM runs WHERE id=?", (x,)).fetchone()
                  for x in laeufe]
        conn.close()
        zeilen = [z for z in zeilen if z]
        if any(z["cancel"] for z in zeilen):
            for x in laeufe:
                _pending_clear(x)
            raise RunCancelled()
        entsch = next((z["decision"] for z in zeilen if z["decision"]), "")
        if any(z["autoconfirm"] for z in zeilen) or entsch == "ok":
            for x in laeufe:
                _pending_clear(x)
            return True
        if entsch == "nein":
            for x in laeufe:
                _pending_clear(x)
            return False
        time.sleep(0.4)
    for x in laeufe:
        _pending_clear(x)
    # Zeitüberschreitung = kein OK → sicher abbrechen. Aber mit eigenem Namen:
    # „Vom Nutzer abgebrochen" wäre schlicht unwahr.
    raise FreigabeAusgeblieben(aktion_text, wartezeit)

def _pending_clear(run_id):
    conn = db()
    conn.execute("UPDATE runs SET pending='', decision='', updated_at=? WHERE id=?",
                 (now(), run_id))
    conn.commit()
    conn.close()

def run_confirm(run_id, ok, alle=False):
    """Entscheidung des Besitzers zur wartenden Aktion (Endpunkt-Helfer)."""
    conn = db()
    r = conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
    if not r or r["status"] != "running":
        conn.close()
        return False
    conn.execute("UPDATE runs SET decision=?, autoconfirm=?, updated_at=? WHERE id=?",
                 ("ok" if ok else "nein", 1 if (ok and alle) else 0, now(), run_id))
    conn.commit()
    conn.close()
    return True

def run_computer_task(auftrag, session_id=None, run_id=None):
    """Der Steuerungs-Loop: plant, findet, führt aus — Schritt für Schritt."""
    verlauf = []
    protokoll = ["# Computer-Use — Auftrag\n\n**Aufgabe:** %s\n" % auftrag]
    try:
        info = computer_info(refresh=True)
        if not info["steuern_aktiviert"]:
            raise RuntimeError("Die Computer-Steuerung ist in den Einstellungen "
                               "deaktiviert (Sicherheits-Standard). Zum Freischalten: "
                               "Einstellungen → Computer-Use → „Steuerung erlauben“.")
        if not info["steuern_bereit"]:
            raise RuntimeError("Computer-Steuerung nicht bereit: "
                               + (" | ".join(info["hinweise"])
                                  or "siehe Diagnose unter Einstellungen."))
        planner = (get_setting("COMPUTER_PLANNER_MODEL", "")
                   or get_setting("DEFAULT_MODEL"))
        max_steps = int(get_setting("COMPUTER_MAX_STEPS", "15"))
        bestaetigen = get_setting("COMPUTER_CONFIRM", "1") == "1"

        for schritt in range(1, max_steps + 1):
            # Das Modell mitschreiben: wenn ein Schritt hängt, will man sofort
            # sehen, WER denkt — sonst sucht man den Fehler an der falschen Stelle.
            run_step(run_id, "Schritt %d/%d — %s betrachtet den Bildschirm …"
                     % (schritt, max_steps, planner), "active")  # prüft auch Abbruch
            png = screenshot_aufnehmen()
            breite, hoehe = png_groesse(png)
            aktion = planner_naechste_aktion(auftrag, verlauf, png, planner)
            typ = aktion.get("aktion", "fertig")

            if typ == "fertig":
                schluss = aktion.get("fertig_text") or "Aufgabe abgeschlossen."
                verlauf.append("fertig: " + schluss)
                protokoll.append("\n## Schritt %d — fertig\n\n%s\n" % (schritt, schluss))
                break

            # Für Klicks erst das Ziel lokalisieren (Grounder)
            beschreibung = aktion.get("ziel", "")
            if typ in ("klick", "doppelklick"):
                run_step(run_id, "Grounder sucht: %s" % beschreibung[:60], "active")
                antwort = grounding_anfrage(png, beschreibung)
                boxen = parse_grounding_boxes(antwort, breite, hoehe)
                if not boxen:
                    verlauf.append("Ziel „%s“ nicht gefunden — übersprungen"
                                   % beschreibung)
                    protokoll.append("\n## Schritt %d — %s\n\nZiel „%s“ nicht "
                                     "gefunden.\n" % (schritt, typ, beschreibung))
                    continue
                aktion["_box"] = boxen[0]

            beschreibungstext = _aktion_klartext(aktion)
            # --- Bestätigungs-Schranke ---
            if bestaetigen:
                run_step(run_id, "Wartet auf Bestätigung: %s" % beschreibungstext,
                         "active")
                if not warte_auf_bestaetigung(run_id, beschreibungstext):
                    verlauf.append("abgelehnt: " + beschreibungstext)
                    protokoll.append("\n## Schritt %d — abgelehnt\n\n%s\n"
                                     % (schritt, beschreibungstext))
                    continue
            skala = bildschirm_skala(breite)
            ergebnis = aktion_ausfuehren(aktion, breite, hoehe, skala)
            gedanke = aktion.get("gedanke", "")
            verlauf.append("%s → %s" % (beschreibungstext, ergebnis))
            run_step(run_id, "%s — %s" % (beschreibungstext, ergebnis))
            protokoll.append("\n## Schritt %d — %s\n\n_Gedanke:_ %s\n\n**Ausgeführt:** "
                             "%s\n" % (schritt, beschreibungstext, gedanke, ergebnis))
            time.sleep(float(get_setting("COMPUTER_STEP_PAUSE", "0.8")))
        else:
            verlauf.append("Maximale Schrittzahl erreicht.")
            protokoll.append("\n## Ende\n\nMaximale Schrittzahl (%d) erreicht.\n"
                             % max_steps)

        art_id = deliver("Computer-Use: %s" % auftrag[:50], "\n".join(protokoll),
                         "computer-use.md", "Computer-Use abgeschlossen \U0001F5B1",
                         "%d Schritt(e). Protokoll unter Dokumente & Artefakte."
                         % len(verlauf))
        zusammenfassung = verlauf[-1] if verlauf else "Nichts ausgeführt."
        if session_id:
            post_to_session(session_id, "**\U0001F5B1 Computer-Use fertig**\n\n%s"
                            % zusammenfassung)
        run_finish(run_id, "done", "\n".join(verlauf), art_id)
    except RunCancelled as _abbruch:
        run_abgebrochen(run_id, "Computer-Use", session_id, grund=_abbruch)
    except Exception as e:
        # Auch ein gescheiterter Lauf hinterlässt ein Protokoll. Vorher stand
        # der Nutzer nach 10 Minuten mit „timed out“ und sonst nichts da —
        # ohne Artefakt war nicht einmal nachvollziehbar, wie weit es kam.
        art_id = None
        try:
            protokoll.append("\n## Abbruch\n\n%s\n" % e)
            art_id = deliver(
                "Computer-Use (abgebrochen): %s" % auftrag[:50],
                "\n".join(protokoll), "computer-use.md",
                "Computer-Use fehlgeschlagen ⚠️",
                "Nach %d Schritt(en) abgebrochen. Protokoll unter Dokumente & "
                "Artefakte." % len(verlauf))
        except Exception:
            fail("Computer-Use fehlgeschlagen ⚠️", e)
        run_finish(run_id, "error", str(e), art_id)
        if session_id:
            post_to_session(session_id, "⚠️ Computer-Use fehlgeschlagen: %s" % e)

def _aktion_klartext(aktion):
    typ = aktion.get("aktion")
    if typ in ("klick", "doppelklick"):
        return "%s auf „%s“" % ("Doppelklick" if typ == "doppelklick" else "Klick",
                                aktion.get("ziel", "?"))
    if typ == "tippen":
        return "Tippen: %r" % (aktion.get("text", "")[:60])
    if typ == "taste":
        return "Taste: %s" % aktion.get("taste", "?")
    return typ or "?"

# ----------------------------------------------------------------------------
# Second Brain — Chats werden Wissen; der Evolver verdichtet & lernt dazu
# ----------------------------------------------------------------------------

SB_SUMMARY_PROMPT = (
    "Du bist das Second Brain von Dive on Wide. Destilliere aus dem folgenden Chatverlauf "
    "kompaktes, wiederverwendbares Wissen als Markdown mit den Abschnitten: "
    "**Thema**, **Kernaussagen & Ergebnisse**, **Fakten & Entscheidungen**, "
    "**Erkenntnisse für zukünftige Aufgaben**. Maximal 300 Wörter, keine Floskeln.")

EVOLVER_PROMPT = (
    "Du bist der Evolver des Dive on Wide Second Brain — ein sich selbst verbesserndes "
    "Gedächtnis. Du erhältst den bisherigen Wissenskern und neue Gedächtniseinträge. "
    "Erzeuge eine VERBESSERTE neue Version des Kerns: führe Dopplungen zusammen, "
    "korrigiere Veraltetes, erkenne wiederkehrende Themen, Vorlieben und Arbeitsweisen "
    "des Nutzers, und destilliere übertragbare Prinzipien. Struktur: "
    "**Über den Nutzer & seine Projekte**, **Wiederkehrende Themen**, "
    "**Gelernte Prinzipien & beste Vorgehensweisen**, **Offene Fäden**. "
    "Maximal 700 Wörter. Jede Version soll klüger sein als die letzte.")

def sync_second_brain(session_id=None, notify=True):
    """Gliedert Chatverläufe als Zusammenfassungen in den Second-Brain-Ordner ein."""
    try:
        conn = db()
        if session_id:
            sessions = rows(conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)))
        else:
            sessions = rows(conn.execute("SELECT * FROM sessions"))
        conn.close()
        model = get_setting("DEFAULT_MODEL")
        count = 0
        for s in sessions:
            conn = db()
            msgs = rows(conn.execute(
                "SELECT role,content FROM messages WHERE session_id=? ORDER BY created_at",
                (s["id"],)))
            existing = conn.execute("SELECT updated_at FROM knowledge WHERE id=?",
                                    ("sb-" + s["id"],)).fetchone()
            conn.close()
            if len(msgs) < 2:
                continue
            if existing and (existing["updated_at"] or 0) >= (s["updated_at"] or 0):
                continue  # bereits aktuell
            transcript = "\n\n".join("%s: %s" % (m["role"].upper(), m["content"][:1500])
                                     for m in msgs)[:12000]
            summary = ollama_chat_once(model, [
                {"role": "system", "content": SB_SUMMARY_PROMPT},
                {"role": "user", "content": transcript}], temperature=0.3)
            conn = db()
            conn.execute(
                "INSERT INTO knowledge(id,name,content,folder,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, content=excluded.content, updated_at=excluded.updated_at",
                ("sb-" + s["id"], "Chat: " + (s["title"] or "Ohne Titel"), summary,
                 "Second Brain", now(), now()))
            conn.commit(); conn.close()
            count += 1
        if notify and count:
            emit("Second Brain synchronisiert 🧠",
                 "%d Chatverlauf/-verläufe wurden als Wissen eingegliedert." % count, "skill")
    except Exception as e:
        fail("Second-Brain-Sync fehlgeschlagen ⚠️", e)

def evolve_brain():
    """Selbstverbesserung: verdichtet alle Second-Brain-Einträge zu einem Wissenskern."""
    try:
        conn = db()
        entries = rows(conn.execute(
            "SELECT * FROM knowledge WHERE folder='Second Brain' AND id!='sb-core' "
            "ORDER BY updated_at DESC"))
        core = conn.execute("SELECT content FROM knowledge WHERE id='sb-core'").fetchone()
        conn.close()
        if not entries:
            raise RuntimeError("Keine Second-Brain-Einträge vorhanden. "
                               "Bitte zuerst synchronisieren.")
        corpus = "\n\n---\n\n".join("## %s\n%s" % (e["name"], e["content"])
                                    for e in entries)[:14000]
        prev = core["content"] if core else "Noch leer — dies ist die erste Version."
        new_core = ollama_chat_once(get_setting("DEFAULT_MODEL"), [
            {"role": "system", "content": EVOLVER_PROMPT},
            {"role": "user", "content": "BISHERIGER KERN:\n%s\n\nNEUE EINTRÄGE:\n%s"
             % (prev, corpus)}], temperature=0.4)
        conn = db()
        conn.execute(
            "INSERT INTO knowledge(id,name,content,folder,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "content=excluded.content, updated_at=excluded.updated_at",
            ("sb-core", "🧠 Kern-Erkenntnisse (Evolver)", new_core,
             "Second Brain", now(), now()))
        conn.commit(); conn.close()
        emit("Evolver abgeschlossen 🧬",
             "Der Wissenskern wurde aus %d Einträgen neu destilliert und verbessert."
             % len(entries), "skill")
    except Exception as e:
        fail("Evolver fehlgeschlagen ⚠️", e)

# ----------------------------------------------------------------------------
# Agenten-Pipelines — mehrere Agenten zu einem Workflow verketten
# ----------------------------------------------------------------------------

# --- Bausteine: Jeder Pipeline-Schritt ist einer dieser Typen -----------------
#
#   agent     eine KI-Persona bearbeitet den Text
#   skill     eine mehrstufige Skill-Pipeline läuft darüber
#   research  Deep-Research-Schleife (Web + Freier KI-Modus)
#   web       Live-Web-Recherche und Verdichtung
#   code      Coding-Agent schreibt und testet Code in der Sandbox
#   image     Bildgenerierung über eine lokale Bild-API (z. B. ComfyUI/A1111)
#   prompt    gespeicherter Prompt aus der Bibliothek
#
# Neue Typen brauchen nur einen Eintrag in STEP_RUNNERS — die UI zieht nach.

# ----------------------------------------------------------------------------
# Modellwahl pro Baustein — das Herz der Multi-Modell-Workflows
#
# Rangfolge (das Spezifischere gewinnt):
#   1. step["model"]  — im Baustein ausdrücklich gewählt (Builder oder Orchestrator)
#   2. eigenes Modell des referenzierten Agenten/Skills
#   3. ctx["model"]   — Override beim Pipeline-Start (gilt nur, wo nichts gesetzt ist)
#   4. DEFAULT_MODEL
# So kann eine Kette laufen wie: Recherche mit Gemma → Code mit Qwen-Coder →
# Synthese mit einem großen Modell — jeder Schritt bei seinem eigenen Provider.
# ----------------------------------------------------------------------------

def step_model(step, ctx, eigen=""):
    return ((step or {}).get("model") or eigen or ctx.get("model")
            or get_setting("DEFAULT_MODEL"))

def model_kurz(model):
    """'providerid@@modell' → nur der Modellname (fürs Protokoll)."""
    return (model or "").split(MODEL_SEP)[-1]

def _step_agent(step, current, ctx):
    agent = ctx["agents"].get(step.get("ref_id") or step.get("agent_id"))
    # Ohne vorab angelegten Agenten darf ein Schritt eine Inline-Rolle
    # mitbringen (system_prompt) — das nutzt der Orchestrator, um Rollen
    # spontan zu besetzen, ohne sie dauerhaft anzulegen.
    sys_prompt = (agent["system_prompt"] if agent
                  else step.get("system_prompt")
                  or "Du bist ein hilfreicher Assistent in Dive on Wide. Antworte auf Deutsch.")
    # In einer Kette darf ein Agent keine Quellen erfinden oder umschreiben —
    # die echte Quellenliste haengt die Pipeline am Ende selbst an.
    if ctx.get("quellen"):
        sys_prompt += ("\n\nWICHTIG: Erfinde KEINE Quellenangaben und schreibe KEINE "
                       "eigene Quellenliste — die tatsaechlich genutzten Quellen werden "
                       "automatisch angehaengt. Uebernimm nur Aussagen aus dem Material "
                       "und kennzeichne Unbelegtes weiterhin als Vermutung.")
    model = step_model(step, ctx, agent.get("model") if agent else "")
    label = (agent["name"] if agent else (step.get("label") or "Assistent")) \
        + " · " + model_kurz(model)
    out = ollama_chat_once(model, [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": step.get("instruction", "") + "\n\n---\n\n" + current}])
    return label, out

def _step_skill(step, current, ctx):
    skill = ctx["skills"].get(step.get("ref_id"))
    if not skill:
        return "Skill (fehlt)", current
    model = step_model(step, ctx, skill.get("model"))
    text = (step.get("instruction", "") + "\n\n" + current).strip()
    for sub in json.loads(skill["steps"] or "[]"):
        text = ollama_chat_once(model, [
            {"role": "system", "content": sub.get("system_prompt", "")},
            {"role": "user", "content": text}])
    return "Skill: " + skill["name"], text

def _step_web(step, current, ctx):
    query = ollama_chat_once(
        step_model(step, ctx),
        [{"role": "system", "content": "Formuliere EINE prägnante Websuchanfrage "
          "(max. 8 Wörter). Gib nur die Suchanfrage aus."},
         {"role": "user", "content": (step.get("instruction", "") + "\n" + current)[:800]}],
        temperature=0.3).strip().strip('"')
    ctxt, src = suche_mit_varianten(query, ctx["model"] or None)
    if not ctxt:
        # Keine Treffer → ehrlich vermerken statt zu erfinden.
        out = ollama_chat_once(step_model(step, ctx), [
            {"role": "system", "content": WEB_NO_RESULTS_PROMPT % (src or "keine Treffer")},
            {"role": "user", "content": "Anliegen: %s" % current[:1500]}])
        return "Web-Recherche (nichts gefunden)", (
            current + "\n\n### Web-Recherche\n\n_Keine verwertbaren Treffer zu '%s'._\n\n%s"
            % (query, out))
    out = ollama_chat_once(step_model(step, ctx), [
        {"role": "system", "content": WEB_GROUNDING_PROMPT +
         "Verdichte die folgenden Ergebnisse zu belastbaren Erkenntnissen mit "
         "Quellenangaben. Trenne klar Gesichertes von Unsicherem."},
        {"role": "user", "content": "Anliegen: %s\n\n%s" % (current[:1500], ctxt)}])
    if isinstance(src, list) and src:
        ctx.setdefault("quellen", []).extend(src)
    quellen = ("\n\n**Quellen:**\n" + "\n".join("- %s" % u for u in dict.fromkeys(src))) \
        if isinstance(src, list) and src else ""
    return "Web-Recherche (%s)" % query, (out + quellen)

def _step_browser(step, current, ctx):
    """Steuert einen echten Browser. Der Auftrag wird aus Anweisung und
    bisherigem Ergebnis gebaut — so kann eine Pipeline erst denken und dann
    gezielt im Web nachsehen."""
    if ctx.get("run_id") in GAST_LAEUFE:
        return GAST_VERBOT
    auftrag = ollama_chat_once(step_model(step, ctx), [
        {"role": "system", "content":
         "Formuliere einen präzisen Browser-Auftrag in höchstens drei Sätzen: "
         "Was soll im Web gesucht, geöffnet und herausgefunden werden? "
         "Nenne konkrete Suchbegriffe. Gib NUR den Auftrag aus."},
        {"role": "user", "content": (step.get("instruction", "") + "\n\nKontext:\n"
                                     + current[:2000])}], temperature=0.3).strip()
    try:
        bericht, urls = browser_run(auftrag, step_model(step, ctx),
                                    step.get("max_steps"), ctx.get("run_id"))
    except Exception as e:
        # Ohne Browser bricht die Pipeline nicht ab, sondern weicht auf die
        # eingebaute Web-Recherche aus.
        run_step(ctx.get("run_id"), "Browser nicht verfügbar — weiche auf "
                                    "Web-Recherche aus", "active")
        ersatz_label, ersatz = _step_web(step, current, ctx)
        return ("Web-Recherche (Browser nicht verfügbar: %s)" % str(e)[:120]), ersatz
    quellen = ("\n\n**Besuchte Seiten:**\n"
               + "\n".join("- %s" % u for u in dict.fromkeys(urls))) if urls else ""
    return "Browser", ("%s\n\n### Browser-Recherche\n\n**Auftrag:** %s\n\n%s%s"
                       % (current, auftrag, bericht, quellen))

def _step_think(step, current, ctx):
    """Denkschritt: keine neue Information, sondern Ordnung im Vorhandenen —
    was wissen wir, was widerspricht sich, was fehlt noch?"""
    anweisung = step.get("instruction") or (
        "Prüfe den bisherigen Stand: Was ist belegt, was ist unsicher, wo "
        "widersprechen sich die Quellen, welche Frage ist noch offen?")
    out = ollama_chat_once(step_model(step, ctx), [
        {"role": "system", "content":
         "Du bist der Denkschritt einer Recherche-Pipeline. Du bringst KEINE neuen "
         "Fakten ein, sondern ordnest das Vorhandene: (1) Was ist durch Quellen "
         "belegt? (2) Was ist plausibel, aber unbelegt? (3) Wo widersprechen sich "
         "Quellen? (4) Welche konkrete Frage muss als Nächstes beantwortet werden? "
         "Antworte in genau diesen vier Abschnitten auf Deutsch."},
        {"role": "user", "content": anweisung + "\n\n---\n\n" + current}],
        temperature=0.3)
    return "Denkschritt", (current + "\n\n### Zwischenstand\n\n" + out)

def _step_research(step, current, ctx):
    loops = int(step.get("loops", 2))
    topic = (step.get("instruction", "") + " " + current[:1500]).strip()
    report = deep_research_core(topic, loops, step_model(step, ctx),
                                use_web=step.get("use_web", True), run_id=ctx.get("run_id"),
                                quellen_sammler=ctx.setdefault("quellen", []))
    return "Deep Research (%d Runden)" % loops, report

def _step_code(step, current, ctx):
    ws = step.get("workspace") or "pipeline"
    task = (step.get("instruction", "") + "\n\n" + current)[:4000]
    log, ok = coding_agent_core(task, ws, step_model(step, ctx),
                                int(step.get("iterations", 3)), "",
                                run_id=ctx.get("run_id"))
    return "Coding-Diver (%s)" % ("erfolgreich" if ok else "mit Rest-Fehlern"), log

def _step_prompt(step, current, ctx):
    pr = ctx["prompts"].get(step.get("ref_id"))
    body = (pr["content"] if pr else step.get("instruction", ""))
    out = ollama_chat_once(step_model(step, ctx), [
        {"role": "system", "content": "Du bist Dive on Wide. " + HERKUNFT + " Antworte präzise auf Deutsch."},
        {"role": "user", "content": body + "\n\n" + current}])
    return "Prompt: " + (pr["name"] if pr else "frei"), out

def _step_image(step, current, ctx):
    """Bildgenerierung über eine lokale Bild-API. Ohne Konfiguration wird der
    fertige Prompt geliefert — es wird nichts vorgetäuscht."""
    model = step_model(step, ctx)
    prompt = ollama_chat_once(model, [
        {"role": "system", "content": "Erzeuge aus dem Material einen hochwertigen "
         "ENGLISCHEN Bildprompt (eine Zeile, kommaseparierte Deskriptoren). "
         "Gib NUR den Prompt aus."},
        {"role": "user", "content": (step.get("instruction", "") + "\n" + current)[:2000]}],
        temperature=0.7).strip()
    url = get_setting("IMAGE_API_URL", "").strip()
    if not url:
        return "Bild-Prompt (keine Bild-API konfiguriert)", (
            "%s\n\n### Bildprompt\n\n```\n%s\n```\n\n_Es ist keine Bild-API hinterlegt. "
            "Trage in den Einstellungen `IMAGE_API_URL` ein (z. B. eine lokale "
            "AUTOMATIC1111- oder ComfyUI-Instanz), dann erzeugt dieser Schritt "
            "echte Bilder._" % (current, prompt))
    try:
        payload = {"prompt": prompt, "steps": int(get_setting("IMAGE_STEPS", "25"))}
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode("utf-8"))
        images = data.get("images") or []
        if not images:
            raise RuntimeError("Bild-API lieferte kein Bild zurück.")
        import base64
        raw = base64.b64decode(images[0].split(",")[-1])
        art_id = nid()
        fn = "bild-%s.png" % art_id
        with open(os.path.join(ARTIFACTS_DIR, fn), "wb") as f:
            f.write(raw)
        conn = db()
        conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)",
                     (art_id, fn, "Bild: " + prompt[:50], None, now()))
        conn.commit(); conn.close()
        return "Bild erzeugt", ("%s\n\n### Bild\n\n![Bild](/api/artifacts/%s/download)\n\n"
                                "**Prompt:** `%s`" % (current, art_id, prompt))
    except Exception as e:
        return "Bild fehlgeschlagen", ("%s\n\n### Bildprompt\n\n```\n%s\n```\n\n"
                                       "_Bild-API nicht erreichbar: %s_"
                                       % (current, prompt, e))

STEP_RUNNERS = {"agent": _step_agent, "skill": _step_skill, "web": _step_web,
                "research": _step_research, "code": _step_code,
                "prompt": _step_prompt, "image": _step_image,
                "browser": _step_browser, "think": _step_think}

STEP_META = {
    "agent":    {"icon": "\U0001F916", "label": "Diver",         "source": "agents"},
    "skill":    {"icon": "⚡",     "label": "Skill",         "source": "skills"},
    "prompt":   {"icon": "\U0001F4AC", "label": "Prompt",        "source": "prompts"},
    "web":      {"icon": "\U0001F310", "label": "Web-Recherche", "source": None},
    "research": {"icon": "\U0001F52D", "label": "Deep Research", "source": None},
    "code":     {"icon": "\U0001F9F0", "label": "Coding-Diver",  "source": None},
    "image":    {"icon": "\U0001F3A8", "label": "Bild",          "source": None},
    "browser":  {"icon": "\U0001F5A5", "label": "Browser",       "source": None},
    "think":    {"icon": "\U0001F914", "label": "Denkschritt",   "source": None},
}

def run_pipeline(pipeline, user_input, model_override, session_id=None, run_id=None):
    """Führt eine Pipeline aus gemischten Bausteinen aus."""
    name = pipeline["name"]
    try:
        steps = json.loads(pipeline["steps"] or "[]")
        conn = db()
        ctx = {
            "agents": {a["id"]: dict(a) for a in
                       conn.execute("SELECT * FROM agents").fetchall()},
            "skills": {s["id"]: dict(s) for s in
                       conn.execute("SELECT * FROM skills").fetchall()},
            "prompts": {p["id"]: dict(p) for p in
                        conn.execute("SELECT * FROM prompts").fetchall()},
            "model": model_override or "", "run_id": run_id,
        }
        conn.close()
        current = user_input
        trace = ["# Pipeline: %s\n\n**Eingabe:**\n\n%s\n" % (name, user_input)]
        for i, step in enumerate(steps):
            stype = step.get("type", "agent")
            meta = STEP_META.get(stype, STEP_META["agent"])
            run_step(run_id, "Schritt %d/%d — %s läuft …"
                     % (i + 1, len(steps), meta["label"]), "active")
            runner = STEP_RUNNERS.get(stype, _step_agent)
            try:
                label, current = runner(step, current, ctx)
            except ModellFehler as e:
                # Ein einzelner Schritt darf die ganze Kette nicht mitnehmen,
                # wenn nur der Speicher nicht reichte. Einmal Speicher freigeben
                # und mit einem kleineren Modell wiederholen — sichtbar im
                # Protokoll, nie stillschweigend (Vorfall 17.09.2026: Pipeline
                # nach 6 Minuten tot, Meldung nur „HTTP Error 500").
                if not e.speicher:
                    raise
                benutzt = step_model(step, ctx)
                ollama_alle_entladen()
                ersatz = ausweichmodell(benutzt)
                if not ersatz or ersatz == model_kurz(benutzt):
                    raise
                run_step(run_id, "Schritt %d/%d: „%s“ passte nicht in den Speicher — "
                                 "Wiederholung mit „%s“"
                         % (i + 1, len(steps), model_kurz(benutzt), ersatz))
                step = dict(step, model=ersatz,
                            model_grund="Ausweichmodell nach Speichernot")
                label, current = runner(step, current, ctx)
            run_step(run_id, "Schritt %d/%d — %s %s"
                     % (i + 1, len(steps), meta["icon"], label))
            # Transparenz: welches Modell (und warum) diesen Schritt gemacht hat
            benutzt = model_kurz(step_model(step, ctx))
            grund = step.get("model_grund") or ""
            kopf = "_Modell: `%s`%s_\n\n" % (benutzt,
                                             (" — " + grund) if grund else "")
            trace.append("\n## Schritt %d — %s %s\n\n%s%s\n"
                         % (i + 1, meta["icon"], label, kopf, current))
        # Quellen ueberleben jeden Folgeschritt: die Pipeline haengt sie am Ende
        # deterministisch an — ein spaeterer Agent kann sie nicht mehr wegkuerzen
        # oder falsch abtippen.
        gesammelt = sorted(set(ctx.get("quellen") or []))
        if gesammelt:
            quellblock = ("\n\n---\n\n## Tatsaechlich genutzte Web-Quellen\n\n"
                          + "\n".join("- <%s>" % u for u in gesammelt))
            current = current + quellblock
            trace.append(quellblock)
        art_id = deliver("Pipeline: %s" % name, "\n".join(trace),
                         "pipeline-" + name + ".md",
                         "Pipeline „%s“ abgeschlossen ⛓️" % name,
                         "%d Schritte durchlaufen. Ergebnis inkl. aller "
                         "Zwischenschritte liegt unter Dokumente & Artefakte."
                         % len(steps))
        if session_id:
            post_to_session(session_id,
                            "**⛓️ Pipeline „%s“ abgeschlossen** "
                            "(%d Schritte)\n\n%s" % (name, len(steps), current))
        run_finish(run_id, "done", current, art_id)
    except RunCancelled as _abbruch:
        run_abgebrochen(run_id, "Pipeline „%s“" % name, session_id, grund=_abbruch)
    except Exception as e:
        fail("Pipeline „%s“ fehlgeschlagen ⚠️" % name, e)
        run_finish(run_id, "error", str(e))
        if session_id:
            post_to_session(session_id, "⚠️ Pipeline „%s“ "
                                        "fehlgeschlagen: %s" % (name, e))

# Rückwärtskompatibler Name
run_agent_pipeline = run_pipeline

# ----------------------------------------------------------------------------
# Orchestrator — aus einer Beschreibung automatisch einen Ablauf bauen & lösen
#
# Der Orchestrator ist der „Dirigent": Er nimmt ein Ziel in natürlicher Sprache,
# plant mit dem lokalen Modell einen Ablauf aus den vorhandenen Bausteinen
# (Agent-Rollen, Web, Deep Research, Coding-Agent, Browser, Denkschritt …),
# richtet die Rollen spontan ein und führt alles über dieselbe Pipeline-
# Maschinerie aus. Das Ergebnis landet — wie bei jedem Lauf — als Artefakt und
# als Antwort im Chat.
# ----------------------------------------------------------------------------

ORCHESTRATOR_PROMPT = (
    "Du bist der Orchestrator von Dive on Wide. Du bekommst ein Ziel und entwirfst einen "
    "Ablauf aus 2-5 Schritten, um es zu erreichen. Nutze AUSSCHLIESSLICH diese "
    "Bausteintypen:\n"
    "- agent: ein Denk-/Schreibschritt; gib in \"rolle\" einen knappen System-Prompt "
    "(wer ist das, wie arbeitet er)\n"
    "- web: kurze Live-Websuche zu einer Frage\n"
    "- research: mehrstufige Deep-Research-Schleife zu einem Thema\n"
    "- code: Coding-Agent, schreibt UND führt Code aus\n"
    "- browser: öffnet echte Webseiten und liest sie\n"
    "- think: ordnet das Vorhandene (belegt/unbelegt/offen), bringt keine neuen Fakten\n"
    "- image: erzeugt einen Bildprompt bzw. ein Bild\n"
    "Antworte NUR mit gültigem JSON nach diesem Schema (deutsch):\n"
    '{"plan":"ein Satz, was der Ablauf tut","schritte":[{"typ":"agent","rolle":"…",'
    '"anweisung":"was dieser Schritt konkret tun soll","modell":"exakter Modellname '
    'aus der Liste","warum_modell":"kurz: warum dieses Modell"}]}\n'
    "MODELLWAHL: Du bekommst unten die verfügbaren Modelle. Wähle für JEDEN Schritt "
    "bewusst das passendste aus und schreibe seinen Namen EXAKT so in \"modell\". "
    "Jede Zeile nennt Groesse und Merkmale des Modells. Faustregeln: Code "
    "schreiben/prüfen → steht bei Modellen eine 'Werkbank-Pruefung hier' (auf diesem "
    "Rechner gemessen), das mit den MEISTEN geloesten Aufgaben, auch wenn es nicht "
    "'Code' heisst; nur ohne Messung ein Modell mit dem Merkmal 'Code'. Ein Bild "
    "ansehen → eines mit 'sieht Bilder'; Recherche, Zusammenfassen und Alltagstext → "
    "ein schnelles, kleineres Modell; schwieriges Schlussfolgern, Synthese und "
    "Endtexte → das grösste passende, gern mit 'denkt gruendlich'. Ein "
    "Modell mit 'PASST NICHT' NIE waehlen. Verschiedene Schritte "
    "dürfen und sollen verschiedene Modelle nutzen. Kennst du keins, lass \"modell\" leer.\n"
    "Jeder Schritt bekommt das Ergebnis des vorherigen. Kein Text ausserhalb des JSON.\n"
    "REGELN FUER SINNVOLLE ABLAEUFE (wichtig):\n"
    "- So WENIGE Schritte wie moeglich. Bei einer einfachen Frage reichen 1-2.\n"
    "- 'code' NUR wenn der Nutzer wirklich Software, ein Skript oder eine Berechnung "
    "will. Bei Recherche-, Wissens- oder Textfragen NIEMALS 'code' einbauen — ein "
    "Bericht ist kein Programm.\n"
    "- 'web' und 'research' NICHT zusammen: 'research' recherchiert bereits selbst im "
    "Web. Nimm 'web' fuer eine schnelle Frage, 'research' fuer ein Thema in der Tiefe.\n"
    "- 'image' nur, wenn ein Bild verlangt wird.\n"
    "- Der LETZTE Schritt liefert das, was der Nutzer sehen will (meist 'agent', der "
    "das Ergebnis verstaendlich zusammenfasst).\n"
    "- Kein Schritt, der nur wiederholt, was der vorherige schon getan hat.")

def _modell_aufloesen(wunsch, verfuegbar):
    """Ordnet den Modellwunsch des Planers einem echten Modell zu (tolerant).

    Der Planer schreibt oft nur 'qwen2.5-coder' statt 'ollama@@qwen2.5-coder:14b' —
    hier wird beides zusammengeführt, damit die Kette nicht an Tippfehlern scheitert."""
    w = (wunsch or "").strip().lower()
    if not w:
        return ""
    for m in verfuegbar:                       # exakte Referenz oder exaktes Label
        if w in (m["name"].lower(), m.get("label", "").lower()):
            return m["name"]
    for m in verfuegbar:                       # Teiltreffer (längster gewinnt)
        if w in m.get("label", "").lower() or m.get("label", "").lower() in w:
            return m["name"]
    return ""

WINZLING_BYTE = 1.2e9           # darunter (≈ 1,5 B Parameter in 4 Bit) wählt der Orchestrator kein Modell selbst


def modellwahl_pruefen(schritt, katalog):
    """Was der Katalog sagt, gilt — nicht nur als Rat an den Planer.

    Zwei Regeln in Code, weil der Planer sie nachweislich nicht zuverlaessig
    befolgt (Messung 26.09.2026, gemma4:12b, 24 Ziele):
    1. Ein Modell mit „passt nicht" (zu gross oder hier abgestuerzt) wird nie
       ausgefuehrt; der Schritt faellt auf das Standardmodell zurueck.
    2. Ein Code-Schritt bekommt das hier gemessen beste Modell, wenn das
       gewaehlte ebenfalls gemessen und schlechter ist. Obwohl der Katalog
       qwen3.6-35b mit 42/72 und qwen2.5-coder mit 17/72 auswies und die
       Anweisung das Bessere verlangte, nahm der Planer in 5 von 5
       Code-Schritten den Coder — er folgt dem Namen, nicht der Messung.
    Gibt einen Hinweis fuer den Verlauf zurueck oder None."""
    nach_name = {e.get("name"): e for e in katalog}
    gewaehlt = nach_name.get(schritt.get("model"))
    # 0. Winzlinge (unter ~1,5 B, z. B. qwen2.5:0.5b) taugen nicht für echte Arbeit. 07.10.2026: Für „erkläre in drei
    #    Sätzen“ nahm der Planer qwen2.5:0.5b, weil der Katalog Zusammenfassen einem „schnellen, kleinen“ Modell gibt.
    groesse = (gewaehlt or {}).get("groesse") or 0
    if gewaehlt and 0 < groesse < WINZLING_BYTE and str(gewaehlt.get("name")).split("@@")[-1] != \
            (get_setting("DEFAULT_MODEL") or "").split("@@")[-1]:
        schritt.pop("model", None)
        schritt.pop("model_grund", None)
        return "„%s“ ist zu klein für diese Arbeit — Standardmodell" % gewaehlt.get("label")
    if gewaehlt and gewaehlt.get("speicher") == "zu_gross":
        schritt.pop("model", None)
        schritt.pop("model_grund", None)
        return "„%s“ passt hier nicht in den Speicher — Standardmodell" % gewaehlt.get("label")
    # 3. „Knapp“ nur, wenn der Nutzer es selbst als Standard gewaehlt hat oder es
    #    hier gemessen wurde. Ein zweites knappes Modell verdraengt das erste; jeder
    #    Wechsel laedt neu. Anwendungstest 29.09.2026: In einer frischen Instanz nahm
    #    der Planer fuer eine Reiseplanung das dichte 27B (7 min je Recherche-Runde,
    #    dann Zeitueberschreitung) — genau das Modell, das hier schon abgestuerzt war.
    standard = (get_setting("DEFAULT_MODEL") or "").split("@@")[-1]
    guete = modell_guete()
    if (gewaehlt and gewaehlt.get("speicher") == "knapp" and not gewaehlt.get("entfernt")
            and str(gewaehlt.get("name") or "").split("@@")[-1] != standard
            and not (guete.get(gewaehlt.get("label")) or {}).get("aufgaben")):
        schritt.pop("model", None)
        schritt.pop("model_grund", None)
        return "„%s“ passt nur knapp in den Speicher und würde das Standardmodell verdrängen — Standardmodell" % gewaehlt.get("label")
    if schritt.get("type") != "code" or not gewaehlt:
        return None
    def geloest(e):
        g = guete.get(e.get("label"))
        return g.get("geloest") if isinstance(g, dict) and g.get("aufgaben") else None
    kandidaten = [e for e in katalog if e.get("speicher") != "zu_gross" and geloest(e) is not None]
    if not kandidaten or geloest(gewaehlt) is None:
        return None
    bestes = max(kandidaten, key=geloest)
    if geloest(bestes) <= geloest(gewaehlt):
        return None
    schritt["model"] = bestes["name"]
    schritt["model_grund"] = "hier gemessen besser: %d statt %d geloeste Werkbank-Aufgaben" % (
        geloest(bestes), geloest(gewaehlt))
    return "Code-Schritt: „%s“ statt „%s“ (%s)" % (bestes.get("label"), gewaehlt.get("label"),
                                                   schritt["model_grund"])

# Am 26.09.2026 fielen zwei echte Programmieraufträge durch dieses Sieb, und
# der Coding-Agent wäre gestrichen worden: „Baue ein Kommandozeilenwerkzeug,
# das CSV-Dateien zusammenführt" und „Finde den Fehler in diesem
# Sortieralgorithmus: def s(l): …". Gefunden vom Prüfstand des Hausmodells.
CODE_WOERTER = ("code", "skript", "script", "programm", "software", "funktion",
                "berechne", "rechne", "app", "tool", "klasse", "debug", "bug",
                "python", "javascript", "html", "css", "sql", "api bauen",
                "algorithm", "kommandozeil", "befehlszeil", "implementier",
                "kompilier", "compil", "regex", "def ", "csv")

def plan_bereinigen(schritte, ziel):
    """Entfernt unsinnige Schritte aus einem Orchestrator-Plan (harte Absicherung).

    Der Planer neigt dazu, Bausteine einzubauen, die zur Aufgabe nicht passen —
    etwa einen Coding-Agenten fuer eine reine Rechercheanfrage. Prompt-Regeln
    allein reichen dafuer nicht, deshalb wird hier zusaetzlich gefiltert."""
    z = (ziel or "").lower()
    will_code = any(w in z for w in CODE_WOERTER)
    will_bild = any(w in z for w in ("bild", "grafik", "illustration", "foto", "logo"))
    raus, sauber = [], []
    for s in schritte:
        typ = s.get("type")
        if typ == "code" and not will_code:
            raus.append("Coding-Diver (kein Code verlangt)"); continue
        if typ == "image" and not will_bild:
            raus.append("Bild (kein Bild verlangt)"); continue
        # web + research ist doppelt gemoppelt: research recherchiert selbst
        if typ == "web" and any(x.get("type") == "research" for x in schritte):
            raus.append("Web-Recherche (Deep Research sucht bereits selbst)"); continue
        # zwei gleiche Bausteine direkt hintereinander
        if sauber and sauber[-1].get("type") == typ and typ in (
                "web", "research", "think", "code", "image", "browser"):
            raus.append("%s (doppelt)" % typ); continue
        sauber.append(s)
    if raus:
        emit("Orchestrator-Plan bereinigt \U0001F9F9",
             "Entfernte Schritte: " + ", ".join(raus))
    return sauber

SCHWARM_FRIST = 240     # Sekunden je Modellaufruf im Schwarm — ein Arbeiter in der Wiederholungsschleife hielt sonst 6 min


def run_schwarm(ziel, session_id=None, run_id=None, planer="", arbeiter=(), runden=3, testbefehl="", angeheftet=""):
    """Der Schwarm (schwarm.py): Planer verteilt, kleine Modelle arbeiten parallel, in Runden."""
    try:
        planer = planer or get_setting("ORCHESTRATOR_MODELL", "") or get_setting("DEFAULT_MODEL")
        arbeiter = [a for a in (arbeiter or []) if a][:4] or [planer]
        wurzel = os.path.join(STORAGE_DIR, "schwarm", run_id or nid())
        zusatz, wissen_namen = orchestrator_kontext(ziel, angeheftet)
        if wissen_namen:
            run_step(run_id, "📚 Wissen genutzt: %s" % ", ".join(wissen_namen))
        voll_ziel = ziel + ("\n\nGrundlage:\n" + zusatz if zusatz else "")
        ausfuehren = None
        if testbefehl:
            if get_setting("SANDBOX_ENABLED", "0") != "1":
                run_step(run_id, "Tests übersprungen: Code-Ausführung ist abgeschaltet (Einstellungen → Code-Sandbox)")
                testbefehl = ""
            else:
                def ausfuehren(befehl, ordner, frist):
                    tmp = tempfile.mkdtemp(prefix="schwarm-")
                    argv = werkbank.befehl_bauen(befehl, ordner, tmp, "projekt", werkbank.sandbox_art())
                    if not argv:
                        return 1, "Keine Sandbox auf diesem System — Tests werden hier nicht ausgeführt."
                    try:
                        r = subprocess.run(argv, cwd=ordner, capture_output=True, text=True, timeout=frist)
                        return r.returncode, r.stdout + r.stderr
                    except subprocess.TimeoutExpired:
                        return 124, "Zeitlimit überschritten"
        run_step(run_id, "🐝 Planer: %s · Arbeiter: %s" % (model_kurz(planer), ", ".join(model_kurz(a) for a in arbeiter)))
        erg = schwarm.laufen(
            voll_ziel, wurzel,
            lambda modell, nachrichten: llm_chat_once(modell, nachrichten, temperature=0.3, no_think=True,
                                                     timeout=SCHWARM_FRIST),
            planer, arbeiter, runden=max(1, min(int(runden or 3), 5)), testbefehl=testbefehl,
            melden=lambda t: run_step(run_id, t), ausfuehren=ausfuehren)   # run_step bricht bei ✕ selbst ab
        text = schwarm.bericht(ziel, erg, wurzel)
        art_id = deliver("Schwarm: " + ziel[:60], text, "schwarm.md",
                         "Schwarm %s 🐝" % ("fertig" if erg["fertig"] else "beendet"),
                         "%d Runden, %d Dateien" % (erg["runden"], len(erg["dateien"])))
        run_finish(run_id, "done" if erg["fertig"] else "warn",
                   "%s nach %d Runden · %d Dateien" % ("Fertig" if erg["fertig"] else "Nicht ganz fertig",
                                                        erg["runden"], len(erg["dateien"])), art_id)
        if session_id:
            post_to_session(session_id, text)
    except RunCancelled as _abbruch:
        run_abgebrochen(run_id, "Schwarm", session_id, grund=_abbruch)
    except Exception as e:
        fail("Schwarm fehlgeschlagen ⚠️", e)
        run_finish(run_id, "error", str(e))
        if session_id:
            post_to_session(session_id, "⚠️ Schwarm fehlgeschlagen: %s" % e)


def orchestrator_kontext(ziel, angeheftet=""):
    """Was der Orchestrator zusätzlich mitgibt: passendes Wissen (wie im Chat) und Angeheftetes aus dem Chat.
    (Text für den ersten Schritt, Namen des genutzten Wissens)."""
    teile, namen = [], []
    try:
        for name, text in chatkontext.wissen_finden(ziel, index=wissens_index()):
            namen.append(name)
            teile.append("### Aus dem Wissen: %s\n%s" % (name, text))
    except Exception as e:
        print("Orchestrator-Wissen übersprungen: %s" % e)
    if angeheftet.strip():
        teile.append(angeheftet.strip()[:16000])
    return ("\n\n".join(teile), list(dict.fromkeys(namen)))


def run_orchestrator(ziel, session_id=None, run_id=None, modell=None, angeheftet=""):
    """Plant aus dem Ziel einen Ablauf und führt ihn aus (Ergebnis in den Chat).

    Wählt dabei pro Schritt bewusst ein Modell aus allen verbundenen Providern —
    so entsteht eine echte Multi-Modell-Kette (z. B. Recherche mit einem schnellen
    Modell, Code mit einem Coder-Modell, Synthese mit dem stärksten)."""
    try:
        # Eigene Rolle fuer den Planer (26.09.2026): Das Hausmodell — ein 4B mit
        # Adapter — plante auf fremden Katalogen 79 % der Plaene regelkonform,
        # gemma4:12b 56 %. Planen und Ausfuehren sind verschiedene Aufgaben; die
        # Schritte laufen weiter mit den Modellen, die der Plan waehlt.
        model = modell or get_setting("ORCHESTRATOR_MODELL", "") or get_setting("DEFAULT_MODEL")
        run_step(run_id, "Orchestrator entwirft den Ablauf …", "active")
        verfuegbar = llm_list_models()   # weiter unten zum Aufloesen der gewaehlten Namen
        katalog = modell_katalog()
        modell_liste = katalog_text(katalog)
        # Der Plan ist der einzige Schritt, ohne den nichts weitergeht. Passt das
        # Standardmodell nicht in den Speicher, wird der Plan mit einem
        # kleineren Modell entworfen statt den ganzen Lauf abzubrechen
        # (Vorfall 17.09.2026: 27-B-Standardmodell, Metal-Speichernot, HTTP 500).
        roh, plan_modell = modell_mit_ausweich(
            model,
            lambda m: ollama_chat_once(m, [
                {"role": "system", "content": ORCHESTRATOR_PROMPT},
                {"role": "user", "content": "Verfügbare Modelle:\n%s\n\nZiel: %s"
                                            % (modell_liste, ziel)}], temperature=0.3,
                # Ohne Denkphase. Gemessen am 25.09.2026 mit gemma4:12b: mit
                # Denken ueber 10 Minuten je Plan und viele Zeitueberschreitungen,
                # ohne Denken 15 Sekunden bei 24 von 24 gueltigen Plaenen.
                no_think=True),
            run_id, "der Entwurf")
        if plan_modell != model:
            run_step(run_id, "Plan entworfen mit „%s“ (Ausweichmodell)" % model_kurz(plan_modell))
        plan = extract_json(roh) or {}
        schritte = []
        for s in (plan.get("schritte") or []):
            typ = s.get("typ", "agent")
            if typ not in STEP_RUNNERS:
                typ = "agent"
            schritt = {"type": typ, "instruction": s.get("anweisung", "")}
            gewaehlt = _modell_aufloesen(s.get("modell"), verfuegbar)
            if gewaehlt:
                schritt["model"] = gewaehlt
                schritt["model_grund"] = s.get("warum_modell", "")
            if typ == "agent" and s.get("rolle"):
                schritt["system_prompt"] = s["rolle"]
                schritt["label"] = (s.get("rolle") or "Diver")[:40]
            hinweis = modellwahl_pruefen(schritt, katalog)
            if hinweis:
                run_step(run_id, "Modellwahl korrigiert: " + hinweis)
            schritte.append(schritt)
        schritte = plan_bereinigen(schritte, ziel)
        # Folgt auf einen Code-Schritt noch ein Textschritt, muss der den Code ÜBERNEHMEN. 09.10.2026: „Programm + kurze
        # Erklärung“ endete mit der Erklärung allein — der letzte Schritt fasste den Code zusammen, statt ihn zu zeigen.
        if len(schritte) > 1 and schritte[-1].get("type") == "agent" and any(x.get("type") == "code" for x in schritte[:-1]):
            schritte[-1]["instruction"] = (schritte[-1].get("instruction") or "") + (
                "\n\nWICHTIG: Übernimm den vollständigen Code aus dem vorherigen Schritt unverändert als Codeblock in "
                "deine Antwort (mit Ausgabe, falls vorhanden) und ergänze erst danach deinen Text.")
        if not schritte:
            # Notfallplan: ein einzelner Agentenschritt löst das Ziel direkt.
            schritte = [{"type": "agent", "instruction": ziel,
                         "label": "Assistent"}]
        flow = " → ".join(
            "%s%s" % (STEP_META.get(x["type"], STEP_META["agent"])["label"],
                      (" [%s]" % model_kurz(x["model"])) if x.get("model") else "")
            for x in schritte)
        run_step(run_id, "Plan (%d Schritte): %s" % (len(schritte), flow))
        if session_id:
            post_to_session(session_id,
                            "**\U0001F3BC Orchestrator-Plan:** %s\n\n*Ablauf:* %s"
                            % (plan.get("plan", ziel), flow))
        # Über dieselbe Pipeline-Maschinerie ausführen — sie liefert Artefakt
        # und Chat-Antwort. Ein eingebauter Baustein bleibt ein eingebauter
        # Baustein; der Orchestrator setzt nur die Kette zusammen.
        pipeline = {"name": "Orchestrator: " + ziel[:50],
                    "steps": json.dumps(schritte, ensure_ascii=False)}
        # Wissen und Angeheftetes gehen mit dem Ziel in den ersten Schritt — der Planer selbst bleibt schlank.
        zusatz, wissen_namen = orchestrator_kontext(ziel, angeheftet)
        if wissen_namen:
            run_step(run_id, "📚 Wissen genutzt: %s" % ", ".join(wissen_namen))
        eingabe = ziel + ("\n\n---\nGrundlage (nutze sie, wo sie passt):\n\n" + zusatz if zusatz else "")
        run_pipeline(pipeline, eingabe, "", session_id, run_id)
    except RunCancelled as _abbruch:
        run_abgebrochen(run_id, "Orchestrator", session_id, grund=_abbruch)
    except Exception as e:
        fail("Orchestrator fehlgeschlagen ⚠️", e)
        run_finish(run_id, "error", str(e))
        if session_id:
            post_to_session(session_id, "⚠️ Orchestrator fehlgeschlagen: %s" % e)

# ----------------------------------------------------------------------------
# Deep-Research-Agent — Schleifen aus Websuche + Freiem KI-Modus
# ----------------------------------------------------------------------------

RESEARCH_CONTEXTS = [
    "Fakten, Daten & Definitionen",
    "Gegenpositionen, Risiken & Kritik",
    "Praxisbeispiele & Anwendungsfälle",
    "Aktuelle Entwicklungen & Trends",
    "Strategische Schlussfolgerungen",
]

def suchnotiz_text(query, ctx, src):
    """„Anfrage „x y“ → 2 Quellen“ — oder warum es keine gab.

    Ein Bericht ohne Web-Belege hat genau zwei mögliche Ursachen: eine
    untaugliche Anfrage oder eine blockierte Suche. Ohne diese Zeile lässt sich
    das nachträglich nicht unterscheiden (gemessen 18.09.2026: zwei Runden ohne
    eine einzige Quelle, Grund im Nachhinein nicht mehr feststellbar)."""
    # Die schlüssellose Suche hängt an DuckDuckGo; sperrt es, bleibt fast nichts.
    # Das muss im Bericht stehen, sonst sieht „keine Belege“ wie ein Befund aus.
    sperre = ""
    if time.time() < _ddg_gesperrt["bis"] and get_setting("SEARCH_BACKEND", "auto") not in _SEARCH_BACKENDS:
        sperre = (" · DuckDuckGo verlangt gerade eine Bot-Prüfung — nur Ausweichquellen. Verlässlich wird "
                  "die Recherche mit einem eigenen Suchdienst (Einstellungen → Web-Recherche)")
    if not ctx:
        grund = src if isinstance(src, str) and src else "keine Treffer"
        return "Anfrage „%s“ → nichts (%s)%s" % (query[:60], grund, sperre)
    anzahl = len(src) if isinstance(src, list) else 0
    return "Anfrage „%s“ → %d Quelle%s%s" % (query[:60], anzahl, "" if anzahl == 1 else "n", sperre)


def deep_research_core(topic, loops, model, use_web=True, run_id=None,
                       quellen_sammler=None):
    """Führt die Recherche-Schleifen aus und gibt den fertigen Bericht zurück."""
    model = model or get_setting("DEFAULT_MODEL")
    loops = max(1, min(int(loops or 3), 5))
    memory = []   # Synthesen aller Runden
    sources = []
    try:
        # Phase 0: Rechercheplan
        plan, model = modell_mit_ausweich(
            model,
            lambda m: ollama_chat_once(m, [
                {"role": "system", "content":
                 "Du bist ein Deep-Research-Agent. Zerlege das Thema in die %d wichtigsten "
                 "Teilfragen. Gib NUR die Fragen als nummerierte Liste aus." % loops},
                {"role": "user", "content": topic}], temperature=0.5, no_think=True),
            run_id, "die Recherche")
        if run_id:
            run_step(run_id, "Recherche-Modell: %s" % model_kurz(model))
        questions = [re.sub(r"^\s*\d+[.)]\s*", "", l).strip()
                     for l in plan.splitlines() if re.match(r"^\s*\d+[.)]", l)] or [topic]
        for i in range(loops):
            question = questions[i % len(questions)]
            context = RESEARCH_CONTEXTS[i % len(RESEARCH_CONTEXTS)]
            gaps = memory[-1]["gaps"] if memory else ""
            # Jede Runde besteht aus vier Modellaufrufen und einer Suche. Wer
            # wissen will, warum eine Runde acht Minuten dauert, braucht die
            # Einzelzeiten — sonst bleibt nur Rätselraten (Vorfall 17.09.2026).
            t_runde = time.time()
            zeiten, suchnotiz = [], ""
            # -- Quelle 1: Web-Recherche (WebBridge) --
            web_summary = "(Web-Recherche deaktiviert oder nicht verfügbar)"
            if use_web:
                t0 = time.time()
                query = ollama_chat_once(model, [
                    {"role": "system", "content":
                     "Formuliere EINE knappe Websuchanfrage aus 2 bis 4 "
                     "Schluesselwoertern. WICHTIG: Die Begriffe des HAUPTTHEMAS "
                     "muessen enthalten bleiben — erfinde keine Randbegriffe, die "
                     "das Thema verlassen. Keine Saetze, keine Satzzeichen, keine "
                     "Anfuehrungszeichen, nur die Suchbegriffe."},
                    {"role": "user", "content":
                     "Hauptthema (muss erhalten bleiben): %s\nTeilfrage: %s"
                     % (topic, question)}], temperature=0.2,
                    no_think=True).strip().strip('"')
                zeiten.append(("Anfrage", time.time() - t0))
                # Sicherheitsnetz: verliert die Anfrage das Thema, Thema anhaengen
                kern = [w.lower() for w in re.findall(r"[A-Za-zÄÖÜäöüß]{4,}", topic)
                        if w.lower() not in _SUCH_STOP][:2]
                if kern and not any(k[:5] in query.lower() for k in kern):
                    query = " ".join(kern) + " " + " ".join(query.split()[:3])
                t0 = time.time()
                ctx, src = suche_mit_varianten(query, model)
                zeiten.append(("Suche", time.time() - t0))
                # Die Anfrage und die Zahl der Quellen gehören ins Protokoll:
                # Ein Bericht ohne Web-Belege hat genau zwei mögliche Ursachen —
                # eine untaugliche Anfrage oder eine blockierte Suche — und ohne
                # diese Zeile kann man sie nicht unterscheiden (Vorfall
                # 18.09.2026: zwei Runden ohne eine einzige Quelle, Grund
                # nachträglich nicht mehr feststellbar).
                suchnotiz = suchnotiz_text(query, ctx, src)
                if ctx:
                    sources += src if isinstance(src, list) else []
                    t0 = time.time()
                    web_summary = ollama_chat_once(model, [
                        {"role": "system", "content":
                         "Extrahiere aus den Rechercheergebnissen die relevanten Fakten "
                         "zur Teilfrage. Kompakte Stichpunkte mit Quellen-URLs."},
                        {"role": "user", "content":
                         "Teilfrage: %s\nPerspektive: %s\n\n%s"
                         % (question, context, ctx)}], temperature=0.3, no_think=True)
                    zeiten.append(("Auszug", time.time() - t0))
                else:
                    web_summary = "(Web nicht erreichbar: %s)" % src
            # -- Quelle 2: Freier KI-Modus (modell-internes Wissen, ohne Web) --
            t0 = time.time()
            free_ki = ollama_chat_once(model, [
                {"role": "system", "content":
                 "HYPOTHESEN-MODUS (ohne Web). Du lieferst KEINE Fakten, sondern nur "
                 "Denkanstoesse: welche Aspekte koennte man pruefen, welche Fragen sind "
                 "offen, worauf sollte man achten. Nenne NIEMALS konkrete Zahlen, "
                 "Jahreszahlen, Eigennamen, Orte oder Ereignisse so, als waeren sie "
                 "belegt — wenn du etwas vermutest, schreibe ausdruecklich "
                 "'(unbelegte Vermutung)' davor. Erfinde nichts."},
                {"role": "user", "content": "Thema: %s\nTeilfrage: %s\nPerspektive: %s"
                 % (topic, question, context)}], temperature=0.3, no_think=True)
            zeiten.append(("Hypothesen", time.time() - t0))
            # -- Synthese der Runde + neue Lücken --
            t0 = time.time()
            synthesis = ollama_chat_once(model, [
                {"role": "system", "content":
                 "Fasse zusammen und TRENNE dabei strikt:\n"
                 "1. 'Belegt (Web):' NUR was in den Web-Erkenntnissen steht — mit "
                 "Quellen-URL. Standen dort keine Treffer, schreibe hier ausdruecklich "
                 "'Keine Web-Belege gefunden.'\n"
                 "2. 'Unbelegte Vermutungen:' alles aus dem Hypothesen-Teil, klar als "
                 "ungeprueft gekennzeichnet.\n"
                 "Uebernimm NIEMALS eine Vermutung in den belegten Teil und erfinde "
                 "keine Fakten, Zahlen oder Namen. Schliesse mit einer Zeile "
                 "'LUECKEN: …' (offene Fragen fuer die naechste Runde)."},
                {"role": "user", "content":
                 "Teilfrage: %s\n\n== WEB-ERKENNTNISSE ==\n%s\n\n== HYPOTHESEN (unbelegt) ==\n%s"
                 % (question, web_summary, free_ki)}], temperature=0.3, no_think=True)
            zeiten.append(("Synthese", time.time() - t0))
            gap_match = re.search(r"L[ÜU]CKEN:\s*(.+)", synthesis, re.S)
            memory.append({"question": question, "context": context,
                           "synthesis": synthesis,
                           "gaps": gap_match.group(1).strip()[:500] if gap_match else ""})
            emit("Deep Research: Runde %d/%d fertig 🔭" % (i + 1, loops),
                 "Teilfrage: %s (Perspektive: %s)" % (question[:90], context),
                 "skill", read=1)
            aufschlag = ", ".join("%s %.0fs" % (n, d) for n, d in zeiten)
            run_step(run_id, "Runde %d/%d in %.0f s — %s  (%s)%s"
                     % (i + 1, loops, time.time() - t_runde, question[:60], aufschlag,
                        " · " + suchnotiz if use_web and suchnotiz else ""))
        # Finale: Gesamtbericht
        all_syntheses = "\n\n---\n\n".join(
            "## Runde %d: %s (%s)\n%s" % (i + 1, m["question"], m["context"],
                                          m["synthesis"][:3000])
            for i, m in enumerate(memory))[:15000]
        quellen_liste = "\n".join(sorted(set(sources)))
        if use_web and not sources:
            # Ehrlich und kurz statt seitenweise Spekulation: Wenn die Recherche
            # nichts gefunden hat, ist ein langer "Bericht" aus Vermutungen
            # irrefuehrend — und kostet nur Zeit.
            offen = "\n".join("- %s" % m.get("gaps", "").strip()[:200]
                               for m in memory if m.get("gaps"))
            return ("## Keine Web-Belege gefunden\n\n"
                    "Zum Thema **%s** liefert die Live-Recherche nichts Verwertbares. "
                    "Deshalb gibt es hier bewusst KEINEN ausformulierten Bericht — "
                    "alles Weitere waere geraten.\n\n"
                    "**Moegliche Gruende:** Schreibweise/Tippfehler in der Anfrage, ein "
                    "sehr spezielles Thema, oder die schluessellose Suche stoesst an "
                    "ihre Grenzen (dann hilft ein Such-Backend unter Einstellungen "
                    "-> Web-Recherche).\n\n"
                    "**Offene Fragen aus den Runden:**\n%s\n"
                    % (topic[:120], offen or "- (keine)"))
        warnung = ("" if quellen_liste else
                   "\n\n> WICHTIG: In diesem Lauf konnten KEINE Web-Quellen gefunden "
                   "werden. Der Bericht MUSS das ganz oben deutlich sagen und darf "
                   "keine Fakten, Zahlen, Jahreszahlen oder Eigennamen als gesichert "
                   "darstellen.")
        report = ollama_chat_once(model, [
            {"role": "system", "content":
             "Erstelle aus allen Forschungsrunden einen Deep-Research-Bericht in "
             "Markdown: Executive Summary, Erkenntnisse je Teilfrage, "
             "Widersprueche & offene Fragen, Fazit & Empfehlungen, Quellen.\n"
             "REGELN GEGEN ERFUNDENE INHALTE:\n"
             "- Uebernimm NUR Aussagen, die in den Runden als 'Belegt (Web)' "
             "gekennzeichnet sind, als Fakten — mit Quellenangabe.\n"
             "- Alles andere kommt in einen eigenen Abschnitt "
             "'Unbelegte Vermutungen (nicht geprueft)'.\n"
             "- Erfinde NIEMALS Zahlen, Jahreszahlen, Eigennamen, Orte oder "
             "Ereignisse. Lieber schreiben, dass etwas unbekannt ist.\n"
             "- Schreibe KEINEN Abschnitt 'Quellen' — die Quellenliste wird "
             "automatisch angehaengt. Erwaehne URLs nur dort, wo du eine Aussage "
             "direkt belegst."
             + warnung},
            {"role": "user", "content": "Thema: %s\n\n%s\n\nGESAMMELTE QUELLEN:\n%s"
             % (topic, all_syntheses, quellen_liste or "(keine)")}], temperature=0.3)
        if not quellen_liste:
            report = ("> ⚠️ **Ohne Web-Belege erstellt** — die Live-Recherche lieferte "
                      "zu diesem Thema keine verwertbaren Quellen. Alle Aussagen unten "
                      "sind ungeprueft.\n\n") + report
        # Quellen deterministisch anhaengen: eine URL ist ein Fakt, den das Modell
        # nicht abtippen (und dabei verstuemmeln) soll.
        if quellen_liste:
            report += ("\n\n---\n\n## Tatsaechlich genutzte Quellen\n\n"
                       + "\n".join("- <%s>" % u for u in sorted(set(sources))))
        else:
            report += ("\n\n---\n\n## Tatsaechlich genutzte Quellen\n\n"
                       "_Keine — die Live-Suche fand zu diesem Thema nichts "
                       "thematisch Passendes._")
        if quellen_sammler is not None:
            quellen_sammler.extend(sorted(set(sources)))
        return report
    except Exception:
        raise   # der aufrufende Lauf meldet den Fehler in Inbox und Chat

def run_deep_research(topic, loops, model, use_web=True, session_id=None, run_id=None):
    """Hintergrundlauf: recherchiert, legt den Bericht ab und meldet ihn."""
    try:
        report = deep_research_core(topic, loops, model, use_web, run_id)
        art_id = deliver("Deep Research: %s" % topic[:60],
                         "# Deep Research: %s\n\n%s\n" % (topic, report),
                         "deep-research.md", "Deep Research abgeschlossen ✅",
                         "%d Runden (Web-Recherche + Hypothesen) durchlaufen. Der Bericht "
                         "liegt unter Dokumente & Artefakte." % loops)
        if session_id:
            post_to_session(session_id,
                            "**🔭 Deep Research abgeschlossen** — Thema: „%s“\n\n%s"
                            % (topic, report))
        run_finish(run_id, "done", report, art_id)
    except RunCancelled as _abbruch:
        run_abgebrochen(run_id, "Deep Research", session_id, grund=_abbruch)
    except Exception as e:
        fail("Deep Research fehlgeschlagen ⚠️", e)
        run_finish(run_id, "error", str(e))
        if session_id:
            post_to_session(session_id, "⚠️ Deep Research fehlgeschlagen: %s" % e)

# ----------------------------------------------------------------------------
# Code-Sandbox — echte Arbeitsumgebung für den Coding-Agenten
#
# Jeder Workspace ist ein isolierter Ordner unter storage/workspaces/<name>/.
# Ausführung passiert per Subprozess mit Timeout und Arbeitsverzeichnis-Bindung.
# Optional (falls Docker vorhanden UND aktiviert) läuft alles im Container —
# damit ist echte Prozess-Isolation möglich, ohne dass Docker Pflicht wäre.
# ----------------------------------------------------------------------------

WORKSPACES_DIR = os.path.join(STORAGE_DIR, "workspaces")
os.makedirs(WORKSPACES_DIR, exist_ok=True)

RUNTIMES = {
    "python": {"ext": ".py", "cmd": ["python3"], "label": "Python 3"},
    "node":   {"ext": ".js", "cmd": ["node"],    "label": "Node.js"},
    "bash":   {"ext": ".sh", "cmd": ["bash"],    "label": "Bash"},
}

def ws_path(name):
    """Workspace-Pfad; schützt vor Ausbrüchen aus dem Storage-Ordner."""
    safe = re.sub(r"[^\w.-]+", "-", name or "").strip("-.") or "default"
    if len(safe.encode("utf-8")) > 100:
        raise ValueError("Workspace-Name zu lang (höchstens 100 Zeichen).")
    path = os.path.abspath(os.path.join(WORKSPACES_DIR, safe))
    if not path.startswith(os.path.abspath(WORKSPACES_DIR)):
        raise ValueError("Ungültiger Workspace-Name.")
    os.makedirs(path, exist_ok=True)
    return path

def ws_file(workspace, filename):
    """Dateipfad innerhalb eines Workspace; verhindert Pfad-Traversal."""
    base = ws_path(workspace)
    clean = re.sub(r"\.\.+", ".", filename or "").lstrip("/")
    if len(clean.encode("utf-8")) > 1000 or any(len(t.encode("utf-8")) > 255 for t in clean.split("/")):
        raise ValueError("Dateiname zu lang.")
    path = os.path.abspath(os.path.join(base, clean))
    if not path.startswith(base):
        raise ValueError("Dateipfad liegt außerhalb des Workspace.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path

def ws_list_files(workspace):
    base = ws_path(workspace)
    out = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__", "node_modules")]
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            out.append({"name": os.path.relpath(fp, base),
                        "size": os.path.getsize(fp),
                        "modified": os.path.getmtime(fp)})
    return out

def ws_venv_programm(workspace, name):
    """Programm aus dem venv des Workspace: Windows legt es unter Scripts\\*.exe ab."""
    if os.name == "nt":
        return os.path.join(ws_path(workspace), ".venv", "Scripts", name + ".exe")
    return os.path.join(ws_path(workspace), ".venv", "bin", name)

def ws_python(workspace):
    """Python aus dem venv des Workspace, falls vorhanden — sonst das Python, in dem
    Dive on Wide selbst läuft. Nicht „python3“: Das gibt es auf Windows nicht, oder es ist
    der Platzhalter, der nur den Microsoft Store anbietet (Windows-VM, 29.09.2026)."""
    venv_py = ws_venv_programm(workspace, "python")
    return venv_py if os.path.exists(venv_py) else (sys.executable or "python3")

def docker_available():
    import shutil
    if not shutil.which("docker"):
        return False
    try:
        import subprocess
        return subprocess.run(["docker", "info"], capture_output=True,
                              timeout=5).returncode == 0
    except Exception:
        return False

def sandbox_mit_docker(use_docker=None):
    if use_docker is None:
        use_docker = get_setting("SANDBOX_DOCKER", "0") == "1"
    return bool(use_docker) and docker_available()

def run_in_workspace(workspace, command, timeout=60, use_docker=None):
    """Führt einen Befehl im Workspace aus. Gibt (exit_code, stdout, stderr) zurück.

    `command` ist eine Argumentliste (bevorzugt, kein Shell-Quoting nötig) oder ein
    Shell-Text (Konsole der Code-Sandbox). Shell ist sh auf macOS/Linux, cmd.exe auf
    Windows — dort gibt es kein sh (Windows-VM, 29.09.2026)."""
    import subprocess
    import shlex
    base = ws_path(workspace)
    if sandbox_mit_docker(use_docker):
        text = command if isinstance(command, str) else " ".join(shlex.quote(a) for a in command)
        cmd = ["docker", "run", "--rm", "--network", "none",
               "--memory", "512m", "--cpus", "1",
               "-v", "%s:/work" % base, "-w", "/work",
               get_setting("SANDBOX_IMAGE", "python:3.11-slim"),
               "sh", "-c", text]
    elif not isinstance(command, str):
        cmd = list(command)
    elif os.name == "nt":
        cmd = werkbank.windows_befehl(command)      # als Text, siehe dort
    else:
        cmd = ["sh", "-c", command]
    try:
        # Kinder schreiben UTF-8 (sonst Windows-1252 bzw. die Konsolen-Codepage) —
        # gelesen wird UTF-8, Unlesbares ersetzt statt Absturz.
        p = subprocess.run(cmd, cwd=base, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout,
                           env={**os.environ, "PYTHONUNBUFFERED": "1",
                                "PYTHONDONTWRITEBYTECODE": "1",
                                "PYTHONIOENCODING": "utf-8"})
        return p.returncode, p.stdout[-20000:], p.stderr[-20000:]
    except subprocess.TimeoutExpired:
        return -1, "", "⏱ Zeitlimit von %ds überschritten — Ausführung abgebrochen." % timeout
    except Exception as e:
        return -1, "", "Ausführung fehlgeschlagen: %s" % e

def run_code(workspace, code, runtime="python", filename=None, timeout=60):
    """Schreibt Code in den Workspace und führt ihn aus."""
    rt = RUNTIMES.get(runtime, RUNTIMES["python"])
    fname = filename or ("main" + rt["ext"])
    path = ws_file(workspace, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    docker = sandbox_mit_docker()
    if runtime == "python":
        interpreter = "python3" if docker else ws_python(workspace)
    else:
        interpreter = rt["cmd"][0]
        if not docker and not shutil.which(interpreter):
            return {"exit_code": -1, "stdout": "", "file": fname,
                    "stderr": "„%s“ ist auf diesem Rechner nicht installiert — %s-Code lässt sich hier nicht ausführen."
                              % (interpreter, rt["label"])}
    rel = os.path.relpath(path, ws_path(workspace))
    code_rc, out, err = run_in_workspace(workspace, [interpreter, rel], timeout)
    return {"exit_code": code_rc, "stdout": out, "stderr": err, "file": rel}

def shlex_quote(s):
    import shlex
    return shlex.quote(s)

def ws_setup_venv(workspace, packages=""):
    """Erstellt ein venv im Workspace und installiert optional Pakete."""
    base = ws_path(workspace)
    steps = []
    docker = sandbox_mit_docker()
    if not os.path.exists(os.path.join(base, ".venv")):
        rc, out, err = run_in_workspace(
            workspace, ["python3" if docker else (sys.executable or "python3"), "-m", "venv", ".venv"], timeout=180)
        steps.append(("venv erstellen", rc, out, err))
    if packages.strip():
        pip = ".venv/bin/pip" if docker else ws_venv_programm(workspace, "pip")
        rc, out, err = run_in_workspace(
            workspace, [pip, "install", "--disable-pip-version-check"] + [p for p in packages.split() if p],
            timeout=600)
        steps.append(("Pakete installieren", rc, out, err))
    return steps

# ----------------------------------------------------------------------------
# Coding-Agent — schreibt Code, führt ihn aus, liest Fehler, korrigiert sich
# ----------------------------------------------------------------------------

CODER_PROMPT = (
    "Du bist der Coding-Agent von Dive on Wide und arbeitest in einer lokalen Sandbox. "
    "Schreibe vollständigen, lauffähigen Code für die Aufgabe. Nutze nur die "
    "Standardbibliothek, außer der Nutzer nennt ausdrücklich Pakete. Das Programm "
    "muss beim Ausführen sichtbare Ausgabe erzeugen. "
    "Antworte AUSSCHLIESSLICH mit gültigem JSON: "
    '{"filename":"main.py","runtime":"python","code":"…","erklaerung":"1-2 Sätze"} '
    "— runtime ist python, node oder bash.")

CODER_FIX_PROMPT = (
    "Du bist der Coding-Agent von Dive on Wide. Dein Code wurde ausgeführt und ist "
    "fehlgeschlagen. Analysiere die Fehlermeldung und liefere eine KORRIGIERTE "
    "Fassung des vollständigen Programms. Antworte AUSSCHLIESSLICH mit gültigem "
    'JSON: {"filename":"…","runtime":"…","code":"…","erklaerung":"was war der Fehler '
    'und wie wurde er behoben"}')

def coding_agent_core(task, workspace, model, max_iter=4, packages="", run_id=None):
    """Selbstheilende Schleife. Gibt (Protokoll, erfolgreich) zurück."""
    if run_id in GAST_LAEUFE:
        return "# Coding-Agent\n\n" + GAST_VERBOT, False
    model = model or get_setting("DEFAULT_MODEL")
    max_iter = max(1, min(int(max_iter or 4), 8))
    log = ["# Coding-Agent\n\n**Aufgabe:** %s\n\n**Workspace:** `%s`\n"
           % (task, workspace)]
    if True:
        if packages.strip():
            log.append("\n## Umgebung vorbereiten\n")
            for label, rc, out, err in ws_setup_venv(workspace, packages):
                log.append("- %s: %s\n" % (label, "✅ ok" if rc == 0 else "⚠️ " + err[:300]))
        history_msg = task
        prompt = CODER_PROMPT
        last = None
        for i in range(max_iter):
            raw = ollama_chat_once(model, [
                {"role": "system", "content": prompt},
                {"role": "user", "content": history_msg}], temperature=0.2)
            try:
                spec = extract_json(raw)
            except Exception:
                # Fallback: Codeblock aus Freitext ziehen
                m = re.search(r"```(\w*)\n([\s\S]*?)```", raw)
                if not m:
                    raise RuntimeError("Modell lieferte keinen verwertbaren Code.")
                spec = {"filename": "main.py", "runtime": m.group(1) or "python",
                        "code": m.group(2), "erklaerung": ""}
            result = run_code(workspace, spec.get("code", ""),
                              spec.get("runtime", "python"), spec.get("filename"))
            last = (spec, result)
            ok = result["exit_code"] == 0
            log.append(
                "\n## Iteration %d — %s\n\n%s\n\n```%s\n%s\n```\n\n**Ausgabe:**\n```\n%s\n```\n"
                % (i + 1, "✅ erfolgreich" if ok else "⚠️ fehlgeschlagen",
                   spec.get("erklaerung", ""), spec.get("runtime", "python"),
                   spec.get("code", "")[:6000],
                   ((result["stdout"] or "") + (result["stderr"] or ""))[:3000] or "(keine)"))
            emit("Coding-Diver: Iteration %d %s" % (i + 1, "✅" if ok else "🔁"),
                 "Aufgabe: %s" % task[:90], "skill", read=1)
            run_step(run_id, "Iteration %d/%d — %s" % (i + 1, max_iter,
                     "läuft fehlerfrei" if ok else "Fehler, korrigiere …"),
                     "done" if ok else "active")
            if ok:
                break
            prompt = CODER_FIX_PROMPT
            history_msg = ("Aufgabe: %s\n\nBisheriger Code (%s):\n```\n%s\n```\n\n"
                           "FEHLERAUSGABE:\n%s\n%s"
                           % (task, spec.get("filename"), spec.get("code", "")[:6000],
                              result["stderr"][:3000], result["stdout"][:1000]))
        spec, result = last
        ok = result["exit_code"] == 0
        log.append("\n---\n\n## Ergebnis\n\n%s Datei `%s` im Workspace `%s`.\n"
                   % ("✅ Läuft fehlerfrei." if ok else
                      "⚠️ Läuft nach %d Versuchen noch nicht fehlerfrei." % max_iter,
                      result["file"], workspace))
        return "".join(log), ok

def run_coding_agent(task, workspace, model, max_iter=4, packages="",
                     session_id=None, run_id=None):
    """Hintergrundlauf des Coding-Agenten inkl. Ablage und Rückmeldung."""
    try:
        log, ok = coding_agent_core(task, workspace, model, max_iter, packages, run_id)
        art_id = deliver("Coding-Diver: %s" % task[:60], log, "coding-agent.md",
                         "Coding-Diver %s" % ("abgeschlossen ✅" if ok else "beendet ⚠️"),
                         "Aufgabe „%s“ — Code liegt im Workspace „%s“, Protokoll unter "
                         "Dokumente & Artefakte." % (task[:70], workspace))
        if session_id:
            post_to_session(session_id, "**🧰 Coding-Agent %s** — Workspace `%s`\n\n%s"
                            % ("fertig" if ok else "beendet (mit Rest-Fehlern)",
                               workspace, log))
        run_finish(run_id, "done" if ok else "warn", log, art_id)
    except RunCancelled as _abbruch:
        run_abgebrochen(run_id, "Coding-Diver", session_id, grund=_abbruch)
    except Exception as e:
        fail("Coding-Diver fehlgeschlagen ⚠️", e)
        run_finish(run_id, "error", str(e))
        if session_id:
            post_to_session(session_id, "⚠️ Coding-Agent fehlgeschlagen: %s" % e)

# ----------------------------------------------------------------------------
# Werkbank-Agent — selbstständig in echten Projekten (werkbank.py)
# ----------------------------------------------------------------------------
# Der Coding-Agent oben schreibt EINE Datei als JSON. Die Werkbank arbeitet
# wie Claude Code: liest, sucht, ändert gezielt, führt Tests aus — mit
# Rechtestufen, Sandbox und Freigabe über dieselbe Schranke wie Computer-Use.

import werkbank
# Was Befehle des Agenten in der Sandbox nicht lesen duerfen: die Schluessel
# von Dive on Wide selbst (Zugaenge, API-Schluessel, MCP-Umgebungen). Als Funktionen,
# weil Pfade zur Laufzeit wechseln koennen (Tests, Volldurchlauf).
werkbank.ZUSAETZLICH_GESPERRT += [
    lambda: DB_PATH, lambda: DB_PATH + "-wal", lambda: DB_PATH + "-shm",
    lambda: os.path.join(BASE_DIR, ".env"), lambda: os.path.join(STORAGE_DIR, "mcp.json")]
import ausgang
import konsolidierung
import checkpunkte
import regeln as werkbank_regeln
import agentskills
import befehle as werkbank_befehle
import agentenprofile
import externe_agenten
import schrittprotokoll
import webhooks
import gedaechtnis as werkbank_gedaechtnis

WERKBANK_DIR = os.path.join(STORAGE_DIR, "werkbank")
CHECKPUNKTE_DIR = os.path.join(WERKBANK_DIR, "checkpunkte")
SKILLS_DIR = os.path.join(WERKBANK_DIR, "skills")
BEFEHLE_DIR = os.path.join(WERKBANK_DIR, "befehle")
AGENTEN_DIR = os.path.join(WERKBANK_DIR, "agenten")
_werkbank_aktiv = {}          # Projektordner -> run_id, solange ein Lauf dort arbeitet
_werkbank_start = {}          # run_id -> (Projektordner, Checkpunkt vor dem Lauf) — für den Live-Diff
_werkbank_lock = threading.Lock()

def werkbank_ohne_sandbox_hinweis(system=None):
    """Was fehlt und was hilft — für DIESES System, nicht für ein anderes."""
    system = system or platform.system()
    if system == "Windows":
        return ("Windows hat keine eingebaute Sandbox für die Befehle des Agenten. Deshalb musst du "
                "jeden Befehl einzeln freigeben. Mehr Schutz: Dive on Wide in WSL2 betreiben "
                "(docs/VM_BETRIEB.md).")
    if system == "Linux":
        return ("Auf diesem Rechner gibt es keine Sandbox (Paket „bubblewrap“ installieren, z. B. "
                "sudo apt install bubblewrap). Bis dahin musst du jeden Befehl des Agenten einzeln freigeben.")
    return ("Auf diesem Rechner gibt es keine Sandbox. Deshalb musst du jeden Befehl des Agenten "
            "einzeln freigeben.")

def werkbank_lage():
    """Was die Werkbank auf diesem Rechner kann — für die Oberfläche."""
    art = werkbank.sandbox_art()
    return {
        "stufen": werkbank.STUFEN, "freigaben": werkbank.FREIGABEN,
        "standard": {"stufe": werkbank.STANDARD_STUFE, "freigabe": werkbank.STANDARD_FREIGABE,
                     "max_schritte": werkbank.MAX_SCHRITTE},
        "sandbox": art,
        "hinweis": ("Befehle laufen in %s: ohne Netz, Schreiben nur im Projekt." % art if art else
                    werkbank_ohne_sandbox_hinweis()),
        "anweisungsdateien": list(werkbank.ANWEISUNGSDATEIEN),
        "modell": get_setting("WERKBANK_MODELL", "") or get_setting("DEFAULT_MODEL"),
        "aktiv": get_setting("SANDBOX_ENABLED", "0") == "1",
    }

_SYSTEMORDNER = ("/", "/bin", "/sbin", "/usr", "/etc", "/var", "/System", "/Library",
                 "/private", "/dev", "/opt", "C:\\", "C:\\Windows", "C:\\Program Files")

def werkbank_ordner(body):
    """Projektordner aus der Anfrage: ein Workspace oder ein eigener Ordner.

    Ein eigener Ordner muss ausdrücklich angegeben werden, existieren und darf
    weder ein Systemordner noch das ganze Home sein — ein Agent mit Schreibrecht
    auf ~ könnte alles anfassen, was dem Nutzer gehört."""
    eigener = str(body.get("ordner") or "").strip()
    if not eigener:
        return ws_path(str(body.get("workspace") or "default"))
    pfad = os.path.realpath(os.path.expanduser(eigener))
    if not os.path.isabs(os.path.expanduser(eigener)):
        raise ValueError("Bitte den vollständigen Pfad des Projektordners angeben.")
    if not os.path.isdir(pfad):
        raise ValueError("Den Ordner „%s“ gibt es nicht." % eigener)
    verboten = {os.path.realpath(p) for p in _SYSTEMORDNER if os.path.isabs(p)}
    verboten.add(os.path.realpath(os.path.expanduser("~")))
    if pfad in verboten:
        raise ValueError("„%s“ ist kein Projektordner — bitte einen Unterordner wählen." % eigener)
    return pfad

def werkbank_spur(lauf_id):
    """Die gespeicherte Trajektorie eines Laufs (für Fortsetzen und Diff)."""
    if not re.match(r"^[\w-]+$", lauf_id or ""):
        raise ValueError("Ungültige Lauf-Kennung.")
    try:
        with open(os.path.join(WERKBANK_DIR, lauf_id + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        raise LookupError("Diesen Werkbank-Lauf gibt es nicht.")

_webhook_zustellungen = webhooks.Zustellungen(pfad=os.path.join(STORAGE_DIR, "webhook_zustellungen.json"))
_webhook_lock = threading.Lock()

def webhooks_laden():
    try:
        liste = json.loads(get_setting("WERKBANK_WEBHOOKS", "[]") or "[]")
        return liste if isinstance(liste, list) else []
    except ValueError:
        return []

def webhooks_speichern(liste):
    set_setting("WERKBANK_WEBHOOKS", json.dumps(liste, ensure_ascii=False))

def webhook_anlegen(daten):
    hook = webhooks.neu(daten)
    werkbank_ordner({"ordner": hook["projekt"]} if os.path.isabs(os.path.expanduser(hook["projekt"])) else {"workspace": hook["projekt"]})
    if hook["profil"] and hook["profil"] not in agentenprofile.finden(None, AGENTEN_DIR):
        raise ValueError("Profil „%s“ gibt es nicht." % hook["profil"])
    if hook["zustellen"] not in RHYTHMUS_ZUSTELLUNG:
        raise ValueError("Unbekannte Zustellung.")
    with _webhook_lock:
        webhooks_speichern(webhooks_laden() + [hook])
    return hook                                   # das Geheimnis nur dieses eine Mal

def webhook_aendern(kennung, aktion):
    with _webhook_lock:
        liste = webhooks_laden()
        hook = next((h for h in liste if h["id"] == kennung), None)
        if not hook:
            raise LookupError("Diesen Webhook gibt es nicht.")
        if aktion == "loeschen":
            liste = [h for h in liste if h["id"] != kennung]
        else:
            hook["aktiv"] = not hook.get("aktiv", True)
        webhooks_speichern(liste)
    return [webhooks.oeffentlich(h) for h in liste]

def webhook_ausloesen(kennung, rumpf, kopfzeilen):
    """(HTTP-Status, Antwort). Ohne gültige Signatur erfährt der Absender nichts über den Webhook."""
    hook = next((h for h in webhooks_laden() if h["id"] == kennung), None)
    signatur = kopfzeilen.get("X-Dowos-Signature") or kopfzeilen.get("X-Hub-Signature-256")
    if not hook or not webhooks.signatur_pruefen(hook["geheimnis"], rumpf, signatur):
        return 401, {"error": "Signatur fehlt oder ist ungültig."}
    if not hook.get("aktiv", True):
        return 409, {"error": "Webhook ist ausgeschaltet."}
    ereignis = kopfzeilen.get("X-GitHub-Event") or kopfzeilen.get("X-Gitea-Event") or ""
    if ereignis == "ping":
        return 200, {"ok": True, "pong": True}
    if hook.get("ereignisse") and ereignis not in hook["ereignisse"]:
        return 202, {"ok": True, "ignoriert": "Ereignis „%s“ ist für diesen Webhook nicht eingetragen." % ereignis}
    if not _webhook_zustellungen.neu(kopfzeilen.get("X-GitHub-Delivery") or kopfzeilen.get("X-Dowos-Delivery"),
                                     signatur):
        return 200, {"ok": True, "doppelt": True,
                     "grund": "Diese Anfrage wurde schon zugestellt (gleiche Kennung oder gleicher "
                              "signierter Rumpf in den letzten 24 Stunden). Wer bewusst dasselbe noch "
                              "einmal ausloesen will, nimmt ein wechselndes Feld in den Rumpf auf."}
    if get_setting("SANDBOX_ENABLED", "0") != "1":
        return 403, {"error": "Code-Ausführung ist in Dive on Wide abgeschaltet."}
    try:
        daten = json.loads(rumpf.decode("utf-8") or "{}")
        auftrag = webhooks.auftrag_bauen(hook["vorlage"], daten if isinstance(daten, dict) else {})
        projekt = hook["projekt"]
        ordner = werkbank_ordner({"ordner": projekt} if os.path.isabs(os.path.expanduser(projekt)) else {"workspace": projekt})
    except ValueError as e:
        return 400, {"error": str(e)}
    profil = agentenprofile.finden(ordner, AGENTEN_DIR).get(hook["profil"]) if hook.get("profil") else None
    stufe, politik = agentenprofile.anwenden(profil, hook["stufe"], "nie")
    run_id = run_begin("code", "Webhook „%s“: %s" % (hook["name"], auftrag[:50]))

    def lauf():
        run_werkbank(auftrag, ordner, "", stufe, politik, werkbank.MAX_SCHRITTE, None, run_id,
                     unbeaufsichtigt=True, profil=profil, quelle="webhook")
        if hook.get("zustellen"):
            rhythmus_zustellen({"name": "Webhook „%s“" % hook["name"], "zustellen": hook["zustellen"]}, run_id)
    background(lauf)
    with _webhook_lock:
        liste = webhooks_laden()
        for h in liste:
            if h["id"] == kennung:
                h["letzter_aufruf"], h["letzter_lauf"] = now(), run_id
        webhooks_speichern(liste)
    return 202, {"ok": True, "run_id": run_id}

def werkbank_abzweig_vorbereiten(abzweig):
    """(Nachrichten bis Schritt N, Abzweig-Angabe, Meta des Ursprungslaufs)."""
    if not isinstance(abzweig, dict):
        raise ValueError("abzweigen erwartet {lauf, schritt, zuruecksetzen}.")
    lauf = str(abzweig.get("lauf") or "")
    p = schrittprotokoll.lesen(WERKBANK_DIR, lauf)
    nachrichten = schrittprotokoll.nachrichten_bis(p, abzweig.get("schritt"))
    schritt = int(abzweig.get("schritt"))
    return nachrichten, {"lauf": lauf, "schritt": schritt, "zuruecksetzen": bool(abzweig.get("zuruecksetzen")),
                         "checkpunkt": schrittprotokoll.checkpunkt_bis(p, schritt)}, p["meta"]

def werkbank_abzweig_zuruecksetzen(abzweig, ordner):
    """Projekt auf den Stand von Schritt N — selbst umkehrbar (zuruecksetzen sichert vorher)."""
    if not abzweig.get("checkpunkt"):
        raise ValueError("Zu diesem Schritt gibt es keinen Checkpunkt (Lauf mit „nur lesen“?).")
    with _werkbank_lock:
        if ordner in _werkbank_aktiv:
            raise ValueError("In diesem Projekt arbeitet gerade ein Lauf — erst danach zurücksetzen.")
    checkpunkte.Checkpunkte(CHECKPUNKTE_DIR, ordner).zuruecksetzen(abzweig["checkpunkt"])
    return True

def werkbank_schritte(lauf_id):
    """Schrittprotokoll für die Verlaufsansicht: ohne System-Prompt-Wiederholung, Ergebnisse gekürzt."""
    p = schrittprotokoll.lesen(WERKBANK_DIR, lauf_id)
    beginn = p["beginn"]
    kurz = lambda t, n=4000: (t[:n] + "\n… (%d Zeichen)" % len(t)) if isinstance(t, str) and len(t) > n else t
    return {"id": lauf_id, "meta": p["meta"], "aufgabe": beginn.get("aufgabe"), "planmodus": beginn.get("planmodus"),
            "system": kurz(beginn["nachrichten"][0]["content"], 20000) if beginn.get("nachrichten") else "",
            "fortsetzung": beginn.get("fortsetzung"), "ende": p["ende"], "verdichtet": p["verdichtet"],
            "unterbrochen": p["ende"] is None and lauf_id not in _werkbank_aktiv.values(),
            "schritte": [dict({k: v for k, v in s.items() if k not in ("art", "roh", "antwort")},
                              roh=kurz(s.get("roh") or "", 6000), antwort=kurz(s.get("antwort"))) for s in p["schritte"]],
            "unteragenten": {nr: [{k: v for k, v in s.items() if k in ("schritt", "werkzeug", "gedanke", "sek", "ergebnis",
                                                                        "argumente")} for s in liste]
                             for nr, liste in p["unteragenten"].items()}}

def werkbank_letzter_lauf(session_id):
    """Der jüngste Werkbank-Lauf eines Chats — Folgenachrichten setzen ihn fort."""
    if not session_id:
        return None
    beste = None
    for n in os.listdir(WERKBANK_DIR) if os.path.isdir(WERKBANK_DIR) else []:
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(WERKBANK_DIR, n), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if d.get("session_id") == session_id and (not beste or d.get("zeit", 0) > beste[1]):
            beste = (n[:-5], d.get("zeit", 0))
    return beste[0] if beste else None

def werkbank_diff(lauf_id):
    """Was der Lauf auf der Platte verändert hat — auch während er noch arbeitet."""
    if lauf_id in _werkbank_start:
        ordner, cp = _werkbank_start[lauf_id]
        laeuft = True
    else:
        spur = werkbank_spur(lauf_id)
        ordner, cp = spur.get("ordner"), (spur.get("ergebnis") or {}).get("checkpunkt_start")
        laeuft = False
    if not cp:
        raise LookupError("Für diesen Lauf gibt es keinen Checkpunkt (Rechtestufe „nur lesen“ oder Projekt zu groß).")
    punkte = checkpunkte.Checkpunkte(CHECKPUNKTE_DIR, ordner)
    return {"lauf": lauf_id, "ordner": ordner, "laeuft": laeuft, "checkpunkt": cp,
            "seit": punkte.seit(cp), "diff": punkte.diff(cp)}

class WerkbankWeb:
    """Die Websuche von Dive on Wide für den Werkbank-Agenten — mit SSRF-Schutz beim Abrufen."""

    @staticmethod
    def suchen(anfrage):
        """Treffer — oder ein Fehler, der sagt, warum es keine gibt.

        Ein leeres Ergebnis und eine gesperrte Suchmaschine sehen für den
        Agenten sonst gleich aus. Beim ersten Lauf mit einem lokalen Modell
        führte genau das in eine Schleife aus zwanzig Suchbegriffen.
        """
        backend = get_setting("SEARCH_BACKEND", "auto")
        fn = _SEARCH_BACKENDS.get(backend)
        if fn:
            try:
                res = fn(anfrage, 8)
                if res:
                    return res
            except Exception:
                pass
        treffer, gruende = _search_keyless_diagnose(anfrage, 8)
        if treffer:
            return treffer
        if gruende and not all("keine Treffer" in g for g in gruende):
            raise RuntimeError(
                "Die Websuche ist gerade nicht verfügbar (%s). Nutze stattdessen „webseite“ "
                "mit einer bekannten Adresse, oder trage unter Einstellungen → Recherche "
                "einen Suchdienst ein." % "; ".join(gruende[:3]))
        return []

    abrufen = staticmethod(lambda url: web_fetch(url, 12000))

def werkbank_chat(model):
    def chat(nachrichten):
        # no_think: Denkende Modelle schreiben sonst seitenweise vor jeder
        # Aktion (gemessen, siehe llm_chat_once). Temperatur wie Stufe 2.
        return llm_chat_once(model, nachrichten, temperature=0.2, no_think=True)
    return chat

def werkbank_profile(ordner):
    """Alle Profile für delegieren — mit eigenem Modell, wo eines eingetragen ist."""
    profile = agentenprofile.finden(ordner, AGENTEN_DIR)
    for pr in profile.values():
        if pr.get("modell"):
            pr["chat"] = werkbank_chat(pr["modell"])
    return profile

def werkbank_externe():
    return externe_agenten.Externe.aus_einstellung(
        get_setting("WERKBANK_EXTERN", ""), {"claude": get_setting("CLAUDE_BIN", ""), "codex": get_setting("CODEX_BIN", "")})

import erfahrung  # noqa: E402

ERFAHRUNG = erfahrung.Erfahrung(os.path.join(STORAGE_DIR, "modell_erfahrung.json"))


def run_werkbank(aufgabe, ordner, model, stufe, politik, max_schritte,
                 session_id=None, run_id=None, mcp_namen=(), vorgaenger=None, planmodus=False, web=False,
                 unbeaufsichtigt=False, bilder=None, profil=None, vorher_nachrichten=None, abgezweigt=None,
                 quelle="dowos"):
    """Hintergrundlauf der Werkbank inkl. Protokoll, Trajektorie und Rückmeldung."""
    model = model or (profil or {}).get("modell") or get_setting("WERKBANK_MODELL", "") or get_setting("DEFAULT_MODEL")
    try:
        wb = werkbank.Werkbank(ordner, stufe)
        budget = int(int(get_setting("NUM_CTX", "16384")) * 0.67)

        chat = werkbank_chat(model)

        def freigabe(text):
            if unbeaufsichtigt:
                run_step(run_id, "abgelehnt, weil niemand zusieht: %s" % text[:120], "done")
                return False
            return warte_auf_bestaetigung(run_id, "🧰 " + text) if run_id else False

        run_step(run_id, "Projekt: %s · Rechte: %s · Sandbox: %s"
                 % (ordner, stufe, wb.sandbox or "keine"), "done")
        with _werkbank_lock:
            if ordner in _werkbank_aktiv:
                raise ValueError("In diesem Projekt arbeitet schon ein Werkbank-Lauf — "
                                 "zwei gleichzeitig würden sich gegenseitig die Dateien ändern.")
            _werkbank_aktiv[ordner] = run_id
        kasten = None
        try:
            if mcp_namen:
                kasten = mcp_client.Werkzeugkasten(mcp_konfig(), mcp_namen)
                for name, grund in kasten.fehler.items():
                    run_step(run_id, "⚠️ MCP „%s“ nicht verbunden: %s" % (name, grund), "done")
                if kasten.verbindungen:
                    run_step(run_id, "MCP verbunden: %s (%d Werkzeuge)" % (
                        ", ".join(kasten.verbindungen), len(kasten.werkzeuge())), "done")
            # „nur lesen“ ändert nichts — dafür braucht es keinen Checkpunkt.
            cp = checkpunkte.Checkpunkte(CHECKPUNKTE_DIR, ordner) if stufe != "lesen" else None
            vorher = vorher_nachrichten or ((werkbank_spur(vorgaenger).get("ergebnis") or {}).get("nachrichten")
                                            if vorgaenger else None)
            regeln, hinweise = werkbank_regeln.fuer_projekt(
                ordner, get_setting("WERKBANK_REGELN", ""),
                werkbank_regeln.Vertrauen(os.path.join(WERKBANK_DIR, "vertrauen.json")),
                fragen=None if unbeaufsichtigt else (lambda text: warte_auf_bestaetigung(run_id, "🔐 " + text) if run_id else False))
            for h in hinweise:
                run_step(run_id, h, "done")
            aufgabe, befehl = werkbank_befehle.anwenden(aufgabe, werkbank_befehle.finden(ordner, BEFEHLE_DIR))
            if befehl:
                run_step(run_id, "Befehl /%s eingesetzt" % befehl, "done")
            skills = agentskills.finden(ordner, SKILLS_DIR)
            if skills:
                run_step(run_id, "Skills verfügbar: %s" % ", ".join(sorted(skills)), "done")
            if abgezweigt:
                run_step(run_id, "Zweigt bei Schritt %s von Lauf %s ab%s" % (
                    abgezweigt["schritt"], abgezweigt["lauf"], " — Projekt auf diesen Stand zurückgesetzt"
                    if abgezweigt.get("zurueckgesetzt") else ""), "done")
            elif vorgaenger:
                run_step(run_id, "Setzt den Lauf %s fort" % vorgaenger, "done")
            if profil:
                run_step(run_id, "Profil „%s“%s" % (profil["name"], " · Werkzeuge: " + ", ".join(profil["werkzeuge"])
                                                   if profil.get("werkzeuge") else ""), "done")
            externe = werkbank_externe()
            if externe.verfuegbar():
                run_step(run_id, "Externe Agenten verfügbar (nur mit Freigabe): %s" % ", ".join(externe.verfuegbar()), "done")
            protokoll_schreiber = schrittprotokoll.Schreiber(
                WERKBANK_DIR, run_id or nid(), ordner=ordner, modell=model, stufe=stufe, freigabe=politik, quelle=quelle,
                vorgaenger=vorgaenger or "", abgezweigt=abgezweigt or None, profil=(profil or {}).get("name", ""),
                session_id=session_id or "", zeit=now())
            argumente = dict(freigabe=freigabe, politik=politik, max_schritte=max_schritte, vorher=vorher,
                             ereignisse=protokoll_schreiber, werkzeuge=(profil or {}).get("werkzeuge"),
                             zusatz=(profil or {}).get("zusatz", ""), profile=werkbank_profile(ordner),
                             extern=externe if externe.verfuegbar() else None,
                             planmodus=planmodus, regeln=regeln, skills=skills,
                             gedaechtnis=werkbank_gedaechtnis.Gedaechtnis(WERKBANK_DIR),
                             web=WerkbankWeb() if web else None, bilder=bilder or None,
                             start_gesichert=lambda cp: _werkbank_start.__setitem__(run_id, (ordner, cp["id"])),
                             budget=budget, lauf=run_id or "", mcp=kasten,
                             melden=lambda text, zustand="done": run_step(run_id, text, zustand))
            try:
                ergebnis = werkbank.arbeiten(aufgabe, wb, chat, checkpunkte=cp, **argumente)
            except checkpunkte.ZuGross as e:
                # Ohne Checkpunkt ließe sich nichts zurücknehmen. Das ist nur
                # vertretbar, wenn das Projekt eine eigene Versionsgeschichte hat.
                if not os.path.isdir(os.path.join(ordner, ".git")):
                    raise ValueError("%s Ohne Checkpunkte ließen sich die Änderungen nicht "
                                     "zurücknehmen — bitte einen kleineren Unterordner wählen "
                                     "oder das Projekt unter Git stellen." % e)
                run_step(run_id, "⚠️ Keine Checkpunkte (%s) — rückgängig nur über Git" % e, "done")
                ergebnis = werkbank.arbeiten(aufgabe, wb, chat, **argumente)
        finally:
            if kasten:
                kasten.schliessen()
            # Arbeitsordner des Laufs (TMPDIR der Befehle) wegräumen — blieb vorher je Lauf liegen
            wb.schliessen()
            _werkbank_start.pop(run_id, None)
            with _werkbank_lock:
                _werkbank_aktiv.pop(ordner, None)
        md = werkbank.protokoll(aufgabe, ordner, ergebnis, stufe, politik, model)
        # Jeder Lauf ist später Trainingsmaterial (Trajektorie) — lokal abgelegt,
        # nie verschickt.
        os.makedirs(WERKBANK_DIR, exist_ok=True)
        with open(os.path.join(WERKBANK_DIR, "%s.json" % (run_id or nid())), "w", encoding="utf-8") as f:
            for n_ in ergebnis.get("nachrichten", []):     # Bilder nicht als Base64 in die Trajektorie
                if n_.pop("images", None):
                    n_["content"] += " [Bild entfernt]"
            json.dump({"aufgabe": aufgabe, "ordner": ordner, "modell": model, "stufe": stufe,
                       "freigabe": politik, "zeit": now(), "ergebnis": ergebnis,
                       "session_id": session_id or "", "vorgaenger": vorgaenger or "",
                       "abgezweigt": abgezweigt or None, "profil": (profil or {}).get("name", ""), "quelle": quelle},
                      f, ensure_ascii=False, indent=1)
        fertig = ergebnis["beendet"] in ("fertig", "frage")
        ERFAHRUNG.buchen(model, ergebnis["beendet"], ergebnis.get("sekunden"))
        art_id = deliver("Werkbank: %s" % aufgabe[:60], md, "werkbank.md",
                         "Werkbank-Diver %s" % ("fertig ✅" if fertig else "beendet ⚠️"),
                         "Aufgabe „%s“ in %s" % (aufgabe[:70], ordner), "skill")
        if session_id:
            post_to_session(session_id, md, model)
        run_finish(run_id, "done" if fertig else "warn", md, art_id)
    except RunCancelled as _abbruch:
        run_abgebrochen(run_id, "Werkbank-Diver", session_id, grund=_abbruch)
    except Exception as e:
        if isinstance(e, ModellFehler):
            ERFAHRUNG.buchen(model, "speichernot" if e.speicher else "fehler")
        fail("Werkbank-Diver fehlgeschlagen ⚠️", e)
        run_finish(run_id, "error", str(e))
        if session_id:
            post_to_session(session_id, "⚠️ Werkbank-Diver fehlgeschlagen: %s" % e)

import fahrplan  # noqa: E402

FAHRPLAN_DIR = os.path.join(WERKBANK_DIR, "fahrplaene")


def _lauf_status(run_id):
    conn = db()
    r = conn.execute("SELECT status, result FROM runs WHERE id=?", (run_id,)).fetchone()
    conn.close()
    return (r["status"], r["result"] or "") if r else ("", "")


def run_fahrplan(run_id, fahrplan_id, ordner, pakete, model, stufe, politik, max_reparaturen):
    """Eine Roadmap Paket für Paket — nach jedem Paket die Tests, bei Rot reparieren, sonst Halt.

    Jedes Paket und jede Reparatur ist ein eigener Werkbank-Lauf (sichtbar, abbrechbar, mit
    Freigaben wie immer). Die Tests laufen in der Sandbox der Werkbank; gibt es keine, wird
    einmal gefragt — ohne Zusage werden keine Tests ausgeführt und die Roadmap hält an."""
    plan = fahrplan.Fahrplan(os.path.join(FAHRPLAN_DIR, fahrplan_id + ".json"))
    plan.z.update(ordner=ordner, modell=model, run_id=run_id)
    befehl = fahrplan.testbefehl(ordner, werkbank.TESTBEFEHL)
    erlaubt = {"ohne_sandbox": None}

    def arbeite(auftrag, vorgaenger):
        unter = run_begin("code", "Werkbank (Roadmap): " + auftrag.split("\n")[0][:60])
        RUN_ELTERN[unter] = run_id          # Freigaben des Pakets erscheinen an der Roadmap
        run_step(run_id, "→ Werkbank-Lauf %s" % unter, "active")
        try:
            run_werkbank(auftrag, ordner, model, stufe, politik, 40, None, unter, (), vorgaenger, quelle="fahrplan")
        finally:
            RUN_ELTERN.pop(unter, None)     # sonst wächst die Zuordnung mit jedem Paket (Stresstest 08.10.2026)
        status, ergebnis = _lauf_status(unter)
        if status == "error":
            raise RuntimeError("Der Werkbank-Lauf scheiterte: %s" % ergebnis[:300])
        if status == "cancelled":
            raise RunCancelled()
        run_step(run_id, "Werkbank-Lauf %s beendet (%s)" % (unter, status), "done")
        return unter

    def teste():
        wb = werkbank.Werkbank(ordner, stufe)
        if wb.sandbox is None:
            if erlaubt["ohne_sandbox"] is None:
                erlaubt["ohne_sandbox"] = warte_auf_bestaetigung(
                    run_id, "🧪 Tests ohne Sandbox ausführen? (%s) — sie sind vom Modell geschrieben "
                            "und laufen mit deinen Rechten." % befehl)
            if not erlaubt["ohne_sandbox"]:
                return "exit=1\nTests nicht ausgeführt: ohne Sandbox nicht freigegeben."
        run_step(run_id, "Tests: %s" % befehl, "active")
        try:
            return wb.ausfuehren(befehl, timeout=300)
        finally:
            wb.schliessen()

    try:
        z = plan.abarbeiten(pakete, arbeite, teste, melden=lambda t: run_step(run_id, t, "done"),
                            max_reparaturen=max_reparaturen,
                            # Kein Ordnername im Auftrag: „Projekt in fp-temperatur“ las das Modell als
                            # Auftrag, einen Unterordner fp-temperatur anzulegen (04.10.2026, 35B)
                            kopf="Du arbeitest direkt im Projektordner (Dateien an seiner Wurzel, kein Unterordner "
                                 "für das Projekt). Lies zuerst DOWOS.md und ROADMAP.md, falls vorhanden.\n",
                            befehl=befehl)
        md = plan.bericht()
        fertig = z["status"] == "fertig"
        art_id = deliver("Roadmap: %s" % os.path.basename(ordner), md, "roadmap.md",
                         "Roadmap fertig ✅" if fertig else "Roadmap angehalten ⏸",
                         "Alle %d Pakete grün" % len(z["pakete"]) if fertig else
                         "Paket %s: %s" % (z.get("halt_bei"), z["ergebnisse"][str(z["halt_bei"])]["grund"]), "skill")
        run_finish(run_id, "done" if fertig else "warn", md, art_id)
    except RunCancelled as _abbruch:
        plan.z["status"] = "abgebrochen"
        plan.sichern()
        run_abgebrochen(run_id, "Roadmap", None, grund=_abbruch)
    except Exception as e:
        plan.z.update(status="fehler", fehler=str(e)[:300])
        plan.sichern()
        fail("Roadmap gescheitert ⚠️", e)
        run_finish(run_id, "error", str(e))


def fahrplaene_liste(anzahl=10):
    """Die letzten Roadmaps — vor allem die angehaltenen, die sich fortsetzen lassen."""
    if not os.path.isdir(FAHRPLAN_DIR):
        return []
    dateien = sorted((f for f in os.listdir(FAHRPLAN_DIR) if f.endswith(".json")),
                     key=lambda f: os.path.getmtime(os.path.join(FAHRPLAN_DIR, f)), reverse=True)[:anzahl]
    aus = []
    for f in dateien:
        z = fahrplan.Fahrplan(os.path.join(FAHRPLAN_DIR, f)).z
        halt = z.get("halt_bei")
        aus.append({"id": f[:-5], "status": z.get("status"), "ordner": z.get("ordner"), "pakete": len(z.get("pakete", [])),
                    "gruen": sum(1 for e in z.get("ergebnisse", {}).values() if e.get("gruen")),
                    "halt_bei": halt, "grund": (z.get("ergebnisse", {}).get(str(halt)) or {}).get("grund", "") if halt else "",
                    "zeit": os.path.getmtime(os.path.join(FAHRPLAN_DIR, f))})
    return aus


def werkbank_aktiv():
    """Alle Werkbank-Läufe, die gerade arbeiten — für die Übersicht (wie die Agentenansicht von Claude Code)."""
    ordner_von = {rid: ordner for ordner, rid in _werkbank_aktiv.items()}
    conn = db()
    laufend = rows(conn.execute("SELECT id, title, progress, pending, created_at, updated_at FROM runs "
                                "WHERE status='running' AND title LIKE 'Werkbank%' ORDER BY created_at DESC"))
    conn.close()
    for r in laufend:
        r["progress"] = json.loads(r["progress"] or "[]")[-8:]
        r["ordner"] = ordner_von.get(r["id"]) or (_werkbank_start.get(r["id"]) or ("",))[0]
    return laufend

def werkbank_laeufe(anzahl=30):
    """Die letzten Werkbank-Läufe mit ihrem Urteil — ohne die Gesprächsverläufe."""
    aus = []
    try:
        namen = [n for n in os.listdir(WERKBANK_DIR) if n.endswith(".json")]
    except OSError:
        return aus
    namen.sort(key=lambda n: os.path.getmtime(os.path.join(WERKBANK_DIR, n)), reverse=True)
    for n in namen[:anzahl]:
        try:
            with open(os.path.join(WERKBANK_DIR, n), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        e = d.get("ergebnis") or {}
        if "ergebnis" not in d:
            continue
        a = e.get("aenderungen") or {}
        aus.append({"id": n[:-5], "aufgabe": d.get("aufgabe", "")[:200], "ordner": d.get("ordner"),
                    "modell": d.get("modell"), "zeit": d.get("zeit"), "beendet": e.get("beendet"),
                    "schritte": e.get("schritte"), "bewertung": d.get("bewertung"), "frage": (e.get("frage") or "")[:300],
                    "zusammenfassung": (e.get("zusammenfassung") or "")[:300], "quelle": d.get("quelle", "dowos"),
                    "profil": d.get("profil", ""), "abgezweigt": d.get("abgezweigt"),
                    "schrittprotokoll": os.path.exists(os.path.join(WERKBANK_DIR, n[:-5] + ".schritte.jsonl")),
                    "aenderungen": sum(len(a.get(k, [])) for k in ("neu", "geaendert", "geloescht")),
                    "git": bool(d.get("ordner") and os.path.isdir(os.path.join(d.get("ordner"), ".git")))})
    return aus

def werkbank_bewerten(lauf_id, bewertung):
    """Urteil des Nutzers über einen Lauf. Nur „gut“ bewertete Läufe werden später
    zu Trainingsdaten (pruefstand/trajektorien.py) — ohne versteckte Tests ist
    der Mensch die einzige Prüfung, die zählt."""
    if not re.match(r"^[\w-]+$", lauf_id or ""):
        raise ValueError("Ungültige Lauf-Kennung.")
    if bewertung not in ("gut", "schlecht", None, ""):
        raise ValueError("Bewertung ist „gut“, „schlecht“ oder leer.")
    pfad = os.path.join(WERKBANK_DIR, lauf_id + ".json")
    if not os.path.exists(pfad):
        raise LookupError("Diesen Werkbank-Lauf gibt es nicht.")
    with open(pfad, encoding="utf-8") as f:
        d = json.load(f)
    d["bewertung"] = bewertung or None
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, pfad)
    return {"id": lauf_id, "bewertung": d["bewertung"]}

# ----------------------------------------------------------------------------
# Trainings-Werkbank — eigenes Modell trainieren (training.py)
# ----------------------------------------------------------------------------

import training

TRAINING_DIR = os.path.join(STORAGE_DIR, "training")

def trainings_werkbank():
    suchorte = [os.path.join(TRAINING_DIR, "modelle"), os.path.join(TRAINING_DIR, "daten")]
    suchorte += [x.strip() for x in get_setting("TRAINING_SUCHORTE", "").split(",") if x.strip()]
    for d in suchorte[:2]:
        os.makedirs(d, exist_ok=True)
    wb = training.Werkbank(TRAINING_DIR, suchorte, python=get_setting("TRAINING_PYTHON", "").strip() or None,
                           caffeinate=get_setting("TRAINING_CAFFEINATE", "1") == "1")
    wb.vor_start = ollama_alle_entladen
    return wb

def ollama_alle_entladen():
    """Vor dem Training: geladene Ollama-Modelle freigeben. Gemessen an Qwen3.5-4B:
    ein geladenes 5-GB-Modell reichte, damit das Training am GPU-Speicher scheiterte."""
    basis = get_setting("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(basis + "/api/ps", timeout=3) as r:
            geladen = [m.get("name") for m in json.load(r).get("models", [])]
    except Exception:
        return []
    for name in geladen:
        try:
            urllib.request.urlopen(urllib.request.Request(
                basis + "/api/generate", data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                headers={"Content-Type": "application/json"}), timeout=30).read()
        except Exception:
            pass
    return geladen

FABRIK_DIR = os.path.join(TRAINING_DIR, "aufgaben")
PRUEFUNGEN_DIR = os.path.join(TRAINING_DIR, "pruefungen")

def pruefungen():
    """Ergebnisse des Prüfstands je Modell — die Grundlage jeder Modellwahl."""
    aus = []
    for ordner in (PRUEFUNGEN_DIR, os.path.join(BASE_DIR, "pruefstand", "ergebnisse")):
        if not os.path.isdir(ordner):
            continue
        for n in sorted(os.listdir(ordner)):
            if not n.endswith(".jsonl") or n.startswith("uebung_"):
                continue
            je_modell = {}
            with open(os.path.join(ordner, n), encoding="utf-8") as f:
                for z in f:
                    try:
                        d = json.loads(z)
                    except ValueError:
                        continue
                    e = je_modell.setdefault(d["modell"], {"modell": d["modell"], "lauf": n[:-6], "geloest": 0,
                                                          "aufgaben": 0, "unlesbar": 0, "sekunden": 0})
                    e["aufgaben"] += 1
                    e["geloest"] += bool(d["ergebnis"].get("geloest"))
                    e["unlesbar"] += d["ergebnis"].get("ungueltig", 0)
                    e["sekunden"] += d["ergebnis"].get("sekunden", 0)
            aus += je_modell.values()
    aus.sort(key=lambda e: (-e["geloest"], e["sekunden"]))
    return aus
LOESUNGEN_DIR = os.path.join(TRAINING_DIR, "loesungen")
DESTILLATION_DIR = os.path.join(TRAINING_DIR, "destillation")
DATENWERT_DIR = os.path.join(TRAINING_DIR, "datenwert")

def fabrik_stand():
    """Wie viele Übungsaufgaben es gibt und wie viele davon gelöst wurden."""
    try:
        with open(os.path.join(FABRIK_DIR, "meta.json"), encoding="utf-8") as f:
            aufgaben = len(json.load(f))
    except (OSError, ValueError):
        aufgaben = 0
    geloest = set()
    if os.path.isdir(LOESUNGEN_DIR):
        for n in os.listdir(LOESUNGEN_DIR):
            if n.endswith(".jsonl"):
                with open(os.path.join(LOESUNGEN_DIR, n), encoding="utf-8") as f:
                    for z in f:
                        try:
                            d = json.loads(z)
                        except ValueError:
                            continue
                        if d.get("ergebnis", {}).get("geloest"):
                            geloest.add((d["modell"], d["aufgabe"]))
    return {"aufgaben": aufgaben, "geloeste_laeufe": len(geloest)}

def _modell_anschluss(ref):
    """Wie Fabrik, Lösen und Prüfstand ein Modell erreichen: (Argumente, Umgebung, Name).

    Jeder eingerichtete Anbieter geht — Ollama nativ, alles andere über die
    OpenAI-Schnittstelle (LM Studio, vLLM, mlx_lm server, OpenRouter …). Der
    Schlüssel reist in der Umgebung des Kindprozesses, nie in der Befehlszeile:
    die stünde in `ps` und im gespeicherten Lauf."""
    try:
        prov, name = parse_model_ref(ref or get_setting("DEFAULT_MODEL"))
    except UnbekannterProvider as e:
        raise ValueError(str(e))
    art = prov.get("type") or "ollama"
    if art == "ollama":
        return ["--ollama", prov.get("base_url") or get_setting("OLLAMA_BASE_URL")], {}, name
    if art == "openai" and prov.get("base_url"):
        adapter = (prov.get("nutzlast") or {}).get("adapters")
        return ["--openai", openai_url(prov, "")] + (["--adapter", adapter] if adapter else []), ({"DOWOS_API_KEY": prov["api_key"]} if prov.get("api_key") else {}), name
    raise ValueError("Modelle des Anbieters „%s“ lassen sich hier nicht nutzen — wähle ein Modell von Ollama "
                     "oder einem OpenAI-kompatiblen Anbieter." % (prov.get("name") or prov.get("id")))

ROLLEN = {
    "WERKBANK_MODELL": "Standardmodell des Werkbank-Divers (leer: das Chat-Standardmodell)",
    "ORCHESTRATOR_MODELL": "Plant die Abläufe des Orchestrators (leer: das Chat-Standardmodell) — "
                           "hierhin gehört ein trainiertes Hausmodell",
    "LEHRER_MODELL": "Lehrer: entwirft Übungsaufgaben und löst sie — ein starkes Modell, lokal oder bei einem Anbieter",
    "SCHUELER_BASIS": "Schüler-Grundmodell fürs Training — ein MLX-Ordner",
    "SCHUELER_MODELL": "Schüler in Dive on Wide — das Modell, das geprüft und benutzt wird",
}

def rollen():
    return {k: {"wert": get_setting(k, ""), "bedeutung": v} for k, v in ROLLEN.items()}

def training_auftrag(art, body):
    wb = trainings_werkbank()
    # Ohne ausdrückliche Wahl: die eingestellte Rolle — Lehrer für Fabrik und Lösen,
    # Schüler für die Prüfung.
    rolle = "SCHUELER_MODELL" if art == "pruefen" else "LEHRER_MODELL"
    gewaehlt = body.get("modell") or get_setting(rolle, "")
    if not gewaehlt:
        raise ValueError("Kein Modell gewählt und keine Rolle %s eingestellt." % rolle)
    if str(gewaehlt).startswith(ABO_ANBIETER + "@@"):
        raise ValueError("Ein Abo-Lehrer arbeitet nur in der Destillation (als Systemtest) — Fabrik und Lösen hier "
                         "erzeugen Trainingsdaten. Wähle ein lokales Modell oder einen API-Anbieter.")
    anschluss, umgebung, modell = _modell_anschluss(gewaehlt)
    skripte = os.path.join(BASE_DIR, "pruefstand")
    if art == "fabrik":
        try:
            anzahl = max(1, min(int(body.get("anzahl") or 20), 500))
        except (TypeError, ValueError):
            raise ValueError("anzahl muss eine Zahl sein.")
        befehl = [sys.executable, "-u", os.path.join(skripte, "fabrik.py"), "--modell", modell,
                  "--anzahl", str(anzahl), "--ziel", FABRIK_DIR] + anschluss
        return wb.auftrag_starten("fabrik", befehl, "Aufgabenfabrik: %d Aufgaben mit %s" % (anzahl, modell), anzahl, BASE_DIR, umgebung)
    if art == "pruefen":
        os.makedirs(PRUEFUNGEN_DIR, exist_ok=True)
        befehl = [sys.executable, "-u", os.path.join(skripte, "stufe2.py"), "--modelle", modell,
                  "--ergebnisse", PRUEFUNGEN_DIR, "--lauf", "pruefung_" + re.sub(r"[^\w.-]+", "_", modell)] + anschluss
        return wb.auftrag_starten("pruefen", befehl, "Prüfstand: %s" % modell, 20, BASE_DIR, umgebung)
    stand = fabrik_stand()
    if not stand["aufgaben"]:
        raise ValueError("Noch keine Übungsaufgaben — erst die Aufgabenfabrik laufen lassen.")
    os.makedirs(LOESUNGEN_DIR, exist_ok=True)
    befehl = [sys.executable, "-u", os.path.join(skripte, "stufe2.py"), "--modelle", modell,
              "--aufgaben-ordner", FABRIK_DIR, "--ergebnisse", LOESUNGEN_DIR,
              "--lauf", "uebung_" + re.sub(r"[^\w.-]+", "_", modell)] + anschluss
    return wb.auftrag_starten("loesen", befehl, "Lösen lassen: %d Aufgaben mit %s" % (stand["aufgaben"], modell),
                              stand["aufgaben"], BASE_DIR, umgebung)

OLLAMA_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}(:[a-z0-9._-]{1,40})?$")

def training_ollama(kennung, body):
    """Fertiges Training → eigenständiges Ollama-Modell (training_export.py), abgekoppelt."""
    wb = trainings_werkbank()
    lauf = next((l for l in wb.liste() if l["id"] == kennung and l.get("art", "training") == "training"), None)
    if not lauf:
        raise LookupError("Dieses Training gibt es nicht.")
    if lauf["zustand"] != "fertig" or not os.path.isfile(os.path.join(lauf["adapter"], "adapters.safetensors")):
        raise ValueError("Nur ein fertiges Training mit Adapter lässt sich übernehmen.")
    name = str(body.get("name") or "").strip().lower()
    if not OLLAMA_NAME_RE.match(name):
        raise ValueError("Modellname: Kleinbuchstaben, Ziffern, . _ - und optional :tag — z. B. dowos-schueler:v1")
    quant = str(body.get("quant") or "Q8_0")
    if quant not in ("Q8_0", "Q4_K_M", "F16"):
        raise ValueError("Quantisierung: Q8_0, Q4_K_M oder F16.")
    kandidaten = [get_setting("LLAMA_CPP_DIR", "")] + [os.path.expanduser(p) for p in ("~/llm/tools/llama.cpp", "~/llama.cpp")]
    llama = next((os.path.expanduser(k) for k in kandidaten if k and os.path.isfile(
        os.path.join(os.path.expanduser(k), "convert_hf_to_gguf.py"))), None)
    if not llama:
        raise ValueError("llama.cpp fehlt (für die Umwandlung nach GGUF): git clone https://github.com/ggml-org/llama.cpp "
                         "~/llama.cpp — oder LLAMA_CPP_DIR einstellen.")
    quantize = get_setting("LLAMA_QUANTIZE", "") or shutil.which("llama-quantize") or ""
    if not quantize and os.path.isfile(os.path.join(llama, "build", "bin", "llama-quantize")):
        quantize = os.path.join(llama, "build", "bin", "llama-quantize")
    if quant != "F16" and not quantize:
        from mesh import verteilt as _v
        raise ValueError("llama-quantize fehlt — oder F16 wählen. " + _v.llama_cpp_anleitung()
                         + " Alternativ den Pfad unter LLAMA_QUANTIZE eintragen.")
    ollama_bin = get_setting("OLLAMA_BIN", "") or shutil.which("ollama") or ""
    if not ollama_bin:
        raise ValueError("Das Programm ollama wurde nicht gefunden.")
    py, _ = wb.trainer()
    if not py:
        raise RuntimeError(wb.lage()["hinweis"])
    arbeit = os.path.join(TRAINING_DIR, "export", "%s_%s" % (kennung, time.strftime("%Y%m%d_%H%M%S")))
    befehl = [sys.executable, "-u", os.path.join(BASE_DIR, "training_export.py"), "--python", py,
              "--basis", lauf["modell"], "--adapter", lauf["adapter"], "--name", name, "--quant", quant,
              "--arbeit", arbeit, "--llama-cpp", llama, "--quantize", quantize, "--ollama", ollama_bin]
    return wb.auftrag_starten("ollama", befehl, "In Ollama: %s (%s)" % (name, quant), 4, BASE_DIR,
                              {"OLLAMA_HOST": get_setting("OLLAMA_BASE_URL", "http://localhost:11434")})

# ---------------------------------------------------------------- Destillation ---
# Spitzenmodelle als Lehrer (destillation.py, doku/DESTILLATION.md). Preise und die Bestätigung der
# Nutzungsbedingungen je Anbieter liegen in den Einstellungen; Schlüssel gehen nur über die Umgebung
# an den Kindprozess.

import destillation as destillation_modul
import lehrer as lehrer_modul

def _lehrer_preise():
    try:
        return json.loads(get_setting("LEHRER_PREISE", "{}") or "{}")
    except ValueError:
        return {}

def _bedingungen():
    try:
        return json.loads(get_setting("LEHRER_BEDINGUNGEN", "{}") or "{}")
    except ValueError:
        return {}

# Lokale Modell-Server, die man oft neben Ollama betreibt — (Port, Name, Typ). Gefragt wird nur 127.0.0.1:
# Nichts verlässt den Rechner. Ein Treffer zählt nur, wenn die Antwort wirklich eine Modellliste ist.
LOKALE_SERVER = [(11434, "Ollama", "ollama"), (1234, "LM Studio", "openai"), (8000, "vLLM", "openai"),
                 (8080, "llama.cpp / LocalAI / MLX", "openai"), (1337, "Jan", "openai"), (5001, "KoboldCpp", "openai"),
                 (5000, "text-generation-webui / TabbyAPI", "openai"), (30000, "SGLang", "openai"),
                 (8600, "MLX", "openai"), (4891, "GPT4All", "openai"), (2242, "Aphrodite", "openai")]


def lokale_server_suchen(frist=0.8):
    """[{name, type, base_url, modelle, eingetragen}] — alle lokalen Server mit Modellen, parallel gefragt."""
    eigener = int(ENV.get("PORT", "3000"))
    vorhanden = {(p.get("base_url") or "").rstrip("/").replace("127.0.0.1", "localhost") for p in get_providers()}
    funde, faeden = [], []

    def fragen(port, name, typ):
        basis = "http://localhost:%d" % port
        try:
            pfad = "/api/tags" if typ == "ollama" else "/v1/models"
            with urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, pfad), timeout=frist) as r:
                d = json.loads(r.read(2_000_000).decode("utf-8", "replace"))
            liste = d.get("models") if typ == "ollama" else d.get("data")
            if not isinstance(liste, list):
                return
            modelle = [str(m.get("name") or m.get("id") or "") for m in liste if isinstance(m, dict)]
            funde.append({"name": name, "type": typ, "base_url": basis, "port": port, "modelle": modelle[:20],
                          "eingetragen": basis in vorhanden})
        except Exception:
            pass

    for port, name, typ in LOKALE_SERVER:
        if port == eigener:
            continue
        t = threading.Thread(target=fragen, args=(port, name, typ), daemon=True)
        t.start()
        faeden.append(t)
    for t in faeden:
        t.join(frist + 0.5)
    return sorted(funde, key=lambda f: f["port"])


def _anbieter_extern(prov):
    basis = (prov.get("base_url") or "").lower()
    return not any(h in basis for h in ("127.0.0.1", "localhost", "[::1]"))

ABO_ANBIETER = "claude-code"
ABO_MODELLE = [("claude-opus-5", "Opus 5"), ("claude-fable-5-1", "Fable 5.1"), ("claude-sonnet-5", "Sonnet 5")]

def _abo_lehrer_liste():
    befehl = lehrer_modul.claude_befehl_finden(get_setting("CLAUDE_BIN", ""))
    return [{"modell": "%s@@%s" % (ABO_ANBIETER, m), "name": "%s (Claude-Abo)" % n, "verfuegbar": bool(befehl)}
            for m, n in ABO_MODELLE]

def _lehrer_eintrag(ref, preis=None, max_aufrufe=None):
    """Aus einem Modellverweis (anbieter@@modell) einen Lehrer für auftrag.json — ohne Schlüssel."""
    if str(ref or "").startswith(ABO_ANBIETER + "@@"):
        modell = ref.split("@@", 1)[1]
        if not re.match(r"^[\w.-]{1,80}$", modell):
            raise ValueError("Ungültiger Modellname.")
        if not lehrer_modul.claude_befehl_finden(get_setting("CLAUDE_BIN", "")):
            raise ValueError("Claude Code wurde nicht gefunden — Desktop-App installieren oder CLAUDE_BIN eintragen.")
        return {"modell": ref, "name": dict(ABO_MODELLE).get(modell, modell) + " (Claude-Abo)", "anbieter": "Claude-Abo",
                "basis_url": "", "ollama": False, "art": "abo", "preis": {}, "max_aufrufe": max_aufrufe,
                "befehl": get_setting("CLAUDE_BIN", "")}
    try:
        prov, name = parse_model_ref(ref)
    except UnbekannterProvider as e:
        raise ValueError(str(e))
    art = prov.get("type") or "ollama"
    if art not in ("ollama", "openai"):
        raise ValueError("„%s“: Anbieter dieser Art gehen nicht als Lehrer." % ref)
    lokal = art == "ollama" or not _anbieter_extern(prov)
    preis = preis or _lehrer_preise().get(ref)
    if not lokal and not preis:
        raise ValueError("Für „%s“ ist kein Preis hinterlegt — ohne Preis keine Budgetgrenze." % name)
    if not lokal and not _bedingungen().get(prov.get("id")):
        raise ValueError("Die Nutzungsbedingungen von „%s“ sind noch nicht bestätigt." % (prov.get("name") or prov.get("id")))
    return {"modell": "%s@@%s" % (prov.get("id"), name), "name": name, "anbieter": prov.get("name") or prov.get("id"),
            "basis_url": prov.get("base_url") or get_setting("OLLAMA_BASE_URL", "http://localhost:11434"),
            "ollama": art == "ollama", "art": "lokal" if lokal else "api",
            "preis": preis or {"eingabe": 0, "ausgabe": 0, "waehrung": "USD"}}

def destillation_uebersicht():
    auftraege = []
    if os.path.isdir(DESTILLATION_DIR):
        for n in sorted(os.listdir(DESTILLATION_DIR), reverse=True):
            a = destillation_modul._json_lesen(os.path.join(DESTILLATION_DIR, n, "auftrag.json"))
            if not a:
                continue
            z = destillation_modul._json_lesen(os.path.join(DESTILLATION_DIR, n, "zustand.json"), {}) or {}
            labels = collections.Counter(p.get("label") for p in (z.get("proben") or {}).values())
            auftraege.append({"id": n, "name": a["name"], "themen": a["themen"], "lehrer": [l["name"] for l in a["lehrer"]],
                              "anzahl": a["anzahl"], "budget": a.get("budget"), "waehrung": a.get("waehrung"),
                              "ausgegeben": z.get("ausgegeben", 0), "ende": z.get("ende"), "proben": len(z.get("proben") or {}),
                              "labels": dict(labels), "ereignisse": (z.get("ereignisse") or [])[-5:],
                              "angelegt": a.get("angelegt")})
    anbieter = [{"id": p.get("id"), "name": p.get("name"), "extern": (p.get("type") == "openai" and _anbieter_extern(p)),
                 "openrouter": "openrouter" in (p.get("base_url") or "")} for p in get_providers()]
    return {"auftraege": auftraege, "preise": _lehrer_preise(), "bedingungen": _bedingungen(), "anbieter": anbieter,
            "abo_lehrer": _abo_lehrer_liste(), "standard_lehrer": get_setting("LEHRER_MODELL", ""),
            "archiv": destillation_archiv_ort(), "datenwert": datenwert_liste(), "datenwert_datensaetze": datenwert_datensaetze(),
            "archiv_vorschlag": "/Volumes/T7 Shield/dowos_destillation" if os.path.isdir("/Volumes/T7 Shield") else "",
            "sandbox": werkbank.sandbox_art(), "labels": list(destillation_modul.LABELS),
            "aufgaben_vorhanden": fabrik_stand()["aufgaben"],
            "hinweis": "Aufgaben, Code und Lösungswege gehen an den Anbieter des Lehrers. Viele Anbieter untersagen, mit "
                       "ihren Ausgaben Modelle zu entwickeln, die mit ihnen konkurrieren. Ob deine private Nutzung "
                       "erlaubt ist, entscheidest du anhand der Bedingungen deines Anbieters."}

def destillation_schaetzen(body):
    anzahl = max(1, min(int(body.get("anzahl") or 1), 10000))
    statistik = destillation_modul.lauf_statistik(WERKBANK_DIR, DESTILLATION_DIR)
    fabrik = {}
    try:
        with open(os.path.join(FABRIK_DIR, "fabrik_bericht.md"), encoding="utf-8") as f:
            werte = re.findall(r"Versuche: (\d+), angenommen: (\d+)", f.read())
        versuche, angenommen = sum(int(v) for v, _ in werte), sum(int(a) for _, a in werte)
        if versuche >= 10:
            fabrik["ausbeute"] = max(0.05, angenommen / versuche)
    except OSError:
        pass
    aus, summe = [], {"niedrig": 0.0, "erwartet": 0.0, "hoch": 0.0}
    for i, l in enumerate(body.get("lehrer") or []):
        preis = l.get("preis") or _lehrer_preise().get(l.get("modell")) or {}
        if str(l.get("modell") or "").startswith(ABO_ANBIETER + "@@"):
            preis, prov = {"eingabe": 0, "ausgabe": 0, "waehrung": "USD"}, {"type": "ollama"}
        else:
            try:
                prov = parse_model_ref(l.get("modell") or "")[0]
            except UnbekannterProvider:
                prov = {}
        if (prov.get("type") or "ollama") == "ollama" or (prov.get("type") == "openai" and not _anbieter_extern(prov)):
            if preis.get("eingabe") in (None, ""):
                preis = {"eingabe": 0, "ausgabe": 0, "waehrung": "USD"}       # lokal: kostet nichts
        if preis.get("eingabe") in (None, "") or preis.get("ausgabe") in (None, ""):
            aus.append({"modell": l.get("modell"), "fehler": "kein Preis"})
            continue
        p = lehrer_modul.Preis(preis["eingabe"], preis["ausgabe"], preis.get("cache_eingabe"), preis.get("waehrung") or "USD")
        s = lehrer_modul.kosten_schaetzen(p, anzahl, statistik, fabrik,
                                          fabrik_durch_lehrer=(body.get("aufgaben_quelle") or "lehrer") == "lehrer" and i == 0)
        for k in summe:
            summe[k] += s[k]["kosten"]
        aus.append(dict(s, modell=l.get("modell")))
    return {"lehrer": aus, "summe": {k: round(v, 4) for k, v in summe.items()}, "anzahl": anzahl,
            "grundlage_fabrik": "Ausbeute gemessen %.0f %%" % (100 * fabrik["ausbeute"]) if fabrik.get("ausbeute") else
            "Ausbeute angenommen 40 %"}

def destillation_starten(body):
    daten = dict(body)
    daten["lehrer"] = [_lehrer_eintrag(l.get("modell"), l.get("preis"), l.get("max_aufrufe") or body.get("max_aufrufe"))
                       for l in (body.get("lehrer") or [])]
    if (daten.get("aufgaben_quelle") or "lehrer") == "vorhanden":
        daten["aufgaben_ordner"] = FABRIK_DIR
    # Lernlücke je Thema aus bisherigen Datenwert-Tests: Lösungsquote des untrainierten Schülers (Variante A).
    quoten = {}
    for w in datenwert_liste():
        z = destillation_modul._json_lesen(os.path.join(DATENWERT_DIR, w["id"], "zustand.json"), {}) or {}
        a_werte = [l["kontroll"]["quote"] for k, l in (z.get("laeufe") or {}).items()
                   if k.startswith("A-") and l.get("kontroll") and l["kontroll"].get("quote") is not None]
        if w.get("thema") and a_werte:
            quoten[w["thema"]] = sum(a_werte) / len(a_werte)
    daten.setdefault("schueler_quoten", quoten)
    auftrag = destillation_modul.auftrag_pruefen(daten)
    if not werkbank.sandbox_art():
        raise ValueError("Ohne Sandbox (sandbox-exec/bwrap) gibt es kein Orakel — Destillation nicht möglich.")
    if any(l["art"] == "api" for l in auftrag["lehrer"]) and auftrag["budget"] is None:
        raise ValueError("Mit Lehrern über eine bezahlte API braucht der Auftrag ein Budget.")
    kennung = time.strftime("d%Y%m%d_%H%M%S")
    ordner = os.path.join(DESTILLATION_DIR, kennung)
    destillation_modul._json_schreiben(os.path.join(ordner, "auftrag.json"), auftrag)
    schluessel = {}
    for l in auftrag["lehrer"]:
        prov = get_provider(l["modell"].split("@@")[0]) or {}
        if prov.get("api_key"):
            schluessel[prov["id"]] = prov["api_key"]
    befehl = [sys.executable, "-u", os.path.join(BASE_DIR, "destillation.py"), "starten", ordner]
    lokal = any(l["art"] == "lokal" for l in auftrag["lehrer"])
    lauf = trainings_werkbank().auftrag_starten("destillation", befehl, "Destillation: %s" % auftrag["name"],
                                                auftrag["anzahl"] * len(auftrag["lehrer"]), BASE_DIR,
                                                {"DOWOS_API_KEYS": json.dumps(schluessel)}, exklusiv=lokal)
    return dict(lauf, auftrag=kennung)

def destillation_detail(kennung):
    if not re.match(r"^d\d{8}_\d{6}$", kennung or ""):
        raise ValueError("Ungültige Kennung.")
    ordner = os.path.join(DESTILLATION_DIR, kennung)
    a = destillation_modul._json_lesen(os.path.join(ordner, "auftrag.json"))
    if not a:
        raise LookupError("Diesen Auftrag gibt es nicht.")
    return {"id": kennung, "auftrag": a, "zustand": destillation_modul._json_lesen(os.path.join(ordner, "zustand.json"), {})}

def destillation_probe(kennung, probe_id):
    destillation_detail(kennung)
    if not re.match(r"^[\w-]+$", probe_id or ""):
        raise ValueError("Ungültige Probe.")
    p = destillation_modul._json_lesen(os.path.join(DESTILLATION_DIR, kennung, "proben", probe_id + ".json"))
    if not p:
        raise LookupError("Diese Probe gibt es nicht.")
    return p

def datenwert_liste():
    import datenwert as datenwert_modul
    aus = []
    if os.path.isdir(DATENWERT_DIR):
        for n in sorted(os.listdir(DATENWERT_DIR), reverse=True):
            plan = destillation_modul._json_lesen(os.path.join(DATENWERT_DIR, n, "experiment.json"))
            if not plan:
                continue
            z = destillation_modul._json_lesen(os.path.join(DATENWERT_DIR, n, "zustand.json"), {}) or {}
            aus.append({"id": n, "thema": plan.get("thema"), "daten": plan["daten_b"], "seeds": plan["seeds"],
                        "iters": (plan.get("werte") or {}).get("iters"), "ergebnis": z.get("ergebnis"),
                        "gemessen": sum(1 for l in (z.get("laeufe") or {}).values() if l.get("kontroll"))})
    return aus

def datenwert_datensaetze():
    """Exportierte Themen-Datensätze mit Kontrollaufgaben — nur auf ihnen ist der Test ehrlich."""
    aus = []
    basis = os.path.join(TRAINING_DIR, "daten")
    for w, _, namen in os.walk(basis) if os.path.isdir(basis) else []:
        if "holdout.json" in namen and "train.jsonl" in namen:
            h = destillation_modul._json_lesen(os.path.join(w, "holdout.json"), {}) or {}
            if h.get("aufgaben"):
                aus.append({"pfad": w, "thema": h.get("thema"), "kontrollaufgaben": len(h["aufgaben"])})
    return aus

def datenwert_starten(body):
    import datenwert as datenwert_modul
    daten = os.path.realpath(str(body.get("daten") or ""))
    if not daten.startswith(os.path.realpath(TRAINING_DIR) + os.sep):
        raise ValueError("Datensatz muss unter storage/training/daten liegen.")
    basis = str(body.get("basis") or get_setting("SCHUELER_BASIS", ""))
    wb = trainings_werkbank()
    if not wb.ist_modell(basis):
        raise ValueError("Kein Schüler-Grundmodell (Rolle „Schüler · Grundmodell“ setzen).")
    try:
        seeds = [int(x) for x in (body.get("seeds") or [1, 2, 3])]
        iters = max(10, min(int(body.get("iters") or 200), 20000))
    except (TypeError, ValueError):
        raise ValueError("Seeds und Iterationen müssen Zahlen sein.")
    kennung = time.strftime("w%Y%m%d_%H%M%S")
    ordner = os.path.join(DATENWERT_DIR, kennung)
    plan = datenwert_modul.experiment_anlegen(ordner, basis, daten, seeds=seeds, werte={"iters": iters},
                                              python=wb.trainer()[0], training_ordner=TRAINING_DIR)
    befehl = [sys.executable, "-u", os.path.join(BASE_DIR, "datenwert.py"), "starten", ordner]
    lauf = wb.auftrag_starten("datenwert", befehl, "Datenwert: %s (%d Seeds)" % (plan.get("thema") or "?", len(seeds)),
                              len(seeds) * 2, BASE_DIR, exklusiv=False)
    return dict(lauf, experiment=kennung)

def destillation_archiv_ort():
    return get_setting("DESTILLATION_ARCHIV", "") or os.path.join(TRAINING_DIR, "archiv")

def destillation_archivieren(kennung, body):
    destillation_detail(kennung)
    ort = str(body.get("ziel") or destillation_archiv_ort())
    if body.get("ziel"):
        set_setting("DESTILLATION_ARCHIV", ort)
    return destillation_modul.archivieren(os.path.join(DESTILLATION_DIR, kennung), ort)

def destillation_export(kennung, body):
    d = destillation_detail(kennung)
    ziel = os.path.join(TRAINING_DIR, "daten", "destillat_%s_%s" % (re.sub(r"[^\w-]+", "_", d["auftrag"]["name"])[:40],
                                                                   time.strftime("%Y%m%d_%H%M")))
    labels = destillation_modul.TRAINIERBAR + (("SILVER",) if body.get("mit_silber") else ())
    return destillation_modul.exportieren(os.path.join(DESTILLATION_DIR, kennung), ziel, labels)

def _dienst_bereit(dienst):
    """Hat der mlx_lm-Server fertig geladen? Vorher würde eine Anfrage lange hängen."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/v1/models" % dienst["port"], timeout=0.5) as r:
            return r.status == 200
    except Exception:
        return False

def training_uebersicht():
    wb = trainings_werkbank()
    return dict(wb.lage(), **wb.finden(), laeufe=wb.liste(), fabrik=fabrik_stand(), pruefungen=pruefungen(),
                rollen=rollen(), dienste=[dict(d, bereit=_dienst_bereit(d)) for d in wb.dienste()],
                python_einstellung=get_setting("TRAINING_PYTHON", ""),
                werkzeuge={k: get_setting(k, "") for k in ("LLAMA_CPP_DIR", "LLAMA_QUANTIZE", "OLLAMA_BIN")},
                bewertete_laeufe=sum(1 for l in werkbank_laeufe(500) if l.get("bewertung") == "gut"))

def _rezept_modul():
    sys.path.insert(0, BASE_DIR)
    import rezept
    return rezept


def rezept_uebersicht():
    """Vorlagen und was gerade als Basis und Daten zur Verfügung steht."""
    r = _rezept_modul()
    wb = trainings_werkbank()
    gefunden = wb.finden()
    return {"vorlagen": r.vorlagen_liste(),
            "modelle": [m["name"] for m in gefunden["modelle"]],
            "datensaetze": [d["name"] for d in gefunden["datensaetze"]],
            "formate": list(r.FORMATE),
            "beispiel": r.vorlage_lesen("werkbank")}


def rezept_vorlage(name):
    r = _rezept_modul()
    return {"name": name, "text": r.vorlage_lesen(name)}


def _rezept_aus_body(body):
    r = _rezept_modul()
    text = str(body.get("text") or "").strip()
    if not text:
        raise ValueError("Kein Rezept übergeben.")
    daten = r.yaml_lesen(text) if not text.startswith("{") else json.loads(text)
    return r, r.pruefen(daten)


def rezept_plan(body):
    r, rez = _rezept_aus_body(body)
    wb = trainings_werkbank()
    p = r.plan(rez, wb)
    return {"rezept": p["rezept"], "modell": p["modell"], "befund": p["befund"],
            "datensatz": p["datensatz"], "automatik": p["automatik"], "tempo": p["tempo"],
            "bericht": r.bericht_text(p)}


def rezept_starten(body):
    r, rez = _rezept_aus_body(body)
    wb = trainings_werkbank()
    ergebnis = r.starten(rez, wb)
    return {"lauf": ergebnis["lauf"], "daten": ergebnis["daten"],
            "bericht": r.bericht_text(ergebnis["plan"])}


def training_daten_exportieren(body):
    """Trainingsdaten aus Werkbank-Läufen: gut bewertete Dive-on-Wide-Läufe und —
    wenn gewünscht — gelöste Prüfstand-Verläufe (nur die freigegebene Hälfte)."""
    sys.path.insert(0, os.path.join(BASE_DIR, "pruefstand"))
    import trajektorien
    teil = body.get("pruefstand_teil") or None
    if teil not in (None, "gerade", "ungerade"):
        raise ValueError("pruefstand_teil ist „gerade“, „ungerade“ oder leer.")
    name = re.sub(r"[^\w-]+", "-", str(body.get("name") or time.strftime("werkbank_%Y%m%d_%H%M"))).strip("-")
    ziel = os.path.join(TRAINING_DIR, "daten", name or "werkbank")
    if os.path.exists(os.path.join(ziel, "train.jsonl")):
        raise ValueError("Einen Datensatz „%s“ gibt es schon." % name)

    def quellen():
        yield from trajektorien.dowos_laeufe(WERKBANK_DIR)
        # Gelöste Übungsaufgaben der Fabrik: nicht im Prüfstand, also ohne Leck.
        if os.path.isdir(LOESUNGEN_DIR):
            for n in sorted(os.listdir(LOESUNGEN_DIR)):
                if n.endswith("_verlaeufe"):
                    yield from trajektorien.stufe2_laeufe(os.path.join(LOESUNGEN_DIR, n))
        if teil:
            ergebnisse = os.path.join(BASE_DIR, "pruefstand", "ergebnisse")
            if os.path.isdir(ergebnisse):
                for n in sorted(os.listdir(ergebnisse)):
                    if n.endswith("_verlaeufe"):
                        yield from trajektorien.stufe2_laeufe(os.path.join(ergebnisse, n))
    erg = trajektorien.exportieren(quellen(), ziel, teil)
    erg["pfad"] = ziel
    if not erg["beispiele"]:
        shutil.rmtree(ziel, ignore_errors=True)
    return erg

# ----------------------------------------------------------------------------
# MCP — fremde Werkzeuge für die Werkbank (mcp.py)
# ----------------------------------------------------------------------------

import mcp as mcp_client

MCP_DATEI = os.path.join(STORAGE_DIR, "mcp.json")
MCP_VERDECKT = "••••••"

def mcp_konfig():
    try:
        with open(MCP_DATEI, encoding="utf-8") as f:
            return mcp_client.konfiguration_pruefen(json.load(f))
    except (OSError, ValueError):
        return {"mcpServers": {}}

def mcp_uebersicht():
    """Konfiguration für die Oberfläche — Werte in env verdeckt, sie sind oft Zugangsschlüssel."""
    k = mcp_konfig()
    for s in k["mcpServers"].values():
        s["env"] = {n: MCP_VERDECKT for n in s["env"]}
        if "headers" in s:
            s["headers"] = {n: MCP_VERDECKT for n in s["headers"]}
    return dict(k, datei=MCP_DATEI, hinweis="MCP-Server laufen außerhalb der Sandbox mit deinen Rechten. "
                "Die Werkbank fragt vor jedem Aufruf, außer ein Server ist als vertraut markiert.")

def mcp_speichern(daten):
    neu = mcp_client.konfiguration_pruefen(daten)
    alt = mcp_konfig()["mcpServers"]
    for name, s in neu["mcpServers"].items():
        for feld in ("env", "headers"):
            for n, v in list(s.get(feld, {}).items()):
                if v == MCP_VERDECKT:             # unverändert zurückgeschickt: alten Wert behalten
                    if n in alt.get(name, {}).get(feld, {}):
                        s[feld][n] = alt[name][feld][n]
                    else:
                        raise ValueError("Server „%s“: Wert für %s fehlt." % (name, n))
    tmp = MCP_DATEI + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(neu, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, MCP_DATEI)
    return mcp_uebersicht()

def werkbank_gedaechtnis_lage(ordner=None):
    g = werkbank_gedaechtnis.Gedaechtnis(WERKBANK_DIR)
    return {"global": g.notizen(None), "projekt": g.notizen(ordner) if ordner else [], "ordner": ordner}

def werkbank_skills(ordner=None):
    return {"skills": list(agentskills.finden(ordner, SKILLS_DIR).values()), "ordner": SKILLS_DIR,
            "projektorte": list(agentskills.PROJEKT_ORTE)}

def werkbank_befehle_lage(ordner=None):
    return {"befehle": list(werkbank_befehle.finden(ordner, BEFEHLE_DIR).values()), "ordner": BEFEHLE_DIR,
            "projektorte": list(werkbank_befehle.PROJEKT_ORTE), "nutzerorte": list(werkbank_befehle.NUTZER_ORTE)}

def werkbank_befehl(name):
    pfad = os.path.join(BEFEHLE_DIR, name + ".md")
    if not os.path.isfile(pfad):
        raise LookupError("Befehl „%s“ gibt es in Dive on Wide nicht." % name)
    with open(pfad, encoding="utf-8") as f:
        felder, rumpf = agentskills.frontmatter(f.read())
    return {"name": name, "beschreibung": felder.get("description", ""), "inhalt": rumpf.strip()}

def werkbank_skill(name):
    s = agentskills.finden(None, SKILLS_DIR).get(name)
    if not s:
        raise LookupError("Skill „%s“ gibt es nicht." % name)
    with open(os.path.join(s["ordner"], "SKILL.md"), encoding="utf-8") as f:
        felder, rumpf = agentskills.frontmatter(f.read())
    return dict(s, inhalt=rumpf.strip())

def werkbank_skill_entwurf(lauf_id):
    """Hermes-Gedanke: aus gelungener Arbeit wird eine wiederverwendbare Anleitung.
    Nur ein Entwurf — gespeichert wird erst, was der Nutzer geprüft hat."""
    spur = werkbank_spur(lauf_id)
    e = spur.get("ergebnis") or {}
    if e.get("beendet") != "fertig":
        raise ValueError("Nur aus abgeschlossenen Läufen entsteht ein Skill.")
    schritte = "\n".join("%d. %s — %s → %s" % (v["schritt"], v.get("werkzeug"), (v.get("gedanke") or "")[:160],
                                                (v.get("ergebnis") or "").replace("\n", " ")[:160])
                          for v in e.get("verlauf", [])[:40])
    text = llm_chat_once(spur.get("modell") or get_setting("WERKBANK_MODELL", "") or get_setting("DEFAULT_MODEL"),
                         [{"role": "system", "content": agentskills.ENTWURF_PROMPT},
                          {"role": "user", "content": "Aufgabe: %s\n\nSchritte:\n%s\n\nZusammenfassung: %s"
                                                      % (spur.get("aufgabe", ""), schritte, e.get("zusammenfassung", ""))}],
                         temperature=0.3, no_think=True)
    return agentskills.entwurf_lesen(text)

COMMIT_PROMPT = ("Schreibe eine Git-Commit-Nachricht für diese Änderungen: erste Zeile höchstens 60 Zeichen im "
                 "Imperativ, dann eine Leerzeile und 1–4 Zeilen, warum. Nur die Nachricht, kein Codeblock.")

def werkbank_commit(lauf_id, nachricht=""):
    """Übernimmt genau die Änderungen eines Laufs als Git-Commit.

    `git commit -- <pfade>` nimmt nur diese Dateien — was der Nutzer sonst schon
    vorgemerkt hat, bleibt unberührt. Ist eine dieser Dateien seit dem Lauf
    anders geworden, wird trotzdem ihr jetziger Stand committet; der Diff davor
    zeigt ihn."""
    spur = werkbank_spur(lauf_id)
    ordner = spur.get("ordner") or ""
    aend = (spur.get("ergebnis") or {}).get("aenderungen") or {}
    pfade = sorted(set(aend.get("neu", []) + aend.get("geaendert", []) + aend.get("geloescht", [])))
    if not os.path.isdir(os.path.join(ordner, ".git")):
        raise ValueError("Das Projekt ist kein Git-Repository.")
    if not pfade:
        raise ValueError("Der Lauf hat nichts verändert — nichts zu committen.")
    def git(*argv, eingabe=None):
        p = subprocess.run(["git"] + list(argv), cwd=ordner, capture_output=True, text=True, input=eingabe, timeout=60)
        if p.returncode != 0:
            raise ValueError("git %s: %s" % (argv[0], (p.stderr or p.stdout).strip()[:400]))
        return p.stdout
    diff = git("diff", "HEAD", "--", *[p for p in pfade if os.path.exists(os.path.join(ordner, p))] or ["."])
    if not nachricht.strip():
        nachricht = llm_chat_once(spur.get("modell") or get_setting("WERKBANK_MODELL", "") or get_setting("DEFAULT_MODEL"),
                                  [{"role": "system", "content": COMMIT_PROMPT},
                                   {"role": "user", "content": "Aufgabe: %s\n\nDiff:\n%s" % (spur.get("aufgabe", ""), diff[:20000])}],
                                  temperature=0.2, no_think=True).strip().strip("`").strip()
    if not nachricht:
        raise ValueError("Keine Commit-Nachricht.")
    git("add", "-A", "--", *pfade)
    git("commit", "-q", "-F", "-", "--", *pfade, eingabe=nachricht + "\n\nErstellt mit dem Werkbank-Diver von Dive on Wide (Lauf %s)\n" % lauf_id)
    return {"ok": True, "commit": git("rev-parse", "--short", "HEAD").strip(), "nachricht": nachricht, "dateien": pfade}

# ----------------------------------------------------------------------------
# Telegram — Dive on Wide vom Handy aus (telegram_bote.py)
# ----------------------------------------------------------------------------

import telegram_bote
import discord_bote
import slack_bote

# Jeder Bote spricht dieselben Befehle (/werkbank, /status, /abbrechen, /koppeln);
# nur Verbindung und Knöpfe unterscheiden sich. Einstellungen: <PRÄFIX>_AKTIV,
# _TOKEN, _CHATS, _PROJEKT, _API (und DISCORD_GATEWAY für Tests).
BOTEN = {
    "telegram": {"klasse": telegram_bote.TelegramBote, "name": "Telegram", "api": "https://api.telegram.org"},
    "discord": {"klasse": discord_bote.DiscordBote, "name": "Discord", "api": "https://discord.com/api/v10"},
    "slack": {"klasse": slack_bote.SlackBote, "name": "Slack", "api": "https://slack.com/api"},
}
_boten = {p: {"bote": None, "gesehen": {}} for p in BOTEN}
_telegram = _boten["telegram"]
_boten_beobachter = {"faden": None}

def _b(plattform, schluessel):
    return plattform.upper() + "_" + schluessel

def boten_lage(plattform):
    bote, name = _boten[plattform]["bote"], BOTEN[plattform]["name"]
    try:
        gespeichert = json.loads(get_setting(_b(plattform, "CHATS"), "[]") or "[]")
    except ValueError:
        gespeichert = []
    return {"aktiv": get_setting(_b(plattform, "AKTIV"), "0") == "1",
            "token_gesetzt": bool(get_setting(_b(plattform, "TOKEN"), "")),
            "app_token_gesetzt": bool(get_setting(_b(plattform, "APP_TOKEN"), "")) if plattform == "slack" else None,
            "laeuft": bool(bote), "chats": sorted(bote.chats) if bote else gespeichert,
            "projekt": get_setting(_b(plattform, "PROJEKT"), plattform), "fehler": bote.letzter_fehler if bote else "",
            "hinweis": "Nachrichten, Aufträge und Ergebnisse laufen über die Server von %s. Nur gekoppelte "
                       "Chats werden bedient; Werkbank-Aufträge laufen mit Rechtestufe „projekt“ und fragen vor jedem Befehl."
                       % name}

def telegram_lage():
    return boten_lage("telegram")

def _bote_chat(plattform, chat_id, text):
    """Normale Nachricht: Antwort des Standardmodells, gespeichert in einem eigenen Chat je Unterhaltung."""
    schluessel = _b(plattform, "SITZUNG_%s" % chat_id)
    sid = get_setting(schluessel, "")
    conn = db()
    if not sid or not conn.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
        sid = nid()
        conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (sid, BOTEN[plattform]["name"], "", now(), now()))
        conn.commit()
        set_setting(schluessel, sid)
    verlauf = rows(conn.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT 20", (sid,)))
    conn.execute("INSERT INTO messages(id,session_id,role,content,model,created_at) VALUES(?,?,?,?,?,?)", (nid(), sid, "user", text, "", now()))
    conn.commit()
    conn.close()
    nachrichten = [{"role": m["role"], "content": m["content"]} for m in reversed(verlauf)] + [{"role": "user", "content": text}]
    modell = get_setting("DEFAULT_MODEL")
    antwort = llm_chat_once(modell, nachrichten, no_think=True)
    post_to_session(sid, antwort, modell)
    return antwort

def _bote_werkbank(plattform, chat_id, auftrag):
    if get_setting("SANDBOX_ENABLED", "0") != "1":
        raise RuntimeError("Code-Ausführung ist in Dive on Wide abgeschaltet.")
    projekt = get_setting(_b(plattform, "PROJEKT"), plattform) or plattform
    ordner = werkbank_ordner({"ordner": projekt} if os.path.isabs(os.path.expanduser(projekt)) else {"workspace": projekt})
    run_id = run_begin("code", "Werkbank (%s): %s" % (BOTEN[plattform]["name"], auftrag[:60]))
    background(run_werkbank, auftrag, ordner, "", "projekt", "befehle", werkbank.MAX_SCHRITTE, None, run_id)
    return run_id

def _boten_status():
    conn = db()
    laufend = rows(conn.execute("SELECT id, title, updated_at FROM runs WHERE status='running' ORDER BY created_at DESC LIMIT 10"))
    conn.close()
    return "\n".join("▶ %s (%s)" % (r["title"], r["id"]) for r in laufend)

def _boten_beobachten():
    """Freigaben und Ergebnisse der Läufe, die ein Bote gestartet hat, dorthin melden."""
    while True:
        for zustand in _boten.values():
            bote = zustand["bote"]
            if not bote:
                continue
            for run_id in list(bote.laeufe):
                conn = db()
                r = conn.execute("SELECT status, pending, result FROM runs WHERE id=?", (run_id,)).fetchone()
                conn.close()
                if not r:
                    continue
                try:
                    if r["status"] == "running" and r["pending"] and zustand["gesehen"].get(run_id) != r["pending"]:
                        zustand["gesehen"][run_id] = r["pending"]
                        bote.lauf_melden(run_id, wartet_auf=r["pending"])
                    elif r["status"] != "running":
                        zustand["gesehen"].pop(run_id, None)
                        bote.lauf_melden(run_id, ergebnis={"done": "✅", "warn": "⚠️", "cancelled": "⏹"}.get(r["status"], "⚠️")
                                         + " " + (r["result"] or "(kein Ergebnis)"))
                except Exception:
                    pass
        time.sleep(1.5)

def boten_starten(plattform):
    """(Neu-)Start nach den Einstellungen. Ohne Token oder ausgeschaltet: nichts."""
    zustand = _boten[plattform]
    alt = zustand["bote"]
    if alt:
        alt.stoppen()
        zustand["bote"] = None
    token = get_setting(_b(plattform, "TOKEN"), "")
    if get_setting(_b(plattform, "AKTIV"), "0") != "1" or not token:
        return boten_lage(plattform)
    try:
        chats = json.loads(get_setting(_b(plattform, "CHATS"), "[]") or "[]")
    except ValueError:
        chats = []
    art = BOTEN[plattform]
    extra = {"gateway": get_setting("DISCORD_GATEWAY", "") or None} if plattform == "discord" else {}
    if plattform == "slack":
        extra = {"app_token": get_setting("SLACK_APP_TOKEN", "")}
        if not extra["app_token"]:
            return boten_lage(plattform)
    bote = art["klasse"](
        token, {"chat": lambda c, t: _bote_chat(plattform, c, t), "werkbank": lambda c, a: _bote_werkbank(plattform, c, a),
                "status": _boten_status, "abbrechen": run_cancel, "freigabe": lambda rid, ok: run_confirm(rid, ok)},
        api_basis=get_setting(_b(plattform, "API"), art["api"]), chats=chats,
        chats_speichern=lambda c: set_setting(_b(plattform, "CHATS"), json.dumps(c)), **extra)
    bote.starten(frist=int(get_setting(_b(plattform, "FRIST"), "50")))
    zustand["bote"] = bote
    if not _boten_beobachter["faden"]:
        _boten_beobachter["faden"] = threading.Thread(target=_boten_beobachten, daemon=True)
        _boten_beobachter["faden"].start()
    return boten_lage(plattform)

def telegram_starten():
    return boten_starten("telegram")


# ------------------------------------------------------------------ Discord-Server-Chat
import discord_server  # noqa: E402

# Minimale Rechte: Kanäle sehen, schreiben, Verlauf lesen, öffentliche und private Threads anlegen und darin
# schreiben, Nachrichten verwalten (die Frage aus dem Kanal in den privaten Chat verschieben)
DISCORD_SERVER_RECHTE = (1 << 10) | (1 << 11) | (1 << 13) | (1 << 16) | (1 << 35) | (1 << 36) | (1 << 38)


def _discord_server_konfig():
    try:
        return discord_server.konfig_pruefen(json.loads(get_setting("DISCORD_SERVER_KONFIG", "{}") or "{}"))
    except ValueError:
        return discord_server.konfig_pruefen({})


def _discord_server_api(methode, verb="GET", token=None):
    token = token or get_setting("DISCORD_SERVER_TOKEN", "")
    req = urllib.request.Request("%s/%s" % (get_setting("DISCORD_SERVER_API", "https://discord.com/api/v10"), methode),
                                 method=verb, headers={"Authorization": "Bot " + token,
                                                       "User-Agent": "DiscordBot (https://github.com/daiams2000-hash/dive-on-wide, 1)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"null")


def _discord_server_werkbank(kanal, auftrag):
    if get_setting("SANDBOX_ENABLED", "0") != "1":
        raise RuntimeError("Code-Ausführung ist in Dive on Wide abgeschaltet.")
    if not auftrag:
        raise RuntimeError("Welcher Auftrag? Beispiel: /werkbank Behebe den Fehler in rechnen.py")
    projekt = get_setting("DISCORD_SERVER_PROJEKT", "discord-server") or "discord-server"
    ordner = werkbank_ordner({"ordner": projekt} if os.path.isabs(os.path.expanduser(projekt)) else {"workspace": projekt})
    run_id = run_begin("code", "Werkbank (Discord-Server): %s" % auftrag[:60])
    background(run_werkbank, auftrag, ordner, "", "projekt", "befehle", werkbank.MAX_SCHRITTE, None, run_id)
    return run_id


# ------------------------------------------------------------------ Lotse
import lotse  # noqa: E402

_lotse_suche = {}


def lotse_lage(frage=""):
    """Was der Lotse über diesen Rechner und diese Installation wissen muss — ohne Modell erhoben."""
    conn = db()
    zaehle = lambda t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    try:
        chats, wissen, plaene = zaehle("sessions"), zaehle("knowledge"), zaehle("zeitplan")
    finally:
        conn.close()
    try:
        modelle = llm_list_models(ohne_netz=True)
    except Exception:
        modelle = []
    namen = [m["name"].split(MODEL_SEP)[-1] for m in modelle]
    hw = hardware_lage()
    lage = {"version": DOWOS_VERSION, "hardware": hw, "modelle": namen, "ollama_ok": bool(modelle),
            "standardmodell": get_setting("DEFAULT_MODEL", ""), "werkbank_modell": get_setting("WERKBANK_MODELL", ""),
            "empfehlungen": hardware.empfehlungen(hw, installiert=namen), "guete": modell_guete(),
            "erfahrung": ERFAHRUNG.zusammenfassung(), "num_ctx": get_setting("NUM_CTX", "16384"),
            "chats": chats, "wissen": wissen, "zeitplaene": plaene,
            "schalter": {"Code ausführen": get_setting("SANDBOX_ENABLED", "0") == "1",
                         "Computer-Steuerung": get_setting("COMPUTER_USE_ENABLED", "0") == "1",
                         "Discord-Server-Chat": get_setting("DISCORD_SERVER_AKTIV", "0") == "1",
                         "Mesh": bool(_mesh_zustand.get("knoten"))}}
    lage["gedacht"] = [(b, hardware.empfehlungen(h)) for b, h in lotse.gedachter_rechner(frage)]
    return lage


LOTSE_FAQ = os.path.join(STORAGE_DIR, "faq_eigen.md")


def lotse_fragen(frage, verlauf=(), modell=None, sprache=""):
    englisch = lotse.ist_englisch(frage)                       # welche Doku: nach der Sprache der Frage
    ui_en = sprache == "en" if sprache else englisch           # Knöpfe und Prüfbericht: nach der Oberfläche
    # Neu aufbauen, sobald sich die Doku ändert (Update, neue FAQ) — sonst antwortet der Lotse aus altem Stand
    stand = (lotse.doku_stand(BASE_DIR), os.path.getmtime(LOTSE_FAQ) if os.path.exists(LOTSE_FAQ) else 0)
    if _lotse_suche.get((englisch, "stand")) != stand:
        _lotse_suche[englisch] = lotse.Suche(lotse.dokumente(BASE_DIR, englisch=englisch, extra=[LOTSE_FAQ]))
        _lotse_suche[(englisch, "stand")] = stand
    treffer = _lotse_suche[englisch].finden(frage, 4)
    modell = modell or get_setting("LOTSE_MODELL", "") or get_setting("WERKBANK_MODELL", "") or get_setting("DEFAULT_MODEL", "")
    lage = lotse_lage(frage)
    if lotse.ist_allgemeine_stoerung(frage):
        # „Etwas funktioniert nicht“: erst selbst nachsehen, dann gezielt nachfragen — ohne Modell, ohne Raten.
        knoepfe = [{"aktion": p["aktion"], "label": p["label"]} for p in lotse.probleme(lage, ui_en)]
        if not (lage.get("schalter") or {}).get("Code ausführen"):
            knoepfe.append({"aktion": "einstellung:Code-Sandbox",
                            "label": "Switch on code execution" if ui_en else "Code-Ausführung einschalten"})
        return {"antwort": lotse.diagnose(lage, ui_en), "modell": "", "quellen": [], "aktionen": knoepfe[:4]}
    antwort = llm_chat_once(modell, lotse.nachrichten(frage, lage, treffer, verlauf), no_think=True, timeout=300)
    antwort = re.sub(r"<think>.*?</think>\s*", "", str(antwort or ""), flags=re.S).strip()
    # Früher landeten unbeantwortete Fragen in einer Liste, deren Antworten der NUTZER eintragen sollte (07.10.2026:
    # „was ist das für ein Quatsch“). Jetzt: ehrlich sagen und einen Weg anbieten — Frage an die Entwicklung.
    beantwortet = not lotse.unbeantwortet(antwort, treffer)
    knoepfe = lotse.aktionen(frage, lage, ui_en, beantwortet)
    if re.search(r"funktioniert nicht|geht nicht|fehler|kaputt|klappt nicht|doesn.t work|not working|error|broken", frage, re.I):
        knoepfe = [{"aktion": p["aktion"], "label": p["label"]} for p in lotse.probleme(lage, ui_en)] + knoepfe
    return {"antwort": re.sub(r"\n*Quellen:.*$", "", antwort, flags=re.S).strip(), "modell": modell,
            "aktionen": knoepfe[:4], "quellen": ["%s — %s" % (t["quelle"], t["titel"]) for t in treffer]}


def discord_server_starten():
    zustand = _boten.setdefault("discord_server", {"bote": None, "gesehen": {}})
    if zustand["bote"]:
        zustand["bote"].stoppen()
        zustand["bote"] = None
    token = get_setting("DISCORD_SERVER_TOKEN", "")
    if get_setting("DISCORD_SERVER_AKTIV", "0") != "1" or not token:
        return discord_server_lage()
    bote = discord_server.ServerChat(
        token, {"antworten": lambda modell, nachrichten: llm_chat_once(modell, nachrichten, no_think=True, timeout=600),
                "modelle": lambda: [m["name"] for m in llm_list_models(ohne_netz=True)],
                "werkbank": _discord_server_werkbank, "status": _boten_status, "abbrechen": run_cancel,
                "freigabe": lambda rid, ok: run_confirm(rid, ok)},
        _discord_server_konfig(), api_basis=get_setting("DISCORD_SERVER_API", "https://discord.com/api/v10"),
        gateway=get_setting("DISCORD_SERVER_GATEWAY", "") or None,
        wahl_datei=os.path.join(STORAGE_DIR, "discord_modellwahl.json"))
    bote.starten()
    zustand["bote"] = bote
    if not _boten_beobachter["faden"]:
        _boten_beobachter["faden"] = threading.Thread(target=_boten_beobachten, daemon=True)
        _boten_beobachter["faden"].start()
    return discord_server_lage()


def discord_server_lage():
    bote = (_boten.get("discord_server") or {}).get("bote")
    k = _discord_server_konfig()
    return {"aktiv": get_setting("DISCORD_SERVER_AKTIV", "0") == "1",
            "token_gesetzt": bool(get_setting("DISCORD_SERVER_TOKEN", "")),
            "laeuft": bool(bote), "fehler": bote.letzter_fehler if bote else "", "hinweis": bote.hinweis if bote else "",
            "beantwortet": bote.beantwortet if bote else 0, "konfig": k,
            "projekt": get_setting("DISCORD_SERVER_PROJEKT", "discord-server"),
            "client_id": get_setting("DISCORD_SERVER_CLIENT_ID", ""),
            "einladung": ("https://discord.com/oauth2/authorize?client_id=%s&permissions=%d&scope=bot%%20applications.commands"
                          % (get_setting("DISCORD_SERVER_CLIENT_ID", ""), DISCORD_SERVER_RECHTE))
            if get_setting("DISCORD_SERVER_CLIENT_ID", "") else ""}


def discord_server_speichern(body):
    """Token prüfen (wer ist der Bot?), Einstellungen säubern, Bot neu starten."""
    token = str(body.get("token") or "").strip()
    if token:
        try:
            ich = _discord_server_api("users/@me", token=token)
        except Exception as e:
            raise ValueError("Discord kennt diesen Token nicht (%s)." % str(e)[:120])
        set_setting("DISCORD_SERVER_TOKEN", token)
        set_setting("DISCORD_SERVER_CLIENT_ID", str(ich.get("id") or ""))
    if isinstance(body.get("konfig"), dict):
        alt = _discord_server_konfig()
        neu = discord_server.konfig_pruefen(dict(alt, **body["konfig"]))
        if neu["modus"] == "privat" and not neu["voll_nutzer"]:
            raise ValueError("Privater Modus braucht mindestens eine eingetragene Discord-Nutzer-ID.")
        set_setting("DISCORD_SERVER_KONFIG", json.dumps(neu))
    if "projekt" in body:
        set_setting("DISCORD_SERVER_PROJEKT", str(body["projekt"]).strip() or "discord-server")
    if "aktiv" in body:
        set_setting("DISCORD_SERVER_AKTIV", "1" if body["aktiv"] else "0")
    return discord_server_starten()


def discord_server_kanaele():
    """Textkanäle aller Server, in denen der Bot ist — zum Auswählen in den Einstellungen."""
    aus = []
    for g in _discord_server_api("users/@me/guilds") or []:
        for c in _discord_server_api("guilds/%s/channels" % g["id"]) or []:
            if c.get("type") in (0, 5):
                aus.append({"id": c["id"], "name": c.get("name", ""), "server": g.get("name", "")})
    return aus

def boten_speichern(plattform, body):
    if "token" in body and str(body["token"]).strip():
        set_setting(_b(plattform, "TOKEN"), str(body["token"]).strip())
    if plattform == "slack" and str(body.get("app_token") or "").strip():
        set_setting("SLACK_APP_TOKEN", str(body["app_token"]).strip())
    if "projekt" in body:
        set_setting(_b(plattform, "PROJEKT"), str(body["projekt"]).strip() or plattform)
    if "chats" in body:
        set_setting(_b(plattform, "CHATS"), json.dumps([BOTEN[plattform]["klasse"].kennung(c) for c in body["chats"]]))
    if "aktiv" in body:
        set_setting(_b(plattform, "AKTIV"), "1" if body["aktiv"] else "0")
    return boten_starten(plattform)

REGELN_BEISPIEL = {
    "erlauben": ["ausfuehren(python -m unittest*)", "ausfuehren(pytest*)"],
    "verbieten": ["lesen(.env)", "lesen(*.pem)", "ausfuehren(rm -rf*)", "ausfuehren(git push*)"],
    "hooks": {"nach_aenderung": [{"befehl": "python -m py_compile {pfad}", "dateien": "*.py"}],
              "vor_fertig": [{"befehl": "python -m unittest discover -s tests -t ."}]},
}

def werkbank_regeln_lage():
    return {"regeln": get_setting("WERKBANK_REGELN", ""), "beispiel": REGELN_BEISPIEL,
            "projektdatei": werkbank_regeln.PROJEKT_DATEI}

def werkbank_regeln_speichern(text):
    text = (text or "").strip()
    if text:
        try:
            werkbank_regeln.Regeln.aus_json(json.loads(text))
        except ValueError as e:
            raise ValueError("Regeln nicht gespeichert: %s" % e)
    set_setting("WERKBANK_REGELN", text)
    return werkbank_regeln_lage()

def werkbank_checkpunkte(ordner):
    return {"ordner": ordner, "laeuft": ordner in _werkbank_aktiv,
            "checkpunkte": checkpunkte.Checkpunkte(CHECKPUNKTE_DIR, ordner).liste()}

def werkbank_checkpunkt(ordner, kennung):
    cp = checkpunkte.Checkpunkte(CHECKPUNKTE_DIR, ordner)
    daten = cp.laden(kennung)
    daten.pop("dateien", None)
    return {"ordner": ordner, "checkpunkt": daten, "seit": cp.seit(kennung), "diff": cp.diff(kennung)}

def werkbank_zuruecksetzen(ordner, kennung):
    with _werkbank_lock:
        if ordner in _werkbank_aktiv:
            raise RuntimeError("Hier arbeitet gerade ein Werkbank-Lauf — erst abbrechen oder abwarten.")
        _werkbank_aktiv[ordner] = "zuruecksetzen"
    try:
        erg = checkpunkte.Checkpunkte(CHECKPUNKTE_DIR, ordner).zuruecksetzen(kennung)
    finally:
        with _werkbank_lock:
            _werkbank_aktiv.pop(ordner, None)
    emit("Werkbank: zurückgesetzt ↶", "%s auf „%s“ — %d Dateien wiederhergestellt, %d entfernt."
         % (ordner, erg["ziel"]["beschreibung"][:80], len(erg["wiederhergestellt"]), len(erg["entfernt"])),
         "system")
    return erg

# ----------------------------------------------------------------------------
# Meta-Creator — KI erstellt Skills & Agenten (Konzept: agent-skill-creator)
# ----------------------------------------------------------------------------

META_SKILL_PROMPT = (
    "Du bist die Skill-Fabrik von Dive on Wide. Erzeuge aus der Beschreibung des Nutzers "
    "einen mehrstufigen Skill. Antworte AUSSCHLIESSLICH mit gültigem JSON nach diesem "
    "Schema (2-4 Schritte, deutsch): "
    '{"name":"…","trigger_word":"kleinbuchstaben-ohne-leerzeichen","description":"…",'
    '"steps":[{"name":"…","system_prompt":"präziser System-Prompt für diesen Schritt"}]}')

META_AGENT_PROMPT = (
    "Du bist die Agenten-Fabrik von Dive on Wide. Erzeuge aus der Beschreibung des Nutzers "
    "eine KI-Persona. Antworte AUSSCHLIESSLICH mit gültigem JSON nach diesem Schema "
    '(deutsch): {"name":"…","emoji":"ein Emoji","description":"1 Satz",'
    '"system_prompt":"ausführlicher System-Prompt, der Rolle, Tonfall, Vorgehen und '
    'Output-Format definiert"}')

def _json_kandidaten(text):
    """Findet ausgewogene {...}-Blöcke — Klammern zählen statt gierig greifen.

    Das alte `\\{[\\s\\S]*\\}` nahm vom ERSTEN `{` bis zum LETZTEN `}`. Sobald ein
    Modell zwei JSON-Blöcke schreibt oder vor dem JSON laut nachdenkt und dabei
    eine Klammer benutzt, spannt der Treffer über beides und ist ungültig.
    Anführungszeichen und Escapes werden mitgezählt, damit ein `}` im Text
    nicht fälschlich als Ende gilt."""
    kandidaten = []
    tiefe = 0
    start = -1
    im_text = False
    escaped = False
    for i, c in enumerate(text):
        if im_text:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                im_text = False
            continue
        if c == '"':
            im_text = True
        elif c == "{":
            if tiefe == 0:
                start = i
            tiefe += 1
        elif c == "}":
            if tiefe > 0:
                tiefe -= 1
                if tiefe == 0 and start >= 0:
                    kandidaten.append(text[start:i + 1])
    return kandidaten

def extract_json_or_none(text):
    """Wie extract_json, wirft aber nicht — für Stellen, die weiterlaufen sollen."""
    for roh in _json_kandidaten(text or ""):
        try:
            return json.loads(roh)
        except ValueError:
            continue
    return None

def extract_json(text):
    wert = extract_json_or_none(text)
    if wert is None:
        raise ValueError("Kein JSON in der Modellantwort gefunden.")
    return wert

# ----------------------------------------------------------------------------
# Backup — kompletter Storage als ZIP
# ----------------------------------------------------------------------------

def build_backup_zip():
    """Packt den gesamten Speicher — und ueberlebt es, wenn er sich dabei bewegt.

    Zwei Dinge, die vorher fehlten und erst auffielen, als der Rhythmus alle
    20 Sekunden die Datenbank anfasste:

    1. SQLite legt im WAL-Modus die Dateien `-wal` und `-shm` an und raeumt sie
       jederzeit wieder weg. `os.walk` sammelt die Namen, `z.write` oeffnet sie
       gleich darauf — verschwindet eine dazwischen, brach das GANZE Backup mit
       einem Fehler ab. Deshalb wird vorher ein Checkpoint erzwungen: Danach
       stecken alle bestaetigten Aenderungen in der .db selbst, und die beiden
       fluechtigen Dateien werden ausgelassen. Es geht dabei nichts verloren.
    2. Eine einzelne unlesbare Datei darf das Backup nicht wertlos machen. Sie
       wird uebersprungen und am Ende benannt — ein unvollstaendiges Backup mit
       Vermerk ist mehr wert als gar keins."""
    import io
    import zipfile
    # Alles Bestaetigte in die .db schreiben, damit -wal entbehrlich wird.
    try:
        conn = db()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass                      # ohne Checkpoint eben mit den WAL-Dateien
    uebersprungen = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(STORAGE_DIR):
            for fn in files:
                if fn.endswith(("-wal", "-shm")):
                    continue      # fluechtig, nach dem Checkpoint entbehrlich
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, STORAGE_DIR)
                try:
                    z.write(fp, rel)
                except (FileNotFoundError, PermissionError, OSError):
                    uebersprungen.append(rel)
        # Der Hinweis reist MIT der Datei. Eine Warnung, die nur in der
        # Oberflaeche steht, ist beim Weitergeben des ZIP nicht mehr da — und
        # weitergegeben wird so eine Datei, etwa zum Umzug auf einen neuen
        # Rechner oder zur Fehlersuche. Nachgesehen am 23.09.2026: `strings`
        # auf der Datenbank im Backup zeigt Zugangsschluessel und
        # API-Schluessel im Klartext.
        z.writestr("ZUERST-LESEN.txt",
                   "Diese Sicherung enthält deine Zugangsschlüssel und alle\n"
                   "eingetragenen API-Schlüssel IM KLARTEXT — sie muss das, sonst\n"
                   "stellt sie den Zugang nicht wieder her.\n\n"
                   "Wer diese Datei hat, hat damit Zugriff auf dein Dive on Wide und auf\n"
                   "die Dienste, deren Schlüssel darin stehen.\n\n"
                   "Also: nicht in einen Chat hängen, nicht in ein Ticket, nicht in\n"
                   "einen geteilten Ordner. Wenn sie doch einmal aus der Hand war:\n"
                   "Einstellungen → Zugänge → Schlüssel widerrufen, und die\n"
                   "API-Schlüssel beim jeweiligen Anbieter neu ausstellen.\n")
        if uebersprungen:
            z.writestr("UEBERSPRUNGEN.txt",
                       "Diese Dateien waren beim Sichern nicht lesbar und "
                       "fehlen in diesem Backup:\n\n"
                       + "\n".join(uebersprungen) + "\n")
    return buf.getvalue()

# ----------------------------------------------------------------------------
# Einrichtung — was beim ersten Start EINMAL gefragt wird
# ----------------------------------------------------------------------------
# Grundhaltung: Nichts, was den Rechner, das Netz oder die eigenen Daten
# anfasst, ist von sich aus an. Kein Vorgabewert entscheidet etwas, das ein
# Mensch entscheiden sollte.
#
# Die Einrichtung fragt genau das ab, was sonst als stille Voreinstellung
# wirken wuerde — und sie zeigt dabei ehrlich, was ueberhaupt verfuegbar ist,
# statt Auswahlmoeglichkeiten anzubieten, die auf diesem Rechner gar nicht
# funktionieren.

# Alles, was ausdruecklich freigeschaltet werden muss. Der Wert ist der
# Zustand, den ein frisch eingerichtetes Dive on Wide hat, wenn der Mensch nichts
# ankreuzt: aus.
# Verstaendliche Namen fuer die Meldung. „Eingeschaltet: SANDBOX_ENABLED"
# sagt niemandem, was er gerade erlaubt hat.
SCHALTER_KLARTEXT = {
    "SANDBOX_ENABLED": "Code ausführen",
    "BRAIN_AUTOSYNC": "Chats ins Wissen übernehmen",
    "COMPUTER_USE_ENABLED": "Maus und Tastatur steuern",
    "ALLOW_LOCAL_FETCH": "interne Adressen abrufen",
}

SCHALTER = {
    "SANDBOX_ENABLED": "0",       # fuehrt erzeugten Code auf diesem Rechner aus
    "BRAIN_AUTOSYNC": "0",        # traegt Chats automatisch ins Wissen ein
    "COMPUTER_USE_ENABLED": "0",  # bewegt Maus und Tastatur
    "ALLOW_LOCAL_FETCH": "0",     # darf interne Adressen abrufen
}


_katalog_zwischen = {"schluessel": None, "zeit": 0.0, "daten": []}


def _ollama_steckbrief(name, basis=None):
    """Was Ollama selbst ueber ein Modell weiss: Faehigkeiten, Familie, Groesse."""
    try:
        d = ollama_json("/api/show", {"model": name}, timeout=8, base=basis)
    except Exception:
        return {}
    info = d.get("model_info") or {}
    kontext = next((v for k, v in info.items() if k.endswith("context_length")), None)
    det = d.get("details") or {}
    return {"faehigkeiten": list(d.get("capabilities") or []),
            "familie": det.get("family") or "",
            "parameter": det.get("parameter_size") or "",
            "kontext": kontext}


def speicher_stufe(groesse_bytes, ram_gib):
    """„passt", „knapp" oder „zu_gross" — geeicht an dem, was hier WIRKLICH geschah.

    Die Regel „hoechstens halber Arbeitsspeicher" ist fuer den Vorschlag in der
    Einrichtung gedacht, nicht als Verbot. Fuer die Grafikkarte zaehlt, was
    Metal hoechstens belegen darf: auf Apple Silicon etwa drei Viertel des
    Arbeitsspeichers. Gegen beide echten Faelle auf diesem Rechner (24 GiB)
    geprueft: qwen3.8:27b (16,9 GiB) loeste am 17.09.2026 die Metal-Speichernot
    aus → zu_gross; qwen3.6-35b-a3b (15,7 GiB) lief wochenlang als
    Lehrermodell → knapp. Mit der Halb-Regel allein haette der Orchestrator ein
    Modell gemieden, das nachweislich laeuft."""
    if not groesse_bytes or not ram_gib:
        return None
    gib = groesse_bytes / (1024 ** 3)
    if gib * 1.2 <= ram_gib / 2:
        return "passt"
    if gib * 1.1 <= ram_gib * 0.75:
        return "knapp"
    return "zu_gross"


# ---------------------------------------------------------------------------
# Fremde Gerüste (Claude Code, Codex …): Fragebogen und Profil
# ---------------------------------------------------------------------------
# Der fremde Agent plant und prüft, Dive on Wide führt lokal aus — so kostet eine Aufgabe
# den fremden Agenten nur Planung und Prüfung, nicht das Ausprobieren. Welche
# lokalen Modelle das tun, fragt der fremde Agent den Nutzer einmal (Fragebogen)
# und legt es hier ab. Rechte, Freigaben und Ausgang bleiben Sache des Besitzers
# in der Oberfläche: Ein Gerüst kann sich nicht selbst mehr erlauben.
EXTERN_PROFIL_VORGABE = {"arbeiter": "", "pruefer": "", "max_schritte": 30, "rueckmeldung": "knapp",
                         "antworten": {}}


def extern_profil():
    try:
        p = json.loads(get_setting("EXTERN_PROFIL", "") or "{}")
    except ValueError:
        p = {}
    return dict(EXTERN_PROFIL_VORGABE, **{k: v for k, v in p.items() if k in EXTERN_PROFIL_VORGABE})


def extern_rechenprofile(katalog=None):
    """Drei Vorschläge aus DIESER Hardware und den Modellen, die wirklich da sind."""
    # Modelle mit entfernten Schranken („abliterated“, „uncensored“) schlägt Dive on Wide nie von
    # selbst vor — wählen darf der Nutzer sie weiter (Profiltest 01.10.2026).
    katalog = [e for e in (katalog if katalog is not None else modell_katalog())
               if e.get("speicher") != "zu_gross" and e.get("groesse") and not e.get("geraete")
               and not re.search(r"abliterat|uncensor|aggressive", e.get("name", ""), re.I)]
    passt = sorted((e for e in katalog if e.get("speicher") in ("passt", None)), key=lambda e: e["groesse"])
    knapp = sorted((e for e in katalog if e.get("speicher") == "knapp"), key=lambda e: e["groesse"])
    guete = modell_guete()

    def geloest(e):
        g = guete.get(e.get("label"))
        return g.get("geloest", -1) if isinstance(g, dict) and g.get("aufgaben") else -1
    klein = next((e for e in passt if e["groesse"] >= 1.5e9), passt[0] if passt else None)
    gross_passt = passt[-1] if passt else None
    stark = max(passt + knapp, key=lambda e: (geloest(e), e["groesse"])) if passt + knapp else None
    def name(e):
        return e["name"] if e else ""
    return {
        "sparsam": {"arbeiter": name(klein), "pruefer": name(klein),
                    "text": "Zwei Aufgaben für ein kleines Modell: eines arbeitet, dasselbe prüft danach. "
                            "Wenig Speicher, schnell, für klar umrissene Aufgaben."},
        "ausgewogen": {"arbeiter": name(gross_passt), "pruefer": name(klein),
                       "text": "Das größte Modell, das bequem passt, arbeitet; ein kleines prüft."},
        "stark": {"arbeiter": name(stark), "pruefer": name(klein),
                  "text": "Das hier am besten gemessene Modell arbeitet (darf knapp passen — dann große "
                          "Programme daneben schließen); ein kleines prüft."},
    }


def extern_fragebogen():
    hw = hardware_lage()
    return {
        "zweck": "Einmal den Nutzer fragen, wie Dive on Wide für dich arbeiten soll. Antworten mit "
                 "POST /api/extern/profil ablegen. Rechte, Freigaben und was nach draußen geht, "
                 "stellt nur der Besitzer in der Oberfläche ein.",
        "rechner": {"ram_gib": hw.get("ram_gib"), "rechnet_auf": hw.get("rechnet_auf"),
                    "modell_speicher_gib": hw.get("modell_speicher_gib")},
        "profile": extern_rechenprofile(),
        "fragen": [
            {"id": "rolle", "frage": "Wer macht was?",
             "optionen": {"planer": "Du (fremder Agent) planst und prüfst, Dive on Wide führt lokal aus — spart deine Tokens (empfohlen)",
                          "selbst": "Du arbeitest selbst, Dive on Wide nur für Tests und Nebenaufgaben"}},
            {"id": "profil", "frage": "Wie viel Rechenleistung darf Dive on Wide nutzen?",
             "optionen": {k: v["text"] for k, v in extern_rechenprofile().items()}},
            {"id": "pruefer", "frage": "Soll ein zweites lokales Modell jedes Ergebnis vorab prüfen, bevor es zu dir zurückkommt?",
             "optionen": {"ja": "Ja — du liest nur noch Befunde statt ganzer Protokolle", "nein": "Nein, schneller"}},
            {"id": "rueckmeldung", "frage": "Wie ausführlich soll die Rückmeldung sein?",
             "optionen": {"knapp": "Ergebnis, geänderte Dateien, Prüfung (empfohlen)", "voll": "mit jedem Schritt"}},
            {"id": "projekte", "frage": "In welchen Projekten (Workspaces) darfst du Aufträge geben?", "frei": True},
        ],
        "grenzen_jetzt": {"rechte": werkbank.STANDARD_STUFE, "freigabe": werkbank.STANDARD_FREIGABE,
                          "code_ausfuehren": get_setting("SANDBOX_ENABLED", "0") == "1"},
        "profil_jetzt": extern_profil(),
    }


def extern_profil_speichern(daten):
    namen = {m.get("name") for m in llm_list_models()}
    neu = extern_profil()
    for rolle in ("arbeiter", "pruefer"):
        if rolle in daten:
            wert = str(daten.get(rolle) or "").strip()
            if wert and wert not in namen:
                raise ValueError("Das Modell „%s“ gibt es hier nicht." % wert[:80])
            neu[rolle] = wert
    if "max_schritte" in daten:
        try:
            neu["max_schritte"] = max(1, min(int(daten["max_schritte"]), 40))
        except (TypeError, ValueError):
            raise ValueError("max_schritte muss eine Zahl zwischen 1 und 40 sein.")
    if "rueckmeldung" in daten:
        if daten["rueckmeldung"] not in ("knapp", "voll"):
            raise ValueError("rueckmeldung ist „knapp“ oder „voll“.")
        neu["rueckmeldung"] = daten["rueckmeldung"]
    if isinstance(daten.get("antworten"), dict):
        neu["antworten"] = {str(k)[:40]: str(v)[:400] for k, v in list(daten["antworten"].items())[:20]}
    set_setting("EXTERN_PROFIL", json.dumps(neu, ensure_ascii=False))
    return neu


_extern_pruefungen = {}      # Auftrag → Lauf des Prüfers


def extern_auftrag_ausfuehren(aufgabe, ordner, profil, schritte, run_id):
    """Arbeiter, danach (wenn gewünscht) ein zweites Modell, das nur liest und prüft."""
    run_werkbank(aufgabe, ordner, profil.get("arbeiter", ""), werkbank.STANDARD_STUFE,
                 werkbank.STANDARD_FREIGABE, schritte, None, run_id, [], None,
                 False, False, False, [], None, None, None)
    if not profil.get("pruefer"):
        return
    pruef_id = run_begin("code", "Werkbank (Prüfung): " + aufgabe[:50], None)
    _extern_pruefungen[run_id] = pruef_id
    # Klar getrennt: Ein 4B las „Behebe beides …“ als eigenen Auftrag, durfte nichts ändern
    # und stellte deshalb eine Rückfrage statt zu prüfen (Profiltest, 30.09.2026).
    run_werkbank("Du bist PRÜFER. Ein anderer Agent hat den Auftrag unten bereits bearbeitet. Du änderst nichts "
                 "und fragst nicht nach: Lies die betroffenen Dateien, führe vorhandene Tests aus und melde mit "
                 "fertig „Erfüllt: ja“ oder „Erfüllt: nein“ und höchstens fünf Befunde (Datei, was, warum).\n\n"
                 "--- Auftrag an den anderen Agenten (schon erledigt) ---\n" + aufgabe +
                 "\n--- Ende des Auftrags ---", ordner, profil["pruefer"], "lesen", "nie", 15, None,
                 pruef_id, [], None, False, False, False, [], None, None, None)


def modell_katalog():
    """Alle Modelle mit dem, was man zum Waehlen wissen muss.

    Der Orchestrator bekam bis zum 25.09.2026 nur Namen und Anbieter zu sehen:
    „- gemma4-finetuned:latest (Provider: Ollama)". Ob ein Modell Bilder sieht,
    Code ergaenzt, in den Speicher passt oder ueberhaupt gross ist, musste er
    aus dem Namen raten — und dass er dabei Namen erfindet, belegt die eigene
    Korrekturstufe weiter unten. Ollama liefert all das per /api/show. Hier
    kommt es zusammen, fuer zehn Minuten zwischengespeichert: Die Liste aendert
    sich selten, und der Orchestrator soll nicht bei jedem Plan zwoelfmal
    nachfragen."""
    modelle = llm_list_models()
    # Cloud-Modelle wählt der Orchestrator (und der Schwarm-Planer) nie von selbst — sonst ginge Arbeit ungefragt
    # nach draußen, nur weil irgendwo ein Cloud-Anbieter eingetragen ist. Ausnahme: ausdrücklich erlaubt, oder
    # der Mensch hat genau dieses Modell als Standard bzw. Orchestrator-Modell gewählt (07.10.2026).
    if get_setting("ORCHESTRATOR_CLOUD", "0") != "1":
        gewaehlt = {get_setting("DEFAULT_MODEL", ""), get_setting("ORCHESTRATOR_MODELL", "")}
        modelle = [m for m in modelle if not m.get("extern") or m.get("name") in gewaehlt]
    # Dasselbe für Modelle auf ANDEREN Geräten im Diving Net: Der Orchestrator auf dem Windows-PC nahm von selbst
    # das große Modell des Macs — langsam, und der Auftrag geht ungefragt auf ein anderes Gerät (09.10.2026).
    # Wer es will, wählt es ausdrücklich.
    if get_setting("ORCHESTRATOR_NETZ", "0") != "1":
        gewaehlt = {get_setting("DEFAULT_MODEL", ""), get_setting("ORCHESTRATOR_MODELL", "")}
        modelle = [m for m in modelle if m.get("provider_id") != MESH_PROVIDER_ID or m.get("name") in gewaehlt]
    schluessel = tuple(sorted(m.get("name", "") for m in modelle))
    jetzt = time.time()
    z = _katalog_zwischen
    if z["schluessel"] == schluessel and jetzt - z["zeit"] < 600:
        return [_absturz_beachten(e) for e in z["daten"]]
    ram = arbeitsspeicher_gib()
    aus = []
    for m in modelle:
        prov_url = (get_provider(m.get("provider_id") or "") or {}).get("base_url", "")
        hier_ram = None if adresse_entfernt(prov_url) else ram
        e = {"name": m.get("name", ""), "label": m.get("label") or m.get("name", ""),
             "anbieter": m.get("provider", ""), "groesse": m.get("size"),
             "passt": modell_passt(m.get("size"), hier_ram),
             "speicher": speicher_stufe(m.get("size"), hier_ram), "geraete": m.get("geraete"),
             "entfernt": hier_ram is None and ram is not None}
        if m.get("provider_id") == "ollama" or (m.get("size") and not m.get("geraete")):
            prov = get_provider(m.get("provider_id") or "") or {}
            e.update(_ollama_steckbrief(e["label"], prov.get("base_url")))
        aus.append(e)
    z.update(schluessel=schluessel, zeit=jetzt, daten=aus)
    return [_absturz_beachten(e) for e in aus]


def modell_abgestuerzt(label):
    """Der Grund, falls dieses Modell HIER unter Last aus dem Speicher lief — sonst None."""
    g = modell_guete().get(label)
    return (g.get("absturz") or None) if isinstance(g, dict) else None


def _absturz_beachten(e):
    """Ein gemessener Absturz schlaegt jede Groessenregel.

    qwen3.8-27B in 3 Bit (12,2 GiB) gilt nach der Groesse als „knapp, passt
    allein" und bestand am 25.09.2026 sogar eine Probe mit 12 000 Token. In
    einem echten Werkbank-Lauf ueber Ollama legte llama.cpp aber je Schritt
    Zwischenstaende von 150 MiB an, dazu einen Zwischenspeicher bis 8 GiB —
    nach 30 Aufgaben war der Speicher voll, macOS beendete das Modell, dann
    Ollama. Die Groesse der Datei sagt das nicht voraus; nur die Messung."""
    grund = modell_abgestuerzt(e.get("label"))
    if not grund:
        return e
    return dict(e, speicher="zu_gross", passt=False, absturz=grund)


_guete_zwischen = {"mtime": None, "daten": {}}


def modell_guete():
    """Gemessene Werkbank-Guete je Modell — nur, was auf DIESEM Rechner gemessen wurde.

    Der Katalog sagt, was ein Modell kann. Er sagt nicht, wie gut es das hier
    tut, und genau das entscheidet: Im Vergleichslauf vom 25.09.2026 loeste
    qwen3.6-35b 42 von 72 Werkbank-Aufgaben, qwen2.5-coder 17 — beide sehen im
    Katalog gleichwertig aus. Die Zahlen stehen in storage/modell_guete.json,
    eingetragen von werkzeuge/guete_eintragen.py aus vollstaendigen Messungen.
    Fehlt die Datei (jeder fremde Rechner), steht im Katalog eben nichts —
    eine erfundene Guete waere schlimmer als keine."""
    pfad = os.path.join(STORAGE_DIR, "modell_guete.json")
    try:
        mtime = os.path.getmtime(pfad)
    except OSError:
        return {}
    if _guete_zwischen["mtime"] != mtime:
        try:
            with open(pfad, encoding="utf-8") as f:
                daten = json.load(f)
            _guete_zwischen.update(mtime=mtime, daten=daten if isinstance(daten, dict) else {})
        except (OSError, ValueError):
            _guete_zwischen.update(mtime=mtime, daten={})
    return _guete_zwischen["daten"]


def katalog_text(katalog, guete=None):
    """Eine Zeile je Modell, in Worten, die ein Planer benutzen kann.

    `guete` ersetzt die Messwerte dieses Rechners — fuer den Pruefstand des
    Hausmodells, der Kataloge fremder Rechner nachbildet."""
    zeilen = []
    guete = modell_guete() if guete is None else guete
    for e in katalog:
        f = set(e.get("faehigkeiten") or [])
        teile = [e["label"]]
        if e.get("parameter"):
            teile.append(e["parameter"])
        if e.get("groesse"):
            teile.append("%.1f GB" % (e["groesse"] / (1024 ** 3)))
        if e.get("absturz"):
            teile.append("PASST NICHT: lief hier unter Last aus dem Speicher — nicht waehlen")
        elif e.get("speicher") == "zu_gross":
            teile.append("PASST NICHT in den Speicher — nicht waehlen")
        elif e.get("speicher") == "knapp":
            # Nicht mehr „hoechstens fuer einen Schritt". Das hielt den Planer
            # am 25.09.2026 vom besten Modell fern: qwen3.6-35b loeste im
            # Vergleichslauf 42/72 und war am schnellsten, der Planer nahm mit
            # dieser Warnung fast nur noch gemma4:12b. Die Begruendung war auch
            # falsch — Schritte laufen nacheinander, Ollama entlaedt, wenn das
            # naechste Modell Platz braucht. Gefaehrlich ist nur, was ALLEIN
            # nicht passt, und das heisst weiterhin „PASST NICHT".
            teile.append("gross, laedt langsamer, passt aber allein")
        if "insert" in f or "coder" in e["label"].lower():
            teile.append("Code")
        if "vision" in f:
            teile.append("sieht Bilder")
        if "thinking" in f:
            teile.append("denkt gruendlich")
        if e.get("kontext"):
            teile.append("Kontext %dk" % (int(e["kontext"]) // 1024))
        if e.get("geraete"):
            teile.append("im Netz, auf %s Geraet(en)" % e["geraete"])
        g = guete.get(e["label"])
        if isinstance(g, dict) and g.get("aufgaben"):
            teile.append("Werkbank-Pruefung hier: %d/%d geloest, %.1f min je Aufgabe"
                         % (g.get("geloest", 0), g["aufgaben"], g.get("min_je_aufgabe", 0)))
        zeilen.append("- " + " · ".join(teile))
    return "\n".join(zeilen) or "(keine Modelle gefunden)"


def einrichtung_noetig():
    return get_setting("EINRICHTUNG_FERTIG", "") != "1"


def arbeitsspeicher_gib():
    """Arbeitsspeicher dieses Rechners in GiB — None, wo nicht ermittelbar."""
    if MESH_DA:
        try:
            gesamt, _ = _mesh.ressourcen.speicher()
            if gesamt:
                return gesamt / (1024 ** 3)
        except Exception:
            pass
    try:
        return (os.sysconf("SC_PAGE_SIZE")
                * os.sysconf("SC_PHYS_PAGES")) / (1024 ** 3)
    except Exception:
        return None


def adresse_entfernt(url):
    """Laeuft ein Anbieter auf einem ANDEREN Rechner? Dann laedt das Modell dort,
    und der Speicher HIER sagt nichts ueber „passt“.

    28.09.2026, Verbrauchertest in der Linux-VM (2,8 GB, Ollama auf dem Mac):
    Der Katalog maß die Modelle des Macs am Speicher der VM, alles bis auf
    qwen2.5:0.5b galt als „passt nicht“ — und modellwahl_pruefen setzte fuer
    die ganze Roadmap das 0,5-B-Modell durch."""
    wirt = (urllib.parse.urlparse(url or "").hostname or "").lower()
    return bool(wirt) and wirt not in ("localhost", "127.0.0.1", "::1")


def modell_passt(groesse_bytes, ram_gib):
    """Laesst dieses Modell auf diesem Rechner noch Luft?

    Faustregel: Ein Modell belegt etwa seine Dateigroesse plus ein Fuenftel
    fuer den Kontext. Mehr als die Haelfte des Arbeitsspeichers darf das nicht
    sein — der Rest gehoert dem Betriebssystem und dem, was der Mensch sonst
    offen hat. Wird die Grenze gerissen, holt sich macOS die Seiten zurueck
    und Ollama antwortet mit HTTP 500 (Metal OOM).

    Ohne Groessenangabe oder ohne bekannten Arbeitsspeicher wird nichts
    behauptet (None) — eine geratene Warnung ist schlimmer als keine."""
    if not groesse_bytes or not ram_gib:
        return None
    return (groesse_bytes / (1024 ** 3)) * 1.2 <= ram_gib / 2


def modell_vorschlag(eintraege):
    """Das groesste Modell, das noch passt — sonst das kleinste ueberhaupt.

    Vorher waehlte die Einrichtung stillschweigend den ERSTEN Eintrag der
    Ollama-Liste. Auf diesem Rechner war das ein 27-B-Modell mit 17 GiB
    Bedarf bei 24 GiB Speicher: Der erste Satz eines neuen Nutzers waere mit
    einem Speicherfehler beantwortet worden."""
    passend = [e for e in eintraege if e.get("passt")]
    if passend:
        return max(passend, key=lambda e: e.get("groesse") or 0)["name"]
    mit_groesse = [e for e in eintraege if e.get("groesse")]
    if mit_groesse:
        return min(mit_groesse, key=lambda e: e["groesse"])["name"]
    return eintraege[0]["name"] if eintraege else ""


import hardware
_hardware_zwischen = {"zeit": 0, "daten": None}


def hardware_lage():
    """Der Scan dauert auf dem Mac 1–2 s (system_profiler) — zehn Minuten merken."""
    if not _hardware_zwischen["daten"] or time.time() - _hardware_zwischen["zeit"] > 600:
        _hardware_zwischen.update(zeit=time.time(), daten=hardware.scan())
    return _hardware_zwischen["daten"]


MODELL_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._/:-]{1,120}$", re.I)


def modell_laden(tag, run_id):
    """Laedt ein Modell ueber Ollamas /api/pull, mit Fortschritt im Lauf.

    Nur Modelle aus modellempfehlungen.json — ein Knopf in der Einrichtung
    soll nicht beliebige Dateien aus dem Netz holen koennen."""
    basis = get_setting("OLLAMA_BASE_URL", "http://localhost:11434")
    req = urllib.request.Request(ollama_url("/api/pull", basis), data=json.dumps({"model": tag, "stream": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    letzte = -10
    try:
        with urllib.request.urlopen(req, timeout=3600) as r:
            for zeile in r:
                if not zeile.strip():
                    continue
                d = json.loads(zeile)
                if d.get("error"):
                    raise RuntimeError(d["error"])
                if d.get("total") and d.get("completed") is not None:
                    prozent = int(100 * d["completed"] / d["total"])
                    if prozent >= letzte + 10:
                        letzte = prozent
                        run_step(run_id, "%s: %d %% von %.1f GB" % (tag, prozent, d["total"] / 1e9), "active")
                elif d.get("status") and d["status"] not in ("pulling manifest",):
                    run_step(run_id, d["status"][:80], "active")
        _katalog_zwischen.update(schluessel=None, zeit=0)
        run_finish(run_id, "done", "%s ist geladen." % tag)
        emit("Modell geladen \U0001F4E6", "%s steht jetzt zur Wahl." % tag)
    except Exception as e:
        run_finish(run_id, "error", "Laden von %s fehlgeschlagen: %s" % (tag, str(e)[:300]))


def einrichtung_lage(pruef_url=None):
    """Was auf DIESEM Rechner tatsaechlich zur Wahl steht.

    `pruef_url`: eine Ollama-Adresse, die der Besitzer gerade eintippt — sie
    wird nur geprueft, nicht gespeichert. Ohne das blieb das Modellfeld nach
    einer geaenderten Adresse ein leeres Textfeld (27.09.2026, erste
    Einrichtung in einer Linux-VM mit Ollama auf dem Gastgeber).

    Bewusst mit echter Pruefung statt einer Liste von Moeglichkeiten: Wer
    Ollama nicht laufen hat, soll das hier sehen und nicht spaeter an einer
    kryptischen Fehlermeldung."""
    aus = {"noetig": einrichtung_noetig(), "schalter": {}}
    for k, standard in SCHALTER.items():
        aus["schalter"][k] = get_setting(k, standard) == "1"
    # Modelle: fragen, nicht raten.
    aus["ollama_url"] = pruef_url or get_setting("OLLAMA_BASE_URL", "http://localhost:11434")
    ram = arbeitsspeicher_gib()
    aus["ram_gib"] = round(ram, 1) if ram else None
    try:
        if pruef_url:
            gefunden = [{"name": "ollama" + MODEL_SEP + m["name"], "label": m["name"], "size": m.get("size")}
                        for m in (ollama_json("/api/tags", timeout=5, base=pruef_url).get("models") or [])][:60]
        else:
            gefunden = llm_list_models()[:60]
        aus["ollama_da"] = True
    except Exception as e:
        gefunden = []
        aus["ollama_da"] = False
        aus["ollama_fehler"] = str(e)[:160]
    # Die Modellliste verschluckt Verbindungsfehler und liefert dann einfach
    # nichts. Die Einrichtung las das als „Ollama antwortet, nur ohne Modell“
    # und riet zu „ollama pull“ — auf einem Linux ohne Ollama (27.09.2026,
    # erster Lauf in einer VM). Deshalb hier ausdruecklich nachfragen.
    if aus["ollama_da"] and not gefunden:
        try:
            ollama_json("/api/version", timeout=4, base=aus["ollama_url"])
        except Exception as e:
            aus["ollama_da"] = False
            aus["ollama_fehler"] = str(e)[:160]
    # 403: Da IST ein Ollama, es lehnt nur den Rechnernamen ab (Ollama prüft den
    # Host-Kopf, solange es nur auf 127.0.0.1 hört). „Kein Ollama“ schickte den
    # Nutzer in die falsche Richtung (Windows-VM über einen Tunnel, 29.09.2026).
    if not aus["ollama_da"]:
        aus["ollama_grund"] = "verweigert" if "403" in str(aus.get("ollama_fehler", "")) else "keine_antwort"
    aus["modelle"] = [m["name"] for m in gefunden]
    # Ollama auf einem ANDEREN Rechner (Lima-VM → Mac, LAN-Server): Das Modell
    # laedt dort, der Speicher HIER sagt nichts. In der VM (3,8 GB) wurde jedes
    # Modell „zu gross“, obwohl der Mac mit 24 GB es rechnet (27.09.2026).
    aus["ollama_entfernt"] = adresse_entfernt(aus["ollama_url"])
    if aus["ollama_entfernt"]:
        aus["ram_gib"] = None
    aus["modell_liste"] = [{
        "name": m["name"],
        "label": m.get("label") or m["name"],
        "groesse": m.get("size"),
        "gib": round(m["size"] / (1024 ** 3), 1) if m.get("size") else None,
        "passt": (None if aus["ollama_entfernt"] else modell_passt(m.get("size"), ram))
                 and not modell_abgestuerzt(m.get("label") or m["name"]),
    } for m in gefunden]
    # Der gespeicherte Standard steht ohne Anbieter-Vorsatz in der Datenbank
    # ("qwen2.5-coder:14b"), die Liste nennt ihn mit ("ollama@@qwen2.5-coder:14b").
    # Ohne diesen Abgleich fand die Auswahlliste ihren eigenen Wert nicht
    # wieder und zeigte den ersten Eintrag — wer dann "Fertig" drueckte,
    # ueberschrieb seinen Standard mit einem fremden Modell.
    gesetzt = get_setting("DEFAULT_MODEL", "")
    namen = [m["name"] for m in gefunden]
    if gesetzt and gesetzt not in namen:
        passend = [n for n in namen if n.split(MODEL_SEP)[-1] == gesetzt]
        if passend:
            gesetzt = passend[0]
        else:
            gesetzt = ""
    # Vor der ersten Einrichtung ist der Standard nur der Wert aus .env.example —
    # keine Wahl des Nutzers. Er wurde bisher als „empfohlen“ vorausgewaehlt,
    # auch wenn das Modell auf diesem Rechner gar nicht passte.
    if aus["noetig"]:
        gesetzt = ""
    aus["standardmodell"] = gesetzt
    aus["empfehlung"] = gesetzt or ("" if aus["ollama_entfernt"] else modell_vorschlag(aus["modell_liste"]))
    # Die Einrichtung soll nicht pauschal von macOS reden, wenn jemand auf
    # Linux sitzt. Sie fragt die Plattform-Rueckseite, wie dieses System heisst.
    aus["plattform_text"] = _schirm().beschreibung
    # Hardware-Scan und passende Modelle (auch nicht installierte) — nur, wenn
    # Ollama auf DIESEM Rechner laeuft; sonst entscheidet der andere Rechner.
    try:
        aus["hardware"] = hardware_lage()
        aus["vorschlaege"] = None if aus["ollama_entfernt"] else hardware.empfehlungen(aus["hardware"], installiert=namen)
    except Exception as e:
        aus["hardware"], aus["vorschlaege"] = None, None
        aus["hardware_fehler"] = str(e)[:160]
    # Netz: nur anbieten, was der Rechner hergibt.
    if MESH_DA:
        aus["mesh"] = {
            "vorhanden": True,
            "adressen": _mesh.transport.eigene_adressen(),
            "krypto": _mesh.crypto.backend_info(),
            "abgebbar_gb": round(
                _mesh.ressourcen.Statthalter(zustimmung=True).abgebbar()
                / (1024 ** 3), 1),
        }
    else:
        aus["mesh"] = {"vorhanden": False}
    aus["computer"] = computer_info() if MESH_DA or True else {}
    return aus


def einrichtung_speichern(wahl):
    """Uebernimmt die Auswahl. Was nicht ausdruecklich an ist, bleibt aus."""
    gesetzt = []
    modell = (wahl.get("modell") or "").strip()
    if modell:
        set_setting("DEFAULT_MODEL", modell)
        gesetzt.append("Modell")
    url = (wahl.get("ollama_url") or "").strip()
    if url:
        set_setting("OLLAMA_BASE_URL", url)
        gesetzt.append("Ollama-Adresse")
    schalter = wahl.get("schalter") or {}
    for k in SCHALTER:
        an = "1" if schalter.get(k) else "0"
        set_setting(k, an)
        if an == "1":
            gesetzt.append(k)
    # Netz: ausdrueckliche Wahl, sonst gar nicht.
    netz = (wahl.get("netz") or "").strip()
    if netz in ("klause", "weite"):
        gb = wahl.get("netz_gb")
        set_setting("MESH_AUTOSTART", netz if wahl.get("netz_dauerhaft") else "")
        if gb:
            set_setting("MESH_AUTOSTART_GB", str(gb))
        try:
            mesh_starten(netz, gb)
            gesetzt.append("Netzwerk (%s)" % netz)
        except Exception as e:
            emit("Netzwerk konnte nicht starten \u26a0\ufe0f", str(e)[:200])
    # Ein Morgenbriefing anbieten, aber nur wenn gewuenscht.
    if wahl.get("briefing"):
        try:
            zeitplan_anlegen({"name": "Morgenbriefing", "was": "briefing",
                              "art": "taeglich",
                              "uhrzeit": wahl.get("briefing_zeit") or "08:00",
                              "wochentage": "0,1,2,3,4"})
            gesetzt.append("Morgenbriefing")
        except Exception:
            pass
    # Mitlernen: nachts schlägt die Nachtschicht Notizen aus den Läufen vor — übernommen wird nur, was du freigibst
    if wahl.get("lernen"):
        try:
            zeitplan_anlegen({"name": "Nachtschicht (lernen)", "was": "konsolidieren", "art": "taeglich",
                              "uhrzeit": "03:00"})
            set_setting("LERNEN_AKTIV", "1")
            gesetzt.append("Mitlernen")
        except Exception:
            pass
    # Kontext passend zum Rechner — nur, wenn der Nutzer ihn nicht selbst gesetzt hat
    if not get_setting("NUM_CTX", ""):
        try:
            kontext = hardware.empfohlener_kontext(hardware_lage())
            set_setting("NUM_CTX", str(kontext))
            if kontext < 16384:
                gesetzt.append("Kontext %d Token (wenig Speicher)" % kontext)
        except Exception:
            pass
    set_setting("EINRICHTUNG_FERTIG", "1")
    emit("Einrichtung abgeschlossen \u2713",
         ("Eingeschaltet: " + ", ".join(gesetzt)) if gesetzt
         else "Nichts eingeschaltet — alles bleibt aus, bis du es willst.")
    return einrichtung_lage()


# ----------------------------------------------------------------------------
# Rhythmus — der Teil von Dive on Wide, der auch dann arbeitet, wenn niemand zusieht
# ----------------------------------------------------------------------------
# Bis hierher tat Dive on Wide immer nur etwas, wenn jemand tippte. Ein Agent, der
# den Tag mittraegt, muss von selbst aufwachen: morgens den Tag vorbereiten,
# abends nachfassen, stuendlich etwas beobachten.
#
# Bewusst ein EIGENER Zeitgeber statt cron: Ein Eintrag in der Systemtabelle
# ueberlebt das Loeschen des Ordners und laeuft weiter, wenn Dive on Wide gar nicht
# mehr da ist. Das waere genau die Art unsichtbarer Rueckstand, die dieses
# Projekt vermeidet. Was hier laeuft, laeuft nur solange Dive on Wide laeuft.

ARTEN = ("taeglich", "intervall", "einmal")
WAS_ARTEN = ("agent", "skill", "pipeline", "orchestrator", "briefing", "werkbank",
             "konsolidieren")
_rhythmus = {"faden": None, "laeuft": False, "letzte_pruefung": 0,
             "aktive": set()}


def _wochentage_lesen(text):
    """'0,1,2' -> {0,1,2}. Leer heisst: jeden Tag."""
    aus = set()
    for teil in (text or "").split(","):
        teil = teil.strip()
        if teil.isdigit() and 0 <= int(teil) <= 6:
            aus.add(int(teil))
    return aus


def naechster_zeitpunkt(plan, ab=None):
    """Wann ist dieser Eintrag das naechste Mal faellig?

    Gibt einen Zeitstempel zurueck oder 0, wenn nie wieder. Bewusst reine
    Rechnung ohne Seiteneffekt — so laesst sie sich ohne Uhr und ohne
    Datenbank pruefen."""
    ab = time.time() if ab is None else ab
    art = plan.get("art")
    if not plan.get("aktiv", 1):
        return 0
    if art == "intervall":
        minuten = max(1, int(plan.get("intervall_min") or 60))
        letzter = float(plan.get("letzter_lauf") or 0)
        if not letzter:
            return ab                      # noch nie gelaufen: sofort
        return letzter + minuten * 60
    if art in ("taeglich", "einmal"):
        uhr = (plan.get("uhrzeit") or "08:00").strip()
        try:
            stunde, minute = [int(x) for x in uhr.split(":")]
        except Exception:
            stunde, minute = 8, 0
        tage = _wochentage_lesen(plan.get("wochentage"))
        for versatz in range(0, 8):
            kandidat = time.localtime(ab + versatz * 86400)
            ziel = time.mktime((kandidat.tm_year, kandidat.tm_mon, kandidat.tm_mday,
                                stunde, minute, 0, 0, 0, -1))
            if ziel <= ab:
                continue
            if tage and time.localtime(ziel).tm_wday not in tage:
                continue
            if art == "einmal" and float(plan.get("letzter_lauf") or 0) > 0:
                return 0                   # einmal heisst einmal
            return ziel
    return 0


BRIEFING_PROMPT = (
    "Du bereitest ein kurzes Tagesbriefing für den Besitzer dieses Rechners. "
    "Nutze AUSSCHLIESSLICH die unten stehenden Angaben aus dem System. "
    "Erfinde nichts dazu — keine Termine, keine Aufgaben, keine Zahlen, die "
    "dort nicht stehen. Wenn wenig da ist, ist das Briefing eben kurz.\n\n"
    "Schreibe höchstens acht Zeilen: was seit gestern passiert ist, was offen "
    "aussieht, und eine einzige konkrete Anregung für heute.")


def briefing_stoff():
    """Was das System ueber die letzten 24 Stunden weiss — als Text.

    Ausdruecklich nur echte Systemdaten. Ein Briefing, das Termine erfindet,
    waere schlimmer als keins."""
    seit = now() - 86400
    conn = db()
    try:
        laeufe = rows(conn.execute(
            "SELECT kind,title,status FROM runs WHERE created_at>? "
            "ORDER BY created_at DESC LIMIT 15", (seit,)))
        arte = rows(conn.execute(
            "SELECT title FROM artifacts WHERE created_at>? "
            "ORDER BY created_at DESC LIMIT 10", (seit,)))
        ungelesen = conn.execute(
            "SELECT COUNT(*) c FROM notifications WHERE read=0").fetchone()["c"]
        offen = rows(conn.execute(
            "SELECT kind,title FROM runs WHERE status='running' LIMIT 5"))
    finally:
        conn.close()
    teile = ["Läufe der letzten 24 Stunden:"]
    teile += ["  - [%s] %s (%s)" % (l["kind"], (l["title"] or "")[:70], l["status"])
              for l in laeufe] or ["  (keine)"]
    teile.append("\nNeue Artefakte:")
    teile += ["  - " + (a["title"] or "")[:70] for a in arte] or ["  (keine)"]
    teile.append("\nUngelesen in der Inbox: %d" % ungelesen)
    if offen:
        teile.append("\nNoch laufend:")
        teile += ["  - [%s] %s" % (o["kind"], (o["title"] or "")[:70]) for o in offen]
    return "\n".join(teile)


def _zeile(tabelle, satz_id):
    """Einen Datensatz nach Kennung holen. Tabellenname ist NIE Nutzereingabe —
    er kommt hier ausschliesslich aus WAS_ARTEN."""
    if tabelle not in ("skills", "pipelines", "agents"):
        raise ValueError("Unbekannte Tabelle: %r" % tabelle)
    conn = db()
    try:
        r = conn.execute("SELECT * FROM %s WHERE id=?" % tabelle,
                         (satz_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def rhythmus_vorarbeit(plan):
    """Das letzte Ergebnis der Einträge, auf denen dieser aufbaut — als Text für die Eingabe.

    So arbeiten Diver über die Zeit zusammen: Einer recherchiert morgens, ein zweiter lädt das Ergebnis abends und
    widerspricht, ein dritter vergleicht beide (07.10.2026). Jeder sieht nur, was er braucht — kein geteilter Verlauf."""
    teile = []
    for vid in [x.strip() for x in str(plan.get("aufbauen_auf") or "").split(",") if x.strip()][:4]:
        conn = db()
        try:
            v = conn.execute("SELECT name, letzter_lauf_id FROM zeitplan WHERE id=?", (vid,)).fetchone()
            r = conn.execute("SELECT artifact_id, result, updated_at FROM runs WHERE id=?",
                             ((v or {"letzter_lauf_id": ""})["letzter_lauf_id"],)).fetchone() if v else None
            a = conn.execute("SELECT filename FROM artifacts WHERE id=?", (r["artifact_id"],)).fetchone() \
                if r and r["artifact_id"] else None
        finally:
            conn.close()
        if not v:
            teile.append("[Eintrag %s gibt es nicht mehr]" % vid)
            continue
        text = ""
        if a and os.path.exists(os.path.join(ARTIFACTS_DIR, a["filename"])):
            text = open(os.path.join(ARTIFACTS_DIR, a["filename"]), encoding="utf-8", errors="replace").read()
        elif r:
            text = r["result"] or ""
        if not text.strip():
            teile.append("[„%s“ hat noch kein Ergebnis]" % v["name"])
            continue
        wann = time.strftime("%d.%m.%Y %H:%M", time.localtime(r["updated_at"])) if r and r["updated_at"] else "?"
        teile.append("### Ergebnis von „%s“ (%s)\n%s" % (v["name"], wann, text[:12000]))
    return "\n\n".join(teile)


RHYTHMUS_VORGAENGER_FRIST = 2700     # so lange wartet ein Eintrag höchstens auf einen noch laufenden Vorgänger


def rhythmus_vorgaenger_abwarten(plan, run_id, frist=None, takt=10):
    """Läuft ein Eintrag, auf dem dieser aufbaut, gerade noch, wird gewartet — sonst lädt der Diver ein halbes oder
    gar kein Ergebnis (07.10.2026: Diver 2 startete 23:08, Diver 1 lief bis 23:09)."""
    ids = [x.strip() for x in str(plan.get("aufbauen_auf") or "").split(",") if x.strip()]
    ende = time.time() + (RHYTHMUS_VORGAENGER_FRIST if frist is None else frist)
    gemeldet = set()
    while time.time() < ende:
        conn = db()
        try:
            laufend = []
            for vid in ids:
                v = conn.execute("SELECT name, letzter_lauf_id, aktiv, naechster_lauf FROM zeitplan WHERE id=?",
                                 (vid,)).fetchone()
                if not v:
                    continue
                r = conn.execute("SELECT status FROM runs WHERE id=?", (v["letzter_lauf_id"],)).fetchone() \
                    if v["letzter_lauf_id"] else None
                # „Noch nicht fertig“ heißt auch: fällig oder schon in der Warteschlange. Sonst lud ein Nachfolger, der
                # zur selben Minute fällig war und zuerst drankam, das Ergebnis von GESTERN (Stresstest 08.10.2026).
                faellig = v["aktiv"] and 0 < float(v["naechster_lauf"] or 0) <= time.time()
                if (r and r["status"] == "running") or vid in _rhythmus["aktive"] or faellig:
                    laufend.append(v["name"])
        finally:
            conn.close()
        if not laufend:
            return True
        for name in laufend:
            if name not in gemeldet:
                run_step(run_id, "Wartet, bis „%s“ fertig ist …" % name, "active")   # ✕ bricht hier ab
                gemeldet.add(name)
        time.sleep(takt)
    return False


def rhythmus_ausfuehren(plan):
    """Fuehrt einen faelligen Eintrag aus — ueber dieselben Wege wie ein Mensch."""
    run_id = run_begin("rhythmus", "%s: %s" % (plan.get("name") or "Rhythmus", plan.get("was")))
    conn = db()
    conn.execute("UPDATE zeitplan SET letzter_lauf_id=? WHERE id=?", (run_id, plan.get("id")))
    conn.commit()
    conn.close()
    if str(plan.get("aufbauen_auf") or "").strip():
        rhythmus_vorgaenger_abwarten(plan, run_id)
    vorarbeit = rhythmus_vorarbeit(plan)
    if str(plan.get("aufbauen_auf") or "").strip() and "### Ergebnis von" not in vorarbeit:
        # Nie ins Blaue: Ohne Vorarbeit erfand ein 4-B-Diver am 07.10.2026 ein ganz anderes Thema.
        text = "Nicht gestartet — die Einträge, auf denen dieser aufbaut, haben noch kein Ergebnis: " + \
               vorarbeit.replace("\n", " ")[:300]
        run_finish(run_id, "warn", text)
        emit("Rhythmus wartet auf Vorarbeit ⏳", "%s: %s" % (plan.get("name") or "Rhythmus", text))
        return "warn"
    if vorarbeit:
        run_step(run_id, "Baut auf %d früheren Ergebnis(sen) auf" % vorarbeit.count("### Ergebnis von"))
        plan = dict(plan, eingabe=(plan.get("eingabe") or "") + "\n\n---\nGRUNDLAGE — frühere Ergebnisse anderer Diver "
                    "(„Diver“ heißen in Dive on Wide die KI-Assistenten; mit Tauchen hat das nichts zu tun):\n\n" + vorarbeit)
    status = _rhythmus_arbeit(plan, run_id)
    if plan.get("zustellen") in BOTEN:
        rhythmus_zustellen(plan, run_id)
    return status


RHYTHMUS_ZUSTELLUNG = ("", "telegram", "discord", "slack")

def rhythmus_zustellen(plan, run_id):
    """Wie der Cron von Hermes: das Ergebnis dorthin, wo man es liest. Das Artefakt,
    falls es eines gibt, sonst das Ergebnis des Laufs. Nur an gekoppelte Chats."""
    conn = db()
    try:
        lauf = conn.execute("SELECT status, result, artifact_id FROM runs WHERE id=?", (run_id,)).fetchone()
        art = conn.execute("SELECT filename FROM artifacts WHERE id=?", (lauf["artifact_id"],)).fetchone() \
            if lauf and lauf["artifact_id"] else None
    finally:
        conn.close()
    text = (lauf["result"] if lauf else "") or ""
    if art:
        try:
            with open(os.path.join(ARTIFACTS_DIR, art["filename"]), encoding="utf-8") as f:
                text = f.read()
        except OSError:
            pass
    zeichen = {"done": "🕰", "warn": "⚠️"}.get(lauf["status"] if lauf else "", "⚠️")
    plattform = plan.get("zustellen")
    bote, name = _boten[plattform]["bote"], BOTEN[plattform]["name"]
    if not bote or not bote.chats:
        emit("Rhythmus: nicht zugestellt", "„%s“ sollte per %s kommen, aber %s ist nicht verbunden "
             "oder kein Chat gekoppelt (Einstellungen → %s)." % (plan.get("name") or "Eintrag", name, name, name), "error")
        return 0
    n = 0
    for chat in sorted(bote.chats):
        try:
            bote.senden(chat, "%s %s\n\n%s" % (zeichen, plan.get("name") or "Rhythmus", text.strip() or "(ohne Ergebnis)"))
            n += 1
        except Exception as e:                       # ein Chat, der nicht will, hält die anderen nicht auf
            print("Rhythmus-Zustellung an %s: %s" % (chat, e))
    return n


def _rhythmus_arbeit(plan, run_id):
    was, ziel, eingabe = plan.get("was"), plan.get("ziel") or "", plan.get("eingabe") or ""
    # Das im Rhythmus gewählte Modell gilt für alles, was dieser Eintrag startet (07.10.2026: fehlte).
    # Briefing und Nachtschicht trugen ihr Modell früher in „ziel“ — das bleibt als Rückfall.
    modell = (plan.get("modell") or "").strip()
    try:
        if was == "briefing":
            run_step(run_id, "Sammle, was das System weiß …", "active")
            stoff = briefing_stoff()
            text = llm_chat_once(
                modell or ziel or get_setting("DEFAULT_MODEL"),
                [{"role": "system", "content": BRIEFING_PROMPT},
                 {"role": "user", "content": stoff}], temperature=0.3)
            art_id = deliver("Tagesbriefing", text, "briefing.md",
                             "Tagesbriefing \U0001F305", text[:180])
            run_finish(run_id, "done", text[:400], art_id)
            return "done"
        if was == "konsolidieren":
            # Die Nachtschicht: aus den Läufen der letzten Zeit dauerhaftes Wissen
            # machen. Die Zahlen werden gezählt, das Modell darf nur vorschlagen,
            # und übernommen wird nur, wenn der Besitzer es eingeschaltet hat.
            run_step(run_id, "Sehe die letzten Läufe durch …", "active")
            g = werkbank_gedaechtnis.Gedaechtnis(WERKBANK_DIR)
            modell = modell or ziel or get_setting("DEFAULT_MODEL")
            befund = ""
            try:
                import fehlerbuch
                pfade = fehlerbuch.protokolle_finden(
                    os.path.join(WERKBANK_DIR, "protokolle"))
                if pfade:
                    befund = fehlerbuch.bericht_text(fehlerbuch.auswerten(pfade))
            except Exception:
                befund = ""
            uebernehmen = get_setting("KONSOLIDIERUNG_UEBERNEHMEN", "0") == "1"
            erg = konsolidierung.laufen(
                WERKBANK_DIR,
                frage_modell=lambda n: llm_chat_once(modell, n, temperature=0.2,
                                                     no_think=True),
                gedaechtnis=g, uebernehmen=uebernehmen, fehlerbefund=befund,
                melden=lambda t: run_step(run_id, t))
            kurz = ("%d Läufe durchgesehen · %d Vorschläge%s"
                    % (erg["laeufe"], len(erg["angenommen"]),
                       ", %d übernommen" % erg["uebernommen"] if erg["uebernommen"]
                       else " (nicht übernommen)"))
            art_id = deliver("Nachtschicht: Konsolidierung", erg["bericht"],
                             "konsolidierung.md", "Nachtschicht fertig 🌙", kurz)
            run_finish(run_id, "done", kurz, art_id)
            return "done"
        if was == "skill":
            skill = _zeile("skills", ziel)
            if not skill:
                raise ValueError("Skill %r gibt es nicht mehr." % ziel)
            run_skill_pipeline(skill, eingabe, modell or get_setting("DEFAULT_MODEL"),
                               run_id=run_id)
            return "done"
        if was == "pipeline":
            pl = _zeile("pipelines", ziel)
            if not pl:
                raise ValueError("Pipeline %r gibt es nicht mehr." % ziel)
            run_pipeline(pl, eingabe, modell or None, run_id=run_id)
            return "done"
        if was == "orchestrator":
            run_orchestrator(eingabe, run_id=run_id, modell=modell or None)
            return "done"
        if was == "werkbank":
            # Niemand sieht zu: Rechte „projekt“ in der Sandbox, und alles, was eine
            # Freigabe bräuchte, wird abgelehnt statt fünf Minuten zu warten.
            if get_setting("SANDBOX_ENABLED", "0") != "1":
                raise ValueError("Code-Ausführung ist abgeschaltet (Einstellungen → Code-Sandbox).")
            ordner = werkbank_ordner({"ordner": ziel} if os.path.isabs(os.path.expanduser(ziel)) else {"workspace": ziel or "rhythmus"})
            run_werkbank(eingabe, ordner, modell, "projekt", "nie", werkbank.MAX_SCHRITTE, None, run_id, unbeaufsichtigt=True)
            conn = db()
            status = (conn.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone() or {"status": "error"})["status"]
            conn.close()
            return "done" if status in ("done", "warn") else "error"
        if was == "agent":
            agent = _zeile("agents", ziel)
            if not agent:
                raise ValueError("Diver %r gibt es nicht mehr." % ziel)
            run_step(run_id, "Diver %s arbeitet …" % agent.get("name"), "active")
            text = llm_chat_once(
                modell or agent.get("model") or get_setting("DEFAULT_MODEL"),
                [{"role": "system", "content": agent.get("system_prompt", "")},
                 {"role": "user", "content": eingabe}])
            art_id = deliver("%s (Rhythmus)" % agent.get("name"), text,
                             "rhythmus.md", "%s hat gearbeitet \U0001F551"
                             % agent.get("name"), text[:180])
            run_finish(run_id, "done", text[:400], art_id)
            return "done"
        raise ValueError("Unbekannte Art: %r" % was)
    except Exception as e:
        # Ein Rhythmus laeuft, wenn niemand zusieht. Die Meldung wird also
        # Stunden spaeter gelesen — sie muss allein erklaeren, was zu tun ist.
        # „Connection refused" um acht Uhr frueh hilft keinem Menschen.
        run_finish(run_id, "error", rhythmus_fehlertext(e, plan))
        # Der Name steckt schon im Text — nicht doppelt davorsetzen.
        emit("Rhythmus fehlgeschlagen \u26a0\ufe0f", rhythmus_fehlertext(e, plan))
        return "error"


def rhythmus_fehlertext(e, plan):
    """Uebersetzt einen technischen Fehler in etwas Handhabbares."""
    roh = str(e)
    niedrig = roh.lower()
    name = plan.get("name") or "Der Eintrag"
    if "connection refused" in niedrig or "connect" in niedrig and "refus" in niedrig:
        return ("%s konnte kein Modell erreichen — Ollama läuft nicht. "
                "Starten mit: ollama serve. Der Eintrag versucht es beim "
                "nächsten Termin von selbst wieder." % name)
    if ist_zeitablauf(e):
        return ("%s hat zu lange gebraucht und wurde abgebrochen. Meist ist "
                "das Modell zu groß für dieses Gerät — ein kleineres wählen "
                "oder den Abstand vergrößern." % name)
    if "gibt es nicht mehr" in niedrig:
        return ("%s zeigt auf etwas, das gelöscht wurde. Eintrag anpassen "
                "oder entfernen: %s" % (name, roh))
    if isinstance(e, UnbekannterProvider):
        return "%s: %s" % (name, roh)
    return "%s: %s" % (name, roh[:200])


def rhythmus_pruefen(jetzt=None):
    """Ein Durchgang: was ist faellig? Gibt die Zahl der gestarteten Laeufe."""
    jetzt = time.time() if jetzt is None else jetzt
    conn = db()
    try:
        plaene = rows(conn.execute("SELECT * FROM zeitplan WHERE aktiv=1"))
    finally:
        conn.close()
    gestartet = 0
    for plan in plaene:
        faellig = float(plan.get("naechster_lauf") or 0)
        if not faellig:
            faellig = naechster_zeitpunkt(plan, jetzt)
            _zeitplan_setzen(plan["id"], naechster_lauf=faellig)
            continue
        if faellig > jetzt:
            continue
        # Nie zwei Laeufe desselben Eintrags gleichzeitig. Ein haengender
        # Lauf darf sich nicht selbst vervielfachen.
        if plan["id"] in _rhythmus["aktive"]:
            continue
        _rhythmus["aktive"].add(plan["id"])

        def lauf(p=plan):
            try:
                # Auf Vorgänger warten, BEVOR ein Laufplatz belegt wird: Drei wartende Nachfolger auf drei Plätzen
                # ließen sonst dem Vorgänger keinen Platz mehr (Festfahren bis zur Frist).
                if str(p.get("aufbauen_auf") or "").strip():
                    rhythmus_vorgaenger_abwarten(p, None)
                with _run_slots:
                    status = rhythmus_ausfuehren(p)
            except Exception as e:
                status = "error: %s" % e
            finally:
                _rhythmus["aktive"].discard(p["id"])
            frisch = dict(p)
            frisch["letzter_lauf"] = time.time()
            _zeitplan_setzen(p["id"], letzter_lauf=frisch["letzter_lauf"],
                             letzter_status=status,
                             naechster_lauf=naechster_zeitpunkt(frisch))
        threading.Thread(target=lauf, daemon=True).start()
        gestartet += 1
    _rhythmus["letzte_pruefung"] = jetzt
    return gestartet


def _zeitplan_setzen(plan_id, **felder):
    if not felder:
        return
    conn = db()
    try:
        teile = ", ".join("%s=?" % k for k in felder)
        conn.execute("UPDATE zeitplan SET %s WHERE id=?" % teile,
                     tuple(felder.values()) + (plan_id,))
        conn.commit()
    finally:
        conn.close()


def zeitplan_liste():
    conn = db()
    try:
        plaene = rows(conn.execute("SELECT * FROM zeitplan ORDER BY created_at"))
    finally:
        conn.close()
    for p in plaene:
        p["naechster_text"] = (
            time.strftime("%a %d.%m. %H:%M", time.localtime(p["naechster_lauf"]))
            if p.get("naechster_lauf") else "\u2014")
        p["letzter_text"] = (
            time.strftime("%d.%m. %H:%M", time.localtime(p["letzter_lauf"]))
            if p.get("letzter_lauf") else "noch nie")
        p["laeuft_gerade"] = p["id"] in _rhythmus["aktive"]
    return {"plaene": plaene, "takt_laeuft": _rhythmus["laeuft"],
            "letzte_pruefung": _rhythmus["letzte_pruefung"]}


def zeitplan_anlegen(daten):
    art = (daten.get("art") or "taeglich").strip()
    was = (daten.get("was") or "briefing").strip()
    if art not in ARTEN:
        raise ValueError("Unbekannte Art: %r" % art)
    if was not in WAS_ARTEN:
        raise ValueError("Unbekannte Aufgabe: %r" % was)
    name = (daten.get("name") or "").strip()
    if not name:
        raise ValueError("Der Eintrag braucht einen Namen.")
    if was in ("skill", "pipeline", "agent") and not (daten.get("ziel") or "").strip():
        raise ValueError("Für %s muss ausgewählt werden, was laufen soll." % was)
    if was == "werkbank" and not (daten.get("eingabe") or "").strip():
        raise ValueError("Der Werkbank-Diver braucht einen Auftrag.")
    if was == "orchestrator" and not (daten.get("eingabe") or "").strip():
        raise ValueError("Der Orchestrator braucht eine Beschreibung des Ziels.")
    plan = {
        "id": nid(), "name": name[:80], "art": art, "was": was,
        "ziel": (daten.get("ziel") or "")[:400],
        "eingabe": (daten.get("eingabe") or "")[:2000],
        "uhrzeit": (daten.get("uhrzeit") or "08:00")[:5],
        "intervall_min": max(1, min(int(daten.get("intervall_min") or 60), 10080)),
        "wochentage": (daten.get("wochentage") or "")[:20],
        "aktiv": 1 if daten.get("aktiv", True) else 0,
        "zustellen": (daten.get("zustellen") or "").strip(),
        "modell": (daten.get("modell") or "").strip()[:200],
        "aufbauen_auf": ",".join(x for x in re.findall(r"[\w-]+", str(daten.get("aufbauen_auf") or "")) if x)[:200],
    }
    if plan["zustellen"] not in RHYTHMUS_ZUSTELLUNG:
        raise ValueError("Unbekannte Zustellung: %r" % plan["zustellen"])
    plan["naechster_lauf"] = naechster_zeitpunkt(dict(plan, letzter_lauf=0))
    conn = db()
    try:
        conn.execute(
            "INSERT INTO zeitplan(id,name,art,was,ziel,eingabe,uhrzeit,"
            "intervall_min,wochentage,aktiv,letzter_lauf,letzter_status,"
            "naechster_lauf,created_at,zustellen,modell,aufbauen_auf) VALUES(?,?,?,?,?,?,?,?,?,?,0,'',?,?,?,?,?)",
            (plan["id"], plan["name"], plan["art"], plan["was"], plan["ziel"],
             plan["eingabe"], plan["uhrzeit"], plan["intervall_min"],
             plan["wochentage"], plan["aktiv"], plan["naechster_lauf"], now(), plan["zustellen"],
             plan["modell"], plan["aufbauen_auf"]))
        conn.commit()
    finally:
        conn.close()
    return zeitplan_liste()


def zeitplan_umschalten(plan_id):
    conn = db()
    try:
        r = conn.execute("SELECT * FROM zeitplan WHERE id=?", (plan_id,)).fetchone()
        if not r:
            raise ValueError("Diesen Eintrag gibt es nicht.")
        plan = dict(r)
        plan["aktiv"] = 0 if plan["aktiv"] else 1
        conn.execute("UPDATE zeitplan SET aktiv=?, naechster_lauf=? WHERE id=?",
                     (plan["aktiv"], naechster_zeitpunkt(plan), plan_id))
        conn.commit()
    finally:
        conn.close()
    return zeitplan_liste()


def zeitplan_loeschen(plan_id):
    conn = db()
    try:
        conn.execute("DELETE FROM zeitplan WHERE id=?", (plan_id,))
        conn.commit()
    finally:
        conn.close()
    return zeitplan_liste()


def zeitplan_jetzt(plan_id):
    """Sofort ausfuehren — zum Ausprobieren, ohne bis morgen früh zu warten."""
    conn = db()
    try:
        r = conn.execute("SELECT * FROM zeitplan WHERE id=?", (plan_id,)).fetchone()
    finally:
        conn.close()
    if not r:
        raise ValueError("Diesen Eintrag gibt es nicht.")
    plan = dict(r)
    if plan["id"] in _rhythmus["aktive"]:
        raise ValueError("Dieser Eintrag läuft gerade schon.")
    _rhythmus["aktive"].add(plan["id"])

    def lauf():
        try:
            status = rhythmus_ausfuehren(plan)
        except Exception as e:
            status = "error: %s" % e
        finally:
            _rhythmus["aktive"].discard(plan["id"])
        frisch = dict(plan, letzter_lauf=time.time())
        _zeitplan_setzen(plan["id"], letzter_lauf=frisch["letzter_lauf"],
                         letzter_status=status,
                         naechster_lauf=naechster_zeitpunkt(frisch))
    background(lauf)
    return {"gestartet": True}


def rhythmus_starten():
    """Der Zeitgeber. Ein Faden, der alle 20 Sekunden nachsieht.

    20 Sekunden sind grob genug, um nichts zu kosten, und fein genug, dass
    eine Uhrzeit auf die Minute genau trifft."""
    if _rhythmus["laeuft"]:
        return False

    def schleife():
        while _rhythmus["laeuft"]:
            try:
                rhythmus_pruefen()
            except Exception:
                pass          # ein kaputter Eintrag darf den Takt nie stoppen
            for _ in range(20):
                if not _rhythmus["laeuft"]:
                    return
                time.sleep(1)

    _rhythmus["laeuft"] = True
    _rhythmus["faden"] = threading.Thread(target=schleife, daemon=True)
    _rhythmus["faden"].start()
    return True


def rhythmus_stoppen():
    _rhythmus["laeuft"] = False


# ----------------------------------------------------------------------------
# Mesh — der dezentrale Unterbau (optional, laeuft nur wenn bewusst gestartet)
# ----------------------------------------------------------------------------
# Dive on Wide funktioniert ohne Mesh vollstaendig. Wer es nicht startet, merkt
# nichts davon — deshalb ist der Import weich und der Knoten standardmaessig aus.

try:
    import mesh as _mesh
    MESH_DA = True
except Exception:
    _mesh = None
    MESH_DA = False

_mesh_zustand = {"knoten": None, "netz": None, "seit": None, "fehler": None}


def pwa_manifest():
    """Damit Dive on Wide auf dem Handy wie eine App auf dem Startbildschirm landet.

    Das ist der Weg, den Brainstorm 2 als Alternative zu einer nativen App
    nennt: kein App-Store, keine Freigabe, keine Installation — die Seite
    wird zur App. Reicht fuer alles, was ein Mensch am Telefon tut
    (Nachrichten, Forum, Auftraege, Netzansicht).

    Was damit NICHT geht, steht ehrlich in der README: RAM beisteuern kann
    ein Browser-Tab nicht sinnvoll (Speicherdeckel, und im Hintergrund wird
    er eingefroren). Dafuer braucht es die native Huelle."""
    return {
        "name": "Dive on Wide",
        "short_name": "Dive on Wide",
        "description": "Lokaler KI-Arbeitsplatz mit dezentralem Netzwerk.",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "orientation": "any",
        "background_color": "#fcfaf6",
        "theme_color": "#3e5670",
        "lang": "de",
        "icons": [
            {"src": "icon.svg", "sizes": "any", "type": "image/svg+xml",
             "purpose": "any maskable"},
        ],
    }


# Der Dienstarbeiter haelt bewusst NUR die Huelle vor. Inhalte werden nie
# zwischengespeichert: Dive on Wide verspricht, dass Nachrichten nur im
# Arbeitsspeicher leben — ein Zwischenspeicher auf der Platte waere genau
# der Wortbruch, den das ganze System vermeiden will.
SERVICE_WORKER = """
const HUELLE = 'dowos-huelle-v1';
self.addEventListener('install', e => {
  e.waitUntil(caches.open(HUELLE).then(c => c.addAll(['./'])).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks =>
    Promise.all(ks.filter(k => k !== HUELLE).map(k => caches.delete(k)))
  ).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  // Alles unter /api/ NIEMALS zwischenspeichern - das sind die Inhalte.
  if (u.pathname.startsWith('/api/')) return;
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request).then(r => r || caches.match('./')))
  );
});
"""


def mesh_info():
    """Der Zustand des Mesh — auch wenn es gar nicht laeuft."""
    if not MESH_DA:
        return {"verfuegbar": False, "laeuft": False,
                "fehler": "Das Mesh-Paket fehlt in diesem Ordner."}
    k = _mesh_zustand["knoten"]
    grund = _mesh.crypto.backend_info()
    aus = {"verfuegbar": True, "laeuft": bool(k),
           "fehler": _mesh_zustand["fehler"],
           "krypto": grund,
           "betriebsarten": list(_mesh.knoten.BETRIEBSARTEN),
           "autostart": get_setting("MESH_AUTOSTART", ""),
           "adressen": _mesh.transport.eigene_adressen()}
    if k:
        lage = k.lage()
        aus.update(lage)
        aus["seit_s"] = int(time.time() - (_mesh_zustand["seit"] or time.time()))
        netz = _mesh_zustand.get("netz")
        if netz is not None:
            # Ehrlich zeigen, WORUEBER gerufen wird — und worueber nicht.
            # Ohne das sieht ein Mensch nur „keine Nachbarn" und keinen Grund.
            aus["wege"] = [ip for ip, _s in getattr(netz, "_sender", [])]
            aus["wege_fehler"] = dict(getattr(netz, "wege_fehler", {}) or {})
            aus["nur_dieser_rechner"] = (aus["wege"] == ["127.0.0.1"])
        # Laeuft der Rechenknoten wirklich, oder steht nur eine Zahl da?
        pr = _rechenknoten["prozess"]
        aus["rechenknoten_laeuft"] = bool(pr is not None and pr.poll() is None)
        aus["verteilt"] = mesh_verteilt_lage()
        # DIE FALLE, die sonst niemand deuten kann: Der eigene Rechenknoten
        # belegt selbst Speicher — auf einem Mac reserviert er sich gleich eine
        # grosse Metal-Arbeitsflaeche. Danach meldet der Statthalter 0 GB
        # abzugeben, und wer gerade eingeschaltet hat, haelt das fuer einen
        # Fehler. Es ist keiner: Der Speicher IST abgegeben, er steht nur
        # bereits in Benutzung statt in der Anzeige.
        if aus["rechenknoten_laeuft"] and not (aus.get("beitrag") or {}).get("erlaubt_gb"):
            aus["beitrag"] = dict(aus.get("beitrag") or {})
            aus["beitrag"]["begruendung"] = (
                "Der eigene Rechenknoten läuft und belegt bereits Speicher "
                "— deshalb steht gerade nichts weiteres zur Abgabe. Das ist "
                "kein Fehler: Dieses Gerät trägt schon bei.")
        try:
            from mesh import verteilt as _v
            _rl = _v.rpc_lage()
            aus["rpc_moeglich"] = _rl["kann_mitrechnen"]
            aus["rpc_hinweise"] = _rl["hinweise"]
        except Exception:
            aus["rpc_moeglich"] = False
            aus["rpc_hinweise"] = []
        aus["modelle"] = _mesh.knoten.was_laeuft_darauf(
            int(lage["compute_gesamt_gb"] * 1024 ** 3))
    else:
        aus["beitrag"] = _mesh.ressourcen.Statthalter().bericht()
    return aus


def mesh_starten(betriebsart, abgeben_gb=None):
    """Startet den Knoten. Der Beitrag ist eine bewusste Entscheidung."""
    if not MESH_DA:
        raise RuntimeError("Das Mesh-Paket fehlt in diesem Ordner.")
    if _mesh_zustand["knoten"]:
        return mesh_info()
    if betriebsart not in _mesh.knoten.BETRIEBSARTEN:
        raise ValueError("Unbekannte Betriebsart: %r" % betriebsart)
    statt = _mesh.ressourcen.Statthalter(
        zustimmung=True,
        hoechstens_gb=(float(abgeben_gb) if abgeben_gb else None))
    netz = _mesh.transport.UdpNetz()
    k = _mesh.knoten.Knoten(betriebsart, netz=netz, statthalter=statt,
                            modelle=_mesh_lokale_modelle, llm=_mesh_llm)
    if not k.starten():
        _mesh_zustand["fehler"] = netz.fehler or "Knoten startete nicht."
        k.stoppen()
        raise RuntimeError(_mesh_zustand["fehler"])
    _mesh_zustand.update({"knoten": k, "netz": netz, "seit": time.time(),
                          "fehler": None})
    emit("Mesh gestartet \U0001F578", "Betriebsart: %s" % betriebsart)
    return mesh_info()


def _mesh_netz_modelle():
    """Modelle, die ANDERE Geraete im Netz fahren. {name: anzahl_geraete}"""
    k = _mesh_zustand.get("knoten")
    if not k:
        return {}
    return {name: len(e["knoten"]) for name, e in k.modell_karte().items()
            if e["knoten"]}


def _mesh_chat_once(modell, messages, timeout=300):
    """Eine Anfrage an ein Modell, das auf einem ANDEREN Geraet laeuft.

    Der ganze Verlauf wird zu einem Text zusammengefasst, weil der
    Rechenknoten kein Gedaechtnis fuehrt — er bekommt genau einen Aufruf und
    vergisst ihn danach. Das ist die Zusage des Netzes, nicht eine Sparsamkeit.

    Es wird bewusst NICHT auf alle Antworten gewartet: Die erste brauchbare
    zaehlt. Wer Antworten vergleichen will, nutzt die Auftragsansicht unter
    Modelle — dort ist der Vergleich sichtbar und gewollt."""
    k = _mesh_zustand.get("knoten")
    if not k:
        raise RuntimeError("Das Netzwerk läuft nicht — dieses Modell liegt auf "
                           "einem anderen Gerät und ist gerade nicht erreichbar.")
    teile = []
    for m in messages or []:
        rolle = m.get("role", "user")
        inhalt = m.get("content", "")
        if not inhalt:
            continue
        if rolle == "system":
            teile.append("[Anweisung]\n%s" % inhalt)
        elif rolle == "assistant":
            teile.append("[Bisherige Antwort]\n%s" % inhalt)
        else:
            teile.append(inhalt)
    prompt = "\n\n".join(teile).strip()
    if not prompt:
        raise ValueError("Leerer Auftrag.")
    auf_id = k.auftrag_verteilen(prompt, modell,
                                 int(get_setting("MESH_AUFTRAG_KOPIEN", "2")))
    frist = time.time() + float(timeout or 300)
    try:
        while time.time() < frist:
            lage = k.auftrag_lage(auf_id)
            if lage is None:
                raise RuntimeError("Der Auftrag ist verschwunden.")
            for e in lage["ergebnisse"]:
                if e.get("text"):
                    return e["text"]
            if lage["offen"] == 0:
                gruende = [e.get("fehler") for e in lage["ergebnisse"]
                           if e.get("fehler")]
                raise RuntimeError(
                    "Kein Gerät konnte den Auftrag ausführen: %s"
                    % ("; ".join(gruende[:3]) or "keine Antwort"))
            time.sleep(0.25)
    finally:
        k.auftrag_vergessen(auf_id)
    raise RuntimeError(
        "Das Gerät mit „%s“ hat in %d s keine Antwort zurückgeschickt. Läuft Dive on Wide dort noch? "
        "Kommt nie etwas an, blockiert oft die Firewall dieses Rechners den Rückweg (Windows: Python im "
        "privaten Netzwerk zulassen). Schneller und sicherer: ein Modell auf diesem Rechner wählen."
        % (modell, int(timeout or 300)))


_mesh_modell_lager = {"zeit": 0.0, "wert": []}
MESH_MODELLE_FRISCH = 10.0     # Sekunden


def _mesh_lokale_modelle():
    """Welche Modelle kann DIESES Gerät fahren?

    Nur lokale Provider. Ein Cloud-Modell im Netz anzubieten waere doppelt
    falsch: Es laeuft nicht auf dem eigenen Geraet, und es wuerde fremde
    Anfragen an einen Anbieter weiterleiten, der davon nichts weiss."""
    # Zwei Dinge waren hier falsch, und beide sah man nur mit der Stoppuhr:
    #
    # 1. `llm_list_models()` wurde JE ANBIETER aufgerufen — also N Runden zu
    #    Ollama, wo eine genuegt. Die Liste ist fuer alle Anbieter dieselbe.
    # 2. Es geschah bei JEDEM Aufruf. Diese Funktion haengt an `knoten.lage()`,
    #    und die Netzwerk-Ansicht fragt alle sechs Sekunden nach. /api/mesh
    #    brauchte dadurch eine halbe Sekunde, in der nichts geschah ausser
    #    Warten auf Ollama.
    #
    # Modelle kommen und gehen, aber nicht im Sekundentakt. Zehn Sekunden
    # Gedaechtnis kosten nichts und nehmen die Last vollstaendig weg.
    jetzt = time.time()
    if jetzt - _mesh_modell_lager["zeit"] < MESH_MODELLE_FRISCH:
        return list(_mesh_modell_lager["wert"])
    erlaubte = {p.get("id") for p in get_providers()
                if not (p.get("type") == "openai" and p.get("api_key"))}
    try:
        alle = llm_list_models(ohne_netz=True)
    except Exception:
        alle = []
    gesehen, sauber = set(), []
    ram = arbeitsspeicher_gib()
    entfernt = {p.get("id") for p in get_providers() if adresse_entfernt(p.get("base_url", ""))}
    for m in alle:
        name = m.get("name")
        if m.get("provider_id") in erlaubte and name and name not in gesehen:
            gesehen.add(name)
            # Nicht anbieten, was hier nicht passt oder hier schon abstuerzte
            # (27.09.2026: Der Mac bot dem Windows-PC das 27B an, das ihn unter
            # Last aus dem Speicher geworfen hatte). Dieselbe Regel wie im Katalog.
            if (m.get("provider_id") not in entfernt and speicher_stufe(m.get("size"), ram) == "zu_gross") \
                    or modell_abgestuerzt(m.get("label") or name):
                continue
            sauber.append(name)
    _mesh_modell_lager["zeit"] = jetzt
    _mesh_modell_lager["wert"] = sauber
    return list(sauber)


def _mesh_llm(modell, prompt):
    """Ein fremder Auftrag auf dem eigenen Modell.

    Bewusst mit knappem Zeitrahmen und ohne Werkzeuge: Ein fremder Auftrag
    darf einen einzelnen Aufruf machen, nichts starten und nichts ausfuehren.
    Wer Rechenzeit spendet, spendet Rechenzeit — nicht seinen Rechner."""
    frist = int(get_setting("MESH_AUFTRAG_TIMEOUT", "120"))
    return llm_chat_once(modell, [{"role": "user", "content": prompt}],
                         timeout=frist)


# Grobe Kenngroessen gaengiger Modelle in 4-Bit-Quantisierung. Nur fuer die
# Planung — der wahre Wert steht in der Modelldatei, aber ohne sie muss man
# etwas annehmen koennen. Bewusst als Schaetzung benannt.
MODELL_MASSE = {
    "8b":   (5.0, 32),  "13b":  (8.0, 40),  "32b": (18.0, 64),
    "70b":  (35.0, 80), "120b": (60.0, 88), "235b": (118.0, 94),
}


def modell_masse_raten(name):
    """Groesse und Schichtzahl aus dem Modellnamen schaetzen."""
    klein = str(name or "").lower()
    for marke in sorted(MODELL_MASSE, key=len, reverse=True):
        if marke in klein:
            gb, schichten = MODELL_MASSE[marke]
            return {"gb": gb, "schichten": schichten, "erkannt": marke,
                    "geschaetzt": True}
    return {"gb": 5.0, "schichten": 32, "erkannt": "", "geschaetzt": True}


def mesh_verteilt(modell="", gb=None, schichten=None):
    """Wie ein Modell ueber die Geraete im Netz fiele — und was noch fehlt.

    Antwortet AUCH ohne laufendes Mesh, dann eben mit der reinen Laufzeitlage.
    Wer wissen will, warum verteilte Inferenz nicht geht, soll das erfahren,
    ohne vorher ein Netz starten zu muessen."""
    from mesh import verteilt as _v
    k = _mesh_zustand["knoten"]
    # ZUERST in die Datei sehen, dann erst raten. Der Name sagt „12b", die
    # Datei sagt 6,87 GB und 48 Schichten — geraten haetten wir 5,0 GB und 32.
    # Ein Plan auf geratenen Zahlen verteilt Schichten, die es nicht gibt.
    masse = None
    modell_pfad = ""
    if modell:
        try:
            modell_pfad = _v.modell_datei_finden(modell)
            gelesen = _v.modell_masse_lesen(modell_pfad)
            if gelesen["gelesen"]:
                masse = {"gb": gelesen["gb"], "schichten": gelesen["schichten"],
                         "erkannt": "aus der Datei gelesen", "geschaetzt": False}
        except Exception:
            pass                      # kein Pfad auffindbar — dann eben raten
    if masse is None:
        masse = modell_masse_raten(modell)
    if gb: masse["gb"] = float(gb); masse["geschaetzt"] = False
    if schichten: masse["schichten"] = int(schichten)
    if k is None:
        return {"laeuft": False, "laufzeit": _v.rpc_lage(), "modell": modell,
                "masse": masse, "plan": None,
                "grund": "Das Mesh läuft nicht. Ohne Netz gibt es keine Geräte, "
                         "auf die sich ein Modell verteilen ließe."}
    antwort = k.rechenplan(masse["gb"], masse["schichten"])
    antwort["laeuft"] = True
    antwort["modell"] = modell
    antwort["masse"] = masse
    antwort["modell_pfad"] = modell_pfad
    antwort["ausfuehrbar"] = bool(
        modell_pfad and antwort.get("plan") and antwort["laufzeit"]["kann_fuehren"])
    return antwort


# Der laufende Rechenknoten dieses Geraets — Prozess und Port.
_rechenknoten = {"prozess": None, "port": 0}


def _rechenknoten_pid_datei():
    return os.path.join(STORAGE_DIR, "rechenknoten.pid")


def _rechenknoten_reste_beenden():
    """Einen Rechenknoten aus einem früheren Lauf beenden.

    09.10.2026 am Mac gefunden: Dive on Wide wurde neu gestartet, der alte
    ggml-rpc-server lief seit Tagen weiter und hielt Port 50052. Der neue kam
    nicht an den Port („Failed to create server socket") und endete sofort —
    die Startprüfung sah aber den Port des ALTEN offen und meldete Erfolg.
    Übrig blieb „Rechenknoten antwortet nicht", ohne Grund."""
    pfad = _rechenknoten_pid_datei()
    try:
        alt = int(open(pfad).read().strip())
    except (OSError, ValueError):
        return 0
    try:
        os.remove(pfad)
    except OSError:
        pass
    try:
        from gguf_dienst import _prozess_befehl
        if "rpc-server" not in _prozess_befehl(alt):
            return 0      # PID längst an etwas anderes vergeben
        os.kill(alt, 15)
    except (OSError, ValueError):
        return 0
    for _ in range(30):            # bis der Port wirklich frei ist
        try:
            os.kill(alt, 0)
        except OSError:
            break
        time.sleep(0.1)
    return alt


def _rechenknoten_anhalten():
    pr = _rechenknoten["prozess"]
    _rechenknoten["prozess"] = None
    _rechenknoten["port"] = 0
    try:
        os.remove(_rechenknoten_pid_datei())
    except OSError:
        pass
    if pr is None:
        return
    try:
        pr.terminate()
        pr.wait(timeout=8)
    except Exception:
        try:
            pr.kill()
        except Exception:
            pass


def _letzte_zeilen(pfad, anzahl=4):
    """Was das Programm zuletzt gesagt hat — statt einer Vermutung.

    Die erste Fassung leitete Ausgabe und Fehler nach /dev/null und riet dann
    im Fehlerfall („Häufigster Grund: …"). Geraten hat hier niemandem
    geholfen: Die wirkliche Ursache war „Failed to connect to 192.168.0.100",
    und das stand die ganze Zeit da — nur eben in /dev/null."""
    try:
        with open(pfad, encoding="utf-8", errors="replace") as f:
            zeilen = [z.strip() for z in f.read().splitlines() if z.strip()]
    except Exception:
        return ""
    wichtig = [z for z in zeilen
               if any(w in z.lower() for w in
                      ("error", "fail", "abort", "unable", "cannot", "invalid",
                       "no such", "not enough", "out of memory"))]
    auswahl = (wichtig or zeilen)[-anzahl:]
    return ("Das Programm meldete: " + " | ".join(auswahl)) if auswahl else ""


def _port_horcht(port, frist=25.0, wirt="127.0.0.1"):
    """Wartet, bis auf dem Port wirklich jemand antwortet.

    Ohne diese Prüfung kündigt man einen Port an, hinter dem nichts steht — und
    ein Nachbar, der einen Verteilplan darauf baut, läuft ins Leere. Der
    Rechenknoten braucht ein paar Sekunden, bis Metal seine Kernel übersetzt
    hat; sofort nachzusehen wäre zu früh."""
    ende = time.time() + frist
    while time.time() < ende:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            # An der Adresse klopfen, auf der WIRKLICH gebunden wurde. Hier
            # stand fest 127.0.0.1 — und der Rechenknoten bindet auf die
            # LAN-Adresse, damit andere Geräte ihn erreichen. Die Prüfung sah
            # also an der falschen Tür nach und meldete „kam nicht hoch",
            # während er längst lief.
            if s.connect_ex((str(wirt), int(port))) == 0:
                return True
        finally:
            s.close()
        time.sleep(0.3)
    return False


# Das verteilte Modell, wenn eines laeuft: Prozess, Port, Modellname, Plan.
_verteiltes = {"prozess": None, "port": 0, "modell": "", "plan": None}
VERTEILT_PROVIDER_ID = "verteilt"


def verteilt_provider():
    """Der Anbieter „Verteiltes Modell".

    Das verteilte Modell muss ueberall dort waehlbar sein, wo Dive on Wide Modelle
    anbietet — Chat, Agenten, Skills, Pipelines, Orchestrator. Sonst waere es
    ein Kunststueck neben dem Programm statt ein Modell darin.

    Es spricht die OpenAI-Schnittstelle auf 127.0.0.1, also genuegt ein
    gewoehnlicher Anbieter dieses Typs. Er wird NIE gespeichert: Ein Anbieter,
    den es beim naechsten Start nicht gibt, hat in der Konfiguration nichts
    verloren."""
    return {"id": VERTEILT_PROVIDER_ID,
            "name": "Verteiltes Modell (%s)" % (_verteiltes["modell"] or "?"),
            "type": "openai",
            "base_url": "http://127.0.0.1:%d/v1" % _verteiltes["port"],
            "api_key": "nicht-noetig"}


def _verteiltes_anhalten():
    pr = _verteiltes["prozess"]
    _verteiltes.update({"prozess": None, "port": 0, "modell": "", "plan": None})
    if pr is None:
        return
    try:
        pr.terminate()
        pr.wait(timeout=10)
    except Exception:
        try:
            pr.kill()
        except Exception:
            pass


def mesh_verteilt_starten(modell, port=None):
    """Ein Modell wirklich über die Geräte des Netzes spannen.

    Der Weg, den 0.1022 nur geplant hat: Plan rechnen, den Aufruf bauen,
    llama-server starten, warten bis er antwortet — und ihn dann als Anbieter
    eintragen, damit er ueberall in Dive on Wide waehlbar ist.

    Die Rechenknoten laufen dabei auf den ANDEREN Geraeten. Von dort kommen
    nur Zwischenergebnisse; **zusammengesetzt wird die Antwort ausschliesslich
    hier**, im Hauptprozess auf diesem Geraet. Kein anderes Geraet sieht je
    den fertigen Text."""
    k = _mesh_zustand["knoten"]
    if k is None:
        raise RuntimeError("Das Mesh läuft nicht.")
    from mesh import verteilt as _v
    lage = _v.rpc_lage()
    if not lage["kann_fuehren"]:
        raise RuntimeError(
            "Dieses Gerät kann keine verteilte Kette anführen: %s"
            % (" ".join(lage["hinweise"]) or "llama-server ohne --rpc."))
    antwort = mesh_verteilt(modell)
    if not antwort.get("plan"):
        raise RuntimeError(antwort.get("grund") or "Kein Plan möglich.")
    if not antwort.get("modell_pfad"):
        raise RuntimeError(
            "Zu %r ließ sich keine Modelldatei finden. llama.cpp braucht eine "
            ".gguf-Datei; Ollama-Modelle werden über `ollama show --modelfile` "
            "aufgelöst." % modell)
    _verteiltes_anhalten()
    port = int(port or 8770)
    befehl = _v.aufruf_bauen(antwort["plan"], antwort["modell_pfad"], port=port)
    befehl[0] = _v.fuehrer_programm() or befehl[0]
    protokoll = os.path.join(STORAGE_DIR, "verteiltes-modell.log")
    try:
        pr = subprocess.Popen(befehl, stdout=open(protokoll, "w"),
                              stderr=subprocess.STDOUT)
    except Exception as e:
        raise RuntimeError("llama-server ließ sich nicht starten: %s" % e)
    _verteiltes.update({"prozess": pr, "port": port, "modell": modell,
                        "plan": antwort["plan"]})
    # Ein grosses Modell ueber mehrere Geraete zu laden dauert — jede Schicht
    # muss ueber das Netz zum jeweiligen Rechenknoten. Zwei Minuten sind
    # grosszuegig und trotzdem endlich.
    if not _port_horcht(port, frist=120.0):
        _verteiltes_anhalten()
        raise RuntimeError(
            "Das verteilte Modell kam auf Port %d nicht hoch. %s"
            % (port, _letzte_zeilen(protokoll)
               or "Häufigster Grund: Ein Rechenknoten hat nicht genug Speicher "
                  "für seine Schichten, oder er ist zwischendurch weggefallen."))
    emit("Verteiltes Modell läuft \U0001F9E9",
         "%s über %d Geräte" % (modell, len(antwort["plan"]["knoten"])))
    return mesh_verteilt_lage()


def mesh_verteilt_lage():
    """Was gerade verteilt läuft — für Anzeige und Anbieterliste."""
    pr = _verteiltes["prozess"]
    laeuft = bool(pr is not None and pr.poll() is None)
    return {"laeuft": laeuft, "modell": _verteiltes["modell"] if laeuft else "",
            "port": _verteiltes["port"] if laeuft else 0,
            "plan": _verteiltes["plan"] if laeuft else None,
            "anbieter": VERTEILT_PROVIDER_ID if laeuft else ""}


def mesh_verteilt_stoppen():
    _verteiltes_anhalten()
    return mesh_verteilt_lage()


def mesh_rpc_schalten(port):
    """Das eigene Rechenangebot ein- oder ausschalten (0 = aus).

    STARTET den Rechenknoten wirklich. Die erste Fassung setzte nur
    `k.rpc_port` und rief ins Netz „ich halte Schichten auf Port 50052" —
    während auf 50052 niemand horchte. Ein Nachbar hätte einen Verteilplan
    darauf gebaut und wäre ins Leere gelaufen. Angekündigt wird deshalb erst,
    wenn der Port wirklich antwortet.

    Getrennt vom Mesh-Start und getrennt von den Auftraegen: Wer Nachrichten
    austauschen will, muss nicht zugleich fremde Modellschichten tragen."""
    k = _mesh_zustand["knoten"]
    if k is None:
        raise RuntimeError("Das Mesh läuft nicht.")
    port = int(port or 0)
    if port and not (1024 <= port <= 65535):
        raise ValueError("Port muss zwischen 1024 und 65535 liegen (oder 0 für aus).")
    if not port:
        _rechenknoten_anhalten()
        k.rpc_port = 0
        try:
            k.rufen()
        except Exception:
            pass
        return {"rpc_port": 0, "laeuft": False}
    if not k.statthalter.zustimmung:
        raise RuntimeError(
            "Ohne freigegebenen Speicher kann dieses Gerät keine Schichten "
            "halten. Erst im Mesh einen Anteil abgeben.")
    from mesh import verteilt as _v
    lage = _v.rpc_lage()
    if not lage["kann_mitrechnen"]:
        # Ehrlich absagen statt einen Port anzukuendigen, den niemand bedient.
        raise RuntimeError(
            "Dieses Gerät kann keine Schichten halten: %s"
            % (" ".join(lage["hinweise"]) or "Rechenknoten fehlt."))
    _rechenknoten_anhalten()
    _rechenknoten_reste_beenden()
    # Auf der Adresse binden, unter der das Mesh diesen Knoten ANKUENDIGT.
    # Mit 127.0.0.1 kuendigt man eine Adresse an, unter der niemand antwortet,
    # und der Hauptprozess der Kette bricht mit „Failed to connect" ab.
    # Bewusst NICHT 0.0.0.0: Die RPC-Schnittstelle kennt keine Anmeldung, also
    # so wenig Fläche wie möglich.
    adressen = _mesh.transport.eigene_adressen()
    wirt = adressen[0] if adressen else "127.0.0.1"
    befehl = _v.rechenknoten_aufruf(port, wirt=wirt)
    protokoll = os.path.join(STORAGE_DIR, "rechenknoten.log")
    try:
        pr = subprocess.Popen(befehl, stdout=open(protokoll, "w"),
                              stderr=subprocess.STDOUT)
    except Exception as e:
        raise RuntimeError("Rechenknoten ließ sich nicht starten: %s" % e)
    _rechenknoten["prozess"] = pr
    _rechenknoten["port"] = port
    try:
        with open(_rechenknoten_pid_datei(), "w") as f:
            f.write(str(pr.pid))
    except OSError:
        pass
    # Horcht der Port, muss es UNSER Prozess sein — ein fremder oder alter auf demselben Port zählt nicht.
    # Ein belegter Port antwortet sofort, der eigene Prozess endet erst Millisekunden später: kurz zusehen.
    horcht = _port_horcht(port, wirt=wirt)
    try:
        pr.wait(1.5)
    except subprocess.TimeoutExpired:
        pass
    if not horcht or pr.poll() is not None:
        _rechenknoten_anhalten()
        if "Failed to create server socket" in _letzte_zeilen(protokoll, 40):
            raise RuntimeError(
                "Port %d ist schon belegt — meist ein Rechenknoten, der von früher noch läuft. "
                "Beenden: %s — oder einen anderen Port wählen."
                % (port, "taskkill /IM *rpc-server.exe /F" if os.name == "nt" else "pkill -f rpc-server"))
        raise RuntimeError(
            "Der Rechenknoten kam auf %s:%d nicht hoch. %s"
            % (wirt, port, _letzte_zeilen(protokoll)
               or "Läuft dort schon etwas anderes? Von Hand: %s" % " ".join(befehl)))
    k.rpc_port = port
    try:
        k.rufen()          # jetzt ankuendigen — jetzt stimmt es auch
    except Exception:
        pass
    return {"rpc_port": k.rpc_port, "laeuft": True,
            "programm": os.path.basename(befehl[0])}


def mesh_modelle():
    """Die drei Wege, ein Modell zu fahren — als eine Uebersicht."""
    k = _mesh_zustand["knoten"]
    lokal = []
    try:
        lokal = [{"name": m["name"], "label": m.get("label", m["name"]),
                  "provider": m.get("provider", "")} for m in llm_list_models()]
    except Exception:
        lokal = []
    karte = k.modell_karte() if k else {}
    im_netz = []
    for name, e in sorted(karte.items()):
        if e["knoten"]:
            im_netz.append({"name": name, "knoten": len(e["knoten"]),
                            "auch_hier": e["hier"]})
    return {
        "laeuft": bool(k),
        "betriebsart": k.betriebsart if k else None,
        "lokal": lokal,
        "eigene_im_netz": k.eigene_modelle() if k else [],
        "im_netz": im_netz,
        "fremdauftraege": k.fremdauftraege if k else 0,
        "auftraege_erlaubt": k.auftraege_erlaubt if k else False,
    }


def mesh_auftraege_erlauben(ja):
    k = _mesh_knoten()
    k.auftraege_erlaubt = bool(ja)
    return mesh_modelle()


def mesh_auftrag_starten(prompt, modell, hoechstens=2):
    k = _mesh_knoten()
    if not (prompt or "").strip():
        raise ValueError("Der Auftrag ist leer.")
    return {"id": k.auftrag_verteilen(prompt, modell, hoechstens)}


def mesh_auftrag_lage(auf_id):
    k = _mesh_knoten()
    l = k.auftrag_lage(auf_id)
    if l is None:
        raise ValueError("Diesen Auftrag gibt es nicht (mehr).")
    return l


def mesh_ticket_neu(host="", notiz=""):
    """Ein Einladungsticket auf diesen Knoten."""
    k = _mesh_knoten()
    t = k.ticket_erzeugen(host or None, notiz)
    link = _mesh.ticket.als_link(t)
    try:
        qr_svg = _mesh.qr.svg(link, modul=4)
    except Exception as e:
        qr_svg = ""                    # lieber kein Bild als ein falsches
    return {"ticket": t, "link": link, "qr": qr_svg,
            "adressen": _mesh.transport.eigene_adressen(),
            "hinweis": ("Diese Adresse gilt nur im eigenen Netz. Fuer den Weg "
                        "ueber das Internet die von aussen erreichbare Adresse "
                        "eintragen und den Port am Router weiterleiten.")}


def mesh_ticket_einloesen(text):
    k = _mesh_knoten()
    try:
        return k.ticket_einloesen(text)
    except _mesh.ticket.UngueltigesTicket as e:
        raise ValueError(str(e))


def _mesh_knoten():
    k = _mesh_zustand["knoten"]
    if not k:
        raise RuntimeError("Das Netzwerk läuft nicht. Zuerst unter Netzwerk "
                           "eine Betriebsart starten.")
    return k


def mesh_anker_neu():
    """Ein frischer Anker zum persönlichen Weitergeben."""
    if not MESH_DA:
        raise RuntimeError("Das Mesh-Paket fehlt in diesem Ordner.")
    return {"anker": _mesh.anker.anker_erzeugen()}


def mesh_kontakte():
    k = _mesh_zustand["knoten"]
    return {"laeuft": bool(k), "kontakte": k.kontaktliste() if k else [],
            "umwege_moeglich": k.zwiebel_moeglich() if k else 0}


def mesh_kontakt_anlegen(name, anker_code):
    k = _mesh_knoten()
    try:
        k.kontakt_anlegen(name, anker_code)
    except _mesh.anker.UngueltigerAnker as e:
        raise ValueError(str(e))
    return mesh_kontakte()


def mesh_kontakt_loeschen(name):
    k = _mesh_knoten()
    weg = k.kontakt_loeschen(name)
    return {"geloescht": weg, "kontakte": k.kontaktliste()}


def mesh_kontakt_bestaetigen(name):
    k = _mesh_knoten()
    return {"ok": k.kontakt_bestaetigen(name), "kontakte": k.kontaktliste()}


def mesh_chat(name):
    k = _mesh_knoten()
    verlauf = k.chat(name)
    if verlauf is None:
        raise ValueError("Kein Kontakt namens %r." % name)
    return {"name": name, "verlauf": verlauf}


def mesh_senden(name, text, umwege=0):
    k = _mesh_knoten()
    if not (text or "").strip():
        raise ValueError("Leere Nachrichten werden nicht gesendet.")
    if len(text.encode("utf-8")) > 4000:
        raise ValueError("Nachricht zu lang (höchstens 4000 Zeichen).")
    try:
        umwege = max(0, min(int(umwege or 0), 4))
    except Exception:
        umwege = 0
    k.nachricht_senden(name, text, umwege)
    aus = mesh_chat(name)
    aus["umwege_moeglich"] = k.zwiebel_moeglich()
    return aus


def mesh_faeden(raum="allgemein"):
    k = _mesh_zustand["knoten"]
    if not k:
        return {"laeuft": False, "faeden": [], "raum": raum}
    return {"laeuft": True, "raum": raum, "faeden": k.forum_raum(raum).faeden()}


def mesh_faden(faden_id, raum="allgemein"):
    k = _mesh_knoten()
    f = k.forum_raum(raum).lesen(faden_id)
    if f is None:
        raise ValueError("Diesen Faden kennt dieser Knoten nicht (mehr).")
    return f


def mesh_faden_neu(titel, text, raum="allgemein", stunden=6):
    k = _mesh_knoten()
    try:
        stunden = max(1, min(int(stunden or 6), 24))
    except Exception:
        stunden = 6
    try:
        fid = k.faden_eroeffnen(titel, text, raum, ttl=stunden * 3600)
    except _mesh.forum.ForumFehler as e:
        raise ValueError(str(e))
    return {"id": fid, "faeden": k.forum_raum(raum).faeden()}


def mesh_beitrag(faden_id, text, raum="allgemein"):
    k = _mesh_knoten()
    try:
        k.beitrag_schreiben(faden_id, text)
    except _mesh.forum.ForumFehler as e:
        raise ValueError(str(e))
    return mesh_faden(faden_id, raum)


def mesh_faden_schliessen(faden_id, raum="allgemein"):
    k = _mesh_knoten()
    k.faden_schliessen(faden_id, raum)
    return mesh_faden(faden_id, raum)


def mesh_autostart():
    """Das Netz beim Programmstart mitstarten — wenn der Besitzer das WILL.

    Standard ist aus. Eine Voreinstellung, die das eigene Gerät ohne Zutun
    in ein Netz haengt, waere genau das Gegenteil dessen, was dieses Projekt
    verspricht. Wer es einmal einschaltet, hat damit bewusst zugestimmt —
    und kann es in derselben Ansicht jederzeit widerrufen."""
    art = get_setting("MESH_AUTOSTART", "").strip()
    if not art or not MESH_DA:
        return None
    if art not in _mesh.knoten.BETRIEBSARTEN:
        return None
    gb = get_setting("MESH_AUTOSTART_GB", "").strip()
    try:
        return mesh_starten(art, float(gb) if gb else None)
    except Exception as e:
        _mesh_zustand["fehler"] = str(e)
        return None


def mesh_autostart_setzen(art, gb=None):
    """Merkt die Wahl fuer den naechsten Start. Leer = nicht mitstarten."""
    art = (art or "").strip()
    if art and (not MESH_DA or art not in _mesh.knoten.BETRIEBSARTEN):
        raise ValueError("Unbekannte Betriebsart: %r" % art)
    set_setting("MESH_AUTOSTART", art)
    set_setting("MESH_AUTOSTART_GB", str(gb) if gb else "")
    return {"autostart": art, "gb": gb}


def mesh_stoppen():
    """Not-Aus: Knoten weg, Schluessel vernichtet, Beitrag auf null."""
    # Der Rechenknoten haelt Schichten FUER ANDERE. Wer das Netz verlaesst,
    # laesst ihn sonst weiterlaufen — ein Prozess, der Speicher belegt fuer
    # ein Netz, in dem man nicht mehr ist. Dasselbe gilt fuer ein verteiltes
    # Modell: Seine Schichten liegen auf Geraeten, die man gerade verlaesst.
    _rechenknoten_anhalten()
    _verteiltes_anhalten()
    k = _mesh_zustand["knoten"]
    if k:
        k.stoppen()
    _mesh_zustand.update({"knoten": None, "netz": None, "seit": None})
    return mesh_info()


# ----------------------------------------------------------------------------
# HTTP-Handler
# ----------------------------------------------------------------------------

class AnfrageZuGross(ValueError):
    """→ 413"""


class UngueltigerRumpf(ValueError):
    """→ 400. Der Fuzz-Test vom 27.09.2026 schickte Listen, null und Zahlen statt
    eines JSON-Objekts: 30 Schnittstellen antworteten mit 500, weil jede body.get()
    aufrief. Hier wird das einmal abgefangen, nicht in 30 Handlern."""


def feld_text(daten, name, standard=""):
    """Ein Textfeld aus einem Anfragerumpf. Zahlen werden Text, None der
    Standard; Listen, Objekte und Wahrheitswerte sind ein Fehler des Aufrufers
    (400) — bisher liefen sie bis in SQLite oder write() und endeten als 500
    (Fuzz-Test 27.09.2026, 15 Schnittstellen)."""
    wert = daten.get(name, standard)
    if wert is None:
        return standard
    if isinstance(wert, bool) or not isinstance(wert, (str, int, float)):
        raise UngueltigerRumpf("„%s“ muss Text sein, nicht %s." % (name, type(wert).__name__))
    return wert if isinstance(wert, str) else str(wert)


def kontext_groesse(model):
    """Wie viele Token passen in dieses Modell — so, wie Dive on Wide es betreibt."""
    try:
        prov, _ = parse_model_ref(model)
    except Exception:
        return int(get_setting("NUM_CTX", "16384"))
    if prov.get("gguf"):
        return int(prov["gguf"].get("kontext") or 16384)
    if prov.get("type") == "openai" and _anbieter_extern(prov):
        return 128000            # Cloud-Modelle: groß; verdichtet wird dort nur bei sehr langen Gesprächen
    return int(get_setting("NUM_CTX", "16384"))


_wissen_index = {"stand": None, "index": None}
_wissen_index_sperre = threading.Lock()


def wissen_geaendert():
    with _wissen_index_sperre:
        _wissen_index["stand"] = None


def wissens_index():
    """Der Suchindex über das Wissen — neu gebaut nur, wenn sich das Wissen geändert hat (Stresstest 08.10.2026)."""
    conn = db()
    try:
        stand = tuple(conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(content)),0), COALESCE(MAX(updated_at),0), "
                                   "COALESCE(MAX(created_at),0), COALESCE(MAX(rowid),0) FROM knowledge").fetchone())
        with _wissen_index_sperre:
            if _wissen_index["stand"] == stand and _wissen_index["index"] is not None:
                return _wissen_index["index"]
            eintraege = rows(conn.execute("SELECT name, content FROM knowledge"))
            index = chatkontext.WissensIndex(eintraege)
            _wissen_index.update(stand=stand, index=index)
            return index
    finally:
        conn.close()


def chat_anreichern(messages, agent_id="", sprache="de", wissen=True):
    """Einführung und Wissen für den Chat beilegen — Fehler hier dürfen den Chat nie verhindern."""
    try:
        conn = db()
        try:
            agent = conn.execute("SELECT name FROM agents WHERE id=?", (agent_id,)).fetchone() if agent_id else None
        finally:
            conn.close()
        return chatkontext.anreichern(messages, BASE_DIR, DOWOS_VERSION, (),
                                      agent["name"] if agent else "", sprache, wissen,
                                      stil=get_setting("ANTWORT_STIL", "kurz"),
                                      index=wissens_index() if wissen else None)
    except Exception as e:
        print("Chat-Kontext übersprungen: %s" % e)
        return messages, [], False


class Handler(BaseHTTPRequestHandler):
    # Eine Anfrage, die weniger schickt, als sie ankuendigt, hielt ihren Thread
    # bisher unbegrenzt fest (Fuzz-Test 27.09.2026: Content-Length 1000, Rumpf
    # 2 Byte). Mit Frist endet jedes blockierende Lesen oder Schreiben nach 60 s.
    timeout = 60
    server_version = "DowOS/Alpha-0.5.0"

    # --- Hilfsfunktionen -----------------------------------------------------

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-Auth-Token, Authorization")

    def send_json(self, obj, status=200):
        # EIN Fehler, ZWEI Schluessel. Gewachsen sind beide: 30 Antworten
        # nannten ihn "error", 10 "fehler" — und das Frontend prueft mal den
        # einen, mal den anderen. Wer den falschen prueft, liest einen
        # Fehlschlag als Erfolg, und genau diese Sorte Fehler bemerkt man erst
        # viel spaeter. (Beim Durchspielen des Forums ist mir das selbst
        # passiert: Ein 404 sah aus wie ein gelungener Aufruf.)
        #
        # Statt einen Schluessel umzubenennen — das braeche die jeweils andere
        # Haelfte — geht jede Fehlerantwort mit BEIDEN hinaus. Danach hat jeder
        # Aufrufer recht, egal welchen er kennt.
        if status >= 400 and isinstance(obj, dict):
            if "error" in obj and "fehler" not in obj:
                obj = dict(obj, fehler=obj["error"])
            elif "fehler" in obj and "error" not in obj:
                obj = dict(obj, error=obj["fehler"])
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    MAX_BODY = 32 * 1024 * 1024        # 32 MB reichen für sehr lange Texte

    def read_body(self):
        """Liest den JSON-Body. Begrenzt die Größe, damit eine einzelne Anfrage
        den Server nicht über den Arbeitsspeicher kippen kann."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise UngueltigerRumpf("Content-Length ist keine Zahl.")
        if length < 0:
            raise UngueltigerRumpf("Content-Length ist negativ.")
        if not length:
            return {}
        if length > self.MAX_BODY:
            self.rfile.read(min(length, 4096))
            raise AnfrageZuGross("Anfrage zu groß (%.1f MB, erlaubt sind %d MB)."
                                 % (length / 1048576.0, self.MAX_BODY // 1048576))
        try:
            roh = self.rfile.read(length)
        except (TimeoutError, socket.timeout):
            raise UngueltigerRumpf("Rumpf unvollständig: angekündigt %d Byte, nach 60 s nicht angekommen." % length)
        try:
            daten = json.loads(roh.decode("utf-8"))
        except (ValueError, RecursionError):
            return {}
        if not isinstance(daten, dict):
            raise UngueltigerRumpf("Der Rumpf muss ein JSON-Objekt sein, kein %s." % type(daten).__name__)
        return daten

    def send_file(self, path, content_type=None, download_name=None, no_cache=False):
        if not os.path.exists(path):
            return self.send_json({"error": "not found"}, 404)
        with open(path, "rb") as f:
            body = f.read()
        ctypes = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                  ".css": "text/css", ".md": "text/markdown; charset=utf-8",
                  ".json": "application/json", ".svg": "image/svg+xml",
                  ".png": "image/png", ".txt": "text/plain; charset=utf-8"}
        ct = content_type or ctypes.get(os.path.splitext(path)[1], "application/octet-stream")
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ct)
        if no_cache:
            self.send_header("Cache-Control", "no-store, must-revalidate")
        if download_name:
            self.send_header("Content-Disposition",
                             'attachment; filename="%s"' % download_name)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("[Dive on Wide] %s\n" % (fmt % args))

    # --- Zugänge -------------------------------------------------------------
    # Schlüsselfrei bleiben nur die Oberfläche selbst und der Gesundheits-Check
    # (den braucht die Schlüssel-Eingabe, um eine Verbindung zu testen).

    AUTH_FREI = {"/", "/index.html", "/api/health",
                 # Die App-Huelle muss ohne Schluessel ladbar sein, sonst
                 # kann ein Handy sie gar nicht erst zum Startbildschirm
                 # hinzufuegen. Inhalte liegen darin keine.
                 "/manifest.webmanifest", "/sw.js", "/icon.svg",
                 # Das Wörterbuch der Oberfläche — schon vor der Anmeldung nötig.
                 "/lang/en.json"}

    def _client_ist_lokal(self):
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _darf_lage_sehen(self):
        """Darf dieser Aufrufer mehr als „lebt" erfahren?

        Dieselbe Regel wie bei `_auth`, nur ohne 401: Wer einen gültigen
        Schlüssel hat oder vom eigenen Rechner kommt, sieht die Lage. Alle
        anderen bekommen die kürzestmögliche wahre Antwort."""
        token = (self.headers.get("X-Auth-Token")
                 or (self.headers.get("Authorization") or "").replace("Bearer ", "", 1))
        if not token and "?" in self.path:
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1])
            token = (qs.get("token") or [""])[0]
        rolle, _ = token_info(token)
        if rolle and rolle != "harness":
            return True
        return (self._client_ist_lokal()
                and get_setting("AUTH_REQUIRE_LOCAL", "0") != "1")

    def _origin_erlaubt(self):
        """Blockt Anfragen fremder Webseiten (Cross-Origin), auch von localhost.

        Browser senden bei Cross-Origin-Aufrufen immer einen Origin-Header.
        Ohne diese Prüfung könnte eine bösartige Webseite im Browser des
        Nutzers Anfragen an die lokal laufende Instanz schicken."""
        origin = self.headers.get("Origin", "")
        if not origin:
            return True
        host = urllib.parse.urlparse(origin).netloc.split(":")[0]
        eigener = (self.headers.get("Host") or "").split(":")[0]
        return host in (eigener, "localhost", "127.0.0.1")

    def _auth(self):
        """Wie _auth_pruefen, merkt sich die Rolle aber fuer diesen Anfrage-Faden (run_begin)."""
        _anfrage.rolle = self._auth_pruefen()
        return _anfrage.rolle

    def _auth_pruefen(self):
        """Prüft den Zugangsschlüssel; liefert die Rolle oder None (401 gesendet).

        Vom eigenen Rechner aus ist Dive on Wide ohne Schlüssel nutzbar (abschaltbar
        über AUTH_REQUIRE_LOCAL=1). Andere Geräte im Netz brauchen immer einen
        Schlüssel aus Einstellungen → Zugänge."""
        p = self.path.split("?")[0]
        if p in self.AUTH_FREI:
            return "frei"
        if not self._origin_erlaubt():
            self.send_json({"error": "Cross-Origin-Zugriff verweigert."}, 403)
            return None
        token = (self.headers.get("X-Auth-Token")
                 or (self.headers.get("Authorization") or "").replace("Bearer ", "", 1))
        if not token and "?" in self.path:
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1])
            token = (qs.get("token") or [""])[0]
        rolle, name = token_info(token)
        if rolle:
            self._zugang_name = name or rolle
            # Ein Harness-Schlüssel kommt nur an die Harness-Schnittstelle. Alles
            # andere — Einstellungen, Dokumente, Chats, Dateien — bleibt hier.
            # Die Grenze bewacht die Seite, die die Daten besitzt.
            # Erst normalisieren, dann vergleichen. `/api/extern/../settings`
            # beginnt buchstaeblich mit /api/extern und kaeme sonst durch diese
            # Grenze. Heute faellt es danach beim Router durch (keine Route
            # passt, 404) — aber dann haengt die Grenze am Router statt an sich
            # selbst, und der naechste Mensch, der eine Route mit
            # Pfadaufloesung ergaenzt, reisst sie auf, ohne es zu merken.
            # Geprueft am 23.09.2026 mit acht Schreibweisen.
            if rolle == "harness" and not posixpath.normpath(p).startswith("/api/extern"):
                self.send_json({"error": "Dieser Schlüssel darf nur die "
                                "Harness-Schnittstelle benutzen (/api/extern/…). "
                                "Alles andere bleibt auf diesem Rechner.",
                                "erlaubt": ["/api/extern/info",
                                            "/api/extern/auftrag"]}, 403)
                return None
            return rolle
        if self._client_ist_lokal() and get_setting("AUTH_REQUIRE_LOCAL", "0") != "1":
            self._zugang_name = "Besitzer (lokal)"
            return "besitzer"
        self.send_json({"error": "Zugangsschlüssel fehlt oder ist ungültig.",
                        "auth": "required"}, 401)
        return None

    # --- Ausgang: was diese Instanz nach draußen gibt (ausgang.py) ---------

    def _ausgang_stufe(self):
        return ausgang.stufe_lesen(get_setting("AUSGANG_STUFE", "aus"))

    def _extern_frei(self):
        """Ist der Zugang für fremde Gerüste an? Sonst 403 mit dem Weg dahin."""
        if self._ausgang_stufe() == "aus":
            self.send_json({"error": "Der Zugang für fremde Gerüste ist aus. Der "
                            "Besitzer schaltet ihn unter Einstellungen → Ausgang "
                            "ein und legt dort fest, wie viel hinausgeht.",
                            "stufe": "aus"}, 403)
            return False
        return True

    def _extern_senden(self, daten, pfad, stufe, entfernt=(), auftrag=""):
        """Antwort nach draußen: senden — und ins Ausgangsbuch schreiben.

        Beides gehört zusammen. Eine Antwort, die den Rechner verlässt, ohne im
        Buch zu stehen, würde die ganze Zusage entwerten."""
        roh = json.dumps(daten, ensure_ascii=False)
        ausgang.eintragen(STORAGE_DIR, getattr(self, "_zugang_name", "?"),
                          pfad, stufe, len(roh), entfernt, auftrag)
        return self.send_json(daten)

    def _nur_besitzer(self, rolle):
        """Für Verwaltungs-Endpunkte: Gäste (Alpha-Tester) kommen nicht durch."""
        if rolle == "besitzer":
            return True
        self.send_json({"error": "Nur der Besitzer dieser Instanz darf das."}, 403)
        return False

    # --- Routing -------------------------------------------------------------

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        p = self.path.split("?")[0]
        rolle = self._auth()
        if not rolle:
            return
        try:
            if p == "/" or p == "/index.html":
                return self.send_file(os.path.join(BASE_DIR, "frontend", "index.html"),
                                      no_cache=True)
            if p == "/api/health":
                return self.api_health()
            if p == "/manifest.webmanifest":
                return self.send_json(pwa_manifest())
            if p == "/lang/en.json":
                pfad = os.path.join(BASE_DIR, "frontend", "lang", "en.json")
                if os.path.exists(pfad):
                    return self.send_file(pfad)
                return self.send_json({})
            if p == "/sw.js":
                daten = SERVICE_WORKER.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript")
                self.send_header("Content-Length", str(len(daten)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                return self.wfile.write(daten)
            if p == "/icon.svg":
                pfad = os.path.join(BASE_DIR, "assets", "favicon.svg")
                if os.path.exists(pfad):
                    return self.send_file(pfad)
                return self.send_error(404)
            if p == "/api/einrichtung":
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                pruef = (qs.get("ollama") or [""])[0].strip()
                # Eine fremde Adresse abfragen darf nur der Besitzer — sonst
                # waere das ein Weg, den Server beliebige Adressen ansprechen zu lassen.
                if pruef and (rolle != "besitzer" or not re.match(r"^https?://[^\s/]+(:\d+)?/?$", pruef)):
                    pruef = ""
                return self.send_json(einrichtung_lage(pruef or None))
            if p == "/api/zeitplan":
                return self.send_json(zeitplan_liste())
            if p == "/api/mesh":
                return self.send_json(mesh_info())
            if p == "/api/werkbank":
                return self.send_json(werkbank_lage())
            if p == "/api/werkbank/laeufe":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json(werkbank_laeufe())
            if p == "/api/werkbank/aktiv":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json(werkbank_aktiv())
            if p == "/api/werkbank/fahrplaene":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json({"fahrplaene": fahrplaene_liste()})
            if p == "/api/werkbank/regeln":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json(werkbank_regeln_lage())
            if p in ("/api/telegram", "/api/discord", "/api/slack"):
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json(boten_lage(p.rsplit("/", 1)[1]))
            if p == "/api/discord-server":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json(discord_server_lage())
            if p == "/api/lotse":
                if not self._nur_besitzer(rolle):      # nennt Rechner und Einstellungen
                    return
                lage = lotse_lage()
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                # Die Oberflächensprache zählt, nicht die des Browsers (07.10.2026: englische Oberfläche, deutsche Schritte)
                englisch = (q.get("sprache") or [""])[0] == "en" if q.get("sprache") else \
                    (self.headers.get("Accept-Language") or "").lower().startswith("en")
                ansicht = (q.get("ansicht") or [""])[0]
                return self.send_json({"erste_schritte": lotse.erste_schritte(lage, englisch),
                                       "probleme": lotse.probleme(lage, englisch),
                                       "vorschlaege": lotse.vorschlaege(ansicht, englisch),
                                       "modell": get_setting("LOTSE_MODELL", "") or get_setting("WERKBANK_MODELL", "") or get_setting("DEFAULT_MODEL", "")})
            if p == "/api/discord-server/kanaele":
                if not self._nur_besitzer(rolle):
                    return
                try:
                    return self.send_json({"kanaele": discord_server_kanaele()})
                except Exception as e:
                    return self.send_json({"error": "Discord nicht erreichbar: %s" % str(e)[:150]}, 502)
            if p in ("/api/werkbank/gedaechtnis", "/api/werkbank/erinnern"):
                if not self._nur_besitzer(rolle):
                    return
                qs = {k: v[0] for k, v in urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "").items()}
                try:
                    ordner = werkbank_ordner(qs) if (qs.get("ordner") or qs.get("workspace")) else None
                    if p.endswith("erinnern"):
                        return self.send_json(werkbank_gedaechtnis.Gedaechtnis(WERKBANK_DIR).erinnern(qs.get("suche"), ordner, 10))
                    return self.send_json(werkbank_gedaechtnis_lage(ordner))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
            m = re.match(r"^/api/werkbank/befehle(?:/([a-z0-9_-]+))?$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                try:
                    if m.group(1):
                        return self.send_json(werkbank_befehl(m.group(1)))
                    qs = {k: v[0] for k, v in urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "").items()}
                    return self.send_json(werkbank_befehle_lage(
                        werkbank_ordner(qs) if (qs.get("ordner") or qs.get("workspace")) else None))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                except LookupError as e:
                    return self.send_json({"error": str(e)}, 404)
            m = re.match(r"^/api/werkbank/skills(?:/([a-z0-9-]+))?$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                if m.group(1):
                    try:
                        return self.send_json(werkbank_skill(m.group(1)))
                    except LookupError as e:
                        return self.send_json({"error": str(e)}, 404)
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                wahl = {k: v[0] for k, v in qs.items()}
                try:
                    ordner = werkbank_ordner(wahl) if (wahl.get("ordner") or wahl.get("workspace")) else None
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                return self.send_json(werkbank_skills(ordner))
            if p == "/api/werkbank/webhooks":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json({"webhooks": [webhooks.oeffentlich(h) for h in webhooks_laden()]})
            if p == "/api/werkbank/unterbrochen":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json(schrittprotokoll.unterbrochen(WERKBANK_DIR, set(_werkbank_aktiv.values())))
            m = re.match(r"^/api/werkbank/agenten(?:/([a-z0-9_-]+))?$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                qs = {k: v[0] for k, v in urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "").items()}
                try:
                    ordner = werkbank_ordner(qs) if (qs.get("ordner") or qs.get("workspace")) else None
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                profile = agentenprofile.finden(ordner, AGENTEN_DIR)
                if m.group(1):
                    if m.group(1) not in profile:
                        return self.send_json({"error": "Profil gibt es nicht."}, 404)
                    return self.send_json(profile[m.group(1)])
                ext = werkbank_externe()
                return self.send_json({"profile": list(profile.values()), "ordner": AGENTEN_DIR,
                                       "projektorte": list(agentenprofile.PROJEKT_ORTE),
                                       "werkzeuge": list(agentenprofile.DOWOS_WERKZEUGE),
                                       "extern": {"eingeschaltet": list(ext.erlaubt), "verfuegbar": ext.verfuegbar(),
                                                  "bekannt": {k: v["name"] for k, v in externe_agenten.AGENTEN.items()},
                                                  "installiert": [a for a in externe_agenten.AGENTEN if
                                                                  externe_agenten.Externe([a], {"claude": get_setting("CLAUDE_BIN", ""), "codex": get_setting("CODEX_BIN", "")}).verfuegbar()]}})
            m = re.match(r"^/api/werkbank/laeufe/([\w-]+)/(diff|schritte)$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                try:
                    if m.group(2) == "schritte":
                        return self.send_json(werkbank_schritte(m.group(1)))
                    return self.send_json(werkbank_diff(m.group(1)))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                except LookupError as e:
                    return self.send_json({"error": str(e)}, 404)
            if p == "/api/destillation" or p.startswith("/api/destillation/"):
                if not self._nur_besitzer(rolle):
                    return
                try:
                    if p == "/api/destillation":
                        return self.send_json(destillation_uebersicht())
                    m = re.match(r"^/api/destillation/([\w]+)(?:/proben/([\w-]+))?$", p)
                    if m and m.group(2):
                        return self.send_json(destillation_probe(m.group(1), m.group(2)))
                    if m:
                        return self.send_json(destillation_detail(m.group(1)))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                except LookupError as e:
                    return self.send_json({"error": str(e)}, 404)
                return self.send_json({"error": "not found"}, 404)
            if p == "/api/training":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json(training_uebersicht())
            if p == "/api/rezept":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json(rezept_uebersicht())
            m = re.match(r"^/api/rezept/vorlage/([\w-]+)$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                try:
                    return self.send_json(rezept_vorlage(m.group(1)))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 404)
            if p == "/api/mcp":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json(mcp_uebersicht())
            m = re.match(r"^/api/werkbank/checkpunkte(?:/([\w-]+))?$", p)
            if m:
                # Checkpunkte zeigen Dateiinhalte beliebiger Projektordner — Besitzer.
                if not self._nur_besitzer(rolle):
                    return
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                wahl = {k: v[0] for k, v in qs.items()}
                try:
                    ordner = werkbank_ordner(wahl)
                    if m.group(1):
                        return self.send_json(werkbank_checkpunkt(ordner, m.group(1)))
                    return self.send_json(werkbank_checkpunkte(ordner))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
            if p == "/api/mesh/kontakte":
                return self.send_json(mesh_kontakte())
            if p == "/api/mesh/modelle":
                return self.send_json(mesh_modelle())
            if p == "/api/mesh/verteilt/lage":
                return self.send_json(mesh_verteilt_lage())
            if p == "/api/mesh/verteilt":
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]
                                           if "?" in self.path else "")
                try:
                    return self.send_json(mesh_verteilt(
                        qs.get("modell", [""])[0],
                        qs.get("gb", [None])[0], qs.get("schichten", [None])[0]))
                except Exception as e:
                    return self.send_json({"fehler": str(e)}, 400)
            if p == "/api/mesh/auftrag":
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]
                                           if "?" in self.path else "")
                try:
                    return self.send_json(mesh_auftrag_lage(
                        qs.get("id", [""])[0]))
                except Exception as e:
                    return self.send_json({"fehler": str(e)}, 400)
            if p in ("/api/mesh/faeden", "/api/mesh/faden"):
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]
                                           if "?" in self.path else "")
                raum = qs.get("raum", ["allgemein"])[0]
                try:
                    if p.endswith("faeden"):
                        return self.send_json(mesh_faeden(raum))
                    return self.send_json(mesh_faden(
                        qs.get("id", [""])[0], raum))
                except Exception as e:
                    return self.send_json({"fehler": str(e)}, 400)
            if p == "/api/mesh/chat":
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]
                                           if "?" in self.path else "")
                try:
                    return self.send_json(mesh_chat(qs.get("name", [""])[0]))
                except Exception as e:
                    return self.send_json({"fehler": str(e)}, 400)
            if p == "/api/settings":
                return self.api_get_settings()
            if p == "/api/models":
                return self.api_models()
            if p == "/api/sessions":
                return self.api_list("sessions", "updated_at DESC")
            m = re.match(r"^/api/sessions/([\w-]+)/messages$", p)
            if m:
                return self.api_messages(m.group(1))
            for table in ("agents", "prompts", "skills", "templates", "notifications",
                          "knowledge", "artifacts", "pipelines"):
                if p == "/api/" + table:
                    return self.api_list(table, "created_at DESC")
            if p == "/api/extern/fragebogen":
                if not self._extern_frei():
                    return
                stufe = self._ausgang_stufe()
                return self._extern_senden(extern_fragebogen(), p, stufe)
            if p == "/api/extern/info":
                # Auch bei abgeschaltetem Zugang beantwortbar: Ein Harness soll
                # erfahren, WARUM es nichts bekommt, statt im Dunkeln zu raten.
                stufe = self._ausgang_stufe()
                daten = {"produkt": "Dive on Wide", "stufe": stufe,
                         "stufen": list(ausgang.STUFEN),
                         "erklaerung": ausgang.ERKLAERUNG[stufe],
                         "auftrag_starten": "POST /api/extern/auftrag {\"aufgabe\": \"…\"}",
                         "auftrag_lesen": "GET /api/extern/auftrag/<id>",
                         "zuerst": "AGENTS.md lesen, dann einmal GET /api/extern/fragebogen "
                                   "und die Antworten des Nutzers mit POST /api/extern/profil ablegen"}
                return self._extern_senden(daten, p, stufe)
            m = re.match(r"^/api/extern/auftrag/([\w-]+)$", p)
            if m:
                if not self._extern_frei():
                    return
                stufe, lauf_id = self._ausgang_stufe(), m.group(1)
                try:
                    auftrag = werkbank_spur(lauf_id)
                except (ValueError, LookupError):
                    auftrag = None
                if auftrag is None:
                    # Noch nichts abgelegt — dann läuft er vielleicht gerade.
                    conn = db()
                    r = conn.execute("SELECT status FROM runs WHERE id=?",
                                     (lauf_id,)).fetchone()
                    conn.close()
                    if not r:
                        return self.send_json({"error": "Diesen Auftrag gibt es nicht."}, 404)
                    zustand = "laeuft" if r["status"] == "running" else r["status"]
                    return self._extern_senden({"id": lauf_id, "zustand": zustand,
                                                "stufe": stufe}, p, stufe, (), lauf_id)
                bericht, entfernt = ausgang.bericht(dict(auftrag, id=lauf_id), stufe,
                                                    auftrag.get("ordner", ""))
                pruef_id = _extern_pruefungen.get(lauf_id)
                if pruef_id:
                    try:
                        pspur = werkbank_spur(pruef_id)
                    except (ValueError, LookupError):
                        pspur = None
                    if pspur is None:
                        bericht["pruefung"] = {"zustand": "laeuft", "weiter": "dieselbe Adresse später erneut abrufen"}
                    else:
                        pb, pw = ausgang.bericht(dict(pspur, id=pruef_id), stufe, pspur.get("ordner", ""))
                        bericht["pruefung"] = {"zustand": "fertig", "beendet": pb.get("beendet"),
                                               "ergebnis": pb.get("zusammenfassung") or pb.get("ergebnis")
                                               or pb.get("frage")}
                        entfernt = list(entfernt) + list(pw)
                if extern_profil().get("rueckmeldung") == "knapp" and isinstance(bericht.get("verlauf"), list):
                    bericht["schritte"] = len(bericht.pop("verlauf"))      # spart dem Gerüst das Mitlesen
                return self._extern_senden(bericht, p, stufe, entfernt, lauf_id)
            if p == "/api/ausgang":
                if not self._nur_besitzer(rolle):
                    return
                ausgang.ausschuetten(STORAGE_DIR, alles=True)     # Gebündeltes zuerst ins Buch
                s_summe = ausgang.summe(STORAGE_DIR)
                conn = db()
                schluessel = [{"name": r["name"], "seit": r["created_at"]}
                              for r in conn.execute(
                                  "SELECT name, created_at FROM tokens WHERE rolle='harness' "
                                  "ORDER BY created_at DESC").fetchall()]
                conn.close()
                return self.send_json({
                    "stufe": self._ausgang_stufe(), "stufen": list(ausgang.STUFEN),
                    "erklaerung": ausgang.ERKLAERUNG, "summe": s_summe,
                    "text": ausgang.summe_text(s_summe),
                    "buch": ausgang.buch_lesen(STORAGE_DIR, 20),
                    "schluessel": schluessel})
            if p == "/api/web/search":
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                return self.send_json({"results": web_search(qs.get("q", [""])[0])})
            if p == "/api/web/fetch":
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                return self.send_json({"text": web_fetch(qs.get("url", [""])[0])})
            if p == "/api/dashboard":
                return self.api_dashboard()
            if p == "/api/tokens":
                if not self._nur_besitzer(rolle):
                    return
                conn = db()
                toks = rows(conn.execute(
                    "SELECT * FROM tokens ORDER BY created_at"))
                conn.close()
                return self.send_json({"tokens": toks})
            m = re.match(r"^/api/runs/([\w-]+)$", p)
            if m:
                conn = db()
                r = conn.execute("SELECT * FROM runs WHERE id=?", (m.group(1),)).fetchone()
                conn.close()
                if not r:
                    return self.send_json({"error": "unbekannter Lauf"}, 404)
                d = dict(r)
                d["progress"] = json.loads(d["progress"] or "[]")
                d["gast"] = d["id"] in GAST_LAEUFE     # mit Gast-Schluessel gestartet: kein Code, kein Browser
                return self.send_json(d)
            if p == "/api/browser/diagnose":
                return self.send_json(browser_info(refresh=True))
            if p == "/api/computer/diagnose":
                return self.send_json(computer_info(refresh=True))
            if p == "/api/web/diagnose":
                return self.send_json(web_bridge_info(refresh=True))
            if p == "/api/anbieter/suchen":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json(lokale_server_suchen())
            if p == "/api/providers":
                # API-Keys werden maskiert (nur has_key), nie ausgeliefert.
                provs = [{"id": x.get("id"), "name": x.get("name"),
                          "type": "gguf" if x.get("gguf") else x.get("type"),
                          "base_url": x["gguf"]["pfad"] if x.get("gguf") else x.get("base_url"),
                          "kontext": (x.get("gguf") or {}).get("kontext"),
                          "laeuft": GGUF.laeuft(x.get("id")) if x.get("gguf") else None,
                          "has_key": bool(x.get("api_key"))} for x in get_providers()]
                return self.send_json({"providers": provs})
            if p == "/api/steptypes":
                return self.send_json({"types": [
                    dict(key=k, **v) for k, v in STEP_META.items()]})
            if p == "/api/sandbox/workspaces":
                names = sorted(d for d in os.listdir(WORKSPACES_DIR)
                               if os.path.isdir(os.path.join(WORKSPACES_DIR, d)))
                return self.send_json({"workspaces": names,
                                       "docker": docker_available(),
                                       "runtimes": {k: v["label"]
                                                    for k, v in RUNTIMES.items()}})
            m = re.match(r"^/api/sandbox/files/([\w.-]+)$", p)
            if m:
                return self.send_json({"files": ws_list_files(m.group(1))})
            m = re.match(r"^/api/sandbox/file/([\w.-]+)$", p)
            if m:
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                path = ws_file(m.group(1), qs.get("name", [""])[0])
                if not os.path.exists(path):
                    return self.send_json({"content": ""})
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    return self.send_json({"content": f.read()})
            if p == "/api/backup":
                if not self._nur_besitzer(rolle):
                    return
                data = build_backup_zip()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition",
                                 'attachment; filename="dowos-backup-%s.zip"'
                                 % time.strftime("%Y%m%d-%H%M"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            m = re.match(r"^/api/artifacts/([\w-]+)/download$", p)
            if m:
                return self.api_artifact_download(m.group(1))
            m = re.match(r"^/api/artifacts/([\w-]+)/content$", p)
            if m:
                return self.api_artifact_content(m.group(1))
            # Ein Artefakt unter seiner eigenen Adresse. Es gab nur
            # /download und /content — wer aus einem Lauf eine artifact_id
            # bekam und sie naheliegend abrief, las "not found", obwohl das
            # Artefakt in der Liste stand. Fuer ein fremdes Geruest, das
            # Dive on Wide ueber die Schnittstelle steuert, ist das eine Sackgasse.
            m = re.match(r"^/api/artifacts/([\w-]+)$", p)
            if m:
                return self.api_artifact(m.group(1))
            return self.send_json({"error": "not found"}, 404)
        except Exception as e:
            return self.send_json({"error": str(e)}, 500)

    def do_POST(self):
        p = self.path.split("?")[0]
        m_hook = re.match(r"^/api/hooks/([0-9a-f]{16})$", p)
        if m_hook:
            # Von außen, ohne Zugangsschlüssel — geschützt durch die HMAC-Signatur des Webhooks.
            laenge = int(self.headers.get("Content-Length") or 0)
            if laenge > 1024 * 1024:
                return self.send_json({"error": "Rumpf zu groß (höchstens 1 MB)."}, 413)
            status, antwort = webhook_ausloesen(m_hook.group(1), self.rfile.read(laenge) if laenge else b"", self.headers)
            return self.send_json(antwort, status)
        rolle = self._auth()
        if not rolle:
            return
        try:
            if p == "/api/chat":
                return self.api_chat_stream()
            if p == "/api/settings":
                if not self._nur_besitzer(rolle):
                    return
                return self.api_save_settings()
            # Das Mesh ist Besitzer-Sache: Wer die eigene Hardware
            # beisteuert, entscheidet das selbst — kein Gast, kein Automatismus.
            if p == "/api/einrichtung":
                if not self._nur_besitzer(rolle):
                    return
                try:
                    return self.send_json(
                        einrichtung_speichern(self.read_body()))
                except Exception as e:
                    return self.send_json({"fehler": str(e)}, 400)
            if p == "/api/modelle/laden":
                # Ein Modell holen, das die Einrichtung vorschlaegt — Gigabytes aus dem Netz,
                # also nur der Besitzer und nur, was in modellempfehlungen.json steht.
                if not self._nur_besitzer(rolle):
                    return
                tag = feld_text(self.read_body(), "tag").strip()
                erlaubt = {m["tag"] for m in hardware.katalog_laden()}
                if not MODELL_TAG_RE.match(tag) or tag not in erlaubt:
                    return self.send_json({"error": "Dieses Modell steht nicht in der Empfehlungsliste."}, 400)
                run_id = run_begin("modell", "Modell laden: " + tag)
                background(modell_laden, tag, run_id)
                return self.send_json({"ok": True, "run_id": run_id})
            # Der Rhythmus laesst Dive on Wide von selbst arbeiten — Besitzer-Sache.
            m_rhy = re.match(r"^/api/zeitplan/(neu|um|weg|jetzt)$", p)
            if m_rhy:
                if not self._nur_besitzer(rolle):
                    return
                koerper = self.read_body()
                try:
                    tu = m_rhy.group(1)
                    if tu == "neu":
                        return self.send_json(zeitplan_anlegen(koerper))
                    if tu == "um":
                        return self.send_json(zeitplan_umschalten(koerper.get("id", "")))
                    if tu == "weg":
                        return self.send_json(zeitplan_loeschen(koerper.get("id", "")))
                    return self.send_json(zeitplan_jetzt(koerper.get("id", "")))
                except Exception as e:
                    return self.send_json({"fehler": str(e)}, 400)
            if p == "/api/mesh/start":
                if not self._nur_besitzer(rolle):
                    return
                koerper = self.read_body()
                try:
                    return self.send_json(mesh_starten(
                        koerper.get("betriebsart", "klause"),
                        koerper.get("abgeben_gb")))
                except Exception as e:
                    return self.send_json({"fehler": str(e)}, 400)
            if p == "/api/mesh/verteilt/start":
                if not self._nur_besitzer(rolle):
                    return
                koerper = self.read_body()
                try:
                    return self.send_json(mesh_verteilt_starten(
                        koerper.get("modell", ""), koerper.get("port")))
                except Exception as e:
                    return self.send_json({"fehler": str(e)}, 400)
            if p == "/api/mesh/verteilt/stop":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json(mesh_verteilt_stoppen())
            if p == "/api/mesh/rpc":
                if not self._nur_besitzer(rolle):
                    return
                koerper = self.read_body()
                try:
                    return self.send_json(mesh_rpc_schalten(koerper.get("port", 0)))
                except Exception as e:
                    return self.send_json({"fehler": str(e)}, 400)
            if p == "/api/mesh/stop":
                if not self._nur_besitzer(rolle):
                    return
                return self.send_json(mesh_stoppen())
            # Der Messenger ist Besitzer-Sache: Anker sind Geheimnisse.
            m_msg = re.match(r"^/api/mesh/(anker|kontakt|kontakt-weg|"
                             r"kontakt-ok|senden|faden-neu|beitrag|"
                             r"faden-zu|auftrag-neu|auftraege-an|"
                             r"ticket-neu|ticket-ein|autostart)$", p)
            if m_msg:
                if not self._nur_besitzer(rolle):
                    return
                koerper = self.read_body()
                was = m_msg.group(1)
                try:
                    if was == "anker":
                        return self.send_json(mesh_anker_neu())
                    if was == "kontakt":
                        return self.send_json(mesh_kontakt_anlegen(
                            koerper.get("name", ""), koerper.get("anker", "")))
                    if was == "kontakt-weg":
                        return self.send_json(mesh_kontakt_loeschen(
                            koerper.get("name", "")))
                    if was == "kontakt-ok":
                        return self.send_json(mesh_kontakt_bestaetigen(
                            koerper.get("name", "")))
                    if was == "faden-neu":
                        return self.send_json(mesh_faden_neu(
                            koerper.get("titel", ""), koerper.get("text", ""),
                            koerper.get("raum", "allgemein"),
                            koerper.get("stunden", 6)))
                    if was == "beitrag":
                        # Beide Feldnamen annehmen: „faden" ist der sprechende,
                        # „id" der historische. Ein stiller Fehlgriff hier
                        # erzeugte Saetze, die niemand mehr lesen konnte.
                        return self.send_json(mesh_beitrag(
                            koerper.get("faden") or koerper.get("id", ""),
                            koerper.get("text", ""),
                            koerper.get("raum", "allgemein")))
                    if was == "autostart":
                        return self.send_json(mesh_autostart_setzen(
                            koerper.get("art", ""), koerper.get("gb")))
                    if was == "ticket-neu":
                        return self.send_json(mesh_ticket_neu(
                            koerper.get("host", ""), koerper.get("notiz", "")))
                    if was == "ticket-ein":
                        return self.send_json(mesh_ticket_einloesen(
                            koerper.get("ticket", "")))
                    if was == "auftrag-neu":
                        return self.send_json(mesh_auftrag_starten(
                            koerper.get("prompt", ""), koerper.get("modell", ""),
                            koerper.get("hoechstens", 2)))
                    if was == "auftraege-an":
                        return self.send_json(mesh_auftraege_erlauben(
                            koerper.get("ja", True)))
                    if was == "faden-zu":
                        return self.send_json(mesh_faden_schliessen(
                            koerper.get("faden") or koerper.get("id", ""),
                            koerper.get("raum", "allgemein")))
                    return self.send_json(mesh_senden(
                        koerper.get("name", ""), koerper.get("text", ""),
                        koerper.get("umwege", 0)))
                except Exception as e:
                    return self.send_json({"fehler": str(e)}, 400)
            if p == "/api/tokens":
                if not self._nur_besitzer(rolle):
                    return
                return self.api_create_token()
            if p == "/api/providers":
                if not self._nur_besitzer(rolle):
                    return
                return self.api_save_provider()
            if p == "/api/providers/test":
                if not self._nur_besitzer(rolle):
                    return
                return self.api_test_provider()
            m = re.match(r"^/api/runs/([\w-]+)/cancel$", p)
            if m:
                if not run_cancel(m.group(1)):
                    return self.send_json(
                        {"error": "Lauf nicht gefunden oder bereits beendet."}, 404)
                return self.send_json({"ok": True, "status": "cancelling"})
            m = re.match(r"^/api/runs/([\w-]+)/confirm$", p)
            if m:
                # Nur der Besitzer darf eine wartende Steuerungs-Aktion freigeben.
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                if not run_confirm(m.group(1), bool(body.get("ok")),
                                   bool(body.get("alle"))):
                    return self.send_json(
                        {"error": "Lauf nicht gefunden oder nicht mehr aktiv."}, 404)
                return self.send_json({"ok": True})
            if p == "/api/computer/steuern":
                # Steuerung bewegt Maus/Tastatur des Rechners — strikt Besitzer.
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                auftrag = (body.get("auftrag") or "").strip()
                if not auftrag:
                    return self.send_json(
                        {"error": "Auftrag fehlt — was soll erledigt werden?"}, 400)
                run_id = run_begin("computer", "Computer-Use: " + auftrag[:60],
                                   body.get("session_id"))
                background(run_computer_task, auftrag,
                           body.get("session_id"), run_id)
                return self.send_json({"run_id": run_id})
            if p == "/api/computer/zeig":
                # Besitzer-Sache: Ein Screenshot zeigt den Bildschirm des
                # Rechners — Gäste (Alpha-Tester im LAN) haben dort nichts
                # zu suchen.
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                beschreibung = (body.get("beschreibung") or "").strip()
                if not beschreibung:
                    return self.send_json(
                        {"error": "Beschreibung fehlt — was soll gefunden "
                                  "werden? Beispiel: der rote Senden-Knopf"}, 400)
                run_id = run_begin("computer",
                                   "Zeig mir wo: " + beschreibung[:60],
                                   body.get("session_id"))
                background(run_computer_zeig, beschreibung,
                           body.get("session_id"), run_id)
                return self.send_json({"run_id": run_id})
            if p == "/api/sessions":
                return self.api_create_session()
            m = re.match(r"^/api/sessions/([\w-]+)/messages$", p)
            if m:
                return self.api_add_message(m.group(1))
            if p == "/api/agents":
                return self.api_create("agents",
                    ["name", "description", "system_prompt", "model", "emoji"])
            if p == "/api/prompts":
                return self.api_create("prompts",
                    ["name", "description", "content", "category"])
            if p == "/api/skills":
                return self.api_create_skill()
            if p == "/api/templates":
                return self.api_create_template()
            if p == "/api/knowledge":
                return self.api_create("knowledge", ["name", "content", "folder"])
            if p == "/api/artifacts":
                return self.api_create_artifact()
            m = re.match(r"^/api/skills/([\w-]+)/run$", p)
            if m:
                return self.api_run_skill(m.group(1))
            if p == "/api/notifications/read_all":
                conn = db(); conn.execute("UPDATE notifications SET read=1")
                conn.commit(); conn.close()
                return self.send_json({"ok": True})
            if p == "/api/pipelines":
                return self.api_create_pipeline()
            m = re.match(r"^/api/pipelines/([\w-]+)/run$", p)
            if m:
                return self.api_run_pipeline(m.group(1))
            if p == "/api/brain/sync":
                body = self.read_body()
                threading.Thread(target=sync_second_brain,
                                 args=(body.get("session_id"),), daemon=True).start()
                return self.send_json({"ok": True, "status": "running",
                                       "info": "Second-Brain-Sync läuft im Hintergrund."})
            if p == "/api/brain/evolve":
                threading.Thread(target=evolve_brain, daemon=True).start()
                return self.send_json({"ok": True, "status": "running",
                                       "info": "Evolver läuft — Ergebnis in der Inbox."})
            if p == "/api/schwarm":
                body = self.read_body()
                ziel = feld_text(body, "ziel").strip()
                if not ziel:
                    return self.send_json({"error": "Bitte ein Ziel beschreiben."}, 400)
                sitzung = feld_text(body, "session_id", None)
                arbeiter = [str(a) for a in (body.get("arbeiter") or []) if isinstance(a, str)][:4]
                run_id = run_begin("schwarm", "Schwarm: " + ziel[:60], sitzung)
                background(run_schwarm, ziel, sitzung, run_id, feld_text(body, "planer", ""), arbeiter,
                           body.get("runden") or 3, feld_text(body, "testbefehl", ""), feld_text(body, "angeheftet", ""))
                return self.send_json({"run_id": run_id})
            if p == "/api/orchestrator":
                body = self.read_body()
                ziel = feld_text(body, "ziel").strip()
                if not ziel:
                    return self.send_json(
                        {"error": "Bitte ein Ziel beschreiben."}, 400)
                sitzung = feld_text(body, "session_id", None)
                run_id = run_begin("orchestrator",
                                   "Orchestrator: " + ziel[:60], sitzung)
                background(run_orchestrator, ziel, sitzung, run_id, feld_text(body, "modell", "") or None,
                           feld_text(body, "angeheftet", ""))
                return self.send_json({"run_id": run_id})
            if p == "/api/research/run":
                body = self.read_body()
                if not body.get("topic", "").strip():
                    return self.send_json({"error": "Bitte ein Thema angeben."}, 400)
                run_id = run_begin("research", "Deep Research: " + body["topic"][:60],
                                   body.get("session_id"))
                background(run_deep_research, body["topic"], body.get("loops", 3),
                           body.get("model", ""), bool(body.get("use_web", True)),
                           body.get("session_id"), run_id)
                return self.send_json({"ok": True, "status": "running", "run_id": run_id,
                                       "title": "Deep Research",
                                       "info": "Deep Research läuft."})
            if p == "/api/web/browser_task":
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                if not body.get("task", "").strip():
                    return self.send_json({"error": "Bitte eine Aufgabe angeben."}, 400)
                info = browser_info(refresh=True)
                if not info["browser_use"]:
                    return self.send_json(
                        {"error": "browser-use ist nicht installiert. Nachrüsten mit: "
                                  "pip3 install browser-use ollama — die eingebaute "
                                  "Web-Recherche funktioniert auch ohne.",
                         "diagnose": info}, 400)
                if not info["bereit"]:
                    return self.send_json(
                        {"error": "Browser-Automation ist noch nicht einsatzbereit: %s"
                                  % " ".join(info["hinweise"]), "diagnose": info}, 400)
                run_id = run_begin("browser", "Browser: " + body["task"][:60],
                                   body.get("session_id"))
                background(run_browser_task, body["task"],
                           body.get("model") or get_setting("DEFAULT_MODEL"),
                           body.get("session_id"), run_id, body.get("max_steps"))
                return self.send_json({"ok": True, "status": "running",
                                       "run_id": run_id, "title": "Browser-Auftrag"})
            if p == "/api/meta/create":
                return self.api_meta_create()
            # --- Code-Sandbox ---
            if p == "/api/sandbox/run":
                if not self._nur_besitzer(rolle):
                    return
                if get_setting("SANDBOX_ENABLED", "0") != "1":
                    return self.send_json(
                        {"error": "Die Code-Sandbox ist in den Einstellungen "
                                  "deaktiviert."}, 403)
                body = self.read_body()
                if not body.get("code", "").strip():
                    return self.send_json({"error": "Kein Code übergeben."}, 400)
                return self.send_json(run_code(
                    body.get("workspace", "default"), body["code"],
                    body.get("runtime", "python"), body.get("filename"),
                    int(body.get("timeout", 60))))
            if p == "/api/sandbox/shell":
                if not self._nur_besitzer(rolle):
                    return
                if get_setting("SANDBOX_ENABLED", "0") != "1":
                    return self.send_json({"error": "Sandbox deaktiviert."}, 403)
                body = self.read_body()
                rc, out, err = run_in_workspace(body.get("workspace", "default"),
                                                body.get("command", ""),
                                                int(body.get("timeout", 120)))
                return self.send_json({"exit_code": rc, "stdout": out, "stderr": err})
            if p == "/api/sandbox/save":
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                path = ws_file(feld_text(body, "workspace", "default"),
                               feld_text(body, "name", "neu.txt"))
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(feld_text(body, "content"))
                except OSError as e:          # Name zu lang, Ordner statt Datei, …
                    raise UngueltigerRumpf("Datei laesst sich nicht schreiben: %s" % e.strerror)
                return self.send_json({"ok": True})
            if p == "/api/sandbox/venv":
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                steps = ws_setup_venv(body.get("workspace", "default"),
                                      body.get("packages", ""))
                return self.send_json({"steps": [
                    {"label": s[0], "exit_code": s[1], "stdout": s[2], "stderr": s[3]}
                    for s in steps]})
            if p == "/api/sandbox/workspaces":
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                ws_path(body.get("name", "default"))
                return self.send_json({"ok": True})
            if p == "/api/mcp" or p.startswith("/api/mcp/"):
                # MCP-Server sind Programme, die Dive on Wide startet — Besitzer.
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                try:
                    if p == "/api/mcp":
                        return self.send_json(mcp_speichern(body))
                    m = re.match(r"^/api/mcp/([\w.-]+)/pruefen$", p)
                    if m:
                        return self.send_json(mcp_client.pruefen(m.group(1), mcp_konfig()))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                except mcp_client.McpFehler as e:
                    return self.send_json({"error": str(e)}, 502)
                return self.send_json({"error": "not found"}, 404)
            if p.startswith("/api/destillation/"):
                # Schickt Aufgaben an bezahlte Anbieter und startet Prozesse — Besitzer.
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                try:
                    if p == "/api/destillation/schaetzen":
                        return self.send_json(destillation_schaetzen(body))
                    if p == "/api/destillation/starten":
                        return self.send_json(destillation_starten(body))
                    if p == "/api/destillation/datenwert":
                        return self.send_json(datenwert_starten(body))
                    if p == "/api/destillation/preise":
                        preise = _lehrer_preise()
                        ref = str(body.get("modell") or "")
                        if not ref:
                            raise ValueError("modell fehlt")
                        if body.get("loeschen"):
                            preise.pop(ref, None)
                        else:
                            lehrer_modul.Preis(body.get("eingabe"), body.get("ausgabe"), body.get("cache_eingabe"))
                            preise[ref] = {"eingabe": float(body["eingabe"]), "ausgabe": float(body["ausgabe"]),
                                           "cache_eingabe": float(body["cache_eingabe"]) if body.get("cache_eingabe") not in (None, "") else None,
                                           "waehrung": str(body.get("waehrung") or "USD")[:3]}
                        set_setting("LEHRER_PREISE", json.dumps(preise))
                        return self.send_json({"preise": preise})
                    if p == "/api/destillation/preise/openrouter":
                        prov = get_provider(str(body.get("anbieter") or "")) or {}
                        if "openrouter" not in (prov.get("base_url") or ""):
                            raise ValueError("Preise lassen sich nur von einem OpenRouter-Anbieter abfragen.")
                        preise, gefunden = _lehrer_preise(), 0
                        for mid, pr in lehrer_modul.openrouter_preise().items():
                            if not body.get("modelle") or mid in body["modelle"]:
                                preise["%s@@%s" % (prov["id"], mid)] = pr.als_dict()
                                gefunden += 1
                        set_setting("LEHRER_PREISE", json.dumps(preise))
                        return self.send_json({"preise": preise, "gefunden": gefunden})
                    if p == "/api/destillation/bedingungen":
                        b = _bedingungen()
                        pid = str(body.get("anbieter") or "")
                        if not get_provider(pid):
                            raise LookupError("Anbieter gibt es nicht.")
                        if body.get("bestaetigt"):
                            b[pid] = time.time()
                        else:
                            b.pop(pid, None)
                        set_setting("LEHRER_BEDINGUNGEN", json.dumps(b))
                        return self.send_json({"bedingungen": b})
                    m = re.match(r"^/api/destillation/([\w]+)/(export|archivieren)$", p)
                    if m:
                        return self.send_json(destillation_export(m.group(1), body) if m.group(2) == "export"
                                              else destillation_archivieren(m.group(1), body))
                except (ValueError, TypeError) as e:
                    return self.send_json({"error": str(e)}, 400)
                except LookupError as e:
                    return self.send_json({"error": str(e)}, 404)
                except RuntimeError as e:
                    return self.send_json({"error": str(e)}, 409)
                except (urllib.error.URLError, OSError) as e:
                    return self.send_json({"error": "Nicht erreichbar: %s" % e}, 502)
                return self.send_json({"error": "not found"}, 404)
            if p.startswith("/api/rezept/"):
                # Ein Rezept startet ein Training — Besitzer.
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                try:
                    if p == "/api/rezept/plan":
                        return self.send_json(rezept_plan(body))
                    if p == "/api/rezept/starten":
                        return self.send_json(rezept_starten(body))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                except LookupError as e:
                    return self.send_json({"error": str(e)}, 404)
                except RuntimeError as e:
                    return self.send_json({"error": str(e)}, 409)
                return self.send_json({"error": "not found"}, 404)
            if p.startswith("/api/training/"):
                # Training startet Prozesse, die den Rechner stundenlang auslasten — Besitzer.
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                wb = trainings_werkbank()
                try:
                    if p == "/api/training/starten":
                        return self.send_json(wb.starten(str(body.get("modell") or get_setting("SCHUELER_BASIS", "")),
                                                         str(body.get("daten") or ""),
                                                         str(body.get("name") or ""), body.get("werte") or {}))
                    if p == "/api/training/daten":
                        return self.send_json(training_daten_exportieren(body))
                    if p in ("/api/training/fabrik", "/api/training/loesen", "/api/training/pruefen"):
                        return self.send_json(training_auftrag(p.rsplit("/", 1)[1], body))
                    m = re.match(r"^/api/training/([\w-]+)/ollama$", p)
                    if m:
                        return self.send_json(training_ollama(m.group(1), body))
                    m = re.match(r"^/api/training/([\w-]+)/(stoppen|vergessen|bereitstellen|beenden)$", p)
                    if m:
                        return self.send_json({"stoppen": wb.stoppen, "vergessen": wb.vergessen,
                                               "bereitstellen": wb.bereitstellen,
                                               "beenden": wb.bereitstellung_beenden}[m.group(2)](m.group(1)))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                except LookupError as e:
                    return self.send_json({"error": str(e)}, 404)
                except RuntimeError as e:
                    return self.send_json({"error": str(e)}, 409)
                return self.send_json({"error": "not found"}, 404)
            if p == "/api/werkbank/gedaechtnis":
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                try:
                    feld_text(body, "ordner"), feld_text(body, "workspace")
                    notizen = body.get("notizen") or []
                    if not isinstance(notizen, list) or not all(
                            isinstance(n, str) or (isinstance(n, dict) and isinstance(n.get("text", ""), str))
                            for n in notizen):
                        raise UngueltigerRumpf("„notizen“ muss eine Liste aus Texten oder {\"text\": …} sein.")
                    ordner = werkbank_ordner(body) if (body.get("ordner") or body.get("workspace")) else None
                    werkbank_gedaechtnis.Gedaechtnis(WERKBANK_DIR).ersetzen(notizen, ordner)
                    return self.send_json(werkbank_gedaechtnis_lage(ordner))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
            m = re.match(r"^/api/(telegram|discord|slack)(/koppelcode)?$", p)
            if m:
                # Wer einen Boten steuert, steuert die Werkbank — Besitzer.
                if not self._nur_besitzer(rolle):
                    return
                if m.group(2):
                    bote = _boten[m.group(1)]["bote"]
                    if not bote:
                        return self.send_json({"error": "%s ist nicht aktiv." % BOTEN[m.group(1)]["name"]}, 409)
                    return self.send_json({"code": bote.koppelcode(), "minuten": telegram_bote.KOPPEL_MINUTEN})
                try:
                    return self.send_json(boten_speichern(m.group(1), self.read_body()))
                except (ValueError, TypeError) as e:
                    return self.send_json({"error": str(e)}, 400)
            if p == "/api/lotse":
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                frage = (feld_text(body, "frage") or "").strip()[:2000]
                if not frage:
                    return self.send_json({"error": "Was möchtest du wissen?"}, 400)
                verlauf = body.get("verlauf") if isinstance(body.get("verlauf"), list) else []
                try:
                    return self.send_json(lotse_fragen(frage, [v for v in verlauf if isinstance(v, dict)][-6:],
                                                       feld_text(body, "modell") or None, feld_text(body, "sprache") or ""))
                except Exception as e:
                    return self.send_json({"error": "Das Modell hat nicht geantwortet: %s" % str(e)[:200]}, 502)
            if p == "/api/discord-server":
                if not self._nur_besitzer(rolle):
                    return
                try:
                    return self.send_json(discord_server_speichern(self.read_body()))
                except (ValueError, TypeError) as e:
                    return self.send_json({"error": str(e)}, 400)
            m = re.match(r"^/api/werkbank/laeufe/([\w-]+)/commit$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                try:
                    return self.send_json(werkbank_commit(m.group(1), str(self.read_body().get("nachricht") or "")))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                except LookupError as e:
                    return self.send_json({"error": str(e)}, 404)
            m = re.match(r"^/api/werkbank/webhooks(?:/([0-9a-f]{16})/(loeschen|umschalten))?$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                try:
                    if m.group(1):
                        return self.send_json({"webhooks": webhook_aendern(m.group(1), m.group(2))})
                    return self.send_json(webhook_anlegen(body))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                except LookupError as e:
                    return self.send_json({"error": str(e)}, 404)
            m = re.match(r"^/api/werkbank/agenten(?:/([a-z0-9_-]+)/loeschen)?$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                try:
                    if m.group(1):
                        agentenprofile.loeschen(AGENTEN_DIR, m.group(1))
                    else:
                        agentenprofile.speichern(AGENTEN_DIR, body)
                    return self.send_json({"ok": True, "profile": list(agentenprofile.finden(None, AGENTEN_DIR).values())})
                except (ValueError, TypeError) as e:
                    return self.send_json({"error": str(e)}, 400)
                except LookupError as e:
                    return self.send_json({"error": str(e)}, 404)
            m = re.match(r"^/api/werkbank/befehle(?:/([a-z0-9_-]+)/loeschen)?$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                try:
                    if m.group(1):
                        werkbank_befehle.loeschen(BEFEHLE_DIR, m.group(1))
                    else:
                        werkbank_befehle.speichern(BEFEHLE_DIR, body.get("name"), body.get("beschreibung", ""), body.get("inhalt", ""))
                    return self.send_json(werkbank_befehle_lage())
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                except LookupError as e:
                    return self.send_json({"error": str(e)}, 404)
            m = re.match(r"^/api/werkbank/(skills|skills/([a-z0-9-]+)/loeschen|laeufe/([\w-]+)/skill-entwurf)$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                body = self.read_body()
                try:
                    if m.group(3):
                        return self.send_json(werkbank_skill_entwurf(m.group(3)))
                    if m.group(2):
                        s = agentskills.finden(None, SKILLS_DIR).get(m.group(2))
                        if not s:
                            return self.send_json({"error": "Skill gibt es nicht."}, 404)
                        shutil.rmtree(s["ordner"])
                        return self.send_json(werkbank_skills())
                    agentskills.speichern(SKILLS_DIR, body.get("name"), body.get("beschreibung"), body.get("inhalt"))
                    return self.send_json(werkbank_skills())
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                except LookupError as e:
                    return self.send_json({"error": str(e)}, 404)
            if p == "/api/werkbank/regeln":
                # Regeln geben Befehle frei — Besitzer.
                if not self._nur_besitzer(rolle):
                    return
                try:
                    return self.send_json(werkbank_regeln_speichern(self.read_body().get("regeln", "")))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
            m = re.match(r"^/api/werkbank/laeufe/([\w-]+)/bewertung$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                try:
                    return self.send_json(werkbank_bewerten(m.group(1), self.read_body().get("bewertung")))
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                except LookupError as e:
                    return self.send_json({"error": str(e)}, 404)
            m = re.match(r"^/api/werkbank/checkpunkte/([\w-]+)/zuruecksetzen$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                try:
                    ordner = werkbank_ordner(self.read_body())
                    return self.send_json({"ok": True, **werkbank_zuruecksetzen(ordner, m.group(1))})
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                except RuntimeError as e:
                    return self.send_json({"error": str(e)}, 409)
            if p == "/api/werkbank/fahrplan":
                if not self._nur_besitzer(rolle):
                    return
                if get_setting("SANDBOX_ENABLED", "0") != "1":
                    return self.send_json({"error": "Code-Ausführung ist abgeschaltet — "
                                           "Einstellungen → Code-Sandbox."}, 403)
                body = self.read_body()
                fid = feld_text(body, "fortsetzen") or ""
                if fid:
                    if not re.fullmatch(r"[\w-]{1,40}", fid) or not os.path.isfile(os.path.join(FAHRPLAN_DIR, fid + ".json")):
                        return self.send_json({"error": "Diese Roadmap gibt es nicht."}, 404)
                    alt = fahrplan.Fahrplan(os.path.join(FAHRPLAN_DIR, fid + ".json")).z
                    if alt.get("status") == "laeuft" and alt.get("run_id") and _lauf_status(alt["run_id"])[0] == "running":
                        return self.send_json({"error": "Diese Roadmap läuft schon."}, 409)
                    pakete, body = alt["pakete"], dict(body, ordner=alt["ordner"], model=body.get("model") or alt.get("modell"))
                    body.pop("workspace", None)
                else:
                    roh = body.get("pakete")
                    if isinstance(roh, list):
                        roh = [x if isinstance(x, dict) else {"titel": str(x), "aufgabe": str(x)} for x in roh]
                        pakete = fahrplan.pakete_lesen(json.dumps(roh))
                    else:
                        pakete = fahrplan.pakete_lesen(feld_text(body, "roadmap") or "")
                    pakete = [p_ for p_ in pakete if p_.get("aufgabe")]
                    if not pakete:
                        return self.send_json({"error": "Keine Pakete erkannt — nummerierte Liste (1. … 2. …) oder JSON."}, 400)
                    if len(pakete) > 30:
                        return self.send_json({"error": "Höchstens 30 Pakete je Roadmap."}, 400)
                stufe = body.get("stufe") or werkbank.STANDARD_STUFE
                politik = body.get("freigabe") or werkbank.STANDARD_FREIGABE
                if stufe not in werkbank.STUFEN or politik not in werkbank.FREIGABEN or stufe == "lesen":
                    return self.send_json({"error": "Unbekannte oder zu geringe Rechtestufe (eine Roadmap ändert Dateien)."}, 400)
                try:
                    ordner = werkbank_ordner(body)
                    reparaturen = max(0, min(int(body.get("max_reparaturen", fahrplan.MAX_REPARATUREN)), 5))
                except (ValueError, TypeError) as e:
                    return self.send_json({"error": str(e) or "max_reparaturen muss eine Zahl sein."}, 400)
                fid = fid or nid()
                run_id = run_begin("code", "Roadmap: %d Pakete in %s" % (len(pakete), os.path.basename(ordner)))
                background(run_fahrplan, run_id, fid, ordner, pakete, body.get("model", ""), stufe, politik, reparaturen)
                return self.send_json({"ok": True, "run_id": run_id, "fahrplan": fid, "pakete": pakete, "ordner": ordner})
            if p == "/api/werkbank":
                # Ein Agent mit Schreibrecht in Ordnern des Rechners — strikt Besitzer.
                if not self._nur_besitzer(rolle):
                    return
                if get_setting("SANDBOX_ENABLED", "0") != "1":
                    return self.send_json({"error": "Code-Ausführung ist abgeschaltet — "
                                           "Einstellungen → Code-Sandbox."}, 403)
                body = self.read_body()
                aufgabe = str(body.get("aufgabe") or "").strip()
                if not aufgabe:
                    return self.send_json({"error": "Bitte eine Aufgabe angeben."}, 400)
                # Abzweigen: bei Schritt N eines früheren (auch abgebrochenen) Laufs weitermachen.
                abzweig, vorher_nachrichten = body.get("abzweigen"), None
                if abzweig:
                    try:
                        vorher_nachrichten, abzweig, meta = werkbank_abzweig_vorbereiten(abzweig)
                    except ValueError as e:
                        return self.send_json({"error": str(e)}, 400)
                    except LookupError as e:
                        return self.send_json({"error": str(e)}, 404)
                    body = dict(body)
                    body["ordner"] = meta.get("ordner")
                    body.pop("workspace", None)
                    for k_neu, k_alt in (("stufe", "stufe"), ("freigabe", "freigabe"), ("model", "modell")):
                        body[k_neu] = body.get(k_neu) or meta.get(k_alt)
                # Fortsetzen: ausdrücklich ein Lauf, oder der letzte Lauf dieses Chats.
                vorgaenger = None if abzweig else body.get("fortsetzen_von") or (
                    werkbank_letzter_lauf(body.get("session_id")) if body.get("fortsetzen") else None)
                if vorgaenger:
                    try:
                        spur = werkbank_spur(vorgaenger)
                    except (ValueError, LookupError) as e:
                        return self.send_json({"error": str(e)}, 404)
                    body = dict(body)
                    body.setdefault("ordner", spur.get("ordner"))
                    body.pop("workspace", None)
                    for k_neu, k_alt in (("stufe", "stufe"), ("freigabe", "freigabe"), ("model", "modell")):
                        body[k_neu] = body.get(k_neu) or spur.get(k_alt)
                stufe = body.get("stufe") or werkbank.STANDARD_STUFE
                politik = body.get("freigabe") or werkbank.STANDARD_FREIGABE
                if stufe not in werkbank.STUFEN or politik not in werkbank.FREIGABEN:
                    return self.send_json({"error": "Unbekannte Rechtestufe oder Freigabe."}, 400)
                try:
                    ordner = werkbank_ordner(body)
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                profil = None
                if body.get("profil"):
                    profil = agentenprofile.finden(ordner, AGENTEN_DIR).get(str(body["profil"]).strip().lower())
                    if not profil:
                        return self.send_json({"error": "Profil „%s“ gibt es nicht." % body["profil"]}, 400)
                    # Ein Profil schränkt nur ein — die strengere Einstellung gilt.
                    stufe, politik = agentenprofile.anwenden(profil, stufe, politik)
                    if profil.get("planmodus"):
                        body = dict(body, planmodus=True)
                    if profil.get("max_schritte") and not body.get("max_schritte"):
                        body = dict(body, max_schritte=profil["max_schritte"])
                try:
                    schritte = max(1, min(int(body.get("max_schritte") or werkbank.MAX_SCHRITTE), 100))
                except (TypeError, ValueError):
                    return self.send_json({"error": "max_schritte muss eine Zahl sein."}, 400)
                bilder = []
                for b in (body.get("bilder") or [])[:4]:
                    roh = str(b).split("base64,", 1)[-1]
                    try:
                        if len(base64.b64decode(roh, validate=True)) > 8_000_000:
                            return self.send_json({"error": "Ein Bild ist größer als 8 MB."}, 400)
                    except (ValueError, TypeError):
                        return self.send_json({"error": "Ein Bild ist nicht lesbar (Base64 erwartet)."}, 400)
                    bilder.append(roh)
                if bilder:
                    faehig = modell_faehigkeiten(body.get("model") or get_setting("WERKBANK_MODELL", "") or get_setting("DEFAULT_MODEL"))
                    if faehig and "vision" not in faehig:
                        return self.send_json({"error": "Das gewählte Modell versteht keine Bilder — wähle ein Modell mit Bildverständnis."}, 400)
                mcp_namen = [str(n) for n in (body.get("mcp") or []) if isinstance(n, str)]
                unbekannt = [n for n in mcp_namen if n not in mcp_konfig()["mcpServers"]]
                if unbekannt:
                    return self.send_json({"error": "MCP-Server nicht konfiguriert: %s" % ", ".join(unbekannt)}, 400)
                # Erst jetzt, nach allen Prüfungen: Ein abgewiesener Auftrag darf keinen Lauf
                # hinterlassen, der für immer „läuft“ (so geschehen bei unbekanntem MCP-Server).
                if abzweig and abzweig.get("zuruecksetzen"):
                    try:
                        abzweig["zurueckgesetzt"] = werkbank_abzweig_zuruecksetzen(abzweig, ordner)
                    except (ValueError, LookupError) as e:
                        return self.send_json({"error": str(e)}, 409)
                run_id = run_begin("code", "Werkbank: " + aufgabe[:60], body.get("session_id"))
                background(run_werkbank, aufgabe, ordner, body.get("model", ""), stufe, politik,
                           schritte, body.get("session_id"), run_id, mcp_namen, vorgaenger,
                           bool(body.get("planmodus")), bool(body.get("web")), False, bilder,
                           profil, vorher_nachrichten, abzweig)
                return self.send_json({"ok": True, "run_id": run_id, "ordner": ordner,
                                       "title": "Werkbank-Diver", "fortsetzung_von": vorgaenger or ""})
            if p == "/api/sprache/fehlend":
                # Deutsche Stücke, die in der englischen Oberfläche noch auftauchten —
                # Futter für die nächste Übersetzung (werkzeuge/sprache.py).
                if not self._nur_besitzer(rolle):
                    return
                neu = [str(t)[:300] for t in (self.read_body().get("texte") or [])[:500] if str(t).strip()]
                pfad = os.path.join(STORAGE_DIR, "sprache_fehlend.json")
                try:
                    alt = json.load(open(pfad, encoding="utf-8"))
                except (OSError, ValueError):
                    alt = []
                alle = sorted(set(alt) | set(neu))[:3000]
                with open(pfad, "w", encoding="utf-8") as f:
                    json.dump(alle, f, ensure_ascii=False)
                return self.send_json({"ok": True, "bekannt": len(alle)})
            if p == "/api/extern/profil":
                if not self._extern_frei():
                    return
                try:
                    neu = extern_profil_speichern(self.read_body())
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                emit("Profil für fremde Gerüste gespeichert", "Arbeiter: %s · Prüfer: %s"
                     % (neu["arbeiter"] or "Standardmodell", neu["pruefer"] or "keiner"))
                return self._extern_senden(neu, p, self._ausgang_stufe())
            if p == "/api/extern/auftrag":
                # Die schmale Tür für fremde Gerüste: eine Aufgabe, ein Ordner,
                # sonst nichts. Rechte und Freigabe kommen aus den Einstellungen
                # dieser Instanz — ein Harness kann sie nicht mitschicken.
                if not self._extern_frei():
                    return
                if get_setting("SANDBOX_ENABLED", "0") != "1":
                    return self.send_json({"error": "Code-Ausführung ist abgeschaltet "
                                           "— Einstellungen → Code-Sandbox."}, 403)
                body = self.read_body()
                aufgabe = str(body.get("aufgabe") or "").strip()
                if not aufgabe:
                    return self.send_json(
                        {"error": "Bitte eine Aufgabe angeben (Feld „aufgabe“)."}, 400)
                try:
                    ordner = werkbank_ordner({"workspace": body.get("workspace") or "extern"})
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                try:
                    schritte = max(1, min(int(body.get("max_schritte")
                                              or werkbank.MAX_SCHRITTE), 40))
                except (TypeError, ValueError):
                    return self.send_json({"error": "max_schritte muss eine Zahl sein."}, 400)
                profil = extern_profil()
                if not body.get("max_schritte"):
                    schritte = profil["max_schritte"]
                run_id = run_begin("code", "Werkbank (extern): " + aufgabe[:60], None)
                background(extern_auftrag_ausfuehren, aufgabe, ordner, profil, schritte, run_id)
                stufe = self._ausgang_stufe()
                return self._extern_senden(
                    {"id": run_id, "zustand": "laeuft", "stufe": stufe,
                     "weiter": "GET /api/extern/auftrag/" + run_id}, p, stufe, (), run_id)
            if p == "/api/sandbox/agent":
                if not self._nur_besitzer(rolle):
                    return
                if get_setting("SANDBOX_ENABLED", "0") != "1":
                    return self.send_json({"error": "Sandbox deaktiviert."}, 403)
                body = self.read_body()
                if not body.get("task", "").strip():
                    return self.send_json({"error": "Bitte eine Aufgabe angeben."}, 400)
                run_id = run_begin("code", "Coding-Diver: " + body["task"][:60],
                                   body.get("session_id"))
                background(run_coding_agent, body["task"],
                           body.get("workspace", "default"), body.get("model", ""),
                           body.get("iterations", 4), body.get("packages", ""),
                           body.get("session_id"), run_id)
                return self.send_json({"ok": True, "status": "running", "run_id": run_id,
                                       "title": "Coding-Agent",
                                       "info": "Coding-Agent arbeitet."})
            return self.send_json({"error": "not found"}, 404)
        except AnfrageZuGross as e:
            return self.send_json({"error": str(e)}, 413)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)
        except Exception as e:
            return self.send_json({"error": str(e)}, 500)

    def do_PUT(self):
        p = self.path.split("?")[0]
        rolle = self._auth()
        if not rolle:
            return
        try:
            m = re.match(r"^/api/pipelines/([\w-]+)$", p)
            if m:
                body = self.read_body()
                conn = db()
                conn.execute("UPDATE pipelines SET name=?, description=?, steps=? WHERE id=?",
                             (body.get("name", ""), body.get("description", ""),
                              json.dumps(body.get("steps", []), ensure_ascii=False),
                              m.group(1)))
                conn.commit(); conn.close()
                return self.send_json({"ok": True})
            for table, fields in (("agents", ["name", "description", "system_prompt",
                                              "model", "emoji"]),
                                  ("prompts", ["name", "description", "content", "category"]),
                                  ("knowledge", ["name", "content", "folder"])):
                m = re.match(r"^/api/%s/([\w-]+)$" % table, p)
                if m:
                    return self.api_update(table, m.group(1), fields)
            m = re.match(r"^/api/skills/([\w-]+)$", p)
            if m:
                return self.api_update_skill(m.group(1))
            m = re.match(r"^/api/sessions/([\w-]+)$", p)
            if m:
                body = self.read_body()
                conn = db()
                conn.execute("UPDATE sessions SET title=?, updated_at=? WHERE id=?",
                             (body.get("title", "Chat"), now(), m.group(1)))
                conn.commit(); conn.close()
                return self.send_json({"ok": True})
            return self.send_json({"error": "not found"}, 404)
        except AnfrageZuGross as e:
            return self.send_json({"error": str(e)}, 413)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)
        except Exception as e:
            return self.send_json({"error": str(e)}, 500)

    def do_DELETE(self):
        p = self.path.split("?")[0]
        rolle = self._auth()
        if not rolle:
            return
        try:
            m = re.match(r"^/api/tokens/([\w-]+)$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                conn = db()
                conn.execute("DELETE FROM tokens WHERE id=? AND rolle!='besitzer'",
                             (m.group(1),))
                conn.commit()
                conn.close()
                return self.send_json({"ok": True})
            m = re.match(r"^/api/providers/([\w.-]+)$", p)
            if m:
                if not self._nur_besitzer(rolle):
                    return
                provs = [x for x in get_providers() if x.get("id") != m.group(1)]
                if not provs:
                    return self.send_json(
                        {"error": "Der letzte Provider kann nicht entfernt werden."}, 400)
                save_providers(provs)
                GGUF.anhalten(m.group(1))
                return self.send_json({"ok": True})
            m = re.match(r"^/api/(sessions|agents|prompts|skills|templates|knowledge|"
                         r"notifications|artifacts|pipelines)/([\w-]+)$", p)
            if not m:
                return self.send_json({"error": "not found"}, 404)
            table, item_id = m.group(1), m.group(2)
            conn = db()
            if table == "artifacts":
                r = conn.execute("SELECT filename FROM artifacts WHERE id=?",
                                 (item_id,)).fetchone()
                if r:
                    fp = os.path.join(ARTIFACTS_DIR, r["filename"])
                    if os.path.exists(fp):
                        os.remove(fp)
            if table == "sessions":
                conn.execute("DELETE FROM messages WHERE session_id=?", (item_id,))
            conn.execute("DELETE FROM %s WHERE id=?" % table, (item_id,))
            if table == "knowledge":
                wissen_geaendert()
            conn.commit(); conn.close()
            return self.send_json({"ok": True})
        except Exception as e:
            return self.send_json({"error": str(e)}, 500)

    # --- API-Implementierungen ----------------------------------------------

    def api_health(self):
        """Gesundheit über ALLE Modell-Provider, nicht nur Ollama.

        `ok` ist wahr, sobald mindestens ein Provider Modelle liefert — wer nur
        vLLM/LM Studio einträgt, gilt damit auch als verbunden. Die Felder
        `ollama`/`models` bleiben für bestehende Ansichten erhalten.

        **Ohne Schlüssel gibt es nur `ok`.** Dieser Pfad ist absichtlich frei:
        Ein Container, eine Überwachung oder ein Anlaufskript muss fragen
        können, ob der Dienst lebt. Er verriet aber auch, dass die Sandbox an
        ist, dass der Browser-Agent bereitsteht, wo Ollama horcht und wie
        viele Modelle es gibt — und Dive on Wide lauscht voreingestellt auf allen
        Schnittstellen. Jeder im selben WLAN konnte so erfahren, dass hier ein
        Arbeitsplatz mit Codeausführung läuft. Das ist Aufklärung, kein
        Gesundheitscheck (gemessen am 23.09.2026 über die LAN-Adresse)."""
        if not self._darf_lage_sehen():
            # `eingeschraenkt` ist kein Zierrat: Ohne dieses Merkmal haelt die
            # Oberflaeche die kurze Antwort fuer eine vollstaendige und schreibt
            # „Ollama verbunden (undefined Modelle)" in die Statuszeile.
            return self.send_json({"ok": True, "eingeschraenkt": True})
        basis = {"browser_use": browseruse_available(),
                 "browser_ready": browser_info()["bereit"],
                 "docker": docker_available(),
                 "sandbox_enabled": get_setting("SANDBOX_ENABLED", "0") == "1",
                 "version": DOWOS_VERSION, "kanal": dowos_kanal()}
        modelle = llm_list_models()
        provs = get_providers()
        aktiv = sorted({m.get("provider_id") for m in modelle if m.get("provider_id")})
        # Anzeige-Adresse: der Provider, der den Standard stellt (meist Ollama).
        adresse = default_provider().get("base_url", "")
        basis.update({"ollama": adresse, "provider_url": adresse,
                      "models": len(modelle),
                      "providers": len(provs), "providers_ok": len(aktiv),
                      "provider_ids": aktiv})
        if modelle:
            basis["ok"] = True
        else:
            basis["ok"] = False
            basis["error"] = ("Kein Modell-Provider erreichbar (%d konfiguriert). "
                              "Läuft Ollama bzw. der eingetragene Server?"
                              % len(provs))
        return self.send_json(basis)

    def api_dashboard(self):
        """Gesamtbild des Systems — ein Blick, alle Flüsse."""
        conn = db()
        counts = {}
        for t in ("sessions", "messages", "agents", "skills", "templates",
                  "prompts", "pipelines", "artifacts", "knowledge"):
            counts[t] = conn.execute("SELECT COUNT(*) c FROM %s" % t).fetchone()["c"]
        counts["brain"] = conn.execute(
            "SELECT COUNT(*) c FROM knowledge WHERE folder='Second Brain'").fetchone()["c"]
        counts["unread"] = conn.execute(
            "SELECT COUNT(*) c FROM notifications WHERE read=0").fetchone()["c"]
        recent = rows(conn.execute(
            "SELECT title, kind, artifact_id, created_at FROM notifications "
            "ORDER BY created_at DESC LIMIT 6"))
        arts = rows(conn.execute(
            "SELECT id, title, filename, created_at FROM artifacts "
            "ORDER BY created_at DESC LIMIT 5"))
        core = conn.execute(
            "SELECT updated_at FROM knowledge WHERE id='sb-core'").fetchone()
        conn.close()
        # Modelle über ALLE Provider zählen (nicht nur Ollama) — sonst zeigt das
        # Dashboard „nicht verbunden", obwohl z. B. ein vLLM-Server läuft.
        alle_modelle = llm_list_models()
        models = len(alle_modelle)
        ollama_ok = models > 0
        try:
            workspaces = len([d for d in os.listdir(WORKSPACES_DIR)
                              if os.path.isdir(os.path.join(WORKSPACES_DIR, d))])
        except Exception:
            workspaces = 0
        # Grounding-Server (mlx_vlm / LocateAnything) prüfen — nutzt den 30-s-Cache
        # von computer_info(), damit das Dashboard nicht bei jedem Aufruf pollt.
        try:
            cu = computer_info()
        except Exception:
            cu = {"api_erreichbar": False, "modell_geladen": False, "modelle": [],
                  "url": get_setting("GROUNDING_API_URL", "") or "http://localhost:8600",
                  "modell": get_setting("GROUNDING_MODEL", "") or "nvidia/LocateAnything-3B",
                  "bereit": False, "steuern_bereit": False, "steuern_aktiviert": False}
        return self.send_json({
            "counts": counts, "recent": recent, "artifacts": arts,
            "workspaces": workspaces,
            "brain_core_at": core["updated_at"] if core else 0,
            "ollama": {"ok": ollama_ok, "models": models,
                       "url": default_provider().get("base_url", ""),
                       "default_model": get_setting("DEFAULT_MODEL")},
            "llm": {"providers": [{"id": p.get("id"), "name": p.get("name"),
                                   "type": p.get("type"),
                                   "models": len([m for m in alle_modelle
                                                  if m.get("provider_id") == p.get("id")])}
                                  for p in get_providers()],
                    "models_total": models},
            "grounding": {"ok": cu.get("api_erreichbar", False),
                          "model_loaded": cu.get("modell_geladen", False),
                          "url": cu.get("url", ""),
                          "model": cu.get("modell", ""),
                          "models": cu.get("modelle", []),
                          "ready": cu.get("bereit", False),
                          "control_ready": cu.get("steuern_bereit", False),
                          "control_enabled": cu.get("steuern_aktiviert", False)},
            "web": {
                "backend": get_setting("SEARCH_BACKEND", "auto"),
                "fetch": get_setting("FETCH_BACKEND", "auto"),
                "aktive_quelle": (get_setting("SEARCH_BACKEND", "auto")
                                  if get_setting("SEARCH_BACKEND", "auto") != "auto"
                                  else "keyless (Mojeek/DDG/Wikipedia)")},
            "ausgang": (lambda s_: dict(s_, stufe=ausgang.stufe_lesen(get_setting("AUSGANG_STUFE", "aus")),
                                        text=ausgang.summe_text(s_)))(ausgang.summe(STORAGE_DIR)),
            "capabilities": {
                "web_bridge": True,
                "browser_use": browseruse_available(),
                                   "browser_ready": browser_info()["bereit"],
                "docker": docker_available(),
                "sandbox": get_setting("SANDBOX_ENABLED", "0") == "1",
                "brain_autosync": get_setting("BRAIN_AUTOSYNC", "0") == "1"}})

    def api_get_settings(self):
        keys = ["OLLAMA_BASE_URL", "DEFAULT_MODEL", "NUM_CTX", "TEMPERATURE"]
        out = {k: get_setting(k) for k in keys}
        out["BRAIN_AUTOSYNC"] = get_setting("BRAIN_AUTOSYNC", "0")
        out["IMAGE_API_URL"] = get_setting("IMAGE_API_URL", "")
        out["BROWSER_CDP_URL"] = get_setting("BROWSER_CDP_URL", "")
        out["BROWSER_HEADLESS"] = get_setting("BROWSER_HEADLESS", "0")
        out["BROWSER_MAX_STEPS"] = get_setting("BROWSER_MAX_STEPS", "25")
        out["BROWSER_USE_SYSTEM_CHROME"] = get_setting("BROWSER_USE_SYSTEM_CHROME", "0")
        out["BROWSER_MODEL"] = get_setting("BROWSER_MODEL", "")
        out["SANDBOX_ENABLED"] = get_setting("SANDBOX_ENABLED", "0")
        out["SANDBOX_DOCKER"] = get_setting("SANDBOX_DOCKER", "0")
        out["ALLOW_LOCAL_FETCH"] = get_setting("ALLOW_LOCAL_FETCH", "0")
        out["AUTH_REQUIRE_LOCAL"] = get_setting("AUTH_REQUIRE_LOCAL", "0")
        out["AUSGANG_STUFE"] = ausgang.stufe_lesen(get_setting("AUSGANG_STUFE", "aus"))
        out["KONSOLIDIERUNG_UEBERNEHMEN"] = get_setting("KONSOLIDIERUNG_UEBERNEHMEN", "0")
        out["GROUNDING_API_URL"] = get_setting("GROUNDING_API_URL", "")
        out["GROUNDING_MODEL"] = get_setting("GROUNDING_MODEL", "")
        out["COMPUTER_PLANNER_MODEL"] = get_setting("COMPUTER_PLANNER_MODEL", "")
        out["COMPUTER_USE_ENABLED"] = get_setting("COMPUTER_USE_ENABLED", "0")
        out["COMPUTER_CONFIRM"] = get_setting("COMPUTER_CONFIRM", "1")
        out["COMPUTER_MAX_STEPS"] = get_setting("COMPUTER_MAX_STEPS", "15")
        out["COMPUTER_PLANNER_TIMEOUT"] = get_setting("COMPUTER_PLANNER_TIMEOUT", "180")
        out["COMPUTER_SCHIRM"] = get_setting("COMPUTER_SCHIRM", "auto")
        out["TRAINING_PYTHON"] = get_setting("TRAINING_PYTHON", "")
        out["TRAINING_SUCHORTE"] = get_setting("TRAINING_SUCHORTE", "")
        for k in ("LLAMA_CPP_DIR", "LLAMA_QUANTIZE", "OLLAMA_BIN", "WERKBANK_EXTERN", "CLAUDE_BIN", "CODEX_BIN"):
            out[k] = get_setting(k, "")
        for k in ("WERKBANK_MODELL", "ORCHESTRATOR_MODELL", "LEHRER_MODELL", "SCHUELER_BASIS", "SCHUELER_MODELL"):
            out[k] = get_setting(k, "")
        # Web-Bridge: Backend-Wahl + nicht-geheime URLs im Klartext …
        out["SEARCH_BACKEND"] = get_setting("SEARCH_BACKEND", "auto")
        out["FETCH_BACKEND"] = get_setting("FETCH_BACKEND", "auto")
        out["SEARXNG_URL"] = get_setting("SEARXNG_URL", "")
        out["FIRECRAWL_URL"] = get_setting("FIRECRAWL_URL", "")
        # … API-Keys NIEMALS ausliefern, nur ob sie gesetzt sind (kein Leak).
        for k in ("TAVILY_API_KEY", "SERPER_API_KEY", "BRAVE_API_KEY",
                  "FIRECRAWL_API_KEY", "JINA_API_KEY"):
            out[k + "_SET"] = bool(get_setting(k, "").strip())
        return self.send_json(out)

    # API-Keys: leeres Feld = behalten, "__CLEAR__" = löschen, sonst überschreiben.
    _SECRET_KEYS = ("TAVILY_API_KEY", "SERPER_API_KEY", "BRAVE_API_KEY",
                    "FIRECRAWL_API_KEY", "JINA_API_KEY")

    def api_save_settings(self):
        body = self.read_body()
        for k in ("OLLAMA_BASE_URL", "DEFAULT_MODEL", "AUSWEICH_MODELL",
                  "AUSGANG_STUFE", "KONSOLIDIERUNG_UEBERNEHMEN",
                  "NUM_CTX", "TEMPERATURE", "ANTWORT_STIL", "CHAT_DENKEN", "ORCHESTRATOR_CLOUD", "ORCHESTRATOR_NETZ",
                  "BRAIN_AUTOSYNC", "SANDBOX_ENABLED", "SANDBOX_DOCKER",
                  "IMAGE_API_URL", "IMAGE_STEPS", "BROWSER_CDP_URL",
                  "BROWSER_HEADLESS", "BROWSER_MAX_STEPS", "BROWSER_MODEL",
                  "BROWSER_USE_SYSTEM_CHROME", "BROWSER_PROFILE",
                  "BROWSER_MODUS", "BROWSER_PYTHON", "BROWSER_PROFIL", "BROWSER_MODELL",
                  "BROWSER_STEUERUNG", "BROWSER_SEITEN_ZEICHEN",
                  "ALLOW_LOCAL_FETCH", "AUTH_REQUIRE_LOCAL",
                  "GROUNDING_API_URL", "GROUNDING_MODEL",
                  "COMPUTER_PLANNER_MODEL", "COMPUTER_USE_ENABLED",
                  "COMPUTER_CONFIRM", "COMPUTER_MAX_STEPS", "COMPUTER_SCHIRM",
                  "COMPUTER_PLANNER_TIMEOUT",
                  "TRAINING_PYTHON", "TRAINING_SUCHORTE", "LLAMA_CPP_DIR", "LLAMA_QUANTIZE", "OLLAMA_BIN",
                  "WERKBANK_EXTERN", "CLAUDE_BIN", "CODEX_BIN",
                  "WERKBANK_MODELL", "ORCHESTRATOR_MODELL", "LEHRER_MODELL", "SCHUELER_BASIS", "SCHUELER_MODELL",
                  "SEARCH_BACKEND", "FETCH_BACKEND", "SEARXNG_URL",
                  "FIRECRAWL_URL"):
            if k in body:
                set_setting(k, str(body[k]))
        for k in self._SECRET_KEYS:
            v = str(body.get(k, "")).strip()
            if v == "__CLEAR__":
                set_setting(k, "")
            elif v:
                set_setting(k, v)          # leeres Feld lässt den Key unberührt
        return self.send_json({"ok": True})

    def api_create_token(self):
        """Legt einen Zugangsschlüssel an — für einen Alpha-Tester oder ein Gerüst.

        Ein Harness-Schlüssel („rolle": "harness") kommt ausschließlich an
        /api/extern/… und sieht nur, was die Ausgangsstufe erlaubt."""
        body = self.read_body()
        name = (feld_text(body, "name") or "").strip() or "Gast"
        rolle = "harness" if str(body.get("rolle") or "").lower() == "harness" else "gast"
        token = "dow_" + secrets.token_urlsafe(18)
        conn = db()
        conn.execute("INSERT INTO tokens VALUES(?,?,?,?,?)",
                     (nid(), name[:60], token, rolle, now()))
        conn.commit()
        conn.close()
        emit("Neuer Zugang angelegt 🔑",
             "Für „%s“ wurde ein Zugangsschlüssel erstellt. Weitergeben und "
             "nach dem Test unter Einstellungen → Zugänge löschen." % name[:60])
        return self.send_json({"ok": True, "name": name[:60], "token": token,
                               "rolle": rolle})

    def api_save_provider(self):
        """Legt einen LLM-Provider an oder aktualisiert ihn (per id)."""
        b = self.read_body()
        typ = (b.get("type") or "openai").strip()
        name = (b.get("name") or "").strip()
        base = (b.get("base_url") or "").strip().rstrip("/")
        if typ == "gguf":
            return self.api_save_gguf(b, name)
        if typ not in ("ollama", "openai"):
            return self.send_json({"error": "type muss 'ollama', 'openai' oder 'gguf' sein."}, 400)
        if not base:
            return self.send_json({"error": "base_url fehlt."}, 400)
        pid = (b.get("id") or "").strip() or re.sub(r"[^a-z0-9]+", "-",
               (name or typ).lower()).strip("-") or ("prov-" + nid()[:6])
        provs = get_providers()
        neu = {"id": pid, "name": name or pid, "type": typ, "base_url": base,
               "api_key": ""}
        gefunden = False
        for x in provs:
            if x.get("id") == pid:
                # leeres Key-Feld = bestehenden Key behalten
                schluessel = (b.get("api_key") or "").strip()
                neu["api_key"] = schluessel if schluessel else x.get("api_key", "")
                x.update(neu)
                gefunden = True
                break
        if not gefunden:
            neu["api_key"] = (b.get("api_key") or "").strip()
            provs.append(neu)
        save_providers(provs)
        return self.send_json({"ok": True, "id": pid})

    def api_save_gguf(self, b, name):
        """Eine .gguf-Datei als Modell: Dive on Wide startet llama-server selbst, wenn es gebraucht wird."""
        pfad = os.path.expanduser(feld_text(b, "pfad") or "").strip()
        if not pfad.lower().endswith(".gguf") or not os.path.isfile(pfad):
            return self.send_json({"error": "Keine .gguf-Datei unter diesem Pfad: %s" % pfad[:200]}, 400)
        try:
            kontext = int(b.get("kontext") or 16384)
        except (TypeError, ValueError):
            return self.send_json({"error": "kontext muss eine Zahl sein."}, 400)
        if not 2048 <= kontext <= 262144:
            return self.send_json({"error": "kontext muss zwischen 2048 und 262144 liegen."}, 400)
        name = name or gguf_dienst.modellname(pfad)
        pid = (feld_text(b, "id") or "").strip() or "gguf-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or nid()[:6])
        neu = {"id": pid, "name": name, "type": "openai", "base_url": "http://127.0.0.1", "api_key": "",
               "gguf": {"pfad": pfad, "kontext": kontext, "groesse": os.path.getsize(pfad)}}
        provs = [x for x in get_providers() if x.get("id") != pid] + [neu]
        save_providers(provs)
        GGUF.anhalten(pid)                  # geänderte Einstellung gilt beim nächsten Start
        return self.send_json({"ok": True, "id": pid, "programm": bool(gguf_dienst.programm_finden())})

    def api_test_provider(self):
        """Testet ein Provider-Setup: erreichbar? Wie viele Modelle?"""
        b = self.read_body()
        vorhanden = get_provider(b.get("id")) if feld_text(b, "id", None) else None
        if vorhanden and vorhanden.get("gguf"):
            # Nicht starten (das belegt den Speicher), nur prüfen, ob es starten könnte
            g = vorhanden["gguf"]
            if not os.path.isfile(g["pfad"]):
                return self.send_json({"ok": False, "error": "Modelldatei fehlt: %s" % g["pfad"]})
            if not gguf_dienst.programm_finden():
                return self.send_json({"ok": False, "error": "llama-server fehlt (macOS: brew install llama.cpp)."})
            return self.send_json({"ok": True, "modelle": 1, "beispiele": [gguf_dienst.modellname(g["pfad"])],
                                   "laeuft": GGUF.laeuft(vorhanden["id"])})
        prov = {"id": "test", "name": "Test", "type": feld_text(b, "type") or "openai",
                "base_url": (feld_text(b, "base_url") or "").strip().rstrip("/"),
                "api_key": (feld_text(b, "api_key") or "").strip()}
        # leeres Key-Feld beim Test eines bestehenden Providers → gespeicherten Key nehmen
        if not prov["api_key"] and feld_text(b, "id", None):
            vorhanden = get_provider(b["id"])
            if vorhanden:
                prov["api_key"] = vorhanden.get("api_key", "")
        try:
            modelle = (_openai_models(prov) if prov["type"] == "openai"
                       else [m.get("name") for m in
                             ollama_json("/api/tags", timeout=6,
                                         base=prov["base_url"]).get("models", [])])
            return self.send_json({"ok": True, "modelle": len(modelle),
                                   "beispiele": modelle[:5]})
        except Exception as e:
            return self.send_json({"ok": False, "error": str(e)[:200]})

    def api_models(self):
        # Modelle ALLER Provider — eingerichtete (Ollama, OpenAI-kompatibel)
        # UND den Netzwerk-Anbieter, solange das Mesh laeuft. Sonst stuenden
        # die Netz-Modelle in der Liste, aber ohne Gruppe im Auswahlfeld.
        models = llm_list_models()
        return self.send_json({"models": models,
                               "default": get_setting("DEFAULT_MODEL"),
                               "providers": [{"id": p.get("id"), "name": p.get("name"),
                                              "type": p.get("type")}
                                             for p in alle_provider()]})

    def api_chat_stream(self):
        """Streaming über den Provider-Router — normalisiert JEDES Provider-Format
        (Ollama-NDJSON, OpenAI-SSE) auf das bestehende Browser-Protokoll, sodass
        das Frontend unverändert bleibt."""
        body = self.read_body()
        messages = body.get("messages", [])
        # Ohne Nachrichten gab es bisher ein stilles {"done": true} — der
        # Aufrufer sah einen gelungenen Aufruf und bekam nichts. Ein leerer
        # Aufruf ist ein Fehler des Aufrufers und soll das auch heissen.
        if not isinstance(messages, list) or not messages:
            return self.send_json(
                {"fehler": "Es wurden keine Nachrichten übergeben. Erwartet "
                           "wird messages=[{\"role\":\"user\","
                           "\"content\":\"…\"}]."}, 400)
        # Jede Nachricht muss {role, content} sein (Fuzz 08.10.2026: ["text"] endete in einem 500).
        if not all(isinstance(m, dict) and m.get("role") in ("system", "user", "assistant", "tool")
                   and isinstance(m.get("content", ""), (str, list)) for m in messages):
            return self.send_json({"fehler": "Jede Nachricht braucht role (system/user/assistant) und content (Text).",
                                   "error": "Each message needs role (system/user/assistant) and content (text)."}, 400)
        model = body.get("model") or get_setting("DEFAULT_MODEL")
        # Einführung und passendes Wissen von selbst beilegen (chatkontext.py).
        messages, wissen_genutzt, mit_einfuehrung = chat_anreichern(
            messages, body.get("agent_id") or "", body.get("sprache") or "de", body.get("wissen", True) is not False)
        # 🌐 Web-Modus: Recherche-Kontext einschleusen (Erdung / Anti-Halluzination).
        if body.get("web"):
            user_msgs = [m for m in messages if m.get("role") == "user"]
            if user_msgs:
                inject, _src = recherche_kontext(user_msgs[-1]["content"], model)
                messages = messages[:-1] + inject + [messages[-1]]
        messages = chatkontext.ein_systemprompt(messages)
        kontext_info = {}
        try:
            messages, kontext_info = chatkontext.verdichten(
                messages, kontext_groesse(model),
                lambda text: llm_chat_once(model, [{"role": "system", "content": chatkontext.ZUSAMMENFASSEN_PROMPT},
                                                   {"role": "user", "content": text}], temperature=0.2, no_think=True))
        except Exception as e:
            print("Verdichten übersprungen: %s" % e)
        prov, _name = parse_model_ref(model)
        temp = body.get("temperature")
        try:
            strom = llm_stream(model, messages, temp, sprache=body.get("sprache") or "de")
            erstes = next(strom, "")      # erste Anfrage hier, um Fehler VOR dem
                                          # 200-Header abzufangen (sauberer 502)
        except (urllib.error.URLError, ConnectionError) as e:
            return self.send_json(
                {"error": "Modell-Provider „%s“ nicht erreichbar unter %s (%s). "
                          "Prüfe die Einstellungen → Modelle & Provider."
                          % (prov.get("name", "?"), prov.get("base_url", "?"), e)}, 502)
        except Exception as e:
            return self.send_json({"error": "Chat fehlgeschlagen: %s" % e}, 502)
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def schreibe(stueck):
            zeile = json.dumps({"message": {"role": "assistant", "content": stueck},
                                "done": False}) + "\n"
            self.wfile.write(zeile.encode("utf-8"))
            self.wfile.flush()
        try:
            self.wfile.write((json.dumps({"wissen": wissen_genutzt, "einfuehrung": mit_einfuehrung,
                                          "kontext": kontext_info}, ensure_ascii=False) + "\n").encode("utf-8"))
            if erstes:
                schreibe(erstes)
            for stueck in strom:
                schreibe(stueck)
            self.wfile.write((json.dumps({"done": True}) + "\n").encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass          # Browser hat getrennt — kein Fehlerfall

    def api_list(self, table, order):
        conn = db()
        data = rows(conn.execute("SELECT * FROM %s ORDER BY %s" % (table, order)))
        conn.close()
        for r in data:
            for k in ("steps", "fields"):
                if k in r and r[k]:
                    try:
                        r[k] = json.loads(r[k])
                    except Exception:
                        pass
        return self.send_json(data)

    def api_create(self, table, fields):
        body = self.read_body()
        item_id = nid()
        conn = db()
        cols = ["id"] + fields + ["created_at"]
        vals = [item_id] + [feld_text(body, f) for f in fields] + [now()]
        conn.execute("INSERT INTO %s(%s) VALUES(%s)"
                     % (table, ",".join(cols), ",".join("?" * len(vals))), vals)
        conn.commit(); conn.close()
        return self.send_json({"id": item_id})

    def api_update(self, table, item_id, fields):
        body = self.read_body()
        sets = ", ".join("%s=?" % f for f in fields)
        conn = db()
        conn.execute("UPDATE %s SET %s WHERE id=?" % (table, sets),
                     [feld_text(body, f) for f in fields] + [item_id])
        conn.commit(); conn.close()
        if table == "knowledge":
            wissen_geaendert()       # eine Änderung gleicher Länge sähe der Index sonst nicht
        return self.send_json({"ok": True})

    def api_create_skill(self):
        body = self.read_body()
        item_id = nid()
        conn = db()
        conn.execute("INSERT INTO skills VALUES(?,?,?,?,?,?,?)",
                     (item_id, feld_text(body, "name", ""), feld_text(body, "trigger_word", ""),
                      feld_text(body, "description", ""),
                      json.dumps(body.get("steps", []), ensure_ascii=False),
                      feld_text(body, "model", ""), now()))
        conn.commit(); conn.close()
        return self.send_json({"id": item_id})

    def api_update_skill(self, item_id):
        body = self.read_body()
        conn = db()
        conn.execute("UPDATE skills SET name=?, trigger_word=?, description=?, steps=?, "
                     "model=? WHERE id=?",
                     (body.get("name", ""), body.get("trigger_word", ""),
                      body.get("description", ""),
                      json.dumps(body.get("steps", []), ensure_ascii=False),
                      body.get("model", ""), item_id))
        conn.commit(); conn.close()
        return self.send_json({"ok": True})

    def api_create_template(self):
        body = self.read_body()
        item_id = nid()
        conn = db()
        conn.execute("INSERT INTO templates VALUES(?,?,?,?,?,?,?,?)",
                     (item_id, feld_text(body, "title", ""), feld_text(body, "description", ""),
                      feld_text(body, "kind", "agent"),
                      json.dumps(body.get("fields", []), ensure_ascii=False),
                      feld_text(body, "base_prompt", ""), feld_text(body, "emoji", "✨"), now()))
        conn.commit(); conn.close()
        return self.send_json({"id": item_id})

    def api_create_session(self):
        body = self.read_body()
        item_id = nid()
        conn = db()
        conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",
                     (item_id, feld_text(body, "title", "Neuer Chat"),
                      feld_text(body, "agent_id", ""), now(), now()))
        conn.commit(); conn.close()
        return self.send_json({"id": item_id})

    def api_messages(self, session_id):
        conn = db()
        data = rows(conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY created_at", (session_id,)))
        conn.close()
        return self.send_json(data)

    def api_add_message(self, session_id):
        body = self.read_body()
        conn = db()
        wissen = body.get("wissen") if isinstance(body.get("wissen"), list) else []
        conn.execute("INSERT INTO messages(id,session_id,role,content,model,created_at,wissen) VALUES(?,?,?,?,?,?,?)",
                     (nid(), session_id, body.get("role", "user"),
                      body.get("content", ""), body.get("model", ""), now(),
                      "\n".join(str(w)[:200] for w in wissen[:10])))
        conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now(), session_id))
        conn.commit(); conn.close()
        # Second Brain Auto-Sync: nach jeder Assistenten-Antwort im Hintergrund
        if body.get("role") == "assistant" and get_setting("BRAIN_AUTOSYNC", "0") == "1":
            threading.Thread(target=sync_second_brain,
                             args=(session_id, False), daemon=True).start()
        return self.send_json({"ok": True})

    def api_create_artifact(self):
        body = self.read_body()
        item_id = nid()
        raw_name = feld_text(body, "filename") or ("artefakt-%s.md" % item_id)
        filename = safe_filename(raw_name, "artefakt-%s.md" % item_id)
        with open(os.path.join(ARTIFACTS_DIR, filename), "w", encoding="utf-8") as f:
            f.write(feld_text(body, "content", ""))
        conn = db()
        conn.execute("INSERT INTO artifacts VALUES(?,?,?,?,?)",
                     (item_id, filename, feld_text(body, "title", filename),
                      feld_text(body, "session_id", None), now()))
        conn.commit(); conn.close()
        return self.send_json({"id": item_id, "filename": filename})

    def api_artifact_download(self, item_id):
        conn = db()
        r = conn.execute("SELECT filename FROM artifacts WHERE id=?", (item_id,)).fetchone()
        conn.close()
        if not r:
            return self.send_json({"error": "not found"}, 404)
        return self.send_file(os.path.join(ARTIFACTS_DIR, r["filename"]),
                              download_name=r["filename"])

    def api_artifact(self, item_id):
        """Das ganze Artefakt: Kopfdaten samt Inhalt, in einer Antwort."""
        conn = db()
        r = conn.execute("SELECT * FROM artifacts WHERE id=?", (item_id,)).fetchone()
        conn.close()
        if not r:
            return self.send_json({"error": "not found",
                                   "fehler": "Kein Artefakt mit dieser Kennung."}, 404)
        aus = dict(r)
        pfad = os.path.join(ARTIFACTS_DIR, r["filename"])
        aus["content"] = ""
        if os.path.exists(pfad):
            with open(pfad, "r", encoding="utf-8", errors="replace") as f:
                aus["content"] = f.read()
        aus["download_url"] = "/api/artifacts/%s/download" % item_id
        return self.send_json(aus)

    def api_artifact_content(self, item_id):
        conn = db()
        r = conn.execute("SELECT filename FROM artifacts WHERE id=?", (item_id,)).fetchone()
        conn.close()
        if not r:
            return self.send_json({"error": "not found"}, 404)
        path = os.path.join(ARTIFACTS_DIR, r["filename"])
        content = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        return self.send_json({"filename": r["filename"], "content": content})

    def api_create_pipeline(self):
        body = self.read_body()
        item_id = nid()
        conn = db()
        conn.execute("INSERT INTO pipelines VALUES(?,?,?,?,?)",
                     (item_id, feld_text(body, "name", ""), feld_text(body, "description", ""),
                      json.dumps(body.get("steps", []), ensure_ascii=False), now()))
        conn.commit(); conn.close()
        return self.send_json({"id": item_id})

    def api_run_pipeline(self, pipeline_id):
        body = self.read_body()
        conn = db()
        r = conn.execute("SELECT * FROM pipelines WHERE id=?", (pipeline_id,)).fetchone()
        conn.close()
        if not r:
            return self.send_json({"error": "Pipeline nicht gefunden"}, 404)
        run_id = run_begin("pipeline", "Pipeline: " + r["name"], body.get("session_id"))
        background(run_pipeline, dict(r), body.get("input", ""), body.get("model", ""),
                   body.get("session_id"), run_id)
        return self.send_json({"ok": True, "status": "running", "run_id": run_id,
                               "title": "Pipeline: " + r["name"],
                               "info": "Pipeline läuft im Hintergrund."})

    def api_meta_create(self):
        """KI erstellt Skills/Agenten aus einer Beschreibung (agent-skill-creator-Prinzip)."""
        body = self.read_body()
        kind = feld_text(body, "kind", "skill")
        desc = feld_text(body, "description", "").strip()
        if not desc:
            return self.send_json({"error": "Bitte eine Beschreibung angeben."}, 400)
        model = feld_text(body, "model") or get_setting("DEFAULT_MODEL")
        prompt = META_AGENT_PROMPT if kind == "agent" else META_SKILL_PROMPT
        try:
            raw = ollama_chat_once(model, [
                {"role": "system", "content": prompt},
                {"role": "user", "content": desc}], temperature=0.4)
            data = extract_json(raw)
        except Exception as e:
            return self.send_json({"error": "KI-Erstellung fehlgeschlagen: %s" % e}, 500)
        item_id = nid()
        conn = db()
        if kind == "agent":
            conn.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?)",
                         (item_id, data.get("name", "Neuer Agent"),
                          data.get("description", ""), data.get("system_prompt", ""),
                          "", data.get("emoji", "🤖"), now()))
        else:
            conn.execute("INSERT INTO skills VALUES(?,?,?,?,?,?,?)",
                         (item_id, data.get("name", "Neuer Skill"),
                          re.sub(r"[^\wäöüß-]", "", data.get("trigger_word", "skill")).lower(),
                          data.get("description", ""),
                          json.dumps(data.get("steps", []), ensure_ascii=False), "", now()))
        conn.commit(); conn.close()
        return self.send_json({"ok": True, "id": item_id, "created": data})

    def api_run_skill(self, skill_id):
        body = self.read_body()
        conn = db()
        r = conn.execute("SELECT * FROM skills WHERE id=?", (skill_id,)).fetchone()
        conn.close()
        if not r:
            return self.send_json({"error": "Skill nicht gefunden"}, 404)
        skill = dict(r)
        model = body.get("model") or skill.get("model") or get_setting("DEFAULT_MODEL")
        run_id = run_begin("skill", "Skill: " + skill["name"], body.get("session_id"))
        background(run_skill_pipeline, skill, body.get("input", ""), model,
                   body.get("session_id"), run_id)
        return self.send_json({"ok": True, "status": "running", "run_id": run_id,
                               "title": "Skill: " + skill["name"],
                               "info": "Skill läuft im Hintergrund."})

# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

class SchnellerServer(ThreadingHTTPServer):
    """ThreadingHTTPServer ohne die Namensaufloesung beim Binden.

    `HTTPServer.server_bind` ruft `socket.getfqdn()` auf — eine
    Rueckwaertssuche im DNS. Auf Rechnern ohne passenden Eintrag laeuft die in
    eine Zeitueberschreitung: hier gemessen **5,0 Sekunden**, bei JEDEM Start,
    bevor auch nur das Banner erscheint.

    Gebraucht wird der Name allein fuer die CGI-Umgebungsvariable
    SERVER_NAME, die Dive on Wide nirgends benutzt. Also setzen wir ihn direkt und
    sparen die Wartezeit vollstaendig ein."""

    # Wie viele Verbindungen warten duerfen, bis ein Faden frei ist. Der
    # Python-Standard ist 5. Unter macOS 27 setzte der Kern bei voller
    # Warteschlange die Verbindung zurueck ("Connection reset by peer"):
    # Der Lasttest mit 24 gleichzeitigen Schreibzugriffen scheiterte am
    # 25.09.2026 fuenfmal von fuenf. Das ist kein Testartefakt — die
    # Einstellungsseite feuert selbst neun Anfragen gleichzeitig ab, ein Handy
    # laedt parallel, ein fremdes Geruest schickt Auftraege in Schueben.
    # 128 ist die Obergrenze, die der Kern hier ohnehin zulaesst (somaxconn).
    request_queue_size = 128

    # Unter Windows heisst SO_REUSEADDR: Mehrere Programme duerfen DENSELBEN
    # Port gleichzeitig belegen. So liefen am 27.09.2026 zwei Dive on Wide auf dem
    # Windows-PC des Besitzers still nebeneinander auf Port 3000 — beide im
    # Mesh, Anfragen landeten mal hier, mal dort. Dort wird der Port deshalb
    # exklusiv belegt; ein zweiter Start scheitert mit klarer Meldung.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host or "localhost"
        self.server_port = port

    def handle_error(self, request, client_address):
        """Ein Aufrufer, der vor der Antwort auflegt, ist kein Fehler von Dive on Wide.

        Der Fuzz-Test vom 27.09.2026 fand 692 volle Tracebacks im Protokoll, alle
        BrokenPipe/ConnectionReset beim Schreiben einer Antwort, die niemand
        mehr lesen wollte. Echte Fehler gingen darin unter. Diese beiden
        bekommen eine Zeile; alles andere weiter den vollen Traceback."""
        fehler = sys.exc_info()[1]
        if isinstance(fehler, (BrokenPipeError, ConnectionResetError)):
            print("[Dive on Wide] %s hat vor der Antwort aufgelegt" % (client_address[0],), flush=True)
            return
        super().handle_error(request, client_address)




def main():
    init_db()
    schluessel = ensure_owner_token()
    # Jede HTTP-Anfrage an einen fremden Rechner ins Ausgangsbuch (ausgang.py) — und die Discord-Verbindung dazu.
    ausgang.einschalten(STORAGE_DIR)
    discord_bote.AUSGANG = lambda url: ausgang.vermerken(STORAGE_DIR, url.replace("wss://", "https://", 1))

    def _buch_takt():
        while True:
            time.sleep(30)
            ausgang.ausschuetten(STORAGE_DIR)
    threading.Thread(target=_buch_takt, daemon=True).start()
    atexit_buch = lambda: ausgang.ausschuetten(STORAGE_DIR, alles=True)
    # Dateimodelle (llama-server) halten viel Speicher fest: Reste eines hart beendeten Laufs weg,
    # und beim Beenden — auch per SIGTERM — die eigenen mitnehmen
    GGUF.aufraeumen()
    try:
        GGUF.leerlauf = max(30, int(get_setting("GGUF_LEERLAUF_S", "300") or 300))
    except ValueError:
        pass
    import atexit
    import signal
    atexit.register(GGUF.alle_anhalten)
    atexit.register(_rechenknoten_anhalten)
    _rechenknoten_reste_beenden()
    atexit.register(atexit_buch)
    try:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    except (ValueError, OSError, AttributeError):
        pass
    port = int(ENV.get("PORT", "3000"))
    # 0.0.0.0: erreichbar für andere Geräte (mit Zugangsschlüssel). Wer das nicht
    # braucht — oder eine Test-Instanz startet —, setzt HOST=127.0.0.1.
    try:
        server = SchnellerServer((ENV.get("HOST", "0.0.0.0") or "0.0.0.0", port), Handler)
    except OSError as e:
        if plattform.konsole_deutsch():
            print("\n  Port %d ist schon belegt (%s).\n"
                  "  Meist läuft Dive on Wide auf diesem Rechner bereits — dann einfach\n"
                  "  http://localhost:%d im Browser öffnen. Ein zweites Dive on Wide würde sonst\n"
                  "  zusätzlich ins Netzwerk gehen. Wer bewusst zwei will: PORT=%d setzen.\n"
                  % (port, e.strerror or e, port, port + 1), flush=True)
        else:
            print("\n  Port %d is already in use (%s).\n"
                  "  Usually Dive on Wide is already running on this computer — just open\n"
                  "  http://localhost:%d in your browser. A second Dive on Wide would also\n"
                  "  join the network. If you really want two: set PORT=%d.\n"
                  % (port, e.strerror or e, port, port + 1), flush=True)
        sys.exit(1)
    # Der Takt laeuft immer mit. Er kostet nichts, solange kein Eintrag
    # existiert — und ohne ihn waere ein eingerichteter Rhythmus eine
    # stille Luege: eingerichtet, aber nie ausgefuehrt.
    rhythmus_starten()
    for _plattform in BOTEN:
        threading.Thread(target=boten_starten, args=(_plattform,), daemon=True).start()
    threading.Thread(target=discord_server_starten, daemon=True).start()
    # Banner: die aufsteigenden Blasen des Logos. Die Breite wird berechnet,
    # damit der Rahmen nicht verrutscht, wenn sich eine Zeile ändert.
    de = plattform.konsole_deutsch()
    zeilen = [
        " ·",
        "  °    Dive on Wide — Alpha 0.5.0",
        " °     " + ("lokaler KI-Arbeitsplatz" if de else "local AI workspace"),
        "O",
    ]
    innen = max(len(z) for z in zeilen) + 4
    print("")
    print("  ╔" + "═" * innen + "╗")
    for z in zeilen:
        print("  ║ " + z.ljust(innen - 2) + " ║")
    print("  ╚" + "═" * innen + "╝")
    print("  UI:      http://localhost:%d" % port)
    print("  Ollama:  %s" % get_setting("OLLAMA_BASE_URL"))
    print("  Storage: %s" % STORAGE_DIR)
    if de:
        print("  Zugang:  %s" % schluessel)
        print("           (Schlüssel für Zugriff von anderen Geräten — auf diesem")
        print("            Rechner ist keine Anmeldung nötig. Tester-Schlüssel legst")
        print("            du unter Einstellungen → Zugänge an.)")
    else:
        print("  Access:  %s" % schluessel)
        print("           (key for access from other devices — no sign-in is needed")
        print("            on this computer. Create tester keys under")
        print("            Settings → Access.)")
    if get_setting("MESH_AUTOSTART", "").strip():
        print("  Mesh:    " + ("startet im Hintergrund …" if de else "starting in the background …"))
    print("  " + ("Beenden" if de else "Quit") + ": Ctrl+C")
    print("")
    # Zwei Gruende, hier zu leeren:
    # 1. Bei umgeleiteter Ausgabe bliebe das Banner sonst im Puffer — und
    #    darin steht der Zugangsschluessel.
    # 2. Der Nutzer soll ihn SOFORT sehen, nicht erst, wenn alles hochgefahren
    #    ist.
    sys.stdout.flush()

    def _mesh_spaeter():
        """Das Netz im Hintergrund hochfahren.

        Der Autostart fragt lokale Modelle ab, und wenn Ollama nicht laeuft,
        wartet das in eine Zeitueberschreitung hinein. Im Vordergrund haette
        der Nutzer solange auf einen leeren Bildschirm gesehen — und die
        Oberflaeche waere noch nicht erreichbar gewesen."""
        lage = mesh_autostart()
        if lage and lage.get("laeuft"):
            print("  Mesh:    %s — %.1f GB beigesteuert"
                  % (lage.get("betriebsart"),
                     lage.get("beitrag", {}).get("erlaubt_gb") or 0))
        elif _mesh_zustand.get("fehler"):
            print("  Mesh:    nicht gestartet — %s" % _mesh_zustand["fehler"])
        sys.stdout.flush()

    if get_setting("MESH_AUTOSTART", "").strip():
        threading.Thread(target=_mesh_spaeter, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Dive on Wide beendet.")

if __name__ == "__main__":
    ausgabe_absichern()
    main()
