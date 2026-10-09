#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Aufgabenfabrik — neue Mini-Projekte für Trainingsdaten, geprüft wie der Prüfstand.

Für ein eigenes Modell braucht es viele gelöste Agentenläufe. Die 20 Aufgaben
des Prüfstands sind dafür tabu — auf ihnen wird gemessen. Die Fabrik lässt ein
lokales Modell neue Aufgaben im selben Stil entwerfen: Issue, fehlerhaftes
Projekt, sichtbare Tests, versteckte Tests, Referenzlösung.

**Geglaubt wird davon nichts.** Jede Aufgabe muss beweisen, dass sie fair ist —
dieselbe Probe, mit der `bau.py` den Prüfstand gebaut hat:

  1. Ausgangscode + versteckte Tests    → scheitert  (es gibt etwas zu tun)
  2. Referenzlösung + versteckte Tests  → besteht, zweimal (lösbar, nicht wackelig)
  3. Referenzlösung + sichtbare Tests   → besteht
  und mindestens drei versteckte Tests, die nicht bloß die sichtbaren wiederholen.

Der erzeugte Code läuft dabei ausschließlich in der Sandbox der Werkbank: ohne
Netz, Schreiben nur im Wegwerf-Ordner. Er stammt von einem Modell und ist bis
zum Beweis des Gegenteils fremder Code.

Außerdem abgewiesen: Aufgaben, die einer Prüfstand-Aufgabe zu ähnlich sind
(Wortüberlappung des Issues, gleicher Modulname), Dubletten untereinander,
Pfade außerhalb des Projekts, Importe außerhalb der Standardbibliothek.

    python3 pruefstand/fabrik.py --modell hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q3_K_XL \\
        --anzahl 40 --ziel storage/training/aufgaben

Die Ausbeute steht ehrlich in `fabrik_bericht.md`: wie viele Entwürfe woran
gescheitert sind.
"""

import argparse
import ast
import collections
import hashlib
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HIER))
sys.path.insert(0, HIER)
import werkbank  # noqa: E402

PRUEFSTAND = os.path.join(HIER, "stufe2", "aufgaben")
AEHNLICH_AB = 0.45
MAX_DATEIEN = 14
MAX_BYTES = 80_000
ERLAUBTE_ENDUNGEN = (".py", ".md", ".txt", ".json", ".csv")
# In Tests verboten: Ergebnis hängt vom Zufall oder von der Außenwelt ab.
WACKELIGE_MODULE = ("random", "secrets", "uuid", "socket", "urllib", "http", "ssl", "requests")

THEMEN = [
    "Terminkalender mit Wiederholungen", "Warenkorb mit Rabattstaffeln", "Textstatistik (Wörter, Sätze, Lesedauer)",
    "Kürzeste Wege in einem kleinen Straßennetz", "Einheitenumrechnung (Länge, Gewicht, Temperatur)",
    "Bibliotheksausleihe mit Fristen und Gebühren", "INI-Datei-Parser", "Ringpuffer für Messwerte",
    "Gleitender Durchschnitt und Ausreißer", "Stundenzettel und Überstunden", "Kontoauszug-Import aus CSV",
    "Versionsnummern vergleichen (semver)", "Markdown-Überschriften zu Inhaltsverzeichnis",
    "Einkaufsliste zusammenführen und sortieren", "Notenberechnung mit Gewichtung", "Lagerplatzvergabe",
    "Mietkosten auf Mitbewohner aufteilen", "Postleitzahlen validieren und gruppieren",
    "Zeitzonen-freie Dauerangaben parsen (\"1h30m\")", "Rezept skalieren mit Einheiten",
    "Bahnfahrplan: nächste Verbindung", "Turniertabelle mit Punkten und Tordifferenz",
    "Passwortregeln prüfen", "Datei-Größen menschenlesbar formatieren", "Bestellstatus als Zustandsautomat",
    "Matrizen addieren und multiplizieren", "Bewertungen aggregieren (Sterne, Median)", "IBAN-Prüfsumme",
    "Wortwolke: Stoppwörter und Häufigkeiten", "Parkhaus-Belegung über den Tag",
    "Farben zwischen Hex und RGB umrechnen", "Kassenbon mit Mehrwertsteuersätzen",
    "Aufgabenliste mit Prioritäten und Fälligkeit", "Baumstruktur von Kategorien ausgeben",
    "Log-Zeilen nach Level filtern und zählen", "Tabellen-Diff zweier CSV-Dateien",
]
ARTEN = [
    ("Fehler", "Im Code steckt ein Fehler, den die sichtbaren Tests zeigen. Das Issue beschreibt das falsche Verhalten."),
    ("Fehler", "Im Code stecken zwei zusammenhängende Fehler. Die sichtbaren Tests zeigen nur einen davon; das Issue beschreibt beide."),
    ("Fehler nur im Issue", "Die sichtbaren Tests sind grün. Der Fehler steht nur im Issue — ein Randfall, den die sichtbaren Tests nicht abdecken."),
    ("Funktion", "Eine im Issue beschriebene Funktion fehlt und muss ergänzt werden; vorhandener Code bleibt nutzbar."),
    ("Umbau", "Logik ist über zwei Module verteilt und dupliziert; das Issue verlangt eine gemeinsame Funktion ohne Verhaltensänderung plus eine kleine neue Anforderung."),
]

PROMPT = """Du entwirfst eine Übungsaufgabe für einen Coding-Agenten: ein kleines Python-Projekt mit einem echten Problem.

Thema: {thema}
Art: {art} — {art_text}
Schwierigkeit: {stufe}

Anforderungen:
- Nur Python-Standardbibliothek. 1 bis 3 Module im Projekt, zusammen höchstens 150 Zeilen.
- Sichtbare Tests unter tests/ (unittest). Versteckte Tests prüfen die Anforderungen aus dem Issue gründlich, mit mindestens 4 Testmethoden, darunter Randfälle. Die versteckten Tests dürfen nicht nur die sichtbaren wiederholen.
- Keine Zufallszahlen, kein datetime.now()/today() (feste Daten wie date(2024, 5, 1) sind gut), keine Dateien außerhalb des Projekts, kein Netz in Tests.
- Jede Erwartung der versteckten Tests muss aus dem Issue folgen. Verlangen die Tests ein genaues Format (z. B. "1.5 KB" mit einer Nachkommastelle) oder eine bestimmte Ausnahme, nennt das Issue es.
- Rechne jeden erwarteten Wert in den Tests von Hand nach, bevor du ihn hinschreibst. Die Referenzlösung muss alle sichtbaren und versteckten Tests bestehen, der Ausgangscode muss mindestens einen versteckten Test nicht bestehen.
- Das Issue nennt nur Dateien, die es im Projekt gibt.
- „repo/“ kennzeichnet nur das Projekt im Antwortformat. Tests laufen im Projektordner und importieren Module direkt: `from modul import …`, niemals `from repo.modul`.
- Die Referenzlösung enthält die vollständigen korrigierten Dateien (nur die geänderten).
- Das Issue ist auf Deutsch, in 2 bis 5 Sätzen, wie ein echter Bugreport oder Wunsch: beschreibt das Verhalten, nicht die Lösung.
- Module und Funktionen bekommen deutsche oder englische sprechende Namen.

Antworte GENAU in diesem Format, ohne Text davor oder danach:
=== ISSUE ===
<Text des Issues>
=== DATEI repo/<modul>.py ===
<fehlerhafter Code>
=== DATEI repo/tests/test_<modul>.py ===
<sichtbare Tests>
=== DATEI versteckt/test_<modul>.py ===
<versteckte Tests; importieren die Module wie die sichtbaren Tests>
=== DATEI loesung/<modul>.py ===
<korrigierter vollständiger Code>
=== ENDE ==="""


# ------------------------------------------------------------------ Zerlegen ---

BLOCK_RE = re.compile(r"^===\s*(ISSUE|DATEI\s+(\S+)|ENDE)\s*===\s*$", re.M)


def standardbibliothek():
    """Namen der Standardbibliothek. `sys.stdlib_module_names` gibt es erst ab Python 3.10 — unter 3.9 (das wir
    unterstützen) stürzte die Aufgabenfabrik ab, und die Überschatten-Prüfung des Orakels sah fast nichts
    (GitHub-CI macOS/3.9, 09.10.2026). Dort wird die Liste aus dem Ordner der Standardbibliothek gelesen."""
    namen = getattr(sys, "stdlib_module_names", None)
    if namen:
        return frozenset(namen)
    import pkgutil
    import sysconfig
    ordner = [sysconfig.get_paths()["stdlib"]]
    ordner.append(os.path.join(ordner[0], "lib-dynload"))
    gefunden = {m.name for m in pkgutil.iter_modules(ordner)}
    return frozenset(gefunden | set(sys.builtin_module_names) | {"__future__"})


def zerlegen(text):
    """(issue, {pfad: inhalt}) aus der Modellantwort. Wirft ValueError mit Grund."""
    text = (text or "").replace("\r\n", "\n")
    treffer = list(BLOCK_RE.finditer(text))
    if not treffer:
        raise ValueError("Format: keine ===-Blöcke")
    issue, dateien = None, {}
    for i, m in enumerate(treffer):
        if m.group(1) == "ENDE":
            break
        ende = treffer[i + 1].start() if i + 1 < len(treffer) else len(text)
        inhalt = text[m.end():ende].strip("\n")
        inhalt = re.sub(r"^```\w*\n|\n```\s*$", "", inhalt)          # Codezäune abstreifen
        if m.group(1) == "ISSUE":
            issue = inhalt.strip()
        else:
            pfad = m.group(2).strip()
            dateien[pfad] = inhalt.rstrip() + "\n"      # doppelt ausgegeben: die letzte Fassung gilt
    # Module liegen im Projekt oben — `from repo.modul import …` wäre dort ein
    # Importfehler. Das Präfix stammt aus der Blockschreibweise des Formats.
    for pfad in list(dateien):
        if pfad.endswith(".py"):
            dateien[pfad] = re.sub(r"\b(from|import)\s+repo\.", r"\1 ", dateien[pfad])
            dateien[pfad] = re.sub(r"^from\s+repo\s+import\s+", "import ", dateien[pfad], flags=re.M)
    if not issue:
        raise ValueError("Format: Issue fehlt")
    return issue, dateien


def wortmenge(text):
    return {w for w in re.findall(r"[a-zäöüß]{4,}", text.lower())}


def aehnlichkeit(a, b):
    a, b = wortmenge(a), wortmenge(b)
    return len(a & b) / max(1, len(a | b))


def _module_namen(dateien, praefix):
    namen = set()
    for p in dateien:
        if p.startswith(praefix) and p.endswith(".py"):
            rel = p[len(praefix):]
            namen.add(rel.split("/")[0].rsplit(".py", 1)[0])
    return namen


def pruefen_statisch(issue, dateien, vergleich):
    """Alles, was sich ohne Ausführen prüfen lässt. Gibt den Grund zurück oder None."""
    if not 60 <= len(issue) <= 1500:
        return "Issue zu kurz oder zu lang"
    if len(dateien) > MAX_DATEIEN or sum(len(v.encode()) for v in dateien.values()) > MAX_BYTES:
        return "zu groß"
    for p in dateien:
        teile = p.split("/")
        if p.startswith("/") or ".." in teile or "\\" in p or teile[0] not in ("repo", "versteckt", "loesung") \
                or len(teile) < 2 or not p.endswith(ERLAUBTE_ENDUNGEN):
            return "unzulässiger Pfad"
    repo_code = [p for p in dateien if p.startswith("repo/") and not p.startswith("repo/tests/") and p.endswith(".py")]
    sichtbar = [p for p in dateien if re.match(r"^repo/tests/test_\w+\.py$", p)]
    versteckt = [p for p in dateien if re.match(r"^versteckt/test_\w+\.py$", p)]
    loesung = [p for p in dateien if p.startswith("loesung/") and p.endswith(".py")]
    if not (repo_code and sichtbar and versteckt and loesung):
        return "Bestandteil fehlt (Code, sichtbare/versteckte Tests oder Lösung)"
    for p in loesung:
        if p.startswith("loesung/tests/"):
            return "Lösung ändert Tests"
    # Nennt das Issue eine Datei, muss es sie geben (oder die Lösung legt sie an) —
    # sonst beschreibt es ein anderes Projekt als das, das der Agent bekommt.
    vorhanden = {p.split("/", 1)[1] for p in dateien if "/" in p}
    for genannt in re.findall(r"`?([\w./-]+\.py)`?", issue):
        if genannt.lstrip("./") not in vorhanden:
            return "Issue nennt eine Datei, die es nicht gibt"
    for p, inhalt in dateien.items():
        if not p.endswith(".py"):
            continue
        try:
            baum = ast.parse(inhalt)
        except SyntaxError:
            return "Syntaxfehler"
        eigene = _module_namen(dateien, "repo/") | _module_namen(dateien, "loesung/") | {"tests"}
        for knoten in ast.walk(baum):
            namen = []
            if isinstance(knoten, ast.Import):
                namen = [a.name.split(".")[0] for a in knoten.names]
            elif isinstance(knoten, ast.ImportFrom) and knoten.level == 0 and knoten.module:
                namen = [knoten.module.split(".")[0]]
            for n in namen:
                if n not in standardbibliothek() and n not in eigene:
                    return "Import außerhalb der Standardbibliothek (%s)" % n
            # Feste Daten wie date(2024, 1, 1) sind in Ordnung — Zufall, „jetzt“ und Netz nicht.
            wackelig = (isinstance(knoten, ast.Name) and knoten.id == "random") or \
                (isinstance(knoten, ast.Attribute) and knoten.attr in ("now", "today", "utcnow", "urlopen")) or \
                any(n in WACKELIGE_MODULE for n in namen)
            if wackelig and (p.startswith("versteckt/") or p.startswith("repo/tests/")):
                return "Tests hängen von Zufall, Uhrzeit oder Netz ab"
    methoden = lambda inhalt: set(re.findall(r"def (test_\w+)", inhalt))
    v_methoden = set().union(*(methoden(dateien[p]) for p in versteckt))
    s_inhalt = "".join(dateien[p] for p in sichtbar)
    if len(v_methoden) < 3:
        return "weniger als 3 versteckte Tests"
    if "".join(dateien[p] for p in versteckt).strip() == s_inhalt.strip():
        return "versteckte Tests = sichtbare Tests"
    module = _module_namen(dateien, "repo/") - {"tests"}
    for name, (text, fremde_module) in vergleich.items():
        # Gleicher Modulname nur gegenüber dem Prüfstand: Dort wäre er ein Hinweis auf
        # dieselbe Aufgabe. Innerhalb der Sammlung dürfen zwei INI-Parser verschieden
        # sein — dort entscheidet die Ähnlichkeit der Issues (gemessen: 13 von 62
        # Entwürfen gingen sonst allein am Namen verloren).
        if module & fremde_module and not re.match(r"^f\d{4}_", name):
            return "gleicher Modulname wie %s" % name
        if aehnlichkeit(issue, text) >= AEHNLICH_AB:
            return "zu ähnlich zu %s" % name
    return None


# ------------------------------------------------------------------- Orakel ---

def _anlegen(dateien, mit_loesung, testordner):
    ordner = tempfile.mkdtemp(prefix="dowos_fabrik_")
    for p, inhalt in dateien.items():
        if p.startswith("repo/"):
            ziel = p[len("repo/"):]
        elif p.startswith("loesung/") and mit_loesung:
            ziel = p[len("loesung/"):]
        elif p.startswith("versteckt/") and testordner == "tests_versteckt":
            ziel = "tests_versteckt/" + p[len("versteckt/"):]
        else:
            continue
        voll = os.path.join(ordner, ziel)
        os.makedirs(os.path.dirname(voll), exist_ok=True)
        with open(voll, "w", encoding="utf-8") as f:
            f.write(inhalt)
    for d in ("tests", "tests_versteckt"):
        if os.path.isdir(os.path.join(ordner, d)) and not os.path.exists(os.path.join(ordner, d, "__init__.py")):
            open(os.path.join(ordner, d, "__init__.py"), "w").close()
    return ordner


def _testlauf(dateien, mit_loesung, testordner, sekunden=30):
    """(bestanden, Zahl der Tests, Ausgabe) — in der Sandbox der Werkbank."""
    ordner = _anlegen(dateien, mit_loesung, testordner)
    try:
        wb = werkbank.Werkbank(ordner, "projekt")
        if not wb.sandbox:
            raise RuntimeError("Keine Sandbox auf diesem Rechner — fremden Code führt die Fabrik nicht ungeschützt aus.")
        aus = wb.ausfuehren("python -m unittest discover -s %s -t ." % testordner, timeout=sekunden)
        wb.schliessen()
    finally:
        shutil.rmtree(ordner, ignore_errors=True)
    m = re.search(r"Ran (\d+) tests?", aus)
    return aus.startswith("exit=0") and "OK" in aus, int(m.group(1)) if m else 0, aus


MAX_GESTRICHEN = 2


def _gescheiterte_tests(ausgabe):
    return sorted(set(re.findall(r"^(?:FAIL|ERROR): (test_\w+) \(", ausgabe, re.M)))


def tests_streichen(inhalt, namen):
    """Entfernt Testmethoden aus einer Testdatei. None, wenn es nicht sauber geht."""
    try:
        baum = ast.parse(inhalt)
    except SyntaxError:
        return None
    entfernt = 0
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ClassDef):
            vorher = len(knoten.body)
            knoten.body = [b for b in knoten.body if not (isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))
                                                          and b.name in namen)]
            entfernt += vorher - len(knoten.body)
            if not knoten.body:
                knoten.body = [ast.Pass()]
    return (ast.unparse(baum) + "\n") if entfernt else None


def _kuerzen(dateien, praefix, ausgabe):
    """Streicht die Tests, die die Referenzlösung nicht besteht. (neue Dateien, Zahl) oder (None, 0)."""
    namen = _gescheiterte_tests(ausgabe)
    if not namen or len(namen) > MAX_GESTRICHEN:
        return None, 0
    neu, gestrichen = dict(dateien), 0
    for p in dateien:
        if p.startswith(praefix) and p.endswith(".py"):
            gekuerzt = tests_streichen(dateien[p], set(namen))
            if gekuerzt is not None:
                neu[p] = gekuerzt
                gestrichen = len(namen)
    return (neu, gestrichen) if gestrichen else (None, 0)


def orakel(dateien):
    """(Grund der Ablehnung oder None, Fehler sichtbar?, geprüfte Dateien, gestrichene Tests).

    Rechnet das Modell in einem von fünf Tests falsch nach, wäre die ganze
    Aufgabe verloren — obwohl vier Tests stimmen. Bis zu MAX_GESTRICHEN Tests,
    die die Referenzlösung nicht besteht, werden deshalb gestrichen, und danach
    muss die GANZE Probe neu bestanden werden. Was übrig bleibt, ist geprüft."""
    gestrichen = 0
    for runde in range(2):
        ok_aus, _, _ = _testlauf(dateien, False, "tests_versteckt")
        if ok_aus:
            return "Ausgangscode besteht die versteckten Tests", None, dateien, gestrichen
        ok_ref, n_ref, aus = _testlauf(dateien, True, "tests_versteckt")
        if not ok_ref:
            neu, n = _kuerzen(dateien, "versteckt/", aus) if runde == 0 else (None, 0)
            if neu:
                dateien, gestrichen = neu, gestrichen + n
                continue
            return "Referenzlösung besteht die versteckten Tests nicht", None, dateien, gestrichen
        if not _testlauf(dateien, True, "tests_versteckt")[0]:
            return "versteckte Tests wackeln (zweiter Lauf anders)", None, dateien, gestrichen
        if n_ref < 3:
            return "weniger als 3 versteckte Tests liefen", None, dateien, gestrichen
        ok_sicht, n_sicht, aus_sicht = _testlauf(dateien, True, "tests")
        if not ok_sicht:
            neu, n = _kuerzen(dateien, "repo/tests/", aus_sicht) if runde == 0 else (None, 0)
            if neu:
                dateien, gestrichen = neu, gestrichen + n
                continue
            return "Referenzlösung besteht die sichtbaren Tests nicht", None, dateien, gestrichen
        if n_sicht < 1:
            return "keine sichtbaren Tests übrig", None, dateien, gestrichen
        sichtbar_rot = not _testlauf(dateien, False, "tests")[0]
        return None, sichtbar_rot, dateien, gestrichen
    return "Referenzlösung besteht die Tests auch nach dem Streichen nicht", None, dateien, gestrichen


# ----------------------------------------------------------------- Fabrik ---

def vergleichsbasis(*ordner):
    """{name: (issue, module)} aus Prüfstand und schon erzeugten Aufgaben."""
    basis = {}
    for wurzel in ordner:
        if not os.path.isdir(wurzel):
            continue
        for name in sorted(os.listdir(wurzel)):
            issue = os.path.join(wurzel, name, "ISSUE.md")
            if not os.path.isfile(issue):
                continue
            with open(issue, encoding="utf-8") as f:
                text = f.read()
            repo = os.path.join(wurzel, name, "repo")
            module = {os.path.splitext(d)[0] for d in os.listdir(repo) if d.endswith(".py")} if os.path.isdir(repo) else set()
            basis[name] = (text, module)
    return basis


def ablegen(ziel, issue, dateien, meta):
    module = sorted(_module_namen(dateien, "repo/") - {"tests"})
    nummer = 1 + max([int(m.group(1)) for d in (os.listdir(ziel) if os.path.isdir(ziel) else [])
                      for m in [re.match(r"^f(\d{4})_", d)] if m] + [0])
    kennung = "f%04d_%s" % (nummer, re.sub(r"[^\w]", "", module[0])[:24] if module else "aufgabe")
    basis = os.path.join(ziel, kennung)
    for p, inhalt in dateien.items():
        voll = os.path.join(basis, p)
        os.makedirs(os.path.dirname(voll), exist_ok=True)
        with open(voll, "w", encoding="utf-8") as f:
            f.write(inhalt)
    for d in ("repo/tests", "versteckt"):
        pfad = os.path.join(basis, d, "__init__.py")
        if not os.path.exists(pfad):
            open(pfad, "w").close()
    with open(os.path.join(basis, "ISSUE.md"), "w", encoding="utf-8") as f:
        f.write(issue + "\n")
    meta_pfad = os.path.join(ziel, "meta.json")
    alle = {}
    if os.path.exists(meta_pfad):
        with open(meta_pfad, encoding="utf-8") as f:
            alle = json.load(f)
    alle[kennung] = meta
    with open(meta_pfad, "w", encoding="utf-8") as f:
        json.dump(alle, f, ensure_ascii=False, indent=1)
    return kennung


def grundklasse(grund):
    """„zu ähnlich zu 02_preise“ und „zu ähnlich zu f0003_x“ zählen als ein Grund."""
    grund = grund.split(" (")[0]
    return re.sub(r"^(zu ähnlich zu|gleicher Modulname wie) .*$", r"\1 …", grund)


def entwerfen(chat, anzahl, ziel, seed=1, melden=print, max_versuche=None, themen=None):
    """Erzeugt bis zu `anzahl` geprüfte Aufgaben. Gibt den Bericht als dict zurück.
    themen: eigene Themenliste (Destillation je Thema); ohne: die eingebaute Liste."""
    themen = [t.strip() for t in (themen or THEMEN) if str(t).strip()] or THEMEN
    rnd = random.Random(seed)
    os.makedirs(ziel, exist_ok=True)
    gruende = collections.Counter()
    angenommen, versuche = [], 0
    max_versuche = max_versuche or anzahl * 4
    while len(angenommen) < anzahl and versuche < max_versuche:
        versuche += 1
        # Themen ohne Aufgabe zuerst — sonst entsteht dieselbe Aufgabe noch einmal
        # und scheitert als Dublette (gemessen: „Tabellen-Diff“ zweimal hintereinander).
        try:
            with open(os.path.join(ziel, "meta.json"), encoding="utf-8") as f:
                vorhandene = json.load(f)
            genutzt = {m.get("thema") for m in vorhandene.values()}
        except (OSError, ValueError):
            vorhandene, genutzt = {}, set()
        thema = rnd.choice([t for t in themen if t not in genutzt] or themen)
        art, art_text = rnd.choice(ARTEN)
        stufe = rnd.choice(("leicht", "mittel", "mittel", "schwer"))
        t0 = time.time()
        auftrag = PROMPT.format(thema=thema, art=art, art_text=art_text, stufe=stufe)
        # Wenige eigene Themen: dasselbe Thema kommt öfter dran. Damit keine Dublette entsteht,
        # sieht das Modell, welche Aufgaben es dazu schon gibt.
        bisher = []
        for kennung, m in sorted(vorhandene.items()):
            if m.get("thema") == thema:
                try:
                    with open(os.path.join(ziel, kennung, "ISSUE.md"), encoding="utf-8") as f:
                        bisher.append(" ".join(f.read().split())[:160])
                except OSError:
                    pass
        if bisher:
            auftrag = auftrag.replace("Antworte GENAU in diesem Format", "Zu diesem Thema gibt es schon diese Aufgaben — "
                                      "wähle einen deutlich anderen Aspekt, andere Funktionen und andere Modulnamen:\n- "
                                      + "\n- ".join(bisher[-8:]) + "\n\nAntworte GENAU in diesem Format", 1)
        try:
            roh = chat([{"role": "user", "content": auftrag}])
        except Exception as e:                   # Modell weg: nicht endlos weiterversuchen
            if getattr(e, "abbruch", False):     # etwa ein aufgebrauchtes Budget: sofort aufhören
                raise
            gruende["Modellfehler"] += 1
            melden("  ⚠️  Modellfehler: %s" % e)
            if gruende["Modellfehler"] >= 3:
                break
            continue
        try:
            issue, dateien = zerlegen(roh)
            grund = pruefen_statisch(issue, dateien, vergleichsbasis(PRUEFSTAND, ziel))
            sichtbar_rot, gestrichen = None, 0
            if not grund:
                grund, sichtbar_rot, dateien, gestrichen = orakel(dateien)
        except ValueError as e:
            grund = str(e)
        if grund:
            gruende[grundklasse(grund)] += 1
            # Verworfene Entwürfe aufheben: Nur so lässt sich sehen, ob die Prüfung
            # zu Recht abgelehnt hat oder der Prompt nachgeschärft werden muss.
            verworfen = os.path.join(ziel, "_verworfen")
            os.makedirs(verworfen, exist_ok=True)
            with open(os.path.join(verworfen, "%s_%03d.txt" % (time.strftime("%Y%m%d_%H%M%S"), versuche)),
                      "w", encoding="utf-8") as f:
                f.write("# Grund: %s\n# Thema: %s · Art: %s · Stufe: %s\n\n%s" % (grund, thema, art, stufe, roh))
            melden("  ❌ %-38s %s (%.0f s)" % (thema[:38], grund, time.time() - t0))
            continue
        meta = {"stufe": stufe, "art": art.split()[0], "fehler_sichtbar": bool(sichtbar_rot),
                "thema": thema, "quelle": "fabrik", "zeit": time.time(), "gestrichene_tests": gestrichen}
        kennung = ablegen(ziel, issue, dateien, meta)
        angenommen.append(kennung)
        gruende["angenommen"] += 1
        melden("  ✅ %-38s %s%s (%.0f s)" % (thema[:38], kennung,
                                            " · %d Test(s) gestrichen" % gestrichen if gestrichen else "", time.time() - t0))
    bericht = {"angenommen": angenommen, "versuche": versuche, "gruende": dict(gruende),
               "ausbeute": round(len(angenommen) / max(1, versuche), 2)}
    with open(os.path.join(ziel, "fabrik_bericht.md"), "a", encoding="utf-8") as f:
        f.write("\n## Lauf %s\n\n- Versuche: %d, angenommen: %d (Ausbeute %d %%)\n" % (
            time.strftime("%Y-%m-%d %H:%M"), versuche, len(angenommen), 100 * bericht["ausbeute"]))
        for g, n in collections.Counter(gruende).most_common():
            f.write("- %s: %d\n" % (g, n))
    return bericht


def nachpruefen(ziel, melden=print):
    """Verworfene Entwürfe mit der aktuellen Prüfung erneut ansehen — ohne Modellaufruf.

    Wird die Prüfung verbessert (etwa das Streichen falsch nachgerechneter
    Tests), sind frühere Ablehnungen vielleicht keine mehr. Was jetzt besteht,
    wird abgelegt; der Entwurf wandert nach _verworfen/nachgeprueft/."""
    verworfen = os.path.join(ziel, "_verworfen")
    erledigt = os.path.join(verworfen, "nachgeprueft")
    os.makedirs(erledigt, exist_ok=True)
    angenommen = []
    for name in sorted(os.listdir(verworfen)) if os.path.isdir(verworfen) else []:
        pfad = os.path.join(verworfen, name)
        if not name.endswith(".txt") or not os.path.isfile(pfad):
            continue
        with open(pfad, encoding="utf-8") as f:
            kopf, _, roh = f.read().partition("\n\n")
        m = re.search(r"# Thema: (.*?) · Art: (.*?) · Stufe: (\w+)", kopf)
        try:
            issue, dateien = zerlegen(roh)
            grund = pruefen_statisch(issue, dateien, vergleichsbasis(PRUEFSTAND, ziel))
            gestrichen, sichtbar_rot = 0, None
            if not grund:
                grund, sichtbar_rot, dateien, gestrichen = orakel(dateien)
        except ValueError as e:
            grund = str(e)
        if grund:
            continue
        meta = {"stufe": m.group(3) if m else "mittel", "art": (m.group(2) if m else "Fehler").split()[0],
                "fehler_sichtbar": bool(sichtbar_rot), "thema": m.group(1) if m else "", "quelle": "fabrik",
                "zeit": time.time(), "gestrichene_tests": gestrichen, "nachgeprueft": True}
        kennung = ablegen(ziel, issue, dateien, meta)
        os.replace(pfad, os.path.join(erledigt, name))
        angenommen.append(kennung)
        melden("  ✅ nachgeprüft %s → %s" % (name, kennung))
    return angenommen


def main():
    ap = argparse.ArgumentParser(description="Neue, geprüfte Mini-Projekte für Trainingsdaten entwerfen")
    ap.add_argument("--modell")
    ap.add_argument("--nachpruefen", action="store_true", help="verworfene Entwürfe mit der aktuellen Prüfung erneut ansehen")
    ap.add_argument("--anzahl", type=int, default=20)
    ap.add_argument("--ziel", required=True)
    ap.add_argument("--seed", type=int, default=int(time.time()))
    ap.add_argument("--ollama", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    ap.add_argument("--openai", help="OpenAI-kompatibler Server statt Ollama")
    ap.add_argument("--adapter", help="LoRA-Adapter, den mlx_lm server je Anfrage laden soll")
    ap.add_argument("--temperatur", type=float, default=0.8)
    a = ap.parse_args()
    if a.nachpruefen:
        neu = nachpruefen(os.path.expanduser(a.ziel), melden=lambda t: print(t, flush=True))
        print("Nachgeprüft: %d Aufgaben angenommen" % len(neu))
        if not a.modell:
            return 0
    if not a.modell:
        ap.error("--modell fehlt")
    import stufe2
    chat = stufe2.openai_chat(a.openai, a.modell, os.environ.get("DOWOS_API_KEY", ""), a.temperatur, a.adapter or "") if a.openai else \
        stufe2.ollama_chat(a.ollama, a.modell, temperatur=a.temperatur)
    if not werkbank.sandbox_art():
        print("Keine Sandbox auf diesem Rechner — die Fabrik führt fremden Code nicht ungeschützt aus.")
        return 1
    print("Fabrik: %d Aufgaben mit %s → %s" % (a.anzahl, a.modell, a.ziel), flush=True)
    b = entwerfen(chat, a.anzahl, os.path.expanduser(a.ziel), a.seed, melden=lambda t: print(t, flush=True))
    print("\nAngenommen %d von %d Versuchen (Ausbeute %d %%)" % (len(b["angenommen"]), b["versuche"], 100 * b["ausbeute"]))
    for g, n in sorted(b["gruende"].items(), key=lambda x: -x[1]):
        print("  %-50s %d" % (g, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
