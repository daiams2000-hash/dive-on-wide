#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Englische Oberfläche: deutsche Textstücke finden, lokal übersetzen, Abdeckung prüfen.

    python3 werkzeuge/sprache.py extrahieren     # Stücke aus frontend/index.html (+ gemeldete Lücken)
    python3 werkzeuge/sprache.py uebersetzen --modell qwen3.6-35b-a3b-text:ud-q3kxl [--stunden 8]
    python3 werkzeuge/sprache.py pruefen         # was fehlt noch?

Die Oberfläche übersetzt zur Laufzeit Textknoten, Platzhalter und Tooltips anhand
von frontend/lang/en.json. Schlüssel sind die deutschen Stücke, Zahlen darin als „§“
(„Gefunden: § Modell(e).“), damit eine Übersetzung für jede Anzahl gilt.
Übersetzt wird mit dem lokalen Modell — Stapel zu 25, nach jedem Stapel gespeichert,
Fortschritt je Stapel, harte Zeitgrenze.
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUELLE = os.path.join(APP, "frontend", "index.html")
ZIEL = os.path.join(APP, "frontend", "lang", "en.json")
FEHLEND = os.environ.get("DOWOS_FEHLEND") or os.path.join(APP, "storage", "sprache_fehlend.json")

DEUTSCH = re.compile(r"[äöüÄÖÜß]|\b(der|die|das|und|nicht|ist|mit|für|auf|ein|eine|einen|zu|von|den|dem|wird|kann|"
                     r"noch|keine|kein|dein|deine|du|oder|bei|wenn|über|aus|hier|nur|alle|alles|neu|neue|bitte|"
                     r"wählen|starten|speichern|abbrechen|löschen|laden|zurück|weiter|fertig|läuft|nichts|jetzt|"
                     r"Einstellungen|Modell|Modelle|Agent|Agenten|Wissen|Aufgabe|Lauf|Läufe|Freigabe)\b")
ENGLISCH = re.compile(r"\b(the|and|is|are|this|that|with|your|you|it|its|of|to|on|at|from|instead|another|answers|"
                      r"machine|weekdays|could|about|missing|Found|address|Morning|briefing|there)\b")
CODE = re.compile(r"[{}=;`]|=>|&&|\|\||\(\)|\bvar\(|rgba?\(|\bpx\b|^--|https?://|/api/|\bfunction\b|\breturn\b|"
                  r"\bconst\b|\blet\b|\.(js|css|py|json|svg|png)\b|^[#.][\w-]+$|\$\{|\)\.[a-z]|\" \+|\+ \"|\s\?\s*$")


def schluessel(text):
    """Zahlen werden zu § — eine Übersetzung gilt für jede Anzahl."""
    return re.sub(r"\d+(?:[.,]\d+)?", "§", " ".join(text.split()))


def _stuecke(text):
    """Ein Literal in die Stücke zerlegen, die später als Textknoten im DOM stehen."""
    teile = []
    tiefe, puffer, i = 0, [], 0
    while i < len(text):                                  # ${…} herausschneiden (verschachtelt)
        if text.startswith("${", i):
            if tiefe == 0:
                teile.append("".join(puffer))
                puffer = []
            tiefe += 1
            i += 2
            continue
        if tiefe and text[i] == "{":
            tiefe += 1
        elif tiefe and text[i] == "}":
            tiefe -= 1
            i += 1
            continue
        if not tiefe:
            puffer.append(text[i])
        i += 1
    teile.append("".join(puffer))
    aus = []
    for t in teile:
        for stueck in re.split(r"<[^>]*>", t):            # Tags trennen Textknoten
            aus.append(stueck)
        for a in re.findall(r'(?:placeholder|title|aria-label)="([^"$]+)"', t):
            aus.append(a)
    return aus


def extrahieren():
    roh = open(QUELLE, encoding="utf-8").read()
    kandidaten = []
    markup = set(re.findall(r">([^<>`$]+)<", roh))                              # Text im Markup
    kandidaten += list(markup)
    kandidaten += re.findall(r"`((?:[^`\\]|\\.)*)`", roh, re.S)                   # Template-Literale
    kandidaten += re.findall(r'"((?:[^"\\\n]|\\.){1,300})"', roh)                 # "…"
    kandidaten += re.findall(r"'((?:[^'\\\n]|\\.){1,300})'", roh)                 # '…'
    gefunden = set()
    for k in kandidaten:
        for s in _stuecke(k):
            s = html.unescape(s.replace("\\n", " ").replace('\\"', '"')).strip()
            s = re.sub(r"^[^\w§]+(?=\w)", "", s)              # „) nur die …“, „＋ Neuer Chat“
            if len(s) < 2 or not re.search(r"[A-Za-zÄÖÜäöüß]{2}", s) or CODE.search(s.replace(" = ", " ")):
                continue
            # Englische Hälften von L("…", "…") sind schon übersetzt
            if not DEUTSCH.search(s) and ENGLISCH.search(s):
                continue
            # Text zwischen Tags ist sichtbare Oberfläche — dort genügt ein Wort mit Kleinbuchstaben
            if k in markup and re.search(r"[a-zäöüß]{3}", s):
                gefunden.add(schluessel(s))
                continue
            if not DEUTSCH.search(s) and not re.fullmatch(r"[A-ZÄÖÜ][\wäöüß-]+( (&|[\wäöüß-]+)){0,5}[.:!?…]?", s):
                continue
            gefunden.add(schluessel(s))
    # Meldungen des Servers, die in der Oberfläche landen (Hinweise, Fehler, Diagnosen).
    # Strenger: nur ganze Sätze mit Leerzeichen und deutschem Wort.
    # Alle Programmteile, deren Meldungen in der Oberfläche landen können (nicht die Tests)
    for datei in sorted(f for f in os.listdir(APP) if f.endswith(".py") and f not in ("install.py", "dowos_cli.py")):
        try:
            text = open(os.path.join(APP, datei), encoding="utf-8").read()
            # Über Zeilen fortgesetzte Literale („…" ↵ "…") sind im Programm ein einziger Text
            text = re.sub(r'"\s*\n\s*"', "", text)
            text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)
        except OSError:
            continue
        for k in re.findall(r'(?<!")"((?:[^"\\\n]|\\.){6,300})"(?!")', text):
            # Die ganze Meldung als Vorlage: „{}“ je eingesetztem Wert (die Oberfläche setzt sie wieder ein)
            platz = r"%[sdr]|%\.\d?f|%\(\w+\)s"
            if 1 <= len(re.findall(platz, k)) <= 4 and DEUTSCH.search(k) and " " in k:
                vorlage = re.sub(platz, "{}", k.replace("\\n", " ").replace('\\"', '"')).replace("%%", "%")
                if not CODE.search(vorlage.replace("{}", "x").replace("; ", ", ").replace(" = ", " ")):
                    gefunden.add(schluessel(vorlage))
            for teil in re.split(r"%[sdr]|%\.\d?f|%\(\w+\)s|\{\w*\}", k):
                teil = teil.replace("\\n", " ").replace('\\"', '"').strip(" ,:—-")
                # „; “ ist in Meldungen Satzzeichen, kein Code
                kurz_name = re.fullmatch(r"[A-ZÄÖÜ][\wäöüß]*-[A-ZÄÖÜ]?[\wäöüß]*[äöüß][\wäöüß]*", teil)  # „Code-Erklärer“
                if (kurz_name or (" " in teil and len(teil) > 5 and DEUTSCH.search(teil))) \
                        and not CODE.search(teil.replace("; ", ", ").replace(" = ", " ")):
                    gefunden.add(schluessel(teil))
    try:
        for m in json.load(open(os.path.join(APP, "modellempfehlungen.json"), encoding="utf-8"))["modelle"]:
            for feld in ("hinweis", "gemessen"):
                if m.get(feld):
                    gefunden.add(schluessel(m[feld]))
    except (OSError, ValueError, KeyError):
        pass
    # Gemeldete Lücken nur, wenn sie aus dem Code von Dive on Wide stammen: Jeder Teil zwischen
    # den Zahlen muss im Quelltext vorkommen. Aufträge, Chat-Titel und Antworten des
    # Modells (Inhalt des Nutzers) kommen so nie ins Wörterbuch.
    quelltext = " ".join(" ".join(open(os.path.join(APP, d), encoding="utf-8").read().split())
                         for d in ["frontend/index.html"] + [f for f in os.listdir(APP) if f.endswith(".py")])
    try:
        for s in json.load(open(FEHLEND, encoding="utf-8")):
            teile = [t.strip(" .,:;·()") for t in schluessel(s).split("§")]
            if all(t in quelltext for t in teile if len(t) >= 2):
                gefunden.add(schluessel(s))
    except (OSError, ValueError):
        pass
    return sorted(gefunden)


# Begriffe, die das Modell ohne Zusammenhang falsch trifft — nach jeder Übersetzung angewandt.
# (deutsches Wort im Schlüssel, englisches Muster, richtiger Begriff)
GLOSSAR = [
    ("Auftrag", r"\b[Oo]rders?\b", "task"),
    ("Freigabe", r"\b[Rr]eleases?\b", "approval"),
    ("Gerüst", r"\b[Ss]caffold(?:s|ing)?\b", "harness"),
    ("Ausgang", r"\b[Ee]xit\b", "outbound"),
    ("Ausgang", r"\b[Oo]utput\b", "outbound"),
]


def begriffe(wb):
    """Glossar anwenden: Großschreibung und Mehrzahl bleiben erhalten."""
    n = 0
    for de, muster, en in GLOSSAR:
        for k in list(wb):
            if de not in k:
                continue
            def ersetzen(m, en=en):
                w = en + ("s" if m.group(0).lower().endswith("s") and not en.endswith("s") else "")
                return w[0].upper() + w[1:] if m.group(0)[0].isupper() else w
            neu = re.sub(muster, ersetzen, wb[k])
            if neu != wb[k]:
                wb[k], n = neu, n + 1
    return n


def woerterbuch():
    try:
        return json.load(open(ZIEL, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _ollama(modell, prompt, frist=600):
    basis = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    daten = json.dumps({"model": modell, "stream": False, "think": False, "format": "json",
                        "options": {"temperature": 0.1, "num_ctx": 8192},
                        "messages": [{"role": "user", "content": prompt}]}).encode()
    with urllib.request.urlopen(urllib.request.Request(basis + "/api/chat", data=daten,
                                                       headers={"Content-Type": "application/json"}), timeout=frist) as r:
        return json.loads(r.read())["message"]["content"]


PROMPT = """Translate these German user-interface texts of a local AI workspace app ("Dive on Wide") into natural, concise
English, as a native English UI writer would. Rules:
- Keep "§" exactly as often as in the German text (it stands for a number), and markers like "[V1]", "[V2]"
  exactly as they are (they stand for inserted values such as a model name or an address).
- Keep emoji, symbols, punctuation style, leading/trailing spaces as they are; keep product names (Dive on Wide, Ollama,
  Werkbank → "Workbench", Diving Net → "Diving Net", Weite → "Open net", Bote → "Messenger", Rhythmus → "Rhythm").
- Address the user informally ("you"). Do not add explanations.
Return ONLY a JSON object mapping each number to the English translation of its text.

%s"""


def uebersetzen(modell, stunden):
    ende = time.time() + stunden * 3600
    wb = woerterbuch()
    offen = [s for s in extrahieren() if s not in wb]
    print("%d Stücke offen, %d schon übersetzt" % (len(offen), len(wb)), flush=True)
    for i in range(0, len(offen), 25):
        if time.time() > ende:
            print("Zeitgrenze — Rest bleibt offen (Wiederaufnahme möglich)", flush=True)
            break
        stapel = offen[i:i + 25]
        t = time.time()
        neu = {}
        # „{}“ verwirrt das Modell (JSON-Klammern) — als nummerierte Marke senden, danach zurücksetzen
        def marken(t):
            n = iter(range(1, 99))
            return re.sub(r"\{\}", lambda m: "[V%d]" % next(n), t)
        # Nummeriert senden: das Modell übersetzt sonst gern auch die Schlüssel mit
        gesendet = {str(n): marken(de) for n, de in enumerate(stapel, 1)}
        for versuch in (1, 2):
            try:
                roh = json.loads(_ollama(modell, PROMPT % json.dumps(gesendet, ensure_ascii=False, indent=0)))
                antwort = {stapel[int(k) - 1]: re.sub(r"\[V\d+\]", "{}", v) if isinstance(v, str) else v
                           for k, v in roh.items() if str(k).isdigit() and 0 < int(k) <= len(stapel)}
            except Exception as e:
                print("  Stapel %d: Fehler %s (Versuch %d)" % (i // 25 + 1, str(e)[:80], versuch), flush=True)
                continue
            for de in stapel:
                en = antwort.get(de)
                if isinstance(en, str) and en.strip() and en.count("§") == de.count("§") and en.count("{}") == de.count("{}"):
                    neu[de] = en
            if len(neu) >= len(stapel) * 0.8:
                break
        wb.update(neu)
        begriffe(wb)
        os.makedirs(os.path.dirname(ZIEL), exist_ok=True)
        tmp = ZIEL + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(wb.items())), f, ensure_ascii=False, indent=0)
        os.replace(tmp, ZIEL)
        print("[%s] Stapel %d/%d: %d/%d übersetzt in %.0f s" % (time.strftime("%H:%M"), i // 25 + 1,
                                                               (len(offen) + 24) // 25, len(neu), len(stapel),
                                                               time.time() - t), flush=True)
    return pruefen()


def pruefen():
    wb, alle = woerterbuch(), extrahieren()
    fehlt = [s for s in alle if s not in wb]
    print("Abdeckung: %d von %d Stücken übersetzt (%.0f %%)" % (len(alle) - len(fehlt), len(alle),
                                                                 100.0 * (len(alle) - len(fehlt)) / max(1, len(alle))))
    for s in fehlt[:15]:
        print("  fehlt:", s[:100])
    return 0 if not fehlt else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("befehl", choices=("extrahieren", "uebersetzen", "pruefen"))
    ap.add_argument("--modell", default="qwen3.6-35b-a3b-text:ud-q3kxl")
    ap.add_argument("--stunden", type=float, default=8)
    a = ap.parse_args()
    if a.befehl == "extrahieren":
        s = extrahieren()
        print("%d Stücke" % len(s))
        for x in s[:40]:
            print(" ", x[:110])
        return 0
    if a.befehl == "uebersetzen":
        return uebersetzen(a.modell, a.stunden)
    return pruefen()


if __name__ == "__main__":
    sys.exit(main())
