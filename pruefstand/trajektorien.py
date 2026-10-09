#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trajektorien — aus gelungenen Agentenläufen werden Trainingsdaten.

Werkzeuggebrauch ist Verhalten: lesen, bevor man ändert; testen, bevor man
„fertig“ sagt; genau ein JSON-Objekt je Schritt. Genau das lernt ein kleines
Modell mit LoRA gut — aus Beispielen, in denen es richtig gemacht wurde.

Quellen:
  --stufe2 DATEI_ODER_ORDNER   Verläufe des Prüfstands (Dive on Wide `pruefstand/ergebnisse/*_verlaeufe`
                               oder der Referenz-Agent `~/llm/work/eval_stufe2/ergebnisse/*_protokolle`).
                               Zählen nur, wenn die VERSTECKTEN Tests bestanden sind.
  --dowos ORDNER               Werkbank-Läufe aus Dive on Wide (`storage/werkbank`). Dort gibt es keine
                               versteckten Tests — aufgenommen wird nur, was der Nutzer als gut
                               markiert hat (`"bewertung": "gut"` in der Datei), außer mit
                               --auch-unbewertet.

Aufbereitung:
  - Der System-Prompt wird durch den aktuellen der Werkbank ersetzt: Das Modell
    soll genau den Prompt lernen, mit dem Dive on Wide es später aufruft.
  - Unlesbare Antworten fliegen samt der Rückmeldung darauf heraus, lesbare
    werden als sauberes JSON geschrieben — gelernt wird das Format, das gilt,
    nicht das, das der Parser gerade noch retten konnte.

**Leckschutz.** Die 20 Prüfstand-Aufgaben sind die Messlatte. Wer auf ihnen
trainiert und dann auf ihnen misst, misst Auswendiglernen. Standard ist daher:
Trajektorien von Prüfstand-Aufgaben werden NICHT exportiert. Mit
--pruefstand-teil gerade|ungerade wird die Hälfte zum Training freigegeben;
die andere Hälfte steht dann in `zurueckgehalten.txt` und ist die einzige,
auf der danach gemessen werden darf.

Ausgabe (Format von mlx-lm und dem Trainings-Dashboard):
  ZIEL/train.jsonl, ZIEL/valid.jsonl   je Zeile {"messages": [...]}
  ZIEL/meta.jsonl                      Herkunft je Beispiel (Modell, Aufgabe, Quelle)
  ZIEL/bericht.md                      was aufgenommen und was warum verworfen wurde

    python3 pruefstand/trajektorien.py --stufe2 ~/llm/work/eval_stufe2/ergebnisse/basiswahl_protokolle \\
        --pruefstand-teil gerade --ziel ~/llm/work/daten/werkbank_v1
"""

import argparse
import collections
import hashlib
import json
import os
import re
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HIER))
import werkbank  # noqa: E402

AUFGABEN = os.path.join(HIER, "stufe2", "aufgaben")
PRUEFSTAND_IDS = sorted(d for d in os.listdir(AUFGABEN) if os.path.isdir(os.path.join(AUFGABEN, d))) \
    if os.path.isdir(AUFGABEN) else []


def aufgabe_nummer(aufgabe_id):
    m = re.match(r"^(\d+)_", aufgabe_id or "")
    return int(m.group(1)) if m else None


def freigegeben(aufgabe_id, teil):
    """Darf eine Prüfstand-Aufgabe ins Training? teil: None | 'gerade' | 'ungerade'."""
    if aufgabe_id not in PRUEFSTAND_IDS:
        return True
    n = aufgabe_nummer(aufgabe_id)
    if teil == "gerade":
        return n is not None and n % 2 == 0
    if teil == "ungerade":
        return n is not None and n % 2 == 1
    return False


# ----------------------------------------------------------------- Quellen ---

def _json_dateien(pfad):
    if os.path.isfile(pfad):
        yield pfad
        return
    for wurzel, _, dateien in os.walk(pfad):
        for d in sorted(dateien):
            if d.endswith(".json"):
                yield os.path.join(wurzel, d)


def stufe2_laeufe(pfad):
    """(Kennung, Nachrichten, Meta, Grund-zu-verwerfen-oder-None) aus Prüfstand-Verläufen.

    Versteht beide Formen: Dive on Wide (`ergebnis` direkt mit `nachrichten`) und den
    Referenz-Agenten (`{"ergebnis":…, "nachrichten":…}`)."""
    for datei in _json_dateien(pfad):
        try:
            with open(datei, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            yield datei, None, {}, "Datei nicht lesbar"
            continue
        ergebnis = d.get("ergebnis", d)
        nachrichten = d.get("nachrichten") or ergebnis.get("nachrichten")
        aufgabe = os.path.splitext(os.path.basename(datei))[0]
        modell = os.path.basename(os.path.dirname(datei))
        meta = {"quelle": "stufe2", "datei": datei, "aufgabe": aufgabe, "modell": modell,
                "schritte": ergebnis.get("schritte")}
        if not ergebnis.get("geloest"):
            grund = "versteckte Tests nicht bestanden"
        elif ergebnis.get("tests_veraendert"):
            grund = "vorhandene Tests verändert"
        elif not nachrichten:
            grund = "keine Nachrichten gespeichert"
        else:
            grund = None
        yield datei, nachrichten, meta, grund


def dowos_laeufe(pfad, auch_unbewertet=False):
    for datei in _json_dateien(pfad):
        try:
            with open(datei, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            yield datei, None, {}, "Datei nicht lesbar"
            continue
        ergebnis = d.get("ergebnis") or {}
        meta = {"quelle": "dowos", "datei": datei, "aufgabe": "dowos:" + hashlib.sha256(
                    str(d.get("aufgabe", "")).encode()).hexdigest()[:10],
                "modell": d.get("modell", "?"), "schritte": ergebnis.get("schritte")}
        if ergebnis.get("beendet") != "fertig":
            grund = "Lauf nicht abgeschlossen (%s)" % ergebnis.get("beendet")
        elif d.get("bewertung") == "schlecht":
            grund = "vom Nutzer als schlecht markiert"
        elif d.get("bewertung") != "gut" and not auch_unbewertet:
            grund = "nicht bewertet (ohne versteckte Tests zählt nur das Urteil des Nutzers)"
        else:
            grund = None
        yield datei, ergebnis.get("nachrichten"), meta, grund


# ------------------------------------------------------------ Aufbereitung ---

def aufbereiten(nachrichten):
    """Saubere Trainingsnachrichten, oder (None, Grund).

    - aktueller System-Prompt der Werkbank (Rechtestufe „projekt“)
    - unlesbare Antwort + Rückmeldung darauf entfernt
    - jede Antwort als kanonisches JSON
    - muss mit „fertig“ enden"""
    if len(nachrichten) < 4 or nachrichten[0].get("role") != "system":
        return None, "zu kurz oder ohne System-Prompt"
    system = werkbank.basis_prompt("projekt")
    aus = [{"role": "system", "content": system}, {"role": "user", "content": nachrichten[1]["content"]}]
    i = 2
    while i < len(nachrichten):
        n = nachrichten[i]
        if n.get("role") != "assistant":
            return None, "Reihenfolge der Rollen gestört"
        antwort = nachrichten[i + 1] if i + 1 < len(nachrichten) else None
        aktion = werkbank.json_lesen(n.get("content", ""))
        if not aktion or not aktion.get("werkzeug") or n.get("content", "").endswith("[gekürzt]"):
            if aktion is None and antwort and antwort["content"].startswith("Ergebnis: Ungültige"):
                i += 2                       # unlesbar: raus mitsamt Rückmeldung
                continue
            if n.get("content", "").endswith("[gekürzt]"):
                return None, "Verlauf wurde verdichtet — Antworten nicht mehr vollständig"
            return None, "Antwort ohne Werkzeug"
        if aktion["werkzeug"] in ("mcp", "skill", "merken", "erinnern", "websuche", "webseite"):
            return None, "nutzt %s (dessen Beschreibung fehlt im Trainings-Prompt)" % aktion["werkzeug"]
        if aktion["werkzeug"] == "frage" and antwort and "(Automatische Antwort des Prüfstands)" in antwort.get("content", ""):
            i += 2                           # unnötige Rückfrage: nicht zum Lernziel machen
            continue
        if aktion["werkzeug"] not in werkbank.WERKZEUGE:
            return None, "unbekanntes Werkzeug „%s“" % aktion["werkzeug"]
        kanonisch = {"gedanke": str(aktion.get("gedanke", "")), "werkzeug": aktion["werkzeug"],
                     "argumente": aktion.get("argumente") if isinstance(aktion.get("argumente"), dict) else {}}
        aus.append({"role": "assistant", "content": json.dumps(kanonisch, ensure_ascii=False)})
        if antwort is None:
            break
        if antwort.get("role") != "user":
            return None, "Reihenfolge der Rollen gestört"
        aus.append({"role": "user", "content": antwort["content"]})
        i += 2
    if aus[-1]["role"] != "assistant" or json.loads(aus[-1]["content"])["werkzeug"] != "fertig":
        return None, "endet nicht mit fertig"
    # Wurde „fertig“ zunächst abgelehnt (ungeprüft), bleibt die Mahnung drin —
    # das ist genau die Lektion. Aber ein Beispiel, das nur aus fertig besteht, lehrt nichts.
    if sum(1 for n in aus if n["role"] == "assistant") < 3:
        return None, "zu wenige Schritte, um etwas zu lernen"
    return aus, None


# ------------------------------------------------------------------ Export ---

ZEICHEN_JE_TOKEN = 2.7          # vorsichtig geschätzt: Deutsch und Code, eher mehr Token als weniger


def schritt_beispiele(sauber, max_token=4096):
    """Ein Trainingsbeispiel je Agentenschritt.

    mlx_lm lernt mit mask_prompt nur die LETZTE Antwort eines Gesprächs
    (nachgelesen in mlx_lm/tuner/datasets.py, ChatDataset.process). Ein ganzer
    Lauf als ein Beispiel brächte dem Modell also nur das abschließende „fertig“
    bei — nicht das Lesen, Ändern und Testen davor. Deshalb wird jeder Schritt
    ein eigenes Beispiel: Verlauf bis dahin, dann genau diese Antwort.

    Wird der Verlauf zu lang, wird er so verdichtet, wie die Werkbank es beim
    Arbeiten selbst tut — das Modell lernt mit dem Kontext, den es später auch
    sieht. Passt es dann immer noch nicht, fällt das Beispiel weg: Schneidet MLX
    ab, bleiben womöglich keine Antwort-Token übrig, und der Loss wird NaN."""
    budget = max_token * ZEICHEN_JE_TOKEN / 3.2          # in der Einheit von werkbank.geschaetzte_token
    aus, zu_lang = [], 0
    for k, n in enumerate(sauber):
        if n["role"] != "assistant":
            continue
        beispiel = [dict(m) for m in sauber[:k + 1]]
        werkbank.verdichten(beispiel, budget, frisch=1)
        if werkbank.geschaetzte_token(beispiel) > budget:
            zu_lang += 1
            continue
        aus.append(beispiel)
    return aus, zu_lang


def exportieren(quellen, ziel, teil=None, valid_anteil=0.1, max_token=4096):
    """quellen: Iterator von (datei, nachrichten, meta, grund). Gibt den Bericht zurück."""
    beispiele, verworfen, gesehen = [], collections.Counter(), set()
    for datei, nachrichten, meta, grund in quellen:
        if grund:
            verworfen[grund] += 1
            continue
        if not freigegeben(meta.get("aufgabe"), teil):
            verworfen["Prüfstand-Aufgabe (Leckschutz)"] += 1
            continue
        sauber, grund = aufbereiten(nachrichten)
        if grund:
            verworfen[grund] += 1
            continue
        fingerabdruck = hashlib.sha256(json.dumps(sauber[1:], ensure_ascii=False).encode()).hexdigest()
        if fingerabdruck in gesehen:
            verworfen["doppelt"] += 1
            continue
        gesehen.add(fingerabdruck)
        beispiele.append((sauber, meta))
    # Aufteilung nach AUFGABE, nicht nach Zeile: Sonst liegt dieselbe Aufgabe
    # (von einem anderen Modell gelöst) in train und valid, und der
    # Validierungsverlust sieht besser aus, als das Modell ist.
    aufgaben = sorted({m["aufgabe"] for _, m in beispiele})
    n_valid = max(1, round(len(aufgaben) * valid_anteil)) if len(aufgaben) > 1 else 0
    valid_aufgaben = set(sorted(aufgaben, key=lambda a: hashlib.sha256(a.encode()).hexdigest())[:n_valid])
    os.makedirs(ziel, exist_ok=True)
    zaehler = collections.Counter()
    zeichen = 0
    with open(os.path.join(ziel, "train.jsonl"), "w", encoding="utf-8") as tr, \
            open(os.path.join(ziel, "valid.jsonl"), "w", encoding="utf-8") as va, \
            open(os.path.join(ziel, "meta.jsonl"), "w", encoding="utf-8") as me:
        for sauber, meta in beispiele:
            teilname = "valid" if meta["aufgabe"] in valid_aufgaben else "train"
            schritte, zu_lang = schritt_beispiele(sauber, max_token)
            if zu_lang:
                verworfen["Schritt länger als %d Token" % max_token] += zu_lang
            for nr, beispiel in enumerate(schritte, 1):
                (va if teilname == "valid" else tr).write(json.dumps({"messages": beispiel}, ensure_ascii=False) + "\n")
                me.write(json.dumps(dict(meta, teil=teilname, schritt=nr), ensure_ascii=False) + "\n")
                zaehler[teilname] += 1
                zeichen += sum(len(m["content"]) for m in beispiel)
    zurueck = [a for a in PRUEFSTAND_IDS if not freigegeben(a, teil)]
    with open(os.path.join(ziel, "zurueckgehalten.txt"), "w", encoding="utf-8") as f:
        f.write("# Auf diesen Prüfstand-Aufgaben darf ein mit diesen Daten trainiertes Modell gemessen werden:\n")
        f.write("\n".join(zurueck) + "\n")
    je_modell = collections.Counter(m["modell"] for _, m in beispiele)
    L = ["# Trajektorien-Export\n",
         "- Gelöste Verläufe: **%d**" % len(beispiele),
         "- Beispiele (ein Schritt je Beispiel): **%d** (train %d · valid %d)" % (
             zaehler["train"] + zaehler["valid"], zaehler["train"], zaehler["valid"]),
         "- ungefähr %d Token, höchstens %d je Beispiel" % (zeichen / ZEICHEN_JE_TOKEN, max_token),
         "- Prüfstand-Teil im Training: %s" % (teil or "keiner (Standard)"),
         "- zurückgehaltene Prüfstand-Aufgaben: %d — nur auf diesen messen" % len(zurueck),
         "\n## Je Modell\n"] + ["- %s: %d" % (m, n) for m, n in je_modell.most_common()] + \
        ["\n## Verworfen\n"] + (["- %s: %d" % (g, n) for g, n in verworfen.most_common()] or ["- nichts"])
    bericht = "\n".join(L) + "\n"
    with open(os.path.join(ziel, "bericht.md"), "w", encoding="utf-8") as f:
        f.write(bericht)
    return {"verlaeufe": len(beispiele), "beispiele": zaehler["train"] + zaehler["valid"],
            "train": zaehler["train"], "valid": zaehler["valid"],
            "verworfen": dict(verworfen), "zurueckgehalten": zurueck, "bericht": bericht}


def main():
    ap = argparse.ArgumentParser(description="Agentenläufe als Trainingsdaten exportieren")
    ap.add_argument("--stufe2", action="append", default=[], help="Verläufe des Prüfstands (Datei oder Ordner)")
    ap.add_argument("--dowos", action="append", default=[], help="storage/werkbank einer Dive-on-Wide-Instanz")
    ap.add_argument("--auch-unbewertet", action="store_true",
                    help="Dive-on-Wide-Läufe auch ohne Nutzerurteil aufnehmen (nicht empfohlen)")
    ap.add_argument("--pruefstand-teil", choices=("gerade", "ungerade"),
                    help="diese Hälfte der Prüfstand-Aufgaben zum Training freigeben")
    ap.add_argument("--ziel", required=True)
    ap.add_argument("--valid-anteil", type=float, default=0.1)
    ap.add_argument("--max-token", type=int, default=4096, help="höchste Länge je Beispiel (= max_seq im Training)")
    a = ap.parse_args()
    if not a.stufe2 and not a.dowos:
        ap.error("mindestens eine Quelle angeben (--stufe2 oder --dowos)")

    def quellen():
        for p in a.stufe2:
            yield from stufe2_laeufe(os.path.expanduser(p))
        for p in a.dowos:
            yield from dowos_laeufe(os.path.expanduser(p), a.auch_unbewertet)
    erg = exportieren(quellen(), os.path.expanduser(a.ziel), a.pruefstand_teil, a.valid_anteil, a.max_token)
    print(erg["bericht"])
    if not erg["beispiele"]:
        print("Keine Beispiele — siehe „Verworfen“. Ohne --pruefstand-teil bleiben Prüfstand-Läufe draußen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
