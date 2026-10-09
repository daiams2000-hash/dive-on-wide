#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rezepte — ein Dokument beschreibt einen ganzen Trainingslauf.

Die Idee stammt aus dem Projekt „Soup“ (LLM-Feintuning über eine einzige
Konfigurationsdatei, `batch_size: auto`). Was Dive on Wide daraus macht:

* **Ein Rezept ist ein Dokument**, kein Formular. Es lässt sich versionieren,
  weitergeben und Monat später noch verstehen — und es beschreibt einen Lauf
  vollständig: Basismodell, Daten, Verfahren, Prüfung.
* **„auto“ heißt nicht „egal“.** Jede automatisch gesetzte Zahl kommt mit einem
  Satz, warum sie so ist — und, wo möglich, aus **gemessenen** Werten deiner
  eigenen früheren Läufe (Schritte je Sekunde, Spitzenspeicher aus den Logs),
  nicht aus Erfahrungswerten fremder Rechner.
* **Die Daten werden vorher untersucht.** Format erkennen, leere Antworten,
  Dubletten, zu lange Beispiele und — am wichtigsten — **Überschneidungen
  zwischen Training und Prüfteil** (sonst misst man Auswendiglernen).
  Ein Rezept mit undichten Daten startet nicht.
* **Ausgeführt wird über die vorhandene Trainings-Werkbank**, damit Lagebild,
  Frühstopp und die Regel „ein schwerer Auftrag zur Zeit“ weiter gelten.

Das Rezept ist JSON oder eine kleine, klar umrissene YAML-Teilmenge:

    name: Werkbank-Agent
    basis: qwen3-4b-2507-mlx-4bit
    daten:
      quelle: storage/training/daten/uebung_20260914_1950
      format: auto
      val_anteil: 0.1
      max_laenge: 4096
    training:
      epochen: 1
      lr: auto
      batch: auto
      lora:
        rang: auto
        schichten: auto
      geduld: 3

Aufruf:  python3 rezept.py pruefen mein.yaml | plan mein.yaml | starten mein.yaml
"""

import hashlib
import json
import os
import random
import re
import sys

HIER = os.path.dirname(os.path.abspath(__file__))

AUFGABEN = ("sft",)
# Bekannt, aber nicht gebaut — mit Grund statt stiller Näherung.
AUFGABEN_OFFEN = {
    "dpo": "Bevorzugungspaare (chosen/rejected) trainiert mlx-lm hier noch nicht",
    "orpo": "wie dpo",
    "pretrain": "freies Weitertrainieren ist kein Feintuning-Rezept",
}
FORMATE = ("chat", "sharegpt", "alpaca", "frage_antwort", "text")
# Andere Namen für dasselbe — wer „openai“ schreibt, meint das Nachrichtenformat.
FORMAT_ALIAS = {"chatml": "chat", "openai": "chat", "werkbank": "chat", "messages": "chat",
                "nachrichten": "chat", "instruct": "alpaca", "qa": "frage_antwort"}


class Fehler(ValueError):
    """Ein Rezept oder seine Daten sind nicht brauchbar — mit Begründung."""


# ------------------------------------------------------------------- YAML ---

_ZAHL = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")


def _wert(text, zeile_nr):
    t = text.strip()
    if not t:
        return ""
    if t[0] in "\"'":
        if len(t) < 2 or t[-1] != t[0]:
            raise Fehler("Zeile %d: Anführungszeichen nicht geschlossen" % zeile_nr)
        return t[1:-1]
    if t.startswith("[") and t.endswith("]"):
        inhalt = t[1:-1].strip()
        return [_wert(x, zeile_nr) for x in inhalt.split(",")] if inhalt else []
    klein = t.lower()
    if klein in ("true", "ja", "yes"):
        return True
    if klein in ("false", "nein", "no"):
        return False
    if klein in ("null", "none", "~"):
        return None
    if _ZAHL.match(t):
        return float(t) if ("." in t or "e" in klein) else int(t)
    return t


def yaml_lesen(text):
    """Eine bewusst kleine YAML-Teilmenge: Einrückung, `schlüssel: wert`, Listen mit `-`.

    Keine Anker, keine Blocktexte, kein Fluss-Stil außer [a, b]. Was nicht
    unterstützt wird, wird mit Zeilennummer abgelehnt — nie stillschweigend
    anders verstanden.
    """
    zeilen = []
    for nr, roh in enumerate(text.splitlines(), 1):
        ohne = roh.split(" #")[0].rstrip() if " #" in roh else roh.rstrip()
        if not ohne.strip() or ohne.strip().startswith("#"):
            continue
        vorne = ohne[:len(ohne) - len(ohne.lstrip())]
        if "\t" in vorne:
            raise Fehler("Zeile %d: Tabulator zum Einrücken — bitte Leerzeichen" % nr)
        zeilen.append((nr, len(vorne), ohne.strip()))

    def block(i, tiefe):
        """Liest alles ab Zeile i mit dieser Einrückung. → (wert, nächster Index)"""
        if i >= len(zeilen):
            return {}, i
        if zeilen[i][2].startswith("- "):
            aus = []
            while i < len(zeilen) and zeilen[i][1] == tiefe and zeilen[i][2].startswith("- "):
                nr, _t, inhalt = zeilen[i]
                rest = inhalt[2:].strip()
                i += 1
                # Tiefer eingerückte Folgezeilen gehören zu diesem Listenpunkt.
                kinder = []
                while i < len(zeilen) and zeilen[i][1] > tiefe:
                    kinder.append(zeilen[i])
                    i += 1
                if ":" in rest and rest.split(":", 1)[1].strip() or kinder:
                    # Ein Listenpunkt, der eine Abbildung ist: "- path: x" samt Kindern.
                    # Axolotl schreibt seine Datensätze genau so.
                    eintrag = {}
                    if ":" in rest:
                        k, _, v = rest.partition(":")
                        if v.strip():
                            eintrag[k.strip()] = _wert(v, nr)
                    elif rest:
                        eintrag["wert"] = _wert(rest, nr)
                    for n2, _t2, text2 in kinder:
                        if text2.startswith("- "):
                            raise Fehler("Zeile %d: Listen in Listen unterstützt Dive on Wide nicht" % n2)
                        if ":" not in text2:
                            raise Fehler("Zeile %d: „%s“ passt nicht in den Listenpunkt darüber"
                                         % (n2, text2[:40]))
                        k2, _, v2 = text2.partition(":")
                        eintrag[k2.strip()] = _wert(v2, n2)
                    aus.append(eintrag)
                elif rest.endswith(":"):
                    raise Fehler("Zeile %d: leerer Abschnitt in einer Liste" % nr)
                else:
                    aus.append(_wert(rest, nr))
            return aus, i
        aus = {}
        while i < len(zeilen) and zeilen[i][1] == tiefe:
            nr, _t, inhalt = zeilen[i]
            if inhalt.startswith("- "):
                raise Fehler("Zeile %d: Listenpunkt mitten in einem Abschnitt" % nr)
            if ":" not in inhalt:
                raise Fehler("Zeile %d: „%s“ ist weder „schlüssel: wert“ noch ein Listenpunkt"
                             % (nr, inhalt[:40]))
            schluessel, _, rest = inhalt.partition(":")
            schluessel, rest = schluessel.strip(), rest.strip()
            if rest in ("|", "|-", ">"):
                # Blocktext: alle folgenden, tiefer eingerückten Zeilen gehören dazu.
                zeilen_text, j = [], i + 1
                while j < len(zeilen) and zeilen[j][1] > tiefe:
                    zeilen_text.append(" " * (zeilen[j][1] - tiefe - 2) + zeilen[j][2])
                    j += 1
                aus[schluessel] = ("\n".join(zeilen_text) if rest.startswith("|")
                                   else " ".join(zeilen_text)).strip()
                i = j
                continue
            if rest:
                aus[schluessel] = _wert(rest, nr)
                i += 1
                continue
            if i + 1 < len(zeilen) and zeilen[i + 1][1] > tiefe:
                kind, i = block(i + 1, zeilen[i + 1][1])
                aus[schluessel] = kind
            else:
                aus[schluessel] = None
                i += 1
        return aus, i

    if not zeilen:
        return {}
    baum, weiter = block(0, zeilen[0][1])
    if weiter < len(zeilen):
        raise Fehler("Zeile %d: Einrückung passt zu nichts darüber" % zeilen[weiter][0])
    if not isinstance(baum, dict):
        raise Fehler("Ein Rezept beginnt mit Schlüsseln, nicht mit einer Liste")
    return baum


def rezept_laden(pfad):
    with open(pfad, encoding="utf-8") as f:
        text = f.read()
    if pfad.endswith(".json"):
        return json.loads(text)
    try:
        return json.loads(text)
    except ValueError:
        return yaml_lesen(text)


# ------------------------------------------------------------------ Daten ---

ROLLEN = {"system": "system", "user": "user", "human": "user", "mensch": "user", "prompter": "user",
          "assistant": "assistant", "gpt": "assistant", "bot": "assistant", "model": "assistant",
          "assistent": "assistant"}


def format_erkennen(beispiele):
    """(Format, Anteil, Begründung) aus einer Stichprobe."""
    zaehler = {}
    for b in beispiele:
        zaehler[_format_eines(b)] = zaehler.get(_format_eines(b), 0) + 1
    if not zaehler:
        raise Fehler("Keine lesbaren Beispiele gefunden.")
    name, anzahl = max(zaehler.items(), key=lambda kv: kv[1])
    anteil = anzahl / sum(zaehler.values())
    if name == "?":
        raise Fehler("Format nicht erkannt. Bekannt: %s. Gefundene Felder: %s"
                     % (", ".join(FORMATE), ", ".join(sorted({k for b in beispiele[:20] for k in b})[:12])))
    begruendung = "%d von %d Beispielen passen auf „%s“" % (anzahl, sum(zaehler.values()), name)
    if anteil < 1.0:
        begruendung += "; gemischte Datei (%s)" % ", ".join("%s: %d" % kv for kv in sorted(zaehler.items()))
    return name, anteil, begruendung


def _format_eines(b):
    if not isinstance(b, dict):
        return "?"
    if isinstance(b.get("messages"), list):
        return "chat"
    if isinstance(b.get("conversations"), list):
        return "sharegpt"
    if "instruction" in b and ("output" in b or "response" in b):
        return "alpaca"
    for frage, antwort in (("question", "answer"), ("frage", "antwort"), ("prompt", "completion"),
                           ("prompt", "response"), ("input", "output")):
        if frage in b and antwort in b:
            return "frage_antwort"
    if isinstance(b.get("text"), str):
        return "text"
    return "?"


def nach_nachrichten(b, format_name=None):
    """Ein Beispiel → [{"role","content"}] (oder {"text": …} bei reinem Text)."""
    art = format_name if format_name and format_name != "auto" else _format_eines(b)
    if art == "chat":
        aus = []
        for m in b["messages"]:
            if not isinstance(m, dict):
                raise Fehler("messages enthält etwas, das keine Nachricht ist")
            rolle = ROLLEN.get(str(m.get("role") or m.get("from") or "").lower())
            inhalt = m.get("content", m.get("value"))
            if rolle is None or not isinstance(inhalt, str):
                raise Fehler("Nachricht ohne brauchbare Rolle oder Inhalt (%r)" % (m.get("role"),))
            aus.append({"role": rolle, "content": inhalt})
        return aus
    if art == "sharegpt":
        aus = []
        for m in b["conversations"]:
            rolle = ROLLEN.get(str(m.get("from") or m.get("role") or "").lower())
            inhalt = m.get("value", m.get("content"))
            if rolle is None or not isinstance(inhalt, str):
                raise Fehler("conversations-Eintrag ohne brauchbare Rolle oder Inhalt")
            aus.append({"role": rolle, "content": inhalt})
        return aus
    if art == "alpaca":
        frage = str(b.get("instruction") or "")
        zusatz = str(b.get("input") or "")
        if zusatz.strip():
            frage = frage.rstrip() + "\n\n" + zusatz
        antwort = b.get("output", b.get("response"))
        aus = []
        if b.get("system"):
            aus.append({"role": "system", "content": str(b["system"])})
        aus.append({"role": "user", "content": frage})
        aus.append({"role": "assistant", "content": str(antwort or "")})
        return aus
    if art == "frage_antwort":
        for f, a in (("question", "answer"), ("frage", "antwort"), ("prompt", "completion"),
                     ("prompt", "response"), ("input", "output")):
            if f in b and a in b:
                return [{"role": "user", "content": str(b[f] or "")},
                        {"role": "assistant", "content": str(b[a] or "")}]
    if art == "text":
        return [{"role": "text", "content": str(b.get("text") or "")}]
    raise Fehler("Beispiel passt zu keinem bekannten Format")


def daten_lesen(quelle, grenze=None):
    """Datei oder Ordner (*.jsonl / *.json) → [(datei, zeile_nr, beispiel|None, fehlertext)]"""
    quelle = os.path.abspath(os.path.expanduser(quelle))
    dateien = []
    if os.path.isdir(quelle):
        for name in sorted(os.listdir(quelle)):
            if name.endswith((".jsonl", ".json")) and not name.startswith("._"):
                dateien.append(os.path.join(quelle, name))
    elif os.path.isfile(quelle):
        dateien = [quelle]
    if not dateien:
        raise Fehler("Keine .jsonl/.json-Datei unter %s" % quelle)
    aus = []
    for pfad in dateien:
        with open(pfad, encoding="utf-8", errors="replace") as f:
            inhalt = f.read()
        text = inhalt.strip()
        if pfad.endswith(".json") and text.startswith("["):
            try:
                for i, b in enumerate(json.loads(text), 1):
                    aus.append((pfad, i, b, None))
            except ValueError as e:
                aus.append((pfad, 0, None, "Datei ist kein gültiges JSON: %s" % e))
            continue
        for nr, zeile in enumerate(inhalt.splitlines(), 1):
            if not zeile.strip():
                continue
            try:
                aus.append((pfad, nr, json.loads(zeile), None))
            except ValueError as e:
                aus.append((pfad, nr, None, str(e)))
            if grenze and len(aus) >= grenze:
                return aus
    return aus


def _text_von(nachrichten):
    return "\n".join(m["content"] for m in nachrichten)


def _fingerabdruck(nachrichten):
    return hashlib.sha256(_text_von(nachrichten).encode("utf-8")).hexdigest()


def zeichen_schaetzer(zeichen_je_token=3.6):
    """Ohne Tokenizer: aus Zeichen schätzen — und das auch so nennen."""
    def zaehlen(text):
        return int(len(text) / zeichen_je_token) + 4
    zaehlen.art = "geschätzt (%.1f Zeichen je Token)" % zeichen_je_token
    return zaehlen


def tokenizer_zaehler(python, modell):
    """Genau zählen, wenn im Trainer-Python ein Tokenizer liegt. Sonst None."""
    if not python or not modell:
        return None
    pruefung = ("import sys;from transformers import AutoTokenizer;"
                "AutoTokenizer.from_pretrained(sys.argv[1]);print('ok')")
    import subprocess
    try:
        fertig = subprocess.run([python, "-c", pruefung, modell], capture_output=True, text=True, timeout=180)
        if fertig.returncode != 0 or "ok" not in fertig.stdout:
            return None
    except Exception:
        return None
    skript = ("import sys, json\n"
              "from transformers import AutoTokenizer\n"
              "t = AutoTokenizer.from_pretrained(sys.argv[1])\n"
              "for zeile in sys.stdin:\n"
              "    zeile = zeile.rstrip('\\n')\n"
              "    if not zeile: continue\n"
              "    print(len(t.encode(json.loads(zeile), add_special_tokens=False)), flush=True)\n")
    proc = subprocess.Popen([python, "-c", skript, modell], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def zaehlen(text):
        proc.stdin.write(json.dumps(text) + "\n")
        proc.stdin.flush()
        zeile = proc.stdout.readline()
        return int(zeile.strip() or 0)
    zaehlen.art = "genau (Tokenizer des Modells)"
    zaehlen.beenden = lambda: proc.kill()
    return zaehlen


# --------------------------------------------------------- der Daten-Doktor ---

def befund(eintraege, format_name="auto", max_laenge=4096, zaehler=None, zeige=5):
    """Was ist mit diesen Daten los? Zahlen, Probleme, Urteil — vor dem Training.

    Der teuerste Fehler beim Feintuning ist nicht eine falsche Lernrate, sondern
    ein Datensatz, der etwas anderes enthält, als man denkt. Deshalb wird hier
    gezählt statt gehofft.
    """
    zaehler = zaehler or zeichen_schaetzer()
    probleme = {}

    def merken(art, wo, rat):
        p = probleme.setdefault(art, {"art": art, "anzahl": 0, "stellen": [], "rat": rat})
        p["anzahl"] += 1
        if len(p["stellen"]) < zeige:
            p["stellen"].append(wo)

    gesehen = {}
    laengen = []
    brauchbar = []
    formate = {}
    for pfad, nr, beispiel, fehler in eintraege:
        wo = "%s:%s" % (os.path.basename(pfad), nr)
        if fehler:
            merken("unlesbare Zeile", wo, "Zeile prüfen oder entfernen — JSON je Zeile, keine Kommas am Ende")
            continue
        art = _format_eines(beispiel)
        formate[art] = formate.get(art, 0) + 1
        try:
            nachrichten = nach_nachrichten(beispiel, format_name)
        except Fehler as e:
            merken("unbekanntes Format", wo, str(e))
            continue
        if not nachrichten:
            merken("leeres Beispiel", wo, "entfernen")
            continue
        rollen = [m["role"] for m in nachrichten]
        if rollen != ["text"]:
            antworten = [m for m in nachrichten if m["role"] == "assistant"]
            if not antworten or not any(m["content"].strip() for m in antworten):
                merken("leere Antwort", wo, "ohne Antwort lernt das Modell nichts — entfernen")
                continue
            if "user" not in rollen:
                merken("keine Nutzerfrage", wo, "Beispiel braucht eine Frage, sonst lernt es nur Fortsetzen")
            if rollen and rollen[0] == "assistant":
                merken("Rollenfolge", wo, "beginnt mit der Antwort — Reihenfolge prüfen")
            for a, b in zip(rollen, rollen[1:]):
                if a == b == "assistant":
                    merken("Rollenfolge", wo, "zwei Antworten hintereinander")
                    break
            if len(antworten[-1]["content"].strip()) < 10:
                merken("sehr kurze Antwort", wo, "unter 10 Zeichen — meist ein Fehler beim Erzeugen")
        abdruck = _fingerabdruck(nachrichten)
        if abdruck in gesehen:
            merken("Dublette", "%s = %s" % (wo, gesehen[abdruck]),
                   "gleiche Beispiele mehrfach verschieben das Gewicht — vor dem Training entfernen")
            continue
        gesehen[abdruck] = wo
        laenge = zaehler(_text_von(nachrichten))
        laengen.append(laenge)
        if laenge > max_laenge:
            merken("zu lang", "%s (%d Token)" % (wo, laenge),
                   "wird beim Training abgeschnitten — max_laenge erhöhen oder Beispiel kürzen")
        brauchbar.append({"messages": nachrichten, "abdruck": abdruck, "laenge": laenge, "wo": wo})

    laengen.sort()

    def stelle(anteil):
        return laengen[min(len(laengen) - 1, int(len(laengen) * anteil))] if laengen else None

    gemischt = len([k for k, v in formate.items() if v]) > 1
    if gemischt:
        probleme["gemischtes Format"] = {
            "art": "gemischtes Format", "anzahl": sum(formate.values()), "stellen": [],
            "rat": "Die Datei enthält mehrere Formate (%s). Das geht, sollte aber Absicht sein."
                   % ", ".join("%s: %d" % kv for kv in sorted(formate.items()))}
    aus = {"gelesen": len(eintraege), "brauchbar": len(brauchbar),
           "formate": formate, "token_art": getattr(zaehler, "art", "unbekannt"),
           "laenge": {"min": laengen[0] if laengen else None, "median": stelle(0.5),
                      "p95": stelle(0.95), "max": laengen[-1] if laengen else None},
           "probleme": sorted(probleme.values(), key=lambda p: -p["anzahl"])}
    kaputt = sum(p["anzahl"] for p in probleme.values()
                 if p["art"] in ("unlesbare Zeile", "unbekanntes Format", "leere Antwort", "leeres Beispiel"))
    if len(brauchbar) < 10:
        aus["urteil"] = "nicht geeignet"
        aus["grund"] = "nur %d brauchbare Beispiele — damit lässt sich nichts messen" % len(brauchbar)
    elif len(eintraege) and kaputt / max(1, len(eintraege)) > 0.3:
        aus["urteil"] = "nicht geeignet"
        aus["grund"] = "%d von %d Zeilen unbrauchbar" % (kaputt, len(eintraege))
    elif probleme:
        aus["urteil"] = "mit Auflagen"
        aus["grund"] = "brauchbar, aber %d Auffälligkeiten — siehe Liste" % sum(p["anzahl"] for p in probleme.values())
    else:
        aus["urteil"] = "geeignet"
        aus["grund"] = None
    return aus, brauchbar


def leckage_pruefen(train, valid):
    """Beispiele, die in beiden Teilen stehen — der stillste Messfehler überhaupt."""
    a = {b["abdruck"] for b in train}
    doppelt = [b for b in valid if b["abdruck"] in a]
    return {"anzahl": len(doppelt), "anteil": round(len(doppelt) / max(1, len(valid)), 4),
            "beispiele": [b["wo"] for b in doppelt[:5]]}


def teilen(brauchbar, val_anteil=0.1, seed=17):
    """Aufteilen — fest am Inhalt, nicht am Zufall der Reihenfolge.

    Der Prüfteil wird über den Fingerabdruck bestimmt: Dasselbe Beispiel landet
    bei jedem Lauf auf derselben Seite. So verschiebt ein späterer Nachschlag
    keine alten Beispiele von Prüfen nach Trainieren (und umgekehrt).
    """
    if not 0.0 < val_anteil < 0.9:
        raise Fehler("val_anteil muss zwischen 0 und 0,9 liegen")
    grenze = int(val_anteil * (1 << 32))
    train, valid = [], []
    for b in brauchbar:
        wuerfel = int(hashlib.sha256(("%s|%d" % (b["abdruck"], seed)).encode()).hexdigest()[:8], 16)
        (valid if wuerfel < grenze else train).append(b)
    if not valid and brauchbar:                     # sehr kleine Datensätze
        valid = [train.pop()] if len(train) > 1 else []
    return train, valid


def daten_bauen(brauchbar, ziel, val_anteil=0.1, seed=17, bericht_text=""):
    """train.jsonl / valid.jsonl schreiben — das Format, das mlx-lm erwartet."""
    train, valid = teilen(brauchbar, val_anteil, seed)
    if len(train) < 5 or not valid:
        raise Fehler("Nach dem Aufteilen bleiben %d Trainings- und %d Prüfbeispiele — zu wenig."
                     % (len(train), len(valid)))
    os.makedirs(ziel, exist_ok=True)
    for name, teil in (("train", train), ("valid", valid)):
        with open(os.path.join(ziel, name + ".jsonl"), "w", encoding="utf-8") as f:
            for b in teil:
                if [m["role"] for m in b["messages"]] == ["text"]:
                    f.write(json.dumps({"text": b["messages"][0]["content"]}, ensure_ascii=False) + "\n")
                else:
                    f.write(json.dumps({"messages": b["messages"]}, ensure_ascii=False) + "\n")
    if bericht_text:
        with open(os.path.join(ziel, "bericht.md"), "w", encoding="utf-8") as f:
            f.write(bericht_text)
    return {"ordner": ziel, "train": len(train), "valid": len(valid),
            "leckage": leckage_pruefen(train, valid)}


# ------------------------------------------------------- Rezept normalisieren ---

VORGABE_REZEPT = {
    "name": "", "basis": "", "aufgabe": "sft",
    "daten": {"quelle": "", "fertig": "", "format": "auto", "val_anteil": 0.1,
              "max_laenge": "auto", "ziel": ""},
    "training": {"epochen": 1, "iters": "auto", "lr": "auto", "batch": "auto", "grad_accum": "auto",
                 "lora": {"rang": "auto", "schichten": "auto"}, "geduld": 3, "seed": 17},
}


def _zahl(wert, name, klein, gross, ganz=True):
    if wert in (None, "", "auto"):
        return "auto"
    try:
        z = int(wert) if ganz else float(wert)
    except (TypeError, ValueError):
        raise Fehler("%s muss eine Zahl sein (oder „auto“), nicht %r" % (name, wert))
    if not klein <= z <= gross:
        raise Fehler("%s muss zwischen %s und %s liegen, ist %s" % (name, klein, gross, z))
    return z


def pruefen(daten):
    """Ein Rezept normalisieren. Wirft Fehler mit klarem Grund."""
    if not isinstance(daten, dict):
        raise Fehler("Ein Rezept ist eine Zuordnung von Schlüsseln auf Werte.")
    unbekannt = [k for k in daten if k not in ("name", "basis", "aufgabe", "daten", "training", "pruefen",
                                               "ausgabe", "notiz")]
    if unbekannt:
        raise Fehler("Unbekannte Schlüssel: %s. Erlaubt: name, basis, aufgabe, daten, training, pruefen, "
                     "ausgabe, notiz" % ", ".join(unbekannt))
    aufgabe = str(daten.get("aufgabe") or "sft").lower()
    if aufgabe in AUFGABEN_OFFEN:
        raise Fehler("Aufgabe „%s“ ist nicht gebaut: %s" % (aufgabe, AUFGABEN_OFFEN[aufgabe]))
    if aufgabe not in AUFGABEN:
        raise Fehler("Aufgabe „%s“ kennt Dive on Wide nicht. Möglich: %s" % (aufgabe, ", ".join(AUFGABEN)))
    basis = str(daten.get("basis") or "").strip()
    if not basis:
        raise Fehler("„basis“ fehlt — welches Modell soll trainiert werden?")
    d = dict(VORGABE_REZEPT["daten"])
    d.update({k: v for k, v in (daten.get("daten") or {}).items() if v is not None})
    unbekannt = [k for k in d if k not in VORGABE_REZEPT["daten"]]
    if unbekannt:
        raise Fehler("Unbekannt unter „daten“: %s" % ", ".join(unbekannt))
    if not str(d["quelle"]).strip() and not str(d["fertig"]).strip():
        raise Fehler("Unter „daten“ fehlt „quelle“ (Rohdaten) oder „fertig“ (schon geteilter Datensatz).")
    d["format"] = FORMAT_ALIAS.get(str(d["format"]).lower(), str(d["format"]).lower())
    if d["format"] not in ("auto",) + FORMATE:
        raise Fehler("daten.format: auto, %s (auch: %s)"
                     % (", ".join(FORMATE), ", ".join(sorted(FORMAT_ALIAS))))
    t = dict(VORGABE_REZEPT["training"])
    t.update({k: v for k, v in (daten.get("training") or {}).items() if v is not None})
    lora = dict(VORGABE_REZEPT["training"]["lora"])
    lora.update({k: v for k, v in (t.get("lora") or {}).items() if v is not None})
    unbekannt = [k for k in t if k not in VORGABE_REZEPT["training"]]
    if unbekannt:
        raise Fehler("Unbekannt unter „training“: %s" % ", ".join(unbekannt))
    unbekannt = [k for k in lora if k not in ("rang", "schichten")]
    if unbekannt:
        raise Fehler("Unbekannt unter „training.lora“: %s" % ", ".join(unbekannt))
    pruefteil = dict(daten.get("pruefen") or {})
    unbekannt = [k for k in pruefteil if k not in ("pruefstand", "datenwert_seeds")]
    if unbekannt:
        raise Fehler("Unbekannt unter „pruefen“: %s" % ", ".join(unbekannt))
    return {
        "name": " ".join(str(daten.get("name") or os.path.basename(basis)).split())[:80],
        "basis": basis, "aufgabe": aufgabe, "notiz": str(daten.get("notiz") or "")[:2000],
        "daten": {"quelle": str(d["quelle"]).strip(), "fertig": str(d["fertig"]).strip(),
                  "format": d["format"], "val_anteil": float(_zahl(d["val_anteil"], "daten.val_anteil",
                                                                   0.01, 0.5, ganz=False) or 0.1)
                  if d["val_anteil"] != "auto" else 0.1,
                  "max_laenge": _zahl(d["max_laenge"], "daten.max_laenge", 256, 65536),
                  "ziel": str(d["ziel"]).strip()},
        "training": {"epochen": float(_zahl(t["epochen"], "training.epochen", 0.1, 100, ganz=False)),
                     "iters": _zahl(t["iters"], "training.iters", 1, 200000),
                     "lr": _zahl(t["lr"], "training.lr", 1e-7, 1e-2, ganz=False),
                     "batch": _zahl(t["batch"], "training.batch", 1, 64),
                     "grad_accum": _zahl(t["grad_accum"], "training.grad_accum", 1, 256),
                     "rang": _zahl(lora["rang"], "training.lora.rang", 1, 256),
                     "schichten": _zahl(lora["schichten"], "training.lora.schichten", 1, 200),
                     "geduld": int(_zahl(t["geduld"], "training.geduld", 0, 100) or 3),
                     "seed": int(_zahl(t["seed"], "training.seed", 0, 2 ** 31 - 1) or 17)},
        "pruefen": {"pruefstand": int(_zahl(pruefteil.get("pruefstand", 0), "pruefen.pruefstand", 0, 500) or 0),
                    "datenwert_seeds": int(_zahl(pruefteil.get("datenwert_seeds", 0),
                                                 "pruefen.datenwert_seeds", 0, 20) or 0)},
        "ausgabe": str(daten.get("ausgabe") or "").strip(),
    }


# ------------------------------------------------------------------ Automatik ---

ZEILE_TEMPO = re.compile(r"It/sec ([\d.]+).*?Tokens/sec ([\d.]+).*?Peak mem ([\d.]+) GB")


def tempo_aus_logs(log_ordner, modell=None, hoechstens=12):
    """Gemessenes Tempo aus den eigenen früheren Läufen — nicht aus Erfahrungswerten."""
    if not os.path.isdir(log_ordner):
        return None
    dateien = sorted((os.path.getmtime(os.path.join(log_ordner, n)), n)
                     for n in os.listdir(log_ordner) if n.startswith("training_") and n.endswith(".log"))
    it, tok, mem, quellen = [], [], [], []
    for _zeit, name in reversed(dateien[-hoechstens:]):
        pfad = os.path.join(log_ordner, name)
        try:
            with open(pfad, encoding="utf-8", errors="replace") as f:
                inhalt = f.read()
        except OSError:
            continue
        treffer = ZEILE_TEMPO.findall(inhalt)
        if not treffer:
            continue
        quellen.append(name)
        for a, b, c in treffer[-20:]:
            it.append(float(a))
            tok.append(float(b))
            mem.append(float(c))
    if not it:
        return None

    def mitte(werte):
        werte = sorted(werte)
        return werte[len(werte) // 2]
    return {"schritte_pro_s": round(mitte(it), 4), "token_pro_s": round(mitte(tok), 1),
            "spitze_gb": round(max(mem), 2), "laeufe": len(quellen), "quelle": quellen[:3]}


def werte_bestimmen(rezept, modell_info, anzahl_train, laenge_p95, tempo=None, speicher_gb=None):
    """Aus Rezept + Daten + Rechner die fehlenden Zahlen — jede mit Begründung."""
    t = dict(rezept["training"])
    gruende = []

    def setzen(name, wert, warum):
        t[name] = wert
        gruende.append({"wert": name, "gesetzt": wert, "warum": warum})

    modell_gb = (modell_info or {}).get("gb") or 0
    max_laenge = rezept["daten"]["max_laenge"]
    if max_laenge == "auto":
        ziel = max(512, min(8192, int((laenge_p95 or 1024) * 1.15 / 256 + 1) * 256))
        max_laenge = ziel
        gruende.append({"wert": "daten.max_laenge", "gesetzt": ziel,
                        "warum": "95 %% der Beispiele bleiben unter %d Token; 15 %% Luft dazu, auf 256 gerundet"
                                 % (laenge_p95 or 0)})
    if t["batch"] == "auto":
        # Was zählt, ist nicht die Zahl der Beispiele, sondern die Token je Schritt:
        # Speicher kostet der Kontext, nicht der Stapel für sich.
        ziel_token = 4096 if modell_gb >= 8 else 6144 if modell_gb >= 3 else 8192
        wert = int(max(1, min(8, ziel_token // max(1, max_laenge))))
        warum = ("Modell %.1f GB, %d Token je Beispiel → %d je Schritt (Ziel: ~%d Token je Schritt)"
                 % (modell_gb, max_laenge, wert, ziel_token))
        if tempo:
            warum += "; dein letzter Lauf brauchte dabei %.1f GB Spitze" % tempo["spitze_gb"]
        setzen("batch", wert, warum)
    if t["grad_accum"] == "auto":
        setzen("grad_accum", max(1, int(round(8 / t["batch"]))),
               "wirksame Stapelgröße ~8 Beispiele je Optimierungsschritt (ruhigere Verläufe bei kleinem Stapel)")
    if t["rang"] == "auto":
        if anzahl_train < 500:
            setzen("rang", 8, "%d Beispiele — ein großer Rang lernt sie eher auswendig" % anzahl_train)
        elif anzahl_train < 5000:
            setzen("rang", 16, "%d Beispiele — mittlerer Rang" % anzahl_train)
        else:
            setzen("rang", 32, "%d Beispiele tragen einen größeren Rang" % anzahl_train)
    if t["schichten"] == "auto":
        setzen("schichten", 16, "die obersten 16 Schichten — Standard für LoRA, spart Speicher und Zeit")
    if t["lr"] == "auto":
        wert = 1e-4 if t["rang"] <= 16 else 5e-5
        setzen("lr", wert, "Rang %d → %.0e (größerer Rang verträgt weniger Lernrate)" % (t["rang"], wert))
    if t["iters"] == "auto":
        pro_epoche = max(1, int(anzahl_train / max(1, t["batch"])))
        wert = max(20, int(pro_epoche * rezept["training"]["epochen"]))
        setzen("iters", wert, "%.1f Epochen à %d Schritte (%d Beispiele / Stapel %d); Frühstopp kann früher enden"
                              % (rezept["training"]["epochen"], pro_epoche, anzahl_train, t["batch"]))
    hinweise = []
    if tempo:
        dauer = t["iters"] / max(1e-6, tempo["schritte_pro_s"])
        hinweise.append("Aus deinen letzten %d Läufen: %.3f Schritte/s → etwa %.0f Minuten für %d Schritte "
                        "(Frühstopp meist früher)." % (tempo["laeufe"], tempo["schritte_pro_s"],
                                                       dauer / 60, t["iters"]))
        hinweise.append("Spitzenspeicher dort: %.1f GB.%s" % (
            tempo["spitze_gb"],
            "" if not speicher_gb else " Auf diesem Rechner nutzbar: %.1f GB." % speicher_gb))
    else:
        hinweise.append("Kein früherer Lauf mit Tempo-Zahlen gefunden — deshalb keine Dauer geschätzt "
                        "(nicht 0).")
    hinweise.append("Diese Zahlen sind Erfahrungswerte, keine Messung. Ob der Datensatz den Schüler wirklich "
                    "besser macht, zeigt erst der Datenwert-Test (mindestens 3 Seeds).")
    return {"werte": {"iters": t["iters"], "batch_size": t["batch"], "grad_accum": t["grad_accum"],
                      "lr": t["lr"], "rank": t["rang"], "num_layers": t["schichten"],
                      "max_seq": max_laenge, "geduld": t["geduld"], "seed": t["seed"]},
            "gruende": gruende, "hinweise": hinweise}


# ------------------------------------------------------------- Plan & Start ---

def vorbereiten(rezept, werkbank, zaehler=None, ziel_ordner=None, melden=None):
    """Modell finden, Daten lesen, untersuchen, Datensatz bauen — alles vor dem Training."""
    melden = melden or (lambda *a: None)
    gefunden = werkbank.finden()
    modelle = {os.path.basename(m["pfad"]): m for m in gefunden["modelle"]}
    modell = modelle.get(rezept["basis"])
    if modell is None:
        for m in gefunden["modelle"]:
            if os.path.abspath(m["pfad"]) == os.path.abspath(os.path.expanduser(rezept["basis"])):
                modell = m
                break
    if modell is None:
        raise Fehler("Basismodell „%s“ nicht gefunden. Vorhanden: %s"
                     % (rezept["basis"], ", ".join(sorted(modelle)) or "keins"))
    d = rezept["daten"]
    if d["fertig"]:
        ordner = os.path.abspath(os.path.expanduser(d["fertig"]))
        info = werkbank.datensatz_info(ordner)
        if not info:
            raise Fehler("„%s“ ist kein fertiger Datensatz (train.jsonl und valid.jsonl fehlen)." % ordner)
        train_e = daten_lesen(os.path.join(ordner, "train.jsonl"))
        valid_e = daten_lesen(os.path.join(ordner, "valid.jsonl"))
        b_train, brauchbar_train = befund(train_e, d["format"], _laenge(d), zaehler)
        b_valid, brauchbar_valid = befund(valid_e, d["format"], _laenge(d), zaehler)
        leck = leckage_pruefen(brauchbar_train, brauchbar_valid)
        return {"modell": modell, "datensatz": {"ordner": ordner, "train": info["train"],
                                                "valid": info["valid"], "leckage": leck},
                "befund": b_train, "befund_valid": b_valid, "gebaut": False,
                "brauchbar": brauchbar_train + brauchbar_valid}
    eintraege = daten_lesen(d["quelle"])
    melden("%d Zeilen gelesen aus %s" % (len(eintraege), d["quelle"]))
    b, brauchbar = befund(eintraege, d["format"], _laenge(d), zaehler)
    if b["urteil"] == "nicht geeignet":
        raise Fehler("Die Daten sind nicht brauchbar: %s" % b["grund"])
    ziel = ziel_ordner or d["ziel"] or os.path.join(werkbank.ordner, "daten", "rezept_" + _kennung(rezept))
    return {"modell": modell, "befund": b, "brauchbar": brauchbar, "gebaut": True, "ziel": ziel}


def _laenge(daten_teil):
    return 65536 if daten_teil["max_laenge"] == "auto" else int(daten_teil["max_laenge"])


def _kennung(rezept):
    roh = "%s|%s|%s" % (rezept["name"], rezept["basis"], rezept["daten"].get("quelle"))
    return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:8]


def plan(rezept, werkbank, zaehler=None, melden=None):
    """Was würde passieren? Ohne irgendetwas zu schreiben oder zu starten."""
    vor = vorbereiten(rezept, werkbank, zaehler, melden=melden)
    b = vor["befund"]
    anzahl = len(vor["brauchbar"]) if vor["gebaut"] else vor["datensatz"]["train"]
    tempo = tempo_aus_logs(os.path.join(werkbank.ordner, "logs"), rezept["basis"])
    speicher_gb = None
    try:
        import shutil  # noqa: F401
        speicher_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9, 1)
    except (ValueError, AttributeError, OSError):
        pass
    auto = werte_bestimmen(rezept, vor["modell"], anzahl, (b.get("laenge") or {}).get("p95"), tempo, speicher_gb)
    aus = {"rezept": rezept, "modell": vor["modell"], "befund": b, "automatik": auto,
           "tempo": tempo, "gebaut": vor["gebaut"]}
    if not vor["gebaut"]:
        aus["datensatz"] = vor["datensatz"]
    else:
        train, valid = teilen(vor["brauchbar"], rezept["daten"]["val_anteil"], rezept["training"]["seed"])
        aus["datensatz"] = {"ordner": vor["ziel"], "train": len(train), "valid": len(valid),
                            "leckage": {"anzahl": 0, "anteil": 0.0, "beispiele": [],
                                        "hinweis": "Aufteilung nach Fingerabdruck — Überschneidung ausgeschlossen"}}
    return aus


def bericht_text(plan_daten):
    r = plan_daten["rezept"]
    b = plan_daten["befund"]
    z = []
    z.append("# Rezept: %s\n" % r["name"])
    z.append("- Basis: `%s` (%s, %s)" % (r["basis"], plan_daten["modell"]["typ"],
                                         plan_daten["modell"]["quantisierung"]))
    z.append("- Daten: %s → %d Trainings-, %d Prüfbeispiele"
             % (r["daten"]["quelle"] or r["daten"]["fertig"], plan_daten["datensatz"]["train"],
                plan_daten["datensatz"]["valid"]))
    z.append("- Formate: %s · Längen (%s): Median %s, 95 %% %s, max %s"
             % (", ".join("%s: %d" % kv for kv in sorted(b.get("formate", {}).items())),
                b.get("token_art"), (b.get("laenge") or {}).get("median"),
                (b.get("laenge") or {}).get("p95"), (b.get("laenge") or {}).get("max")))
    z.append("- Urteil der Datenprüfung: **%s**%s" % (b["urteil"], " — " + b["grund"] if b.get("grund") else ""))
    if b.get("probleme"):
        z.append("\n## Auffälligkeiten")
        for p in b["probleme"]:
            z.append("- **%s** (%dx, z. B. %s): %s" % (p["art"], p["anzahl"], ", ".join(p["stellen"][:3]), p["rat"]))
    z.append("\n## Eingestellte Werte")
    for g in plan_daten["automatik"]["gruende"]:
        z.append("- `%s = %s` — %s" % (g["wert"], g["gesetzt"], g["warum"]))
    umbenannt = {"batch": "batch_size", "rang": "rank", "schichten": "num_layers",
                 "daten.max_laenge": "max_seq"}
    automatisch = {umbenannt.get(g["wert"], g["wert"]) for g in plan_daten["automatik"]["gruende"]}
    festgelegt = {k: v for k, v in plan_daten["automatik"]["werte"].items() if k not in automatisch}
    if festgelegt:
        z.append("- vom Rezept vorgegeben: %s" % ", ".join("`%s = %s`" % kv for kv in sorted(festgelegt.items())))
    z.append("\n## Hinweise")
    for h in plan_daten["automatik"]["hinweise"]:
        z.append("- %s" % h)
    if r["notiz"]:
        z.append("\n## Notiz\n%s" % r["notiz"])
    return "\n".join(z) + "\n"


def starten(rezept, werkbank, zaehler=None, melden=None):
    """Daten bauen (falls nötig) und das Training über die Trainings-Werkbank starten."""
    melden = melden or (lambda *a: None)
    p = plan(rezept, werkbank, zaehler, melden=melden)
    vor = vorbereiten(rezept, werkbank, zaehler)
    if vor["gebaut"]:
        info = daten_bauen(vor["brauchbar"], vor["ziel"], rezept["daten"]["val_anteil"],
                           rezept["training"]["seed"], bericht_text(p))
        melden("Datensatz gebaut: %s (%d/%d)" % (info["ordner"], info["train"], info["valid"]))
        daten_ordner = info["ordner"]
    else:
        daten_ordner = vor["datensatz"]["ordner"]
        leck = vor["datensatz"]["leckage"]
        if leck["anzahl"]:
            raise Fehler("%d von %d Prüfbeispielen stehen auch im Training (%s …). So misst der Val-Loss "
                         "Auswendiglernen. Datensatz neu aufteilen lassen (daten.quelle statt daten.fertig)."
                         % (leck["anzahl"], vor["datensatz"]["valid"], ", ".join(leck["beispiele"][:2])))
    lauf = werkbank.starten(vor["modell"]["pfad"], daten_ordner, name=rezept["name"],
                            werte=p["automatik"]["werte"])
    return {"lauf": lauf, "plan": p, "daten": daten_ordner}


# ---------------------------------------------------------------- Werkzeug ---

def einstellung(schluessel, vorgabe=""):
    """Eine Dive-on-Wide-Einstellung lesen, ohne den Server zu starten: erst die
    Datenbank, dann .env, dann die Umgebung."""
    datenbank = os.path.join(HIER, "storage", "dowos.db")
    if os.path.isfile(datenbank):
        try:
            import sqlite3
            conn = sqlite3.connect("file:%s?mode=ro" % datenbank, uri=True, timeout=5)
            try:
                zeile = conn.execute("SELECT value FROM settings WHERE key=?", (schluessel,)).fetchone()
            finally:
                conn.close()
            if zeile and str(zeile[0]).strip():
                return str(zeile[0])
        except Exception:
            pass
    pfad = os.path.join(HIER, ".env")
    if os.path.isfile(pfad):
        try:
            with open(pfad, encoding="utf-8") as f:
                for zeile in f:
                    zeile = zeile.strip()
                    if zeile.startswith("#") or "=" not in zeile:
                        continue
                    k, _, v = zeile.partition("=")
                    if k.strip() == schluessel:
                        return v.strip().strip('"').strip("'")
        except OSError:
            pass
    return os.environ.get(schluessel, vorgabe)


def werkbank_bauen(training_dir=None, suchorte=(), python=None):
    """Dieselbe Trainings-Werkbank wie die Oberfläche — damit Lagebild und
    „ein schwerer Auftrag zur Zeit“ auch für Rezepte gelten."""
    import training
    ordner = training_dir or os.path.join(HIER, "storage", "training")
    orte = [os.path.join(ordner, "modelle"), os.path.join(ordner, "daten")] + list(suchorte)
    orte += [os.path.expanduser(x.strip()) for x in einstellung("TRAINING_SUCHORTE", "").split(",") if x.strip()]
    python = python or (einstellung("TRAINING_PYTHON", "").strip() or None)
    for d in orte[:2]:
        os.makedirs(d, exist_ok=True)
    return training.Werkbank(ordner, orte, python=python)


def vorlage_lesen(name):
    pfad = os.path.join(HIER, "vorlagen", "rezepte", "%s.yaml" % re.sub(r"[^a-z0-9_-]", "", str(name).lower()))
    if not os.path.isfile(pfad):
        vorhanden = sorted(n[:-5] for n in os.listdir(os.path.join(HIER, "vorlagen", "rezepte"))
                           if n.endswith(".yaml"))
        raise Fehler("Vorlage „%s“ gibt es nicht. Vorhanden: %s" % (name, ", ".join(vorhanden)))
    with open(pfad, encoding="utf-8") as f:
        return f.read()


def vorlagen_liste():
    ordner = os.path.join(HIER, "vorlagen", "rezepte")
    aus = []
    for name in sorted(os.listdir(ordner)):
        if not name.endswith(".yaml"):
            continue
        with open(os.path.join(ordner, name), encoding="utf-8") as f:
            kopf = [z[1:].strip() for z in f.read().splitlines() if z.startswith("#")][:3]
        aus.append({"name": name[:-5], "beschreibung": " ".join(kopf[1:]) or (kopf[0] if kopf else "")})
    return aus


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--hilfe", "--help"):
        print(__doc__)
        return 0
    befehl = argv[0]
    if befehl == "vorlagen":
        for v in vorlagen_liste():
            print("%-12s %s" % (v["name"], v["beschreibung"][:90]))
        return 0
    if befehl == "vorlage":
        if len(argv) < 2:
            print("Welche Vorlage? " + ", ".join(v["name"] for v in vorlagen_liste()))
            return 2
        print(vorlage_lesen(argv[1]), end="")
        return 0
    if befehl not in ("pruefen", "plan", "starten") or len(argv) < 2:
        print(__doc__)
        return 2
    try:
        r = pruefen(rezept_laden(argv[1]))
    except (Fehler, OSError, ValueError) as e:
        print("Rezept nicht brauchbar: %s" % e)
        return 2
    wb = werkbank_bauen()
    zaehler = None
    try:
        if befehl == "pruefen":
            vor = vorbereiten(r, wb, zaehler, melden=print)
            b = vor["befund"]
            print("Modell:   %s (%s, %.1f GB)" % (vor["modell"]["name"], vor["modell"]["quantisierung"],
                                                  vor["modell"]["gb"]))
            print("Daten:    %d gelesen, %d brauchbar, Urteil: %s" % (b["gelesen"], b["brauchbar"], b["urteil"]))
            for p in b["probleme"]:
                print("  ⚠️ %-22s %5dx  z. B. %s\n     → %s"
                      % (p["art"], p["anzahl"], ", ".join(p["stellen"][:3]), p["rat"]))
            if not vor["gebaut"] and vor["datensatz"]["leckage"]["anzahl"]:
                print("  ⛔ %d Prüfbeispiele stehen auch im Training" % vor["datensatz"]["leckage"]["anzahl"])
            return 0
        p = plan(r, wb, zaehler, melden=print)
        print()
        print(bericht_text(p))
        if befehl == "starten":
            ergebnis = starten(r, wb, zaehler, melden=print)
            print("Training gestartet: %s" % ergebnis["lauf"].get("id"))
        return 0
    except Fehler as e:
        print("Abbruch: %s" % e)
        return 2
    finally:
        if zaehler is not None and hasattr(zaehler, "beenden"):
            zaehler.beenden()


if __name__ == "__main__":
    sys.exit(main())
