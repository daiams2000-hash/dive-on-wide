#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dowos — der Werkbank-Agent im Terminal, in Skripten und in CI.

Das Gegenstück zu `claude -p` und `codex exec`: eine Aufgabe, ein Projektordner,
ein Ergebnis. Es braucht kein laufendes Dive on Wide. Einstellungen (Modelle, Rollen,
Anbieter) kommen aus derselben .env und Datenbank; Checkpunkte und Verläufe
landen dort, wo auch die Oberfläche sie findet.

    dowos werkbank "Behebe den Fehler in rechnen.py"            # im aktuellen Ordner
    dowos werkbank --plan --freigabe befehle "Baue den Export um"
    cat fehler.log | dowos werkbank "Finde die Ursache"          # Eingabe wird angehängt
    dowos fortsetzen "Ja, Kommazahlen"                            # letzten Lauf hier fortsetzen
    dowos review                                                  # uncommittete Git-Änderungen prüfen
    dowos review --basis main                                     # Branch gegen main prüfen
    dowos checkpunkte                                             # Stände dieses Projekts
    dowos zuruecksetzen <checkpunkt>                              # zurück (selbst umkehrbar)
    dowos werkbank "/review-pr 123"                               # eigener Befehl aus .dowos/befehle/
    dowos befehle                                                 # eigene Befehle auflisten
    dowos werkbank --profil reviewer "Prüfe den Export"            # Agentenprofil (dowos profile)
    dowos verlauf                                                 # Schrittprotokoll des letzten Laufs hier
    dowos abzweigen <lauf> 7 "Nimm lieber csv" --zuruecksetzen    # ab Schritt 7 anders weitermachen
    dowos wiederholen <lauf>                                      # ohne Modell nachspielen: gleiche Ergebnisse?
    dowos acp                                                     # als Agent in Zed, JetBrains, Neovim (ACP)

Freigaben werden im Terminal gefragt. Ohne Terminal (CI, Pipe) wird jede
freigabepflichtige Aktion abgelehnt — wer Befehle ohne Rückfrage will, sagt
das ausdrücklich mit --freigabe nie (in der Sandbox ist das der Standard).

Rückgabewerte: 0 fertig · 1 Fehler · 2 Schrittlimit · 3 Rückfrage an den Nutzer.
--json schreibt das Ergebnis maschinenlesbar nach stdout, Fortschritt geht nach stderr.
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import plattform  # noqa: E402  Windows-Eigenheiten (Ausgabe, localhost)
from plattform import ausgabe_absichern  # noqa: E402,F401
plattform.localhost_ipv4_zuerst()
import time
import uuid

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
sys.path.insert(0, os.path.join(HIER, "pruefstand"))
import checkpunkte  # noqa: E402
import agentskills  # noqa: E402
import befehle as werkbank_befehle  # noqa: E402
import agentenprofile  # noqa: E402
import externe_agenten  # noqa: E402
import schrittprotokoll  # noqa: E402
import gedaechtnis as werkbank_gedaechtnis  # noqa: E402
import regeln as werkbank_regeln  # noqa: E402
import werkbank  # noqa: E402

RUECKGABE = {"fertig": 0, "limit": 2, "frage": 3, "festgefahren": 4, "modell": 5}


# ------------------------------------------------------------ Einstellungen ---

def env_laden():
    env = {"STORAGE_DIR": os.path.join(HIER, "storage"), "OLLAMA_BASE_URL": "http://localhost:11434",
           "NUM_CTX": "16384", "DEFAULT_MODEL": ""}
    pfad = os.path.join(HIER, ".env")
    if os.path.exists(pfad):
        with open(pfad, encoding="utf-8") as f:
            for zeile in f:
                zeile = zeile.strip()
                if zeile and not zeile.startswith("#") and "=" in zeile:
                    k, v = zeile.split("=", 1)
                    env[k.strip()] = v.strip()
    # Wie server.load_env: Die Umgebung schlaegt die Datei. Sonst schreibt
    # `STORAGE_DIR=… dowos werkbank` (zweite Instanz, CI) still in den
    # Speicher der Hauptinstanz.
    for k in list(env):
        if os.environ.get(k) not in (None, ""):
            env[k] = os.environ[k]
    return env


class Einstellungen:
    """Liest wie der Server: erst die Datenbank, dann .env — nur lesend."""

    def __init__(self):
        self.env = env_laden()
        self.storage = self.env["STORAGE_DIR"]
        self._db = {}
        db = os.path.join(self.storage, "dowos.db")
        if os.path.exists(db):
            try:
                conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
                self._db = dict(conn.execute("SELECT key, value FROM settings").fetchall())
                conn.close()
            except sqlite3.Error:
                pass

    def get(self, key, default=""):
        return self._db.get(key, self.env.get(key, default))

    def chat(self, ref):
        """chat(nachrichten) für ein Modell im Dive-on-Wide-Format („anbieter@@name“ oder nur der Name)."""
        import stufe2
        ref = ref or self.get("WERKBANK_MODELL") or self.get("DEFAULT_MODEL")
        if not ref:
            raise SystemExit("Kein Modell: --modell angeben oder in Dive on Wide WERKBANK_MODELL einstellen.")
        anbieter = {"id": "ollama", "type": "ollama", "base_url": self.get("OLLAMA_BASE_URL")}
        name = ref
        if "@@" in ref:
            pid, name = ref.split("@@", 1)
            try:
                liste = json.loads(self.get("LLM_PROVIDERS") or "[]")
            except ValueError:
                liste = []
            gefunden = next((p for p in liste if p.get("id") == pid), None)
            if gefunden:
                anbieter = gefunden
            elif pid != "ollama":
                raise SystemExit("Anbieter „%s“ ist nicht eingerichtet (Netz-Modelle gehen nur über Dive on Wide selbst)." % pid)
        if anbieter.get("type") == "openai":
            return stufe2.openai_chat(anbieter["base_url"], name, anbieter.get("api_key", "")), ref
        return stufe2.ollama_chat(anbieter.get("base_url") or self.get("OLLAMA_BASE_URL"), name,
                                  int(self.get("NUM_CTX", "16384"))), ref


# ------------------------------------------------------------------ Ausgabe ---

class Ausgabe:
    def __init__(self, als_json, strom=False):
        self.als_json = als_json
        self.strom = strom
        self.t0 = time.time()

    def ereignis(self, art, **felder):
        """Eine JSON-Zeile je Ereignis nach stdout — für Programme, die mitlesen."""
        sys.stdout.write(json.dumps(dict(felder, typ=art, sekunden=round(time.time() - self.t0, 1)), ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def melden(self, text, zustand="done"):
        if self.strom:
            self.ereignis("schritt", text=text, zustand=zustand)
            return
        sys.stderr.write("  %5.0fs  %s\n" % (time.time() - self.t0, text.replace("\n", "\n          ")))
        sys.stderr.flush()


def kann_fragen():
    """Sitzt jemand am Terminal? stdin ist eines — oder stdin kommt aus einer Pipe,
    aber die Ausgabe geht noch ins Terminal (cat log | dowos …). In CI und in
    Tests ist beides umgeleitet: dann wird nie gefragt und nie gewartet."""
    return sys.stdin.isatty() or sys.stderr.isatty()


def terminal_freigabe(text):
    """Im Terminal nachfragen. Ohne Terminal: ablehnen — niemand kann zustimmen."""
    if not kann_fragen():
        return False
    if not sys.stdin.isatty():
        try:
            tty = open("/dev/tty", "r+")
        except OSError:
            return False
    else:
        tty = None
    frage = "\n  ⚠️  Freigabe: %s\n  Ausführen? [j/N] " % text.replace("\n", "\n      ")
    if tty:
        tty.write(frage)
        tty.flush()
        antwort = tty.readline()
        tty.close()
    else:
        sys.stderr.write(frage)
        sys.stderr.flush()
        antwort = sys.stdin.readline()
    return antwort.strip().lower() in ("j", "ja", "y", "yes")


# ------------------------------------------------------------------- Befehle ---

def lauf_ausfuehren(a, aufgabe, ordner, vorher=None, vorgaenger="", stufe=None, politik=None, modell=None,
                    planmodus=False, testbefehl=None, abgezweigt=None):
    e = Einstellungen()
    ablage = os.path.join(e.storage, "werkbank")
    profil = None
    if getattr(a, "profil", None):
        profil = agentenprofile.finden(ordner, os.path.join(ablage, "agenten")).get(a.profil.strip().lower())
        if not profil:
            raise SystemExit("Profil „%s“ gibt es nicht — dowos profile listet sie." % a.profil)
    chat, modell_ref = e.chat(modell or a.modell or (profil or {}).get("modell"))
    stufe, politik = agentenprofile.anwenden(profil, stufe or a.stufe, politik or a.freigabe)
    planmodus = planmodus or bool((profil or {}).get("planmodus"))
    aus = Ausgabe(a.json, getattr(a, "stream_json", False))
    wb = werkbank.Werkbank(ordner, stufe)
    cp = checkpunkte.Checkpunkte(os.path.join(ablage, "checkpunkte"), ordner) if stufe != "lesen" else None
    lauf_id = "cli" + uuid.uuid4().hex[:10]
    profile = agentenprofile.finden(ordner, os.path.join(ablage, "agenten"))
    for pr in profile.values():
        if pr.get("modell"):
            pr["chat"] = e.chat(pr["modell"])[0]
    externe = externe_agenten.Externe.aus_einstellung(e.get("WERKBANK_EXTERN"),
                                                      {"claude": e.get("CLAUDE_BIN"), "codex": e.get("CODEX_BIN")})
    regeln, hinweise = werkbank_regeln.fuer_projekt(
        ordner, e.get("WERKBANK_REGELN"), werkbank_regeln.Vertrauen(os.path.join(ablage, "vertrauen.json")),
        fragen=terminal_freigabe if kann_fragen() else None)
    for h in hinweise:
        aus.melden(h)
    aus.melden("Projekt %s · %s · Sandbox %s · Modell %s%s" % (ordner, stufe, wb.sandbox or "keine", modell_ref,
                                                             " · Profil " + profil["name"] if profil else ""))
    if abgezweigt:
        aus.melden("Zweigt bei Schritt %d von Lauf %s ab" % (abgezweigt["schritt"], abgezweigt["lauf"]))
    try:
        ergebnis = werkbank.arbeiten(aufgabe, wb, chat, freigabe=terminal_freigabe, politik=politik,
                                     max_schritte=a.max_schritte, budget=int(int(e.get("NUM_CTX", "16384")) * 0.67),
                                     melden=aus.melden, checkpunkte=cp, lauf=lauf_id, vorher=vorher,
                                     planmodus=planmodus, testbefehl=testbefehl, regeln=regeln,
                                     skills=agentskills.finden(ordner, os.path.join(ablage, "skills")),
                                     gedaechtnis=werkbank_gedaechtnis.Gedaechtnis(ablage),
                                     ereignisse=schrittprotokoll.Schreiber(
                                         ablage, lauf_id, ordner=ordner, modell=modell_ref, stufe=stufe, freigabe=politik,
                                         quelle="cli", vorgaenger=vorgaenger, abgezweigt=abgezweigt,
                                         profil=(profil or {}).get("name", ""), zeit=time.time()),
                                     werkzeuge=(profil or {}).get("werkzeuge"), zusatz=(profil or {}).get("zusatz", ""),
                                     profile=profile, extern=externe if externe.verfuegbar() else None)
    except checkpunkte.ZuGross as fehler:
        raise SystemExit("%s — Checkpunkte nicht möglich. Kleineren Ordner wählen." % fehler)
    os.makedirs(ablage, exist_ok=True)
    with open(os.path.join(ablage, lauf_id + ".json"), "w", encoding="utf-8") as f:
        json.dump({"aufgabe": aufgabe, "ordner": ordner, "modell": modell_ref, "stufe": stufe, "freigabe": politik,
                   "zeit": time.time(), "ergebnis": ergebnis, "session_id": "", "vorgaenger": vorgaenger,
                   "quelle": "cli", "abgezweigt": abgezweigt, "profil": (profil or {}).get("name", "")},
                  f, ensure_ascii=False, indent=1)
    if aus.strom:
        kurz = {k: v for k, v in ergebnis.items() if k not in ("nachrichten", "verlauf")}
        aus.ereignis("ergebnis", lauf=lauf_id, ordner=ordner, **kurz)
    elif a.json:
        kurz = {k: v for k, v in ergebnis.items() if k not in ("nachrichten",)}
        print(json.dumps(dict(kurz, lauf=lauf_id, ordner=ordner), ensure_ascii=False, indent=1))
    else:
        print(werkbank.protokoll(aufgabe, ordner, ergebnis, stufe, politik, modell_ref))
    return RUECKGABE.get(ergebnis["beendet"], 1)


def befehl_einsetzen(aufgabe, ordner):
    """/name args → Text des eigenen Befehls. Vor der Pipe-Eingabe, damit $ARGUMENTS sie nicht schluckt."""
    ablage = os.path.join(Einstellungen().storage, "werkbank", "befehle")
    text, name = werkbank_befehle.anwenden(aufgabe, werkbank_befehle.finden(ordner, ablage))
    if name:
        sys.stderr.write("  » Befehl /%s\n" % name)
    elif aufgabe.strip().startswith("/") and re.match(r"^/[\w:-]+(\s|$)", aufgabe.strip()):
        sys.stderr.write("  » kein eigener Befehl „%s“ — die Aufgabe geht wörtlich an den Agenten\n"
                         % aufgabe.strip().split()[0])
    return text


def befehl_profile(a, ordner):
    liste = agentenprofile.finden(ordner, os.path.join(Einstellungen().storage, "werkbank", "agenten"))
    if a.json:
        print(json.dumps(list(liste.values()), ensure_ascii=False, indent=1))
        return 0
    for p in liste.values():
        print("%-14s %s  [%s%s%s]" % (p["name"], p["beschreibung"], p["herkunft"],
                                     " · " + p["stufe"] if p["stufe"] else "",
                                     " · " + ", ".join(p["werkzeuge"]) if p["werkzeuge"] else ""))
    return 0


def befehl_verlauf(a, ordner):
    ablage = os.path.join(Einstellungen().storage, "werkbank")
    lauf = a.lauf or (letzter_lauf(ordner) or (None,))[0]
    if not lauf:
        raise SystemExit("In %s gab es noch keinen Werkbank-Lauf." % ordner)
    try:
        p = schrittprotokoll.lesen(ablage, lauf)
    except (LookupError, ValueError) as fehler:
        raise SystemExit(str(fehler))
    if a.json:
        print(json.dumps(p, ensure_ascii=False, indent=1))
        return 0
    print("Lauf %s — %s" % (lauf, p["beginn"].get("aufgabe", "")[:200]))
    for s in p["schritte"]:
        ziel = " ".join(str(v) for v in (s.get("argumente") or {}).values())[:70]
        print("%3d  %-11s %-70s %5.1fs  ~%dk Token%s" % (s["schritt"], s.get("werkzeug") or "unlesbar", ziel,
                                                        s.get("sek", 0), round((s.get("kontext") or 0) / 1000),
                                                        "  ⎌ " + s["checkpunkt"] if s.get("checkpunkt") else ""))
        if s.get("gedanke"):
            print("     %s" % s["gedanke"][:150])
    ende = p["ende"]
    print("→ %s" % (ende["beendet"] if ende else "unterbrochen — dowos abzweigen %s %d \"Mach weiter\"" % (
        lauf, p["schritte"][-1]["schritt"] if p["schritte"] else 0)))
    return 0


def befehl_befehle(a, ordner):
    ablage = os.path.join(Einstellungen().storage, "werkbank", "befehle")
    liste = werkbank_befehle.finden(ordner, ablage)
    if a.json:
        print(json.dumps(list(liste.values()), ensure_ascii=False, indent=1))
        return 0
    if not liste:
        print("Keine eigenen Befehle. Lege Markdown-Dateien in .dowos/befehle/ oder .claude/commands/ an.")
    for b in liste.values():
        print("/%-24s %s%s" % (b["name"] + (" " + b["hinweis"] if b["hinweis"] else ""), b["beschreibung"],
                               "  [%s]" % b["herkunft"]))
    return 0


def eingabe_anhaengen(aufgabe):
    if not sys.stdin.isatty():
        daten = sys.stdin.read(200_000)
        if daten.strip():
            aufgabe += "\n\nEingabe (per Pipe übergeben):\n" + daten
    return aufgabe


def letzter_lauf(ordner):
    e = Einstellungen()
    ablage = os.path.join(e.storage, "werkbank")
    beste = None
    for n in os.listdir(ablage) if os.path.isdir(ablage) else []:
        if not n.endswith(".json"):
            continue
        try:
            with open(os.path.join(ablage, n), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if os.path.realpath(d.get("ordner") or "") == ordner and (not beste or d.get("zeit", 0) > beste[1].get("zeit", 0)):
            beste = (n[:-5], d)
    return beste


def befehl_review(a, ordner):
    """Code-Review wie `codex /review`: nur lesend, Befunde statt Änderungen."""
    if not os.path.isdir(os.path.join(ordner, ".git")):
        raise SystemExit("Kein Git-Repository — Review braucht Änderungen, die sich vergleichen lassen.")
    if a.basis:
        argv = ["git", "diff", "%s...HEAD" % a.basis]
        worum = "Änderungen dieses Branches gegenüber %s" % a.basis
    else:
        argv = ["git", "diff", "HEAD"]
        worum = "uncommittete Änderungen"
    diff = subprocess.run(argv, cwd=ordner, capture_output=True, text=True).stdout
    neu = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=ordner,
                         capture_output=True, text=True).stdout.split()
    if not diff.strip() and not neu:
        print("Keine Änderungen zu prüfen.")
        return 0
    if len(diff) > 60_000:
        diff = diff[:60_000] + "\n… (Diff gekürzt — der Agent kann Dateien selbst lesen)"
    aufgabe = ("Führe ein Code-Review der %s durch. Suche nach echten Fehlern (falsches Verhalten, Abstürze, "
               "Sicherheitsprobleme, fehlende Randfälle) — keine Stilfragen. Lies bei Bedarf die betroffenen "
               "Dateien und führe Tests aus. Ändere nichts. Melde mit fertig eine Liste der Befunde, je Befund: "
               "Datei:Zeile, Schwere (hoch/mittel/niedrig), was passiert, und warum. Gibt es keine, sag das.\n\n"
               "Diff:\n%s%s") % (worum, diff, ("\n\nNeue, noch nicht versionierte Dateien: " + ", ".join(neu)) if neu else "")
    return lauf_ausfuehren(a, aufgabe, ordner, stufe="lesen", politik="nie")


def befehl_checkpunkte(a, ordner):
    e = Einstellungen()
    cp = checkpunkte.Checkpunkte(os.path.join(e.storage, "werkbank", "checkpunkte"), ordner)
    liste = cp.liste()
    if a.json:
        print(json.dumps(liste, ensure_ascii=False, indent=1))
        return 0
    if not liste:
        print("Keine Checkpunkte für %s." % ordner)
    for c in liste:
        z = c.get("aenderung_zahl", {})
        print("%s  %s  %s  (+%d ~%d -%d)" % (c["id"], time.strftime("%d.%m. %H:%M:%S", time.localtime(c["zeit"])),
                                           c["beschreibung"][:70], z.get("neu", 0), z.get("geaendert", 0), z.get("geloescht", 0)))
    return 0


def befehl_zuruecksetzen(a, ordner):
    e = Einstellungen()
    cp = checkpunkte.Checkpunkte(os.path.join(e.storage, "werkbank", "checkpunkte"), ordner)
    try:
        seit = cp.seit(a.checkpunkt)
    except ValueError as fehler:
        raise SystemExit(str(fehler))
    print("Würde zurücksetzen: %d geändert, %d wiederhergestellt, %d entfernt."
          % (len(seit["geaendert"]), len(seit["geloescht"]), len(seit["neu"])))
    if not a.ja and not terminal_freigabe("Projekt auf Checkpunkt %s zurücksetzen" % a.checkpunkt):
        print("Abgebrochen.")
        return 1
    erg = cp.zuruecksetzen(a.checkpunkt)
    print("Zurückgesetzt. Rückgängig: dowos zuruecksetzen %s" % erg["sicherung"]["id"])
    return 0




HINWEIS_OHNE_BEFEHL = {
    "de": ("dowos ist das Terminal-Werkzeug von Dive on Wide (z. B. dowos werkbank \"Behebe den Test\").\n"
           "Die App selbst startest du so:\n"
           "  macOS:   Doppelklick auf „Dive on Wide starten.command“\n"
           "  Windows: Doppelklick auf „Dive on Wide starten.cmd“\n"
           "  Linux:   ./start.sh   (oder: python3 server.py)\n"
           "Alle Befehle: dowos --help"),
    "en": ("dowos is the terminal tool of Dive on Wide (e.g. dowos werkbank \"Fix the test\").\n"
           "To start the app itself:\n"
           "  macOS:   double-click \"Dive on Wide starten.command\"\n"
           "  Windows: double-click \"Dive on Wide starten.cmd\"\n"
           "  Linux:   ./start.sh   (or: python3 server.py)\n"
           "All commands: dowos --help"),
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["acp"]:                  # Editor-Anbindung: eigenes Protokoll auf stdin/stdout
        import acp
        return acp.main(argv[1:])
    if argv[:1] == ["rezept"]:               # Trainingsrezepte haben eigene Unterbefehle
        import rezept
        return rezept.main(argv[1:])
    if argv[:1] == ["tuev"]:                 # TÜV für Trainingsdaten — läuft auch ohne Dive on Wide
        import tuev
        return tuev.main(argv[1:])
    if argv[:1] == ["fremd"]:                # Axolotl/LLaMA-Factory-Konfiguration übersetzen
        import fremd
        return fremd.main(argv[1:])
    if argv[:1] == ["fehlerbuch"]:           # Woran scheitern die Läufe?
        import fehlerbuch
        return fehlerbuch.main(argv[1:])
    if argv[:1] == ["ausgang"]:              # Was hat diese Instanz herausgegeben?
        import ausgang
        return ausgang.main(argv[1:])
    if argv[:1] == ["konsolidieren"]:        # Nachtschicht: Läufe durchsehen (ohne Modell)
        import konsolidierung
        return konsolidierung.main(argv[1:])
    ap = argparse.ArgumentParser(prog="dowos", description="Dive on Wide Werkbank-Agent im Terminal")
    ap.add_argument("--ordner", default=".", help="Projektordner (Standard: aktueller Ordner)")
    ap.add_argument("--modell", help="Modell (Standard: WERKBANK_MODELL aus Dive on Wide)")
    ap.add_argument("--stufe", choices=tuple(werkbank.STUFEN), default=werkbank.STANDARD_STUFE)
    ap.add_argument("--freigabe", choices=tuple(werkbank.FREIGABEN), default=werkbank.STANDARD_FREIGABE)
    ap.add_argument("--max-schritte", type=int, default=werkbank.MAX_SCHRITTE)
    ap.add_argument("--json", action="store_true", help="Ergebnis als JSON nach stdout")
    ap.add_argument("--stream-json", action="store_true", help="jeden Schritt als JSON-Zeile nach stdout, am Ende das Ergebnis")
    # Ohne Befehl (Doppelklick im Finder, 06.10.2026): erklären statt nur „error: arguments are required“
    if not (sys.argv[1:] if argv is None else argv):
        import plattform
        print(HINWEIS_OHNE_BEFEHL["de" if plattform.konsole_deutsch() else "en"])
        return 0
    unter = ap.add_subparsers(dest="befehl", required=True)
    w = unter.add_parser("werkbank", help="eine Aufgabe im Projekt lösen")
    w.add_argument("aufgabe")
    w.add_argument("--plan", action="store_true", help="erst planen, Plan freigeben lassen")
    w.add_argument("--testbefehl", help="Hinweis an den Agenten, wie Tests laufen")
    f = unter.add_parser("fortsetzen", help="den letzten Lauf in diesem Ordner fortsetzen")
    f.add_argument("aufgabe")
    r = unter.add_parser("review", help="Git-Änderungen prüfen, ohne etwas zu ändern")
    r.add_argument("--basis", help="Branch vergleichen mit (z. B. main); ohne: uncommittete Änderungen")
    unter.add_parser("checkpunkte", help="Checkpunkte dieses Projekts auflisten")
    b = unter.add_parser("befehle", help="eigene /Befehle für dieses Projekt auflisten")
    pr = unter.add_parser("profile", help="Agentenprofile auflisten (für --profil und delegieren)")
    v = unter.add_parser("verlauf", help="Schrittprotokoll eines Laufs (Standard: der letzte hier)")
    v.add_argument("lauf", nargs="?")
    wh = unter.add_parser("wiederholen", help="aufgezeichneten Lauf ohne Modell nachspielen — hat sich die Werkbank verändert?")
    wh.add_argument("lauf", nargs="?")
    ab = unter.add_parser("abzweigen", help="bei Schritt N eines Laufs mit neuer Anweisung weitermachen")
    ab.add_argument("lauf")
    ab.add_argument("schritt", type=int)
    ab.add_argument("aufgabe")
    ab.add_argument("--zuruecksetzen", action="store_true", help="Projekt vorher auf den Stand dieses Schritts")
    unter.add_parser("acp", help="als Agent in Zed, JetBrains, Neovim (Agent Client Protocol über stdin/stdout)")
    unter.add_parser("rezept", help="Trainingsrezepte: vorlagen | vorlage <name> | pruefen | plan | starten <datei>")
    unter.add_parser("tuev", help="TÜV für Trainingsdaten: Gesundheit, Leckage, Verseuchung, Herkunft, Siegel")
    unter.add_parser("fremd", help="Axolotl-/LLaMA-Factory-Konfiguration in ein Rezept übersetzen")
    unter.add_parser("fehlerbuch", help="Schrittprotokolle auswerten: woran scheitern die Läufe?")
    unter.add_parser("ausgang", help="Ausgangsbuch: was ist an fremde Gerüste hinausgegangen?")
    unter.add_parser("konsolidieren", help="Nachtschicht: die letzten Läufe durchsehen und zählen")
    z = unter.add_parser("zuruecksetzen", help="Projekt auf einen Checkpunkt zurücksetzen")
    z.add_argument("checkpunkt")
    z.add_argument("--ja", action="store_true", help="ohne Rückfrage")
    for p in (w, f, r, z, b, pr, v, ab, wh):     # Optionen auch hinter dem Unterbefehl erlauben
        for opt in ("--ordner", "--modell", "--max-schritte"):
            p.add_argument(opt, dest=opt[2:].replace("-", "_"), default=argparse.SUPPRESS,
                           type=int if opt == "--max-schritte" else str)
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        p.add_argument("--stream-json", action="store_true", default=argparse.SUPPRESS)
    ap.add_argument("--profil", help="Agentenprofil (dowos profile)")
    for p in (w, f, ab):
        p.add_argument("--profil", default=argparse.SUPPRESS)
        p.add_argument("--stufe", choices=tuple(werkbank.STUFEN), default=argparse.SUPPRESS)
        p.add_argument("--freigabe", choices=tuple(werkbank.FREIGABEN), default=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    ordner = os.path.realpath(os.path.expanduser(a.ordner))
    if not os.path.isdir(ordner):
        raise SystemExit("Ordner „%s“ gibt es nicht." % a.ordner)
    if a.befehl == "werkbank":
        return lauf_ausfuehren(a, eingabe_anhaengen(befehl_einsetzen(a.aufgabe, ordner)), ordner,
                               planmodus=a.plan, testbefehl=a.testbefehl)
    if a.befehl == "fortsetzen":
        vorher = letzter_lauf(ordner)
        if not vorher:
            raise SystemExit("In %s gab es noch keinen Werkbank-Lauf." % ordner)
        lauf_id, spur = vorher
        return lauf_ausfuehren(a, eingabe_anhaengen(befehl_einsetzen(a.aufgabe, ordner)), ordner,
                               vorher=(spur.get("ergebnis") or {}).get("nachrichten"), vorgaenger=lauf_id,
                               stufe=spur.get("stufe"), politik=spur.get("freigabe"),
                               modell=a.modell or spur.get("modell"))
    if a.befehl == "review":
        return befehl_review(a, ordner)
    if a.befehl == "checkpunkte":
        return befehl_checkpunkte(a, ordner)
    if a.befehl == "befehle":
        return befehl_befehle(a, ordner)
    if a.befehl == "profile":
        return befehl_profile(a, ordner)
    if a.befehl == "verlauf":
        return befehl_verlauf(a, ordner)
    if a.befehl == "wiederholen":
        import wiederholung
        ablage = os.path.join(Einstellungen().storage, "werkbank")
        lauf = a.lauf or (letzter_lauf(ordner) or (None,))[0]
        if not lauf:
            raise SystemExit("In %s gab es noch keinen Werkbank-Lauf." % ordner)
        # Die HEUTIGEN Regeln auf den alten Stand. Die Kopie liegt woanders als
        # das Projekt, ist also nie „vertraut" — Verbote greifen trotzdem,
        # Freigaben und Hooks nicht. Fuer eine Wiederholung ist genau das
        # richtig: Sie soll pruefen, nicht ausfuehren.
        e_w = Einstellungen()
        def regeln_fuer(kopie):
            r, _ = werkbank_regeln.fuer_projekt(
                kopie, e_w.get("WERKBANK_REGELN"),
                werkbank_regeln.Vertrauen(os.path.join(ablage, "vertrauen.json")),
                fragen=None)
            return r
        try:
            w = wiederholung.wiederholen(ablage, lauf, os.path.join(ablage, "checkpunkte"),
                                         regeln_fuer=regeln_fuer)
        except (LookupError, ValueError) as fehler:
            raise SystemExit(str(fehler))
        if a.json:
            print(json.dumps(w, ensure_ascii=False, indent=1))
        else:
            print("Wiederholung von %s: %d Schritte — %s" % (lauf, w["schritte"], "gleich ✅" if w["gleich"]
                                                              else "%d Abweichung(en) ⚠️" % len(w["abweichungen"])))
            for x in w["abweichungen"]:
                print("\n  %s (%s): %s" % (x["wo"], x["werkzeug"], x.get("grund") or ""))
                if "damals" in x:
                    print("    damals: %s\n    jetzt:  %s" % (x["damals"][:300].replace("\n", " ⏎ "),
                                                             x["jetzt"][:300].replace("\n", " ⏎ ")))
            if w["nicht_wiederholbar"]:
                print("\n  nicht wiederholbar (braucht Netz oder externe Programme): %s" % ", ".join(
                    "%s %s" % (x["wo"], x["werkzeug"]) for x in w["nicht_wiederholbar"]))
        return 0 if w["gleich"] else 1
    if a.befehl == "abzweigen":
        ablage = os.path.join(Einstellungen().storage, "werkbank")
        try:
            p = schrittprotokoll.lesen(ablage, a.lauf)
            vorher = schrittprotokoll.nachrichten_bis(p, a.schritt)
        except (LookupError, ValueError) as fehler:
            raise SystemExit(str(fehler))
        ordner = p["meta"].get("ordner") or ordner
        abzweig = {"lauf": a.lauf, "schritt": a.schritt, "zuruecksetzen": a.zuruecksetzen,
                   "checkpunkt": schrittprotokoll.checkpunkt_bis(p, a.schritt)}
        if a.zuruecksetzen:
            if not abzweig["checkpunkt"]:
                raise SystemExit("Zu diesem Schritt gibt es keinen Checkpunkt.")
            checkpunkte.Checkpunkte(os.path.join(ablage, "checkpunkte"), ordner).zuruecksetzen(abzweig["checkpunkt"])
            sys.stderr.write("  ⎌ Projekt auf den Stand von Schritt %d zurückgesetzt (selbst umkehrbar)\n" % a.schritt)
        return lauf_ausfuehren(a, eingabe_anhaengen(befehl_einsetzen(a.aufgabe, ordner)), ordner, vorher=vorher,
                               stufe=p["meta"].get("stufe"), politik=p["meta"].get("freigabe"),
                               modell=a.modell or p["meta"].get("modell"), abgezweigt=abzweig)
    if a.befehl == "zuruecksetzen":
        return befehl_zuruecksetzen(a, ordner)
    return 1


if __name__ == "__main__":
    ausgabe_absichern()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.stderr.write("\n  abgebrochen\n")
        sys.exit(130)
