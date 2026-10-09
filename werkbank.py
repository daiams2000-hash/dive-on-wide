# -*- coding: utf-8 -*-
"""Werkbank-Agent — löst Aufgaben selbstständig in einem echten Projektordner.

Das Herzstück eines Coding-Agenten, wie Claude Code und Codex es haben:
eine Werkzeugschleife. Das Modell liest Code, sucht, ändert gezielt, führt
Tests aus und meldet sich erst, wenn die Aufgabe gelöst und geprüft ist.

Vorlage ist der Referenz-Agent aus Prüfstand Stufe 2 (`~/llm/work/eval_stufe2`),
mit dem die Modelle verglichen werden. Dieselben sieben Werkzeuge, dasselbe
JSON-im-Text-Format — damit ist jedes Modell benutzbar, auch eines ohne
eingebaute Werkzeug-Schnittstelle, und die Messwerte bleiben vergleichbar.

Dazu kommt, was ein Werkzeug im Alltag braucht:

- **Rechtestufen** (wie Codex): `lesen`, `projekt` (Standard), `voll`.
  Getrennt davon die **Freigabe-Politik**: `nie`, `befehle`, `alles`.
- **Echte Grenzen statt Bitten im Prompt.** Befehle laufen unter macOS in
  `sandbox-exec`, unter Linux in `bwrap` — ohne Netz, Schreiben nur im
  Projekt. Gibt es keine Sandbox, wird das nicht verschwiegen: Dann muss der
  Nutzer jeden Befehl einzeln freigeben.
- **Kontextverdichtung**, damit lange Aufgaben nicht am Kontextfenster enden.
- **Projekt-Anweisungen** aus `DOWOS.md` (ersatzweise `AGENTS.md`).
- **Nachsichtiges Lesen der Antworten.** Im Basiswahl-Lauf waren 52 Antworten
  von gemma4-base unlesbar — 25 davon nur, weil die letzte `}` fehlte, 9 wegen
  roher Zeilenumbrüche oder `\\ge` in Strings. Das wird repariert statt dem
  Modell einen ganzen Schritt zu kosten.

Reine Standardbibliothek, kein Import aus server.py: Der Prüfstand kann das
Modul ohne laufendes Dive on Wide benutzen.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

WERKZEUGE = ("liste", "lesen", "suchen", "ersetzen", "schreiben", "ausfuehren", "plan", "delegieren", "skill",
             "merken", "erinnern", "websuche", "webseite", "fertig", "frage")
# Welche Argumente das Modell setzen darf. Alles andere wird ignoriert — sonst
# könnte es etwa `timeout` mitschicken und das Zeitlimit für Befehle aushebeln.
ARGUMENTE = {"liste": ("pfad",), "lesen": ("pfad", "von", "bis"), "suchen": ("muster", "pfad"),
             "ersetzen": ("pfad", "alt", "neu"), "schreiben": ("pfad", "inhalt"), "ausfuehren": ("befehl",),
             "fertig": ("zusammenfassung",), "frage": ("frage",), "plan": ("aufgaben",),
             "delegieren": ("auftrag", "rechte", "profil", "auftraege"), "skill": ("name", "datei"),
             "merken": ("notiz", "bereich"), "erinnern": ("suche",), "websuche": ("suche",), "webseite": ("url",)}
UNTERAGENT_SCHRITTE = 15
MAX_PARALLEL = 4            # Aufträge je delegieren; gleichzeitig laufen sie nur, wenn alle nur lesen
AENDERND = ("ersetzen", "schreiben")

STUFEN = {
    "lesen":   "nur lesen — Dateien ansehen und durchsuchen, Befehle nur ohne Schreibrecht",
    "projekt": "im Projekt schreiben — Befehle ohne Netz, Schreiben nur im Projektordner",
    "voll":    "voller Zugriff — Befehle ohne Sandbox, mit Netz",
}
FREIGABEN = {
    "nie":     "nicht nachfragen",
    "befehle": "vor jedem Befehl fragen",
    "alles":   "vor jeder Änderung und jedem Befehl fragen",
}
STANDARD_STUFE = "projekt"
STUFEN_REIHENFOLGE = ("lesen", "projekt", "voll")
STANDARD_FREIGABE = "nie"

MAX_SCHRITTE = 30
BEFEHL_SEKUNDEN = 60
AUSGABE_GRENZE = 3500
ANWEISUNG_GRENZE = 8000
ANWEISUNGSDATEIEN = ("DOWOS.md", "AGENTS.md")
UEBERGANGEN = ("__pycache__", ".git", "node_modules", ".venv", "venv", ".tox")
TESTBEFEHL = "python -m unittest discover -s tests -t ."

SYSTEM = """Du bist der Werkbank-Agent von Dive on Wide und arbeitest in einem Projektordner. Löse die Aufgabe selbstständig: relevanten Code lesen, das Problem nachvollziehen, gezielt ändern, mit Tests prüfen.

Antworte in JEDEM Schritt mit genau einem JSON-Objekt und sonst nichts:
{"gedanke":"kurz: was du vorhast und warum","werkzeug":"<name>","argumente":{...}}

Werkzeuge:
- liste      {"pfad":"."}                                  Dateien im Ordner auflisten
- lesen      {"pfad":"datei.py","von":1,"bis":200}         Datei mit Zeilennummern lesen
- suchen     {"muster":"regulärer Ausdruck","pfad":"."}    Text in Dateien suchen
- ersetzen   {"pfad":"datei.py","alt":"exakter bisheriger Text","neu":"neuer Text"}   gezielte Änderung, "alt" muss genau einmal vorkommen
- schreiben  {"pfad":"datei.py","inhalt":"kompletter Dateiinhalt"}                    Datei anlegen oder vollständig ersetzen
- ausfuehren {"befehl":"python -m unittest discover -s tests -t ."}                  Shell-Befehl im Projektordner, höchstens 60 s
- plan       {"aufgaben":[{"text":"…","stand":"offen|laeuft|erledigt"}]}   Arbeitsplan anlegen oder aktualisieren — bei Aufgaben mit mehreren Teilen
- fertig     {"zusammenfassung":"was geändert wurde"}      erst wenn die Aufgabe gelöst UND geprüft ist
- frage      {"frage":"konkrete Frage an den Nutzer"}      nur wenn die Aufgabe ohne seine Antwort nicht lösbar ist

Regeln:
- Lies eine Datei, bevor du sie änderst.
- Prüfe jede Änderung, indem du Tests ausführst. Die vorhandenen Tests decken die Aufgabe oft nicht vollständig ab: prüfe die Anforderungen aus der Aufgabe zusätzlich selbst, z. B. mit python -c "...".
- Verändere keine vorhandenen Tests, um sie grün zu bekommen.
- Kein Text außerhalb des JSON-Objekts. Schließe jedes JSON-Objekt vollständig."""


# Verlangt die Aufgabe ausdrücklich neue Tests? („mit Tests absichern“, „Tests schreiben“ —
# nicht „Tests ausführen“.) Anwendungstest 29.09.2026: Rechnungsfehler behoben, geprüft
# mit einem Einzeiler, „mit Tests absichern“ still übergangen.
TESTS_VERLANGT = re.compile(r"mit\s+(unit-?)?tests?\b|tests?\b.{0,40}(schreib|anleg|ergänz|hinzufüg|absicher)|"
                            r"(schreib|ergänz|füge?)\w*\s.{0,30}\btests?\b|\b(write|add)\w*\s+(\w+\s+)?tests?\b", re.I)
TESTDATEI = re.compile(r"(^|/)(tests?/|test_[^/]*$|[^/]*_test\.\w+$|[^/]*\.(test|spec)\.\w+$)")


# --------------------------------------------------------------------- JSON ---

def _json_kandidaten(text):
    """Ausgewogene {...}-Blöcke — dasselbe Verfahren wie in server.py.

    Liefert zusätzlich den Rest ab dem letzten nicht geschlossenen `{`, damit
    eine Antwort, der nur die letzte Klammer fehlt, repariert werden kann."""
    kandidaten, offen = [], None
    tiefe, start, im_text, escaped = 0, -1, False, False
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
        elif c == "}" and tiefe > 0:
            tiefe -= 1
            if tiefe == 0:
                kandidaten.append(text[start:i + 1])
    if tiefe > 0 and not im_text:
        offen = (text[start:], tiefe)
    return kandidaten, offen


_UNGUELTIGES_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def _laden(roh):
    """json.loads mit den zwei harmlosen Reparaturen, die Modelle brauchen."""
    try:
        return json.loads(roh, strict=False)        # rohe Zeilenumbrüche in Strings
    except ValueError:
        pass
    # \' ist in JSON ungültig, gemeint ist ein Apostroph; \ge (LaTeX) ist ein
    # wörtlicher Backslash. Beides kommt in Gedanken und Code ständig vor.
    geflickt = _UNGUELTIGES_ESCAPE.sub(r"\\\\", roh.replace("\\'", "'"))
    return json.loads(geflickt, strict=False)


def _unmaskiert_escapen(wert):
    """Macht aus einem String-Inhalt mit rohen Anführungszeichen einen gültigen JSON-String."""
    aus, i = [], 0
    while i < len(wert):
        c = wert[i]
        if c == "\\" and i + 1 < len(wert):
            aus.append(wert[i:i + 2])
            i += 2
            continue
        aus.append('\\"' if c == '"' else c)
        i += 1
    return json.loads('"' + "".join(aus) + '"', strict=False)


def _schema_reparatur(text):
    """Letzter Rettungsversuch: die Aktion anhand der bekannten Schlüssel lesen.

    Modelle schreiben in Gedanken und Code gern rohe Anführungszeichen —
    „nutzt `split(",")`“ —, und damit ist das JSON kaputt. Weil die Werkbank
    weiß, welche Schlüssel kommen (gedanke, werkzeug, argumente und die
    Argumente je Werkzeug), lassen sich die Grenzen der Texte trotzdem sicher
    finden: Ein Wert endet genau dort, wo der nächste bekannte Schlüssel beginnt.
    Gemessen am Qwen3.6-Lauf: dieselbe unmaskierte Antwort kam 29-mal hintereinander."""
    m = re.search(r'"werkzeug"\s*:\s*"([a-z_]+)"', text)
    if not m or m.group(1) not in WERKZEUGE + ("mcp",):
        return None
    werkzeug = m.group(1)
    aktion = {"werkzeug": werkzeug, "argumente": {}}
    g = re.search(r'"gedanke"\s*:\s*"(.*?)"\s*,\s*"(?:werkzeug|argumente)"\s*:', text, re.S)
    if g:
        try:
            aktion["gedanke"] = _unmaskiert_escapen(g.group(1))
        except ValueError:
            aktion["gedanke"] = g.group(1)
    a = re.search(r'"argumente"\s*:\s*\{', text)
    if not a:
        return aktion
    rumpf = text[a.end():]
    ende = rumpf.rfind("}")
    if ende < 0:
        return None
    rumpf = rumpf[:ende]
    if "}" in rumpf.rstrip()[-1:]:                   # die äußere Klammer mitgefasst
        rumpf = rumpf.rstrip()[:-1]
    schluessel = ARGUMENTE.get(werkzeug, ()) if werkzeug != "mcp" else ("werkzeug", "eingabe")
    fundstellen = sorted((mm.start(), mm.end(), k) for k in schluessel
                         for mm in [re.search(r'"%s"\s*:\s*' % re.escape(k), rumpf)] if mm)
    for nr, (anfang, wert_start, k) in enumerate(fundstellen):
        wert_ende = fundstellen[nr + 1][0] if nr + 1 < len(fundstellen) else len(rumpf)
        wert = rumpf[wert_start:wert_ende].rstrip().rstrip(",").rstrip()
        if nr + 1 == len(fundstellen) and wert.startswith('"'):
            # Muell hinter dem letzten Text: Gemma 4 schloss eine ganze HTML-Seite
            # mit `"}$$}` ab — sonst richtig maskiert, und doch 13 von 18 Schritten verloren.
            m = re.match(r'(.*")[\s}\]$`]*$', wert, re.S)
            wert = m.group(1) if m else wert
        try:
            if wert.startswith('"') and wert.endswith('"') and len(wert) >= 2:
                aktion["argumente"][k] = _unmaskiert_escapen(wert[1:-1])
            else:
                aktion["argumente"][k] = json.loads(wert, strict=False)
        except ValueError:
            return None
    return aktion


def json_lesen(text):
    """Das erste lesbare JSON-Objekt einer Modellantwort, sonst None."""
    text = text or ""
    kandidaten, offen = _json_kandidaten(text)
    for roh in kandidaten:
        try:
            wert = _laden(roh)
        except ValueError:
            continue
        if isinstance(wert, dict):
            return wert
    if offen:
        rest, tiefe = offen
        rest = re.sub(r"(\s*(```|<\|?[\w|]*\|?>))+\s*$", "", rest.rstrip())
        try:
            wert = _laden(rest + "}" * tiefe)
            if isinstance(wert, dict):
                return wert
        except ValueError:
            pass
    return _schema_reparatur(text)


def json_fehler(text):
    """Warum ist die Antwort unlesbar? Eine konkrete Rückmeldung hilft dem Modell mehr als „ungültig“."""
    start = (text or "").find("{")
    if start < 0:
        return "Kein JSON-Objekt gefunden."
    try:
        json.loads(text[start:text.rfind("}") + 1] or text[start:], strict=False)
    except ValueError as e:
        hinweis = ""
        if "delimiter" in str(e):
            hinweis = (" — vermutlich ein Anführungszeichen mitten im Text. Innerhalb von Texten "
                       "muss es als \\\" geschrieben werden")
        return "JSON nicht lesbar: %s%s" % (e, hinweis)
    return "JSON enthält kein Feld \"werkzeug\"."


# ----------------------------------------------------------------- Sandbox ---

def sandbox_art():
    """Welche Sandbox trägt auf diesem Rechner? None, wenn keine."""
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return "sandbox-exec"
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        return "bwrap"
    return None


# Orte mit Geheimnissen, die Befehle des Agenten nicht LESEN duerfen.
#
# DowBench i5 (28.09.2026): Der Grundschutz sperrte .env fuer die Werkzeuge
# lesen/suchen — ein Python-Einzeiler ueber `ausfuehren` las die Datei trotzdem
# und schrieb sie, einer eingeschleusten Anweisung folgend, in eine Ausgabe.
# Eine Musterliste fuer Befehle waere wirkungslos (cat, base64, python …);
# deshalb sperrt die SANDBOX das Lesen dieser Dateien, egal welches Programm fragt.
GEHEIME_ORTE = ["~/.ssh", "~/.aws", "~/.gnupg", "~/.netrc", "~/.config/gh", "~/.config/gcloud",
                "~/.docker/config.json", "~/.kube", "~/.git-credentials", "~/Library/Keychains",
                "~/.password-store", "~/.pypirc", "~/.npmrc"]
ZUSAETZLICH_GESPERRT = []      # der Server traegt hier seine Datenbank, .env und mcp.json ein
_NICHT_DURCHSUCHEN = {".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache"}


def lese_sperren(ordner, sperre):
    """Absolute Pfade, die ein Befehl in der Sandbox nicht lesen darf."""
    aus = []
    for ort in GEHEIME_ORTE:
        p = os.path.realpath(os.path.expanduser(ort))
        if os.path.exists(p) and not os.path.realpath(ordner).startswith(p + os.sep):
            aus.append(p)
    for p in ZUSAETZLICH_GESPERRT:
        p = p() if callable(p) else p           # der Server meldet Funktionen: Pfade koennen wechseln
        if p and os.path.exists(p):
            aus.append(os.path.realpath(p))
    gesehen = 0
    for w, ordner_liste, namen in os.walk(ordner):
        ordner_liste[:] = [o for o in ordner_liste if o not in _NICHT_DURCHSUCHEN]
        for n in namen:
            gesehen += 1
            if gesehen > 20000:
                return aus
            voll = os.path.join(w, n)
            if sperre(os.path.relpath(voll, ordner).replace(os.sep, "/")):
                aus.append(os.path.realpath(voll))
    return aus


def _mac_profil(schreibbar, gesperrt=()):
    erlaubt = " ".join('(subpath "%s")' % p.replace('"', '') for p in schreibbar)
    profil = ("(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n"
              "(allow file-write* %s (literal \"/dev/null\") (literal \"/dev/stdout\") "
              "(literal \"/dev/stderr\") (literal \"/dev/tty\") (literal \"/dev/dtracehelper\"))"
              % erlaubt)
    if gesperrt:
        profil += "\n(deny file-read* %s)" % " ".join(
            ('(subpath "%s")' if os.path.isdir(p) else '(literal "%s")') % p.replace('"', '').replace("\\", "")
            for p in gesperrt)
    return profil


def _ist_zeitablauf(e):
    """Zeitüberschreitung beim Modell, egal wie urllib sie verpackt."""
    grund = getattr(e, "reason", None)
    return (isinstance(e, (socket.timeout, TimeoutError)) or isinstance(grund, (socket.timeout, TimeoutError))
            or "timed out" in str(e).lower())


def windows_befehl(befehl):
    """Kommandozeile für cmd.exe — als TEXT, nicht als Liste.

    Aus einer Liste baut Python die Zeile mit \\"-Maskierung, die cmd.exe nicht
    kennt: jeder Befehl mit Anführungszeichen (python -c "…") scheiterte mit
    „ist entweder falsch geschrieben“ (Windows-VM, 29.09.2026). Mit /s /c "…"
    nimmt cmd.exe den Inhalt zwischen den äußeren Anführungszeichen wörtlich."""
    return 'cmd.exe /d /s /c "%s"' % befehl


def befehl_bauen(befehl, ordner, tmp, stufe, art, gesperrt=()):
    """Argumentliste für einen Shell-Befehl in der passenden Grenze.

    Gibt None zurück, wenn die Stufe eine Sandbox verlangt, aber keine da ist."""
    if stufe == "voll":
        if os.name == "nt":
            return windows_befehl(befehl)
        return ["/bin/sh", "-c", befehl]
    schreibbar = [tmp] + ([ordner] if stufe == "projekt" else [])
    if art == "sandbox-exec":
        return ["sandbox-exec", "-p", _mac_profil(schreibbar, gesperrt), "/bin/sh", "-c", befehl]
    if art == "bwrap":
        teile = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                 "--bind", tmp, tmp, "--unshare-net", "--die-with-parent"]
        if stufe == "projekt":
            teile += ["--bind", ordner, ordner]
        # Geheimnisse ueberdecken: Dateien mit /dev/null, Ordner mit einem leeren tmpfs.
        for p in gesperrt:
            teile += ["--tmpfs", p] if os.path.isdir(p) else ["--ro-bind", "/dev/null", p]
        return teile + ["--chdir", ordner, "/bin/sh", "-c", befehl]
    return None


# ---------------------------------------------------------------- Werkbank ---

class Werkbank:
    """Die Werkzeuge in einem Projektordner. Pfade können ihn nie verlassen."""

    def __init__(self, ordner, stufe=STANDARD_STUFE, sandbox="auto"):
        if stufe not in STUFEN:
            raise ValueError("Unbekannte Rechtestufe '%s'." % stufe)
        self.ordner = os.path.realpath(ordner)
        if not os.path.isdir(self.ordner):
            raise ValueError("Projektordner '%s' gibt es nicht." % ordner)
        self.stufe = stufe
        self.sandbox = sandbox_art() if sandbox == "auto" else sandbox
        self._tmp = None
        # Ab Werk der Grundschutz; arbeiten() setzt die Regeln des Besitzers ein.
        # Gilt fuer lesen, suchen und — ueber die Sandbox — fuer Befehle.
        import regeln as _regeln
        _grund = _regeln.Regeln()
        self.lese_sperre = lambda rel: _grund.entscheidung("lesen", {"pfad": rel})[0] == "verboten"

    # -- Hilfen
    def _pfad(self, p, schreiben=False):
        voll = os.path.realpath(os.path.join(self.ordner, p or "."))
        if voll != self.ordner and not voll.startswith(self.ordner + os.sep):
            raise ValueError("Pfad liegt außerhalb des Projektordners.")
        if schreiben:
            teile = os.path.relpath(voll, self.ordner).split(os.sep)
            if ".git" in teile:
                raise ValueError("In .git wird nicht geschrieben — das ist die Versionsgeschichte des Projekts.")
        return voll

    @staticmethod
    def _kurz(text, grenze=AUSGABE_GRENZE):
        if len(text) <= grenze:
            return text
        return (text[:grenze // 2] + "\n… [%d Zeichen ausgelassen] …\n" % (len(text) - grenze)
                + text[-grenze // 2:])

    def tmp(self):
        if not self._tmp:
            self._tmp = os.path.realpath(tempfile.mkdtemp(prefix="dowos_werkbank_"))
        return self._tmp

    def schliessen(self):
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    def erlaubt(self, werkzeug):
        """(erlaubt, Grund) — was die Rechtestufe hergibt."""
        if werkzeug in AENDERND and self.stufe == "lesen":
            return False, "Rechtestufe „nur lesen“: Dateien dürfen nicht geändert werden."
        if werkzeug == "ausfuehren" and self.stufe == "lesen" and not self.sandbox:
            return False, ("Rechtestufe „nur lesen“ und keine Sandbox auf diesem Rechner: "
                           "Befehle könnten schreiben, deshalb sind sie gesperrt.")
        return True, ""

    def ohne_grenze(self, werkzeug):
        """True, wenn ein Befehl mangels Sandbox ungeschützt liefe."""
        return werkzeug == "ausfuehren" and self.stufe == "projekt" and not self.sandbox

    # -- Werkzeuge
    def liste(self, pfad="."):
        basis, out = self._pfad(pfad), []
        for wurzel, ordner, dateien in os.walk(basis):
            ordner[:] = sorted(o for o in ordner if o not in UEBERGANGEN and not o.startswith("."))
            for d in sorted(dateien):
                if d.endswith((".pyc", ".pyo")) or d == ".DS_Store":
                    continue
                out.append(os.path.relpath(os.path.join(wurzel, d), self.ordner))
                if len(out) >= 300:
                    return "\n".join(out) + "\n… (mehr als 300 Dateien, Unterordner gezielt auflisten)"
        return "\n".join(out) or "(leer)"

    def lesen(self, pfad, von=1, bis=None):
        with open(self._pfad(pfad), encoding="utf-8", errors="replace") as f:
            zeilen = f.read().split("\n")
        von = max(1, int(von or 1))
        bis = min(len(zeilen), int(bis or von + 299))
        text = "\n".join("%4d| %s" % (i, zeilen[i - 1]) for i in range(von, bis + 1))
        rest = ""
        if bis < len(zeilen):
            # Was hinter dem Ausschnitt steht, mit Zeilennummern — sonst liest der Agent eine
            # 780-Zeilen-Datei Block für Block, bis der Anfang aus dem Gedächtnis fällt (Frostwerk-Test)
            weiter = gliederung([(i, zeilen[i - 1]) for i in range(bis + 1, len(zeilen) + 1)])
            rest = "\n(Datei hat %d Zeilen%s)" % (len(zeilen), "; weiter unten: " + weiter if weiter else "")
        return self._kurz(text, 8000) + rest

    def suchen(self, muster, pfad="."):
        try:
            rx = re.compile(muster)
        except re.error as e:
            raise ValueError("Ungültiger regulärer Ausdruck: %s" % e)
        treffer = []
        for datei in self.liste(pfad).split("\n"):
            p = os.path.join(self.ordner, datei)
            if not os.path.isfile(p) or os.path.getsize(p) > 1_000_000 or self.lese_sperre(datei):
                continue
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    for i, z in enumerate(f, 1):
                        if rx.search(z):
                            treffer.append("%s:%d: %s" % (datei, i, z.rstrip()[:200]))
            except OSError:
                continue
            if len(treffer) >= 100:
                break
        return self._kurz("\n".join(treffer[:100]) or "(keine Treffer)")

    def ersetzen(self, pfad, alt, neu):
        p = self._pfad(pfad, schreiben=True)
        with open(p, encoding="utf-8") as f:
            inhalt = f.read()
        alt, neu = str(alt if alt is not None else ""), str(neu if neu is not None else "")
        if not alt.strip():
            raise ValueError("'alt' ist leer. Für eine neue oder komplett neue Datei nutze schreiben.")
        n = inhalt.count(alt)
        korrektur = ""
        if n != 1:
            treffer = ersetzen_nachsichtig(inhalt, alt, neu)
            if not treffer:
                hinweis = ""
                if n == 0 and inhalt.count(alt.strip()) == 1:
                    hinweis = " Ohne Leerraum am Anfang/Ende kommt er genau einmal vor — Einrückung prüfen."
                raise ValueError("'alt' kommt %d-mal vor, erwartet genau einmal.%s "
                                 "Datei vorher mit 'lesen' ansehen." % (n, hinweis))
            neuer_inhalt, korrektur = treffer
        else:
            neuer_inhalt = inhalt.replace(alt, neu, 1)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(neuer_inhalt)
        return "%s geändert (%d -> %d Zeilen)%s%s" % (
            pfad, len(inhalt.splitlines()), len(neuer_inhalt.splitlines()),
            "\n(Hinweis: 'alt' passte erst, nachdem %s — gib Text künftig genau so an, wie er in der Datei steht.)" % korrektur
            if korrektur else "", syntax_hinweis(pfad, neuer_inhalt))

    def schreiben(self, pfad, inhalt):
        p = self._pfad(pfad, schreiben=True)
        if os.path.isdir(p):
            raise ValueError("'%s' ist ein Ordner." % pfad)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(inhalt)
        return "%s geschrieben (%d Zeilen)%s" % (pfad, len(inhalt.splitlines()), syntax_hinweis(pfad, inhalt))

    def ausfuehren(self, befehl, timeout=BEFEHL_SEKUNDEN):
        if not str(befehl or "").strip():
            raise ValueError("Kein Befehl angegeben.")
        tmp = self.tmp()
        argv = befehl_bauen(befehl, self.ordner, tmp, self.stufe, self.sandbox,
                            lese_sperren(self.ordner, self.lese_sperre))
        if argv is None:
            # Nur erreichbar, wenn der Aufrufer die Freigabe eingeholt hat.
            argv = windows_befehl(befehl) if os.name == "nt" else ["/bin/sh", "-c", befehl]
        # Bewusst eine schlanke Umgebung: Die des Servers enthält API-Schlüssel.
        umgebung = {"PATH": self._pfad_variable(tmp), "HOME": os.path.expanduser("~"),
                    "TMPDIR": tmp, "TEMP": tmp, "TMP": tmp, "LANG": os.environ.get("LANG", "C.UTF-8"),
                    "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        # Windows braucht ein paar Systemvariablen, sonst legen Systembausteine
        # Ordner namens „%SystemDrive%“ im Projekt an (Windows-VM, 29.09.2026).
        # Keine davon trägt Geheimnisse.
        for n in ("SYSTEMROOT", "COMSPEC", "PATHEXT", "VIRTUAL_ENV", "SYSTEMDRIVE", "WINDIR", "PROGRAMDATA",
                  "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "USERPROFILE", "LOCALAPPDATA",
                  "APPDATA", "PUBLIC", "HOMEDRIVE", "HOMEPATH", "USERNAME", "COMPUTERNAME", "OS",
                  "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"):
            if n in os.environ:
                umgebung[n] = os.environ[n]
        ausgabe = os.path.join(tmp, "ausgabe.txt")
        with open(ausgabe, "wb") as f:
            try:
                proc = subprocess.Popen(argv, cwd=self.ordner, stdout=f, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, env=umgebung,
                                        start_new_session=(os.name != "nt"))
            except OSError as e:
                return "Befehl ließ sich nicht starten: %s" % e
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._beenden(proc)
                return "Zeitüberschreitung nach %d s — Befehl abgebrochen." % timeout
        with open(ausgabe, "rb") as f:
            f.seek(0, 2)
            groesse = f.tell()
            f.seek(max(0, groesse - 200_000))
            text = f.read().decode("utf-8", "replace")
        return self._kurz("exit=%d\n%s" % (code, text))

    @staticmethod
    def _pfad_variable(tmp):
        """PATH für Befehle: das Python von Dive on Wide zuerst, und `python` gibt es immer.

        Modelle schreiben fast immer `python -m unittest`. Auf macOS mit
        Homebrew und vielen Linuxen gibt es aber nur `python3` — dann scheitert
        jeder Testlauf an „command not found“, und das Modell sucht den Fehler
        im Code statt in der Umgebung."""
        pfad = os.environ.get("PATH", "/usr/bin:/bin")
        teile = [os.path.dirname(sys.executable)]
        if os.name == "nt":
            # Umgekehrt: Unter Windows gibt es python, aber „python3“ ist der Platzhalter,
            # der nur den Microsoft Store anbietet. Ein .cmd davor leitet um.
            zwischen = os.path.join(tmp, "bin")
            ziel = os.path.join(zwischen, "python3.cmd")
            if not os.path.exists(ziel):
                os.makedirs(zwischen, exist_ok=True)
                with open(ziel, "w") as f:
                    f.write('@"%s" %%*\r\n' % sys.executable)
            teile.append(zwischen)
        else:
            zwischen = os.path.join(tmp, "bin")
            ziel = os.path.join(zwischen, "python")
            if not os.path.exists(ziel):
                os.makedirs(zwischen, exist_ok=True)
                try:
                    os.symlink(sys.executable, ziel)
                except OSError:
                    pass
            teile.append(zwischen)
        return os.pathsep.join(teile + [pfad])

    @staticmethod
    def _beenden(proc):
        try:
            if os.name != "nt":
                os.killpg(proc.pid, 9)
            else:
                # Ganzer Baum: proc.kill() traf nur cmd.exe, das gestartete Programm
                # lief weiter (Windows-VM, 29.09.2026).
                r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30)
                if r.returncode != 0:
                    proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def werkzeug(self, name, args):
        if name not in WERKZEUGE or name in ("fertig", "frage"):
            raise ValueError("Unbekanntes Werkzeug '%s'. Erlaubt: %s." % (name, ", ".join(WERKZEUGE)))
        if not isinstance(args, dict):
            args = {}
        erlaubt = {k: v for k, v in args.items() if k in ARGUMENTE[name]}
        ignoriert = sorted(k for k in args if k not in ARGUMENTE[name])
        ergebnis = getattr(self, name)(**erlaubt)
        if ignoriert:
            ergebnis += "\n(ignorierte Argumente: %s — erlaubt sind %s)" % (", ".join(ignoriert), ", ".join(ARGUMENTE[name]))
        return ergebnis


ZEILENNUMMER_RE = re.compile(r"^\s*\d+\| ?")


def ersetzen_nachsichtig(inhalt, alt, neu):
    """Zweite Chance für `ersetzen`, wenn 'alt' nicht genau einmal vorkommt — nur bei eindeutigem Treffer.

    Gemessen am Schüler Qwen3-4B (Prüfstand 2026-09-15): Von 61 gescheiterten Ersetzungen enthielten 34 den
    Zeilenumbruch doppelt maskiert (wörtlich „\\n“ statt Umbruch), andere kopierten die Zeilennummern aus
    `lesen` mit oder hatten Leerraum am Zeilenende. Die Datei war richtig verstanden, nur der Text nicht
    Zeichen für Zeichen getroffen. Gibt (neuer Inhalt, was korrigiert wurde) oder None zurück."""
    versuche = []
    if "\\n" in alt and "\n" not in alt:
        entmaskieren = lambda t: t.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
        versuche.append((entmaskieren(alt), entmaskieren(neu) if "\n" not in neu else neu,
                         "wörtliche \\n als Zeilenumbrüche gelesen wurden"))
    zeilen = [z for z in alt.splitlines() if z.strip()]
    if zeilen and all(ZEILENNUMMER_RE.match(z) for z in zeilen):
        ohne = lambda t: "\n".join(ZEILENNUMMER_RE.sub("", z, count=1) for z in t.split("\n"))
        neu_ohne = ohne(neu) if all(ZEILENNUMMER_RE.match(z) for z in neu.splitlines() if z.strip()) else neu
        versuche.append((ohne(alt), neu_ohne, "Zeilennummern aus 'lesen' entfernt wurden"))
    for a, n, grund in list(versuche) + [(alt, neu, "")]:
        if inhalt.count(a) == 1:
            if grund:
                return inhalt.replace(a, n, 1), grund
            continue
        # Zeilenweise ohne Leerraum am Zeilenende vergleichen — Einrückung zählt weiter.
        a_zeilen = [z.rstrip() for z in a.strip("\n").split("\n")]
        datei = inhalt.split("\n")
        stellen = [i for i in range(len(datei) - len(a_zeilen) + 1)
                   if [z.rstrip() for z in datei[i:i + len(a_zeilen)]] == a_zeilen]
        if len(stellen) == 1 and a_zeilen != [""]:
            i = stellen[0]
            ersatz = n.strip("\n").split("\n")
            return "\n".join(datei[:i] + ersatz + datei[i + len(a_zeilen):]), \
                (grund + " und " if grund else "") + "Leerraum am Zeilenende ignoriert wurde"
    return None


def syntax_hinweis(pfad, inhalt):
    """Sofortige Rückmeldung nach einer Änderung, wie die Diagnosen, die Claude Code aus der IDE bekommt —
    nur ohne Sprachserver: Python wird übersetzt (nicht ausgeführt), JSON geparst. Kleine Modelle
    zerbrechen beim Ersetzen oft die Einrückung und merken es erst Schritte später beim Testen."""
    endung = os.path.splitext(str(pfad))[1].lower()
    try:
        if endung == ".py":
            compile(inhalt, str(pfad), "exec", dont_inherit=True)
        elif endung == ".json":
            json.loads(inhalt)
        else:
            return ""
    except SyntaxError as e:
        zeile = (e.text or "").rstrip("\n")
        return "\n⚠️ Syntaxfehler in %s, Zeile %s: %s%s" % (pfad, e.lineno, e.msg, ("\n    " + zeile.strip()) if zeile else "")
    except ValueError as e:
        return "\n⚠️ Ungültiges JSON in %s: %s" % (pfad, e)
    return ""


# -------------------------------------------------------- Projektanweisungen ---

def projekt_anweisungen(ordner):
    """(Dateiname, Text) der Projekt-Anweisungsdatei oder (None, "")."""
    for name in ANWEISUNGSDATEIEN:
        p = os.path.join(ordner, name)
        if os.path.isfile(p):
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read(ANWEISUNG_GRENZE + 1)
            if len(text) > ANWEISUNG_GRENZE:
                text = text[:ANWEISUNG_GRENZE] + "\n… (gekürzt)"
            return name, text.strip()
    return None, ""


def basis_prompt(stufe, tiefe=0):
    """Werkzeuge, Regeln, Rechte — ohne Projekt, Skills, Gedächtnis. Auch die Grundlage
    der Trainingsdaten: Das Modell lernt genau den Prompt, den es später bekommt."""
    teile = [SYSTEM, "\nRechte: %s." % STUFEN[stufe]]
    if tiefe == 0:
        teile.append('Zusätzlich: delegieren {"auftrag":"klar umrissene Teilaufgabe","rechte":"lesen|projekt"} — '
                     "ein Unteragent mit eigenem, frischem Kontext erledigt sie und du bekommst nur sein Ergebnis. "
                     "Nützlich für Suchen und Untersuchungen, die deinen Kontext sonst füllen würden.")
    else:
        teile.append("Du bist ein UNTERAGENT: Erledige nur den Auftrag, ohne Rückfragen, und berichte das Ergebnis "
                     "knapp und vollständig mit fertig. delegieren und frage stehen dir nicht zur Verfügung.")
    return "\n".join(teile)


def system_prompt(werkbank, mcp=None, tiefe=0, skills=None, gedaechtnis=None, web=None):
    teile = [basis_prompt(werkbank.stufe, tiefe)]
    if werkbank.sandbox:
        # Ohne diesen Satz versucht ein Modell, fehlende Pakete nachzuinstallieren,
        # und verbrennt Schritt für Schritt an pip, ensurepip, venv und brew —
        # gemessen am 16.09.2026 mit einem lokalen 14-B-Modell, das sechs Schritte
        # lang gegen die Sandbox lief, weil die Fehlermeldungen von pip nichts über
        # das fehlende Netz sagen.
        teile.append('Sandbox: Befehle laufen ohne Netzzugang und dürfen nur im Projektordner schreiben. '
                     'Nachinstallieren ist unmöglich — pip, brew, npm install und Downloads scheitern immer. '
                     'Schreib deinen Code so, dass er mit der Standardbibliothek auskommt — für eine HTML-Prüfung '
                     'also re statt bs4, für JSON json statt requests. Scheitert ein Import, schreib das Skript um, '
                     'statt zu installieren. Nur wenn die Aufgabe ohne ein fremdes Paket wirklich unlösbar ist, '
                     'sag es mit "frage".')
    if os.name == "nt":
        # Windows-VM 29.09.2026: gemma4:12b schrieb „python3 auswertung.py“ — unter
        # Windows öffnet das nur den Microsoft Store; ls, cat und grep gibt es in
        # cmd.exe gar nicht.
        teile.append('System: Windows. Befehle laufen in cmd.exe — dir statt ls, type statt cat, findstr statt grep, '
                     'keine Unix-Werkzeuge. Python heißt python.')
    if web is not None:
        teile.append('Web: websuche {"suche":"Suchanfrage"} liefert Treffer mit Adresse und Kurztext; '
                     'webseite {"url":"https://…"} liest eine Seite als Text. Für aktuelle Dokumentation, Fehlermeldungen, '
                     'Versionen. Schreibe keine Geheimnisse oder Code des Nutzers in Suchanfragen.')
    if gedaechtnis is not None:
        teile.append('Gedächtnis: merken {"notiz":"kurzer dauerhafter Fakt","bereich":"projekt|global"} — z. B. wie '
                     'Tests hier laufen oder was der Nutzer bevorzugt; nicht für Einmaliges. '
                     'erinnern {"suche":"Stichworte"} — findet frühere Läufe, etwa wie etwas schon einmal gelöst wurde.')
        notizen = gedaechtnis.fuer_prompt(werkbank.ordner)
        if notizen:
            teile.append("\n" + notizen)
    if skills:
        import agentskills
        teile.append('\nSkills — bewährte Anleitungen. Passt einer zur Aufgabe, lade ihn ZUERST mit '
                     'skill {"name":"…"} und folge ihm:\n' + agentskills.beschreibung_fuer_prompt(skills))
    if werkbank.stufe == "lesen":
        teile.append("ersetzen und schreiben stehen dir nicht zur Verfügung. Untersuche und berichte mit fertig.")
    elif mcp and mcp.werkzeuge():
        teile.append("\nZusätzliche Werkzeuge über MCP — aufrufen mit\n"
                     "{\"gedanke\":\"…\",\"werkzeug\":\"mcp\",\"argumente\":{\"werkzeug\":\"server/name\",\"eingabe\":{…}}}\n"
                     "(Parameter mit ? sind optional):\n" + mcp.beschreibung())
    name, text = projekt_anweisungen(werkbank.ordner)
    if text:
        teile.append("\nAnweisungen des Projekts aus %s (gelten zusätzlich):\n%s" % (name, text))
    return "\n".join(teile)


# ------------------------------------------------------------------ Kontext ---

# Ein Bild kostet das Modell Token, die im Text nicht auftauchen. Ohne diesen
# Posten hielt die Werkbank einen Bild-Lauf mit Gemma 4 für 10 000 Token groß,
# während Ollama bei 16 383 anschlug — und jede Antwort mitten im Satz endete.
BILD_TOKEN = 1500


class Abgeschnitten(str):
    """Eine Modellantwort, die an der Kontextgrenze endete (Ollama: done_reason „length“)."""
    abgeschnitten = True


def geschaetzte_token(nachrichten):
    return (sum(len(n.get("content") or "") for n in nachrichten) / 3.2
            + BILD_TOKEN * sum(len(n.get("images") or ()) for n in nachrichten))


GLIEDERUNG = re.compile(r"^\s*(?:async\s+def|def|class|function|fn|func|interface|struct|enum|impl|"
                        r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function|(?:export\s+)?(?:const|let)\s+\w+\s*=\s*(?:async\s*)?\(|"
                        r"(?:public|private|protected)\s+[\w<>\[\]]+\s+\w+\s*\()")


def gliederung(nummerierte_zeilen, hoechstens=40):
    """Funktionen, Klassen und Überschriften mit Zeilennummer: „Z.12 def laden(text)“ — kurz genug fürs Gedächtnis."""
    eintraege = []
    for nr, zeile in nummerierte_zeilen:
        if GLIEDERUNG.match(zeile):
            eintraege.append("Z.%d %s" % (nr, zeile.strip().rstrip(":{").strip()[:70]))
    if len(eintraege) > hoechstens:
        # Anfang und Ende behalten: Am Dateiende steht oft das Wichtigste (Klasse, main)
        eintraege = (eintraege[:hoechstens - 5] + ["… %d weitere" % (len(eintraege) - hoechstens)]
                     + eintraege[-5:])
    return "; ".join(eintraege)


def _gelesen_kurz(inhalt, pfad=""):
    """Eine alte Leseausgabe auf ihre Gliederung schrumpfen: Der Agent weiß noch, WO was stand."""
    zeilen = [(int(m.group(1)), m.group(2)) for m in re.finditer(r"^\s*(\d+)\| ?(.*)$", inhalt, re.M)]
    if not zeilen:
        return None
    teile = "Z.%d–%d" % (zeilen[0][0], zeilen[-1][0])
    g = gliederung(zeilen, 25)
    return ("Ergebnis von lesen: [ältere Ausgabe gekürzt — %s%s gelesen%s. Bei Bedarf gezielt mit von/bis erneut lesen]"
            % ((pfad + " ") if pfad else "", teile, "; enthielt: " + g if g else ""))


def verdichten(nachrichten, budget, frisch=4):
    """Hält den Verlauf unter dem Token-Budget, ohne den Faden zu verlieren.

    Drei Stufen, jeweils von alt nach neu und nur so weit wie nötig. System,
    Aufgabe und die letzten `frisch` Nachrichten bleiben immer unverändert.
      1. alte Werkzeugausgaben auf ihre erste Zeile kürzen
      2. alte Modellantworten auf Werkzeug und Pfad kürzen (ein `schreiben`
         trägt sonst die ganze Datei im Verlauf mit)
      3. die ältesten Schritte ganz entfernen, mit einem Vermerk
    Gibt zurück, wie viele Nachrichten angefasst wurden."""
    if geschaetzte_token(nachrichten) <= budget:
        return 0
    mitte = range(2, max(2, len(nachrichten) - frisch))
    angefasst = 0
    for i in mitte:
        n = nachrichten[i]
        if n["role"] == "user" and n["content"].startswith("Ergebnis") and len(n["content"]) > 200:
            kurz = None
            if n["content"].startswith("Ergebnis von lesen:"):
                vorher = json_lesen(nachrichten[i - 1]["content"]) if i > 0 and nachrichten[i - 1]["role"] == "assistant" else None
                args = (vorher or {}).get("argumente") if isinstance((vorher or {}).get("argumente"), dict) else {}
                kurz = _gelesen_kurz(n["content"], str(args.get("pfad") or ""))
            n["content"] = kurz or (n["content"].split("\n", 1)[0] + "\n[ältere Ausgabe gekürzt, bei Bedarf erneut lesen]")
            angefasst += 1
            if geschaetzte_token(nachrichten) <= budget:
                return angefasst
    for i in mitte:
        n = nachrichten[i]
        if n["role"] == "assistant" and len(n["content"]) > 400:
            aktion = json_lesen(n["content"]) or {}
            args = aktion.get("argumente") if isinstance(aktion.get("argumente"), dict) else {}
            kurz = {"werkzeug": aktion.get("werkzeug"),
                    "argumente": {k: v for k, v in args.items() if k in ("pfad", "befehl", "muster")}}
            n["content"] = json.dumps(kurz, ensure_ascii=False) + " [gekürzt]"
            angefasst += 1
            if geschaetzte_token(nachrichten) <= budget:
                return angefasst
    entfernt = 0
    # Platz für den Vermerk gleich mitrechnen, sonst schiebt er über das Budget.
    while geschaetzte_token(nachrichten) + 30 > budget and len(nachrichten) > 2 + frisch + 1:
        del nachrichten[2:4]
        entfernt += 2
    if entfernt:
        aufgabe = nachrichten[1]["content"]
        frueher = re.search(r"\n\n\[(\d+) ältere Schritte aus dem Verlauf entfernt[^\]]*\]$", aufgabe)
        bisher = int(frueher.group(1)) if frueher else 0
        if frueher:
            aufgabe = aufgabe[:frueher.start()]
        nachrichten[1]["content"] = aufgabe + (
            "\n\n[%d ältere Schritte aus dem Verlauf entfernt, damit der Kontext reicht.]"
            % (bisher + entfernt // 2))
        angefasst += entfernt
    return angefasst


# --------------------------------------------------------------------- Lauf ---

def _beschreibung(werkzeug, args):
    args = args if isinstance(args, dict) else {}
    if werkzeug == "mcp":
        return "MCP-Werkzeug %s mit %s" % (str(args.get("werkzeug", "?"))[:80],
                                           json.dumps(args.get("eingabe", {}), ensure_ascii=False)[:220])
    if werkzeug == "ausfuehren":
        return "Befehl ausführen: %s" % str(args.get("befehl", ""))[:300]
    if werkzeug == "ersetzen":
        return "Ändern: %s (%d -> %d Zeilen)" % (args.get("pfad", "?"), len(str(args.get("alt", "")).splitlines()),
                                                 len(str(args.get("neu", "")).splitlines()))
    if werkzeug == "schreiben":
        return "Schreiben: %s (%d Zeilen)" % (args.get("pfad", "?"), len(str(args.get("inhalt", "")).splitlines()))
    return "%s %s" % (werkzeug, json.dumps(args, ensure_ascii=False)[:200])


def braucht_freigabe(werkzeug, politik, werkbank, mcp=None, args=None, regeln=None):
    if regeln is not None and werkzeug in ("ausfuehren", "mcp") + AENDERND \
            and regeln.entscheidung(werkzeug, args)[0] == "erlaubt":
        return False                      # ausdrücklich erlaubt — vom Nutzer, nicht vom Modell
    if werkzeug == "mcp":
        # Ein MCP-Server läuft außerhalb der Sandbox. Gefragt wird immer, außer
        # der Nutzer hat genau diesen Server als vertraut markiert.
        server = str((args or {}).get("werkzeug", "")).partition("/")[0]
        return not (mcp and server in mcp.vertraut)
    if werkzeug not in ("ausfuehren",) + AENDERND:
        return False
    if werkbank.ohne_grenze(werkzeug):
        return True                       # keine Sandbox: immer fragen, egal was eingestellt ist
    if politik == "alles":
        return True
    return politik == "befehle" and werkzeug == "ausfuehren"


def arbeiten(aufgabe, werkbank, chat, freigabe=None, politik=STANDARD_FREIGABE,
             max_schritte=MAX_SCHRITTE, budget=11000, melden=None, testbefehl=None,
             checkpunkte=None, lauf="", mcp=None, vorher=None, start_gesichert=None, planmodus=False,
             regeln=None, tiefe=0, skills=None, gedaechtnis=None, web=None, bilder=None,
             ereignisse=None, werkzeuge=None, zusatz="", profile=None, extern=None, parallel=MAX_PARALLEL):
    """Die Werkzeugschleife. Gibt ein Ergebnis-Wörterbuch zurück.

    chat(nachrichten) -> str       eine Modellantwort (wirft bei Fehlern)
    freigabe(beschreibung) -> bool fragt den Nutzer; None heißt: niemand da,
                                   freigabepflichtige Aktionen werden abgelehnt
    melden(text, zustand)          Fortschritt (darf zum Abbrechen werfen)
    checkpunkte                    checkpunkte.Checkpunkte des Projekts: Stand vor
                                   dem Lauf und nach jedem Schritt, der etwas auf
                                   der Platte verändert hat — auch durch Befehle
    mcp                            mcp.Werkzeugkasten: zusätzliches Werkzeug „mcp“
    vorher                         Nachrichten eines früheren Laufs: Die neue Aufgabe
                                   setzt dieses Gespräch fort (wie eine Folgenachricht
                                   in Claude Code), statt bei null anzufangen
    start_gesichert(cp)            wird mit dem Checkpunkt vor dem Lauf aufgerufen
    planmodus                      erst nur lesen und mit „plan“ einen Plan vorlegen;
                                   die eingestellten Rechte gelten erst nach Freigabe
    regeln                         regeln.Regeln: erlauben/verbieten und Hooks
    tiefe                          0 = Hauptagent; Unteragenten (1) dürfen nicht weiter delegieren
    skills                         agentskills.finden(…): Name und Beschreibung im Prompt, Inhalt per „skill“
    gedaechtnis                    gedaechtnis.Gedaechtnis: Notizen im Prompt, Werkzeuge merken/erinnern
    web                            Objekt mit suchen(anfrage) und abrufen(url) — nur, wenn der Nutzer
                                   die Websuche für diesen Lauf eingeschaltet hat
    bilder                         Base64-Bilder zur Aufgabe (Screenshot, Entwurf) — nur für Modelle mit Bildverständnis
    ereignisse(dict)               Schrittprotokoll, während der Lauf entsteht (wie das Session-Log von
                                   DeepSeek Harness): „beginn“ mit allem, was das Modell zuerst sieht, je
                                   Schritt die rohe Antwort und das Ergebnis — daraus lässt sich jeder Stand
                                   wiederherstellen, auch nach Abbruch oder Absturz
    werkzeuge                      nur diese Werkzeuge (Profil); fertig und frage gehen immer
    zusatz                         zusätzliche Anweisungen (Profil) — hinter dem System-Prompt
    profile                        {name: profil} für delegieren {"profil": …} — Unteragenten mit eigenem Zuschnitt
    extern                         externe Agenten (Claude Code, Codex) als Werkzeug „extern“; jeder Aufruf braucht Freigabe
    parallel                       wie viele lesende Unteragenten gleichzeitig laufen (1: nacheinander, etwa bei Wiederholungen)
    """
    if politik not in FREIGABEN:
        raise ValueError("Unbekannte Freigabe-Politik '%s'." % politik)
    if regeln is None:
        # Ohne Regeln gab es bisher auch keinen Grundschutz: Wer die Werkbank
        # ohne regeln= aufrief (Pruefstand, DowBench), bekam einen Agenten,
        # der .env las und — einer eingeschleusten Anweisung folgend — in eine
        # Ausgabedatei schrieb (DowBench i5, 28.09.2026). Ein Schutz, den jeder
        # Aufrufer selbst mitbringen muss, fehlt bei dem, der es vergisst.
        import regeln as _regeln
        regeln = _regeln.Regeln()
    melden = melden or (lambda text, zustand="done": None)
    if regeln is not None:
        werkbank.lese_sperre = lambda rel: regeln.entscheidung("lesen", {"pfad": rel})[0] == "verboten"
    ziel_stufe = werkbank.stufe
    plan, plan_freigegeben = [], not planmodus
    if planmodus:
        werkbank.stufe = "lesen"
    einstieg = "Aufgabe:\n%s\n\nDateien im Projektordner:\n%s" % (aufgabe.strip(), werkbank.liste())
    if testbefehl:
        einstieg += "\n\nTests laufen mit: %s" % testbefehl
    system = system_prompt(werkbank, mcp, tiefe, skills, gedaechtnis, web)
    erlaubte = tuple(w for w in (werkzeuge or ()) if w) or None
    if extern is not None and tiefe == 0 and extern.verfuegbar() and (erlaubte is None or "extern" in erlaubte):
        system += ('\nExterne Agenten: extern {"agent":"%s","auftrag":"klar umrissene Teilaufgabe"} — ein anderer '
                   "Coding-Agent erledigt sie im selben Projekt und berichtet. Jeder Aufruf braucht die Freigabe des "
                   "Nutzers; nutze ihn für Teile, an denen du selbst scheiterst." % "|".join(extern.verfuegbar()))
    if tiefe == 0 and parallel > 1 and (erlaubte is None or "delegieren" in erlaubte):
        system += ('\nMehrere Untersuchungen auf einmal: delegieren {"auftraege":[{"auftrag":"…"},{"auftrag":"…"}]} — '
                   "bis zu %d Unteragenten, die gleichzeitig arbeiten, wenn alle nur lesen." % MAX_PARALLEL)
    if profile and tiefe == 0 and (erlaubte is None or "delegieren" in erlaubte):
        system += "\nProfile für delegieren (\"profil\":\"name\"): " + "; ".join(
            "%s — %s" % (n, (p.get("beschreibung") or "")[:120]) for n, p in list(profile.items())[:12])
    if erlaubte:
        system += ("\nFür diesen Auftrag stehen dir NUR diese Werkzeuge zur Verfügung: %s (dazu immer fertig und frage)."
                   % ", ".join(erlaubte))
    if zusatz:
        system += "\n\nZusätzliche Anweisungen für diesen Auftrag:\n" + str(zusatz).strip()[:6000]
    nachrichten = [{"role": "system", "content": system},
                   {"role": "user", "content": einstieg}]
    if bilder:
        nachrichten[1]["images"] = list(bilder)
        nachrichten[1]["content"] += "\n\n(%d Bild%s zur Aufgabe angehängt.)" % (len(bilder), "" if len(bilder) == 1 else "er")
    if planmodus:
        nachrichten[0]["content"] += (
            "\n\nPLAN-MODUS: Untersuche zuerst nur (liste, lesen, suchen, Befehle ohne Schreibrecht). "
            "Lege dann mit plan einen konkreten Plan vor. Ändern darfst du erst, wenn der Nutzer den Plan "
            "freigegeben hat — danach gilt: %s." % STUFEN[ziel_stufe])
    if vorher and len(vorher) >= 2:
        # Fortsetzung: frischer System-Prompt (DOWOS.md kann sich geändert haben),
        # dann das bisherige Gespräch, dann die neue Anweisung.
        folge = "Neue Anweisung des Nutzers:\n%s\n\nDateien im Projektordner jetzt:\n%s" % (
            aufgabe.strip(), werkbank.liste())
        nachrichten = [nachrichten[0]] + [dict(n) for n in vorher[1:]]
        if nachrichten[-1]["role"] == "user":
            nachrichten[-1]["content"] += "\n\n" + folge
        else:
            nachrichten.append({"role": "user", "content": folge})
    ereignis = ereignisse or (lambda e: None)
    # Gleichzeitige Unteragenten fragen nacheinander: Es gibt nur eine Schranke, an der der Nutzer entscheidet.
    freigabe_schloss = threading.Lock()

    def freigabe_einzeln(text):
        if freigabe is None:
            return False
        with freigabe_schloss:
            return freigabe(text)
    zaehler, verlauf, geaendert = {}, [], []
    ungueltig = abgelehnt = schritte = 0
    beendet, zusammenfassung = "limit", ""
    ungeprueft_gemahnt = tests_gemahnt = False
    zeitablaeufe = 0
    hook_blockiert = 0
    seit_aenderung_geprueft = True
    letzte = []
    fenster, gewarnt_fortschritt = [], False
    t0 = time.time()
    start = checkpunkte.sichern("Vor dem Lauf: %s" % aufgabe.strip()[:120], lauf, 0) if checkpunkte else None
    if start and start_gesichert:
        start_gesichert(start)
    ereignis({"art": "beginn", "aufgabe": aufgabe, "tiefe": tiefe, "checkpunkt_start": start["id"] if start else None,
              "nachrichten": [{k: v for k, v in n.items() if k != "images"} for n in nachrichten],
              "stufe": ziel_stufe, "planmodus": planmodus, "fortsetzung": bool(vorher and len(vorher) >= 2)})
    frage = ""
    unteragenten = []
    try:
        verlauf_cp = None
        while schritte < max_schritte:
            if verdichten(nachrichten, budget):
                ereignis({"art": "verdichtet", "nach_schritt": schritte, "token": round(geschaetzte_token(nachrichten))})
            kontext = round(geschaetzte_token(nachrichten))
            t = time.time()
            try:
                roh = chat(nachrichten)
            except Exception as e:
                # Eine Zeitüberschreitung beendete den ganzen Lauf als „Fehler“ — direkt
                # nach dem Verdichten, mit fast vollem Kontext (Windows-VM, 29.09.2026).
                # Einmal kleiner neu versuchen; beim zweiten Mal sauber enden.
                if not _ist_zeitablauf(e):
                    raise
                if zeitablaeufe >= 1:
                    beendet = "modell"
                    zusammenfassung = ("Das Modell hat zweimal nicht rechtzeitig geantwortet (%s). Die Änderungen bis "
                                       "hierher sind gesichert; mit einem kleineren Modell oder weniger Kontext "
                                       "fortsetzen." % e)
                    melden("Modell antwortet nicht — Lauf beendet", "done")
                    break
                zeitablaeufe += 1
                budget = max(2000, int(budget * 0.6))
                verdichten(nachrichten, budget)
                ereignis({"art": "zeitablauf", "nach_schritt": schritte, "neues_budget": budget})
                melden("Modell antwortet nicht rechtzeitig — verdichte und versuche es noch einmal", "active")
                continue
            modell_sek = round(time.time() - t, 1)
            schritte += 1
            nachrichten.append({"role": "assistant", "content": roh or ""})
            aktion = json_lesen(roh)
            werkzeug = aktion.get("werkzeug") if aktion else None
            args = aktion.get("argumente") if aktion else None
            gedanke = str(aktion.get("gedanke", ""))[:300] if aktion else ""
            if werkzeug and erlaubte and werkzeug not in erlaubte and werkzeug not in ("fertig", "frage"):
                antwort = ("Ergebnis von %s: Fehler: In diesem Auftrag nicht verfügbar. Erlaubt: %s, fertig, frage."
                           % (werkzeug, ", ".join(erlaubte)))
            elif not werkzeug and getattr(roh, "abgeschnitten", False):
                # Nicht das JSON ist kaputt, sondern der Platz war zu Ende. Der alte Hinweis
                # („Anführungszeichen maskieren“) schickte Gemma 4 zehnmal in dieselbe Wand.
                ungueltig += 1
                budget = max(2000, min(budget, int(kontext * 0.75)))
                antwort = ("Ergebnis: Deine Antwort wurde abgeschnitten — das Arbeitsgedächtnis des Modells ist voll. "
                           "Ältere Schritte werden jetzt gekürzt. Schreibe lange Dateien in Teilen: zuerst ein kurzes "
                           "Grundgerüst mit schreiben, dann Abschnitt für Abschnitt mit ersetzen ergänzen.")
                ereignis({"art": "abgeschnitten", "nach_schritt": schritte, "token": kontext, "neues_budget": budget})
                melden("Schritt %d: Antwort abgeschnitten — Kontext voll, verdichte" % schritte, "active")
            elif not werkzeug:
                ungueltig += 1
                antwort = ("Ergebnis: Ungültige Antwort (%s) Antworte mit genau einem vollständigen JSON-Objekt "
                           "der Form {\"gedanke\":\"…\",\"werkzeug\":\"…\",\"argumente\":{…}}." % json_fehler(roh))
                if len(nachrichten) >= 3 and nachrichten[-3]["role"] == "assistant" and nachrichten[-3]["content"] == roh:
                    # Dieselbe kaputte Antwort noch einmal: Ohne neuen Hinweis bliebe es dabei
                    # (gemessen: 29-mal hintereinander bei Temperatur 0,2).
                    antwort += (" Du hast genau dieselbe ungültige Antwort erneut geschickt. Formuliere den Gedanken "
                                "kürzer, ohne Anführungszeichen und Backslashes.")
                melden("Schritt %d: unlesbare Antwort" % schritte, "active")
            elif werkzeug == "plan":
                plan, fehler = _plan_lesen(args)
                if fehler:
                    antwort = "Ergebnis von plan: Fehler: %s" % fehler
                else:
                    zaehler["plan"] = zaehler.get("plan", 0) + 1
                    liste = plan_text(plan)
                    melden("📋 Plan\n" + liste, "done")
                    antwort = "Ergebnis von plan: gespeichert.\n" + liste
                    if not plan_freigegeben:
                        if freigabe is not None and freigabe("Plan freigeben?\n" + liste):
                            plan_freigegeben = True
                            werkbank.stufe = ziel_stufe
                            antwort += ("\n\nDer Nutzer hat den Plan freigegeben. Du hast jetzt die Rechte: %s. "
                                        "Arbeite ihn ab und halte den Plan mit plan aktuell." % STUFEN[ziel_stufe])
                        else:
                            abgelehnt += 1
                            antwort += ("\n\nDer Nutzer hat den Plan NICHT freigegeben. Überarbeite ihn, "
                                        "frage mit frage nach, was fehlt, oder beende mit fertig.")
            elif not plan_freigegeben and werkzeug in AENDERND:
                antwort = ("Ergebnis von %s: Fehler: Plan-Modus — lege zuerst mit plan einen Plan vor. "
                           "Ändern ist erst nach der Freigabe erlaubt." % werkzeug)
            elif werkzeug == "delegieren":
                args = args if isinstance(args, dict) else {}
                roh_liste = args.get("auftraege") if isinstance(args.get("auftraege"), list) else [args]
                auftraege, fehler = [], ""
                for eintrag in roh_liste[:MAX_PARALLEL + 1]:
                    eintrag = eintrag if isinstance(eintrag, dict) else {"auftrag": eintrag}
                    auftrag = str(eintrag.get("auftrag", "")).strip()
                    profil = (profile or {}).get(str(eintrag.get("profil") or "").strip().lower()) if eintrag.get("profil") else None
                    rechte = eintrag.get("rechte") if eintrag.get("rechte") in STUFEN else (profil or {}).get("stufe") or "lesen"
                    if profil and profil.get("stufe") in STUFEN and \
                            STUFEN_REIHENFOLGE.index(rechte) > STUFEN_REIHENFOLGE.index(profil["stufe"]):
                        rechte = profil["stufe"]
                    # Nie mehr Rechte als der Hauptagent gerade selbst hat.
                    if STUFEN_REIHENFOLGE.index(rechte) > STUFEN_REIHENFOLGE.index(werkbank.stufe):
                        rechte = werkbank.stufe
                    if not auftrag:
                        fehler = "„auftrag“ ist leer."
                    elif eintrag.get("profil") and not profil:
                        fehler = "Profil „%s“ gibt es nicht. Verfügbar: %s" % (
                            eintrag.get("profil"), ", ".join(sorted(profile or {})) or "keine")
                    auftraege.append((auftrag, rechte, profil))
                if tiefe > 0:
                    antwort = "Ergebnis von delegieren: Fehler: Unteragenten dürfen nicht weiter delegieren."
                elif len(roh_liste) > MAX_PARALLEL:
                    antwort = "Ergebnis von delegieren: Fehler: höchstens %d Aufträge auf einmal." % MAX_PARALLEL
                elif not auftraege or fehler:
                    antwort = "Ergebnis von delegieren: Fehler: %s" % (fehler or "keine Aufträge.")
                else:
                    zaehler["delegieren"] = zaehler.get("delegieren", 0) + 1
                    mehrere = len(auftraege) > 1
                    # Gleichzeitig nur, wenn keiner schreibt — zwei schreibende Unteragenten
                    # würden sich gegenseitig die Dateien unter den Händen ändern.
                    gleichzeitig = mehrere and parallel > 1 and all(r == "lesen" for _, r, _ in auftraege)
                    ergebnisse = [None] * len(auftraege)

                    def unteragent(nr, auftrag, rechte, profil, schritt=schritte):
                        marke = "  ↳%s " % ("[%d]" % (nr + 1) if mehrere else "")
                        melden("Schritt %d: Unteragent%s (%s%s): %s" % (schritt, " %d" % (nr + 1) if mehrere else "", rechte,
                                                                       ", " + profil["name"] if profil else "", auftrag[:80]),
                               "active")
                        unter = Werkbank(werkbank.ordner, rechte, sandbox=werkbank.sandbox)
                        ergebnisse[nr] = arbeiten(
                            auftrag, unter, (profil or {}).get("chat") or chat, freigabe=freigabe_einzeln, politik=politik,
                            max_schritte=(profil or {}).get("max_schritte") or UNTERAGENT_SCHRITTE, budget=budget,
                            melden=lambda text, zustand="done": melden(marke + text, zustand),
                            mcp=mcp, regeln=regeln, tiefe=tiefe + 1, skills=skills, gedaechtnis=gedaechtnis, web=web,
                            werkzeuge=(profil or {}).get("werkzeuge"), zusatz=(profil or {}).get("zusatz", ""),
                            ereignisse=lambda e: ereignis(dict(e, unteragent_von="%d#%d" % (schritt, nr + 1) if mehrere else schritt)))

                    if gleichzeitig:
                        faeden, fehler_faeden = [], []

                        def sicher(*a):
                            try:
                                unteragent(*a)
                            except BaseException as ausnahme:      # Abbruch des Laufs, Modellfehler …
                                fehler_faeden.append(ausnahme)
                        for nr, (auftrag, rechte, profil) in enumerate(auftraege[:parallel]):
                            faeden.append(threading.Thread(target=sicher, args=(nr, auftrag, rechte, profil), daemon=True))
                        for f in faeden:
                            f.start()
                        for nr, (auftrag, rechte, profil) in enumerate(auftraege[parallel:], parallel):
                            sicher(nr, auftrag, rechte, profil)
                        for f in faeden:
                            f.join()
                        if fehler_faeden:
                            raise fehler_faeden[0]
                    else:
                        for nr, (auftrag, rechte, profil) in enumerate(auftraege):
                            unteragent(nr, auftrag, rechte, profil)
                    teile = []
                    for nr, ((auftrag, rechte, profil), e_unter) in enumerate(zip(auftraege, ergebnisse)):
                        unteragenten.append({"auftrag": auftrag[:300], "rechte": rechte, "profil": (profil or {}).get("name", ""),
                                             "beendet": e_unter["beendet"], "parallel": gleichzeitig,
                                             "schritte": e_unter["schritte"], "zusammenfassung": e_unter["zusammenfassung"][:1000],
                                             "geaendert": e_unter["geaendert"]})
                        for p_neu in e_unter["geaendert"]:
                            if p_neu not in geaendert:
                                geaendert.append(p_neu)
                            seit_aenderung_geprueft = False
                        teile.append("%sDer Unteragent hat %s nach %d Schritten.\n%s%s" % (
                            "Unteragent %d — %s\n" % (nr + 1, auftrag[:120]) if mehrere else "",
                            "abgeschlossen" if e_unter["beendet"] == "fertig" else "abgebrochen (%s)" % e_unter["beendet"],
                            e_unter["schritte"], e_unter["zusammenfassung"] or "(keine Zusammenfassung)",
                            "\nGeänderte Dateien: " + ", ".join(e_unter["geaendert"]) if e_unter["geaendert"] else ""))
                    if checkpunkte and any(e_u["geaendert"] for e_u in ergebnisse):
                        cp = checkpunkte.sichern("Schritt %d: Unteragent — %s" % (schritte, auftraege[0][0][:100]),
                                                 lauf, schritte, nur_wenn_geaendert=True)
                        if cp:
                            verlauf_cp = cp["id"]
                    antwort = "Ergebnis von delegieren:%s\n%s" % (
                        " (%d Unteragenten gleichzeitig)" % len(auftraege) if gleichzeitig else "", "\n\n".join(teile))
            elif werkzeug == "extern":
                args = args if isinstance(args, dict) else {}
                agent, auftrag = str(args.get("agent", "")).strip().lower(), str(args.get("auftrag", "")).strip()
                if extern is None or tiefe > 0 or not extern.verfuegbar():
                    antwort = "Ergebnis von extern: Fehler: Externe Agenten sind in diesem Lauf nicht eingeschaltet."
                elif agent not in extern.verfuegbar():
                    antwort = "Ergebnis von extern: Fehler: Agent „%s“ gibt es nicht. Verfügbar: %s" % (
                        agent, ", ".join(extern.verfuegbar()))
                elif not auftrag:
                    antwort = "Ergebnis von extern: Fehler: „auftrag“ ist leer."
                elif not plan_freigegeben:
                    antwort = "Ergebnis von extern: Fehler: Plan-Modus — erst nach der Freigabe des Plans."
                elif freigabe is None or not freigabe("Externer Agent %s (%s, Rechte %s):\n%s" % (
                        agent, extern.hinweis(agent), werkbank.stufe, auftrag[:1500])):
                    abgelehnt += 1
                    antwort = "Ergebnis von extern: Abgelehnt. Der Nutzer hat den externen Agenten nicht freigegeben."
                else:
                    zaehler["extern"] = zaehler.get("extern", 0) + 1
                    melden("Schritt %d: externer Agent %s: %s" % (schritte, agent, auftrag[:80]), "active")
                    ok_, bericht = extern.ausfuehren(agent, auftrag, werkbank.ordner, werkbank.stufe)
                    antwort = "Ergebnis von extern (%s, %s):\n%s" % (agent, "fertig" if ok_ else "Fehler",
                                                                     Werkbank._kurz(bericht or "(keine Ausgabe)", 8000))
                    if checkpunkte:
                        cp = checkpunkte.sichern("Schritt %d: externer Agent %s — %s" % (schritte, agent, auftrag[:80]),
                                                 lauf, schritte, nur_wenn_geaendert=True)
                        if cp:
                            verlauf_cp = cp["id"]
                            seit_start = checkpunkte.seit(start["id"]) if start else {}
                            for pfad_neu in seit_start.get("neu", []) + seit_start.get("geaendert", []):
                                if pfad_neu not in geaendert:
                                    geaendert.append(pfad_neu)
                            seit_aenderung_geprueft = False
            elif werkzeug in ("websuche", "webseite"):
                args = args if isinstance(args, dict) else {}
                ziel = str(args.get("suche" if werkzeug == "websuche" else "url", "")).strip()
                urteil = regeln.entscheidung(werkzeug, args) if regeln is not None else (None, "")
                if web is None:
                    antwort = "Ergebnis von %s: Fehler: Die Websuche ist für diesen Lauf nicht eingeschaltet." % werkzeug
                elif urteil[0] == "verboten":
                    antwort = "Ergebnis von %s: Fehler: durch die Regel %s verboten." % (werkzeug, urteil[1])
                elif not ziel:
                    antwort = "Ergebnis von %s: Fehler: Angabe fehlt." % werkzeug
                elif politik == "alles" and urteil[0] != "erlaubt" and (freigabe is None or not freigabe(
                        "%s: %s" % ("Websuche" if werkzeug == "websuche" else "Seite abrufen", ziel[:300]))):
                    abgelehnt += 1
                    antwort = "Ergebnis von %s: Abgelehnt. Der Nutzer hat den Netzzugriff nicht freigegeben." % werkzeug
                else:
                    zaehler[werkzeug] = zaehler.get(werkzeug, 0) + 1
                    melden("Schritt %d: %s %s" % (schritte, werkzeug, ziel[:80]), "active")
                    try:
                        if werkzeug == "websuche":
                            treffer = web.suchen(ziel) or []
                            antwort = "Ergebnis von websuche:\n" + ("\n\n".join(
                                "%d. %s\n   %s\n   %s" % (i, t.get("title", ""), t.get("url", ""), (t.get("snippet") or "")[:300])
                                for i, t in enumerate(treffer[:8], 1)) or "(keine Treffer)")
                        else:
                            antwort = "Ergebnis von webseite:\n" + Werkbank._kurz(str(web.abrufen(ziel) or "(leer)"), 6000)
                    except Exception as e:                       # Netzfehler, gesperrte Adresse …
                        antwort = "Ergebnis von %s: Fehler: %s" % (werkzeug, e)
            elif werkzeug in ("merken", "erinnern"):
                args = args if isinstance(args, dict) else {}
                try:
                    if gedaechtnis is None:
                        raise ValueError("In diesem Lauf gibt es kein Gedächtnis.")
                    if werkzeug == "merken":
                        global_ = args.get("bereich") == "global"
                        neu = gedaechtnis.merken(args.get("notiz"), None if global_ else werkbank.ordner)
                        antwort = "Ergebnis von merken: %s" % ("gemerkt (%s)." % ("global" if global_ else "für dieses Projekt")
                                                               if neu else "stand schon so im Gedächtnis.")
                        melden("Schritt %d: gemerkt — %s" % (schritte, str(args.get("notiz"))[:80]), "active")
                    else:
                        treffer = gedaechtnis.erinnern(args.get("suche"), werkbank.ordner, ausser=lauf or None)
                        antwort = "Ergebnis von erinnern:\n" + ("\n\n".join(
                            "%s%s — Aufgabe: %s\nErgebnis: %s%s" % (
                                time.strftime("%d.%m.%Y", time.localtime(t["zeit"])),
                                "" if t["dieses_projekt"] else " (anderes Projekt %s)" % t["ordner"],
                                t["aufgabe"], t["zusammenfassung"] or "—",
                                ("\nDateien: " + t["dateien"]) if t["dateien"] else "") for t in treffer)
                            or "(nichts gefunden)")
                    zaehler[werkzeug] = zaehler.get(werkzeug, 0) + 1
                except ValueError as e:
                    antwort = "Ergebnis von %s: Fehler: %s" % (werkzeug, e)
            elif werkzeug == "skill":
                import agentskills
                args = args if isinstance(args, dict) else {}
                try:
                    antwort = "Ergebnis von skill:\n" + agentskills.laden(skills or {}, args.get("name"), args.get("datei"))
                    zaehler["skill"] = zaehler.get("skill", 0) + 1
                    melden("Schritt %d: Skill „%s“ geladen" % (schritte, args.get("name")), "active")
                except ValueError as e:
                    antwort = "Ergebnis von skill: Fehler: %s" % e
            elif werkzeug == "frage" and tiefe > 0:
                antwort = "Ergebnis von frage: Fehler: Als Unteragent kannst du nicht fragen — entscheide selbst oder berichte mit fertig."
            elif werkzeug == "frage":
                frage = str((args or {}).get("frage", "")).strip() if isinstance(args, dict) else ""
                if not frage:
                    antwort = "Ergebnis von frage: Fehler: „frage“ ist leer."
                else:
                    zaehler["frage"] = zaehler.get("frage", 0) + 1
                    verlauf.append({"schritt": schritte, "werkzeug": "frage", "gedanke": gedanke,
                                    "sek": round(time.time() - t, 1), "modell_sek": modell_sek, "kontext": kontext,
                                    "ergebnis": frage[:300]})
                    ereignis({"art": "schritt", "schritt": schritte, "roh": roh, "antwort": None, **verlauf[-1]})
                    beendet = "frage"
                    melden("Schritt %d: Rückfrage an dich" % schritte, "done")
                    break
            elif werkzeug == "fertig":
                hook_fehler = []
                if regeln and hook_blockiert < 2 and werkbank.erlaubt("ausfuehren")[0]:
                    for befehl in regeln.hooks_fuer("vor_fertig"):
                        melden("Hook vor_fertig: %s" % befehl[:80], "active")
                        aus = werkbank.ausfuehren(befehl)
                        if not aus.startswith("exit=0"):
                            hook_fehler.append("Hook „%s“:\n%s" % (befehl, aus))
                if hook_fehler:
                    # Wie der Stop-Hook in Claude Code: fertig gilt erst, wenn die Prüfung
                    # des Projekts besteht — höchstens zweimal, sonst hinge der Lauf fest.
                    hook_blockiert += 1
                    antwort = ("Ergebnis von fertig: Noch nicht. Die Projekt-Prüfung vor dem Abschluss schlägt fehl:\n"
                               + "\n\n".join(hook_fehler))
                elif geaendert and not seit_aenderung_geprueft and not ungeprueft_gemahnt \
                        and werkbank.erlaubt("ausfuehren")[0]:
                    # Im Rauchtest meldete das Modell „fertig“, ohne die Änderung geprüft
                    # zu haben — und lag falsch. Einmal nachhaken kostet einen Schritt.
                    ungeprueft_gemahnt = True
                    antwort = ("Ergebnis von fertig: Noch nicht. Seit deiner letzten Änderung hast du nichts "
                               "ausgeführt. Prüfe die Änderung mit Tests und den Anforderungen der Aufgabe, "
                               "dann melde fertig.")
                elif geaendert and not tests_gemahnt and TESTS_VERLANGT.search(aufgabe) \
                        and not any(TESTDATEI.search(str(p)) for p in geaendert):
                    tests_gemahnt = True
                    antwort = ("Ergebnis von fertig: Noch nicht. Die Aufgabe verlangt Tests, aber du hast keine "
                               "Testdatei angelegt oder geändert. Schreib einen Test, der den geschilderten Fall "
                               "nachstellt, führe alle Tests aus, dann melde fertig.")
                else:
                    zaehler["fertig"] = zaehler.get("fertig", 0) + 1
                    zusammenfassung = str((args or {}).get("zusammenfassung", "")) if isinstance(args, dict) else ""
                    verlauf.append({"schritt": schritte, "werkzeug": "fertig", "gedanke": gedanke,
                                    "sek": round(time.time() - t, 1), "modell_sek": modell_sek, "kontext": kontext,
                                    "ergebnis": zusammenfassung[:300]})
                    ereignis({"art": "schritt", "schritt": schritte, "roh": roh, "antwort": None, **verlauf[-1]})
                    beendet = "fertig"
                    melden("Schritt %d: fertig" % schritte, "done")
                    break
            else:
                zaehler[werkzeug] = zaehler.get(werkzeug, 0) + 1
                antwort = _schritt(werkbank, werkzeug, args, politik, freigabe, melden, schritte, mcp, regeln)
                if regeln and werkzeug in AENDERND and antwort.startswith("Ergebnis von %s:\n" % werkzeug):
                    pfad_neu = str((args or {}).get("pfad", ""))
                    for befehl in regeln.hooks_fuer("nach_aenderung", pfad_neu):
                        aus = werkbank.ausfuehren(befehl)
                        melden("Hook nach_aenderung: %s" % befehl[:80], "active")
                        if not aus.startswith("exit=0"):
                            antwort += "\n\nHook „%s“ meldet ein Problem:\n%s" % (befehl, aus)
                if antwort.startswith("Ergebnis von %s: Abgelehnt" % werkzeug):
                    abgelehnt += 1
                elif werkzeug in AENDERND and antwort.startswith("Ergebnis von %s:\n" % werkzeug):
                    seit_aenderung_geprueft = False
                    pfad = str((args or {}).get("pfad", ""))
                    if pfad and pfad not in geaendert:
                        geaendert.append(pfad)
                elif antwort.startswith("Ergebnis von ausfuehren:\nexit="):
                    seit_aenderung_geprueft = True
                if checkpunkte and (werkzeug in AENDERND or werkzeug in ("ausfuehren", "mcp")) \
                        and antwort.startswith("Ergebnis von %s:\n" % werkzeug):
                    cp = checkpunkte.sichern("Schritt %d: %s" % (schritte, _beschreibung(werkzeug, args)),
                                             lauf, schritte, nur_wenn_geaendert=True)
                    if cp:
                        verlauf_cp = cp["id"]
                fingerabdruck = json.dumps([werkzeug, args], sort_keys=True, ensure_ascii=False, default=str)
                letzte = (letzte + [fingerabdruck])[-3:]
                if len(letzte) == 3 and len(set(letzte)) == 1 and werkzeug not in AENDERND:
                    antwort += ("\n\nHinweis: Du hast dieselbe Aktion dreimal hintereinander ausgeführt. "
                                "Das Ergebnis ändert sich nicht — wähle einen anderen Schritt.")
            eintrag = {"schritt": schritte, "werkzeug": werkzeug, "gedanke": gedanke,
                       "sek": round(time.time() - t, 1), "modell_sek": modell_sek, "kontext": kontext,
                       "ergebnis": antwort[:300]}
            if isinstance(args, dict):
                eintrag["argumente"] = {k: (v[:200] if isinstance(v, str) else v) for k, v in args.items()
                                        if k in ("pfad", "befehl", "muster", "suche", "url", "name", "auftrag", "agent", "profil")}
            if verlauf_cp:
                eintrag["checkpunkt"], verlauf_cp = verlauf_cp, None
            verlauf.append(eintrag)
            nachrichten.append({"role": "user", "content": antwort})
            ereignis({"art": "schritt", "roh": roh, "antwort": antwort, **eintrag})
            # Fortschrittswaechter (28.09.2026, Verbrauchertest „Frostwerk“): Alle
            # 16 Laeufe endeten am Schrittlimit. Einer fuehrte 29-mal fast denselben
            # Pruefbefehl aus, jedes Mal mit demselben Ergebnis; der alte Hinweis
            # griff nicht, weil sich der Befehlstext leicht aenderte. Gemessen wird
            # jetzt das ERGEBNIS: Kommt seit der letzten Aenderung immer wieder
            # dasselbe heraus, erst ein deutlicher Hinweis, dann Schluss —
            # „festgefahren“ statt eines verbrauchten Schrittbudgets.
            abdruck = hash(re.sub(r"\d+(\.\d+)?", "#", antwort)[:4000])
            fenster = (fenster + [(werkzeug in AENDERND and "Fehler" not in antwort[:80], abdruck)])[-14:]
            seit = []
            for aendernd, a in reversed(fenster):
                if aendernd:
                    break
                seit.insert(0, a)
            wiederholt = len(seit) - len(set(seit))
            if not gewarnt_fortschritt and len(seit) >= 6 and wiederholt >= 4:
                gewarnt_fortschritt = True
                nachrichten[-1]["content"] += (
                    "\n\nWICHTIG: Seit deiner letzten Änderung kam %d-mal ein Ergebnis, das du schon hattest. "
                    "Du drehst dich im Kreis. Ändere jetzt den Code, geh das Problem anders an — oder melde mit "
                    "„frage“, was dich blockiert." % wiederholt)
            elif gewarnt_fortschritt and len(seit) >= 12 and wiederholt >= 9:
                beendet = "festgefahren"
                zusammenfassung = ("Kein Fortschritt mehr: In den letzten %d Schritten ohne Änderung kam %d-mal ein "
                                   "schon gesehenes Ergebnis. Abgebrochen, statt das Schrittbudget zu verbrauchen. "
                                   "Die Aufgabe kleiner fassen oder den blockierenden Fehler selbst ansehen."
                                   % (len(seit), wiederholt))
                break
    finally:
        werkbank.schliessen()
        werkbank.stufe = ziel_stufe
    ereignis({"art": "ende", "beendet": beendet, "schritte": schritte, "zusammenfassung": zusammenfassung[:2000],
              "frage": frage[:2000], "geaendert": geaendert})
    ergebnis = {"beendet": beendet, "schritte": schritte, "sekunden": round(time.time() - t0, 1),
                "modell_sekunden": round(sum(v.get("modell_sek", 0) for v in verlauf), 1),
                "ungueltig": ungueltig, "abgelehnt": abgelehnt, "werkzeuge": zaehler,
                "geaendert": geaendert, "zusammenfassung": zusammenfassung[:2000], "frage": frage[:2000],
                "plan": plan, "plan_freigegeben": plan_freigegeben if planmodus else None, "unteragenten": unteragenten,
                "verlauf": verlauf, "nachrichten": nachrichten}
    if start:
        # Was wirklich auf der Platte anders ist — nicht nur, was der Agent
        # über seine Werkzeuge geschrieben hat, sondern auch durch Befehle.
        ergebnis["checkpunkt_start"] = start["id"]
        ergebnis["aenderungen"] = checkpunkte.seit(start["id"])
    return ergebnis


PLAN_STAENDE = {"offen": "☐", "laeuft": "▶", "erledigt": "☑"}


def _plan_lesen(args):
    """(Liste von {text, stand}, Fehler) — nimmt auch eine bloße Liste von Texten an."""
    roh = (args or {}).get("aufgaben") if isinstance(args, dict) else None
    if not isinstance(roh, list) or not roh:
        return [], "„aufgaben“ muss eine nicht leere Liste sein."
    plan = []
    for eintrag in roh[:30]:
        if isinstance(eintrag, str):
            eintrag = {"text": eintrag}
        if not isinstance(eintrag, dict) or not str(eintrag.get("text", "")).strip():
            return [], "Jede Aufgabe braucht einen Text."
        stand = str(eintrag.get("stand", "offen")).lower().replace("ä", "ae")
        plan.append({"text": str(eintrag["text"]).strip()[:200],
                     "stand": stand if stand in PLAN_STAENDE else "offen"})
    return plan, None


def plan_text(plan):
    return "\n".join("%s %s" % (PLAN_STAENDE[a["stand"]], a["text"]) for a in plan)


def _schritt(werkbank, werkzeug, args, politik, freigabe, melden, nr, mcp=None, regeln=None):
    if regeln is not None and werkzeug in WERKZEUGE + ("mcp",):
        urteil, regel = regeln.entscheidung(werkzeug, args)
        if urteil == "verboten":
            melden("Schritt %d: %s durch Regel %s verboten" % (nr, werkzeug, regel), "active")
            return ("Ergebnis von %s: Fehler: Diese Aktion ist durch die Regel %s verboten. "
                    "Wähle einen anderen Weg." % (werkzeug, regel))
    if werkzeug == "mcp":
        if not mcp or not mcp.werkzeuge():
            return "Ergebnis von mcp: Fehler: In diesem Lauf sind keine MCP-Werkzeuge verbunden."
        if werkbank.stufe == "lesen":
            return ("Ergebnis von mcp: Fehler: Rechtestufe „nur lesen“ — ein MCP-Werkzeug könnte etwas "
                    "verändern, deshalb ist es gesperrt.")
        erlaubt, grund = True, ""
    else:
        erlaubt, grund = werkbank.erlaubt(werkzeug) if werkzeug in WERKZEUGE else (True, "")
    if not erlaubt:
        melden("Schritt %d: %s gesperrt" % (nr, werkzeug), "active")
        return "Ergebnis von %s: Fehler: %s" % (werkzeug, grund)
    if (werkzeug in WERKZEUGE or werkzeug == "mcp") and braucht_freigabe(werkzeug, politik, werkbank, mcp, args, regeln):
        text = _beschreibung(werkzeug, args)
        if werkbank.ohne_grenze(werkzeug):
            text += "  (ohne Sandbox — keine Grenze für Netz und Dateien)"
        if freigabe is None or not freigabe(text):
            melden("Schritt %d: abgelehnt — %s" % (nr, text[:80]), "active")
            return ("Ergebnis von %s: Abgelehnt. Der Nutzer hat diese Aktion nicht freigegeben. "
                    "Wähle einen anderen Weg oder beende mit fertig und erkläre, was fehlt." % werkzeug)
    melden("Schritt %d: %s" % (nr, _beschreibung(werkzeug, args)[:90]), "active")
    try:
        if werkzeug == "mcp":
            args = args if isinstance(args, dict) else {}
            return "Ergebnis von mcp:\n%s" % mcp.aufrufen(args.get("werkzeug", ""), args.get("eingabe", {}))
        return "Ergebnis von %s:\n%s" % (werkzeug, werkbank.werkzeug(werkzeug, args))
    except TypeError as e:
        return "Ergebnis von %s: Fehler in den Argumenten: %s" % (werkzeug, e)
    except (OSError, ValueError, UnicodeError) as e:
        return "Ergebnis von %s: Fehler: %s" % (werkzeug, e)


def protokoll(aufgabe, ordner, ergebnis, stufe, politik, modell=""):
    """Das Protokoll eines Laufs als Markdown — für Chat und Artefakte."""
    zeichen = {"fertig": "✅", "limit": "⚠️", "zeit": "⚠️", "frage": "❓", "festgefahren": "🔁"}.get(ergebnis["beendet"], "⚠️")
    z = ["# Werkbank-Diver\n",
         "**Aufgabe:** %s\n" % aufgabe.strip(),
         "**Projekt:** `%s` · **Rechte:** %s · **Freigabe:** %s%s\n"
         % (ordner, STUFEN[stufe], FREIGABEN[politik], " · **Modell:** %s" % modell if modell else ""),
         "\n## Verlauf\n"]
    for v in ergebnis["verlauf"]:
        # Eine Zeile je Schritt: Ein eingeschobener Absatz ließe Markdown die
        # Nummerierung bei jedem Schritt neu mit 1 beginnen.
        zeile = "- **%d · %s** — %s" % (v["schritt"], v["werkzeug"] or "unlesbar", v.get("gedanke") or "")
        if v.get("ergebnis"):
            kurz = " ".join(v["ergebnis"].split("\n")[:3])
            kurz = kurz.split(":", 1)[-1].strip() if kurz.startswith("Ergebnis") else kurz
            zeile += " → `%s`" % kurz[:140].replace("`", "'")
        z.append(zeile)
    z.append("\n---\n\n## Ergebnis\n")
    z.append("%s %s nach %d Schritten (%.0f s)." % (
        zeichen, {"fertig": "Abgeschlossen", "limit": "Schrittlimit erreicht",
                  "festgefahren": "Festgefahren — kein Fortschritt mehr",
                  "modell": "Das Modell antwortet nicht",
                  "frage": "Wartet auf deine Antwort"}.get(ergebnis["beendet"], ergebnis["beendet"]),
        ergebnis["schritte"], ergebnis["sekunden"]))
    if ergebnis.get("plan"):
        z.append("\n**📋 Plan:**\n\n" + "\n".join("- %s %s" % (PLAN_STAENDE[a["stand"]], a["text"]) for a in ergebnis["plan"]))
    if ergebnis.get("frage"):
        z.append("\n**❓ Rückfrage:** %s\n\n_Antworte einfach im Chat — der Agent arbeitet mit deiner Antwort weiter._"
                 % ergebnis["frage"])
    a = ergebnis.get("aenderungen")
    if a is not None:
        teile = [("neu", "neu"), ("geaendert", "geändert"), ("geloescht", "gelöscht")]
        zeilen = ["%s: %s" % (titel, ", ".join("`%s`" % p for p in a[k][:30]) + (" …" if len(a[k]) > 30 else ""))
                  for k, titel in teile if a[k]]
        z.append("\n**Auf der Platte verändert:** " + ("; ".join(zeilen) if zeilen else "nichts"))
        if zeilen:
            z.append("\n↶ **Rückgängig:** Werkzeuge → Werkbank-Diver → ↶ Checkpunkte → „Vor dem Lauf“ (Checkpunkt `%s`)."
                     % ergebnis["checkpunkt_start"])
    elif ergebnis["geaendert"]:
        z.append("\n**Geänderte Dateien:** " + ", ".join("`%s`" % p for p in ergebnis["geaendert"]))
    if ergebnis["zusammenfassung"]:
        z.append("\n**Zusammenfassung des Agenten:** " + ergebnis["zusammenfassung"])
    if ergebnis["ungueltig"] or ergebnis["abgelehnt"]:
        z.append("\n_%d unlesbare Antworten, %d abgelehnte Aktionen._" % (ergebnis["ungueltig"], ergebnis["abgelehnt"]))
    return "\n".join(z) + "\n"
