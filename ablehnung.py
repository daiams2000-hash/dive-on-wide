#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ablehnungs-Stichprobe — Trainingsdaten, deren Richtigkeit ausgeführt wurde.

Bei den Sokoban-Versuchen war der Lehrer eine Breitensuche: Jedes Label war
bewiesen optimal, und das Ergebnis war entsprechend eindeutig (0 % → 80 %).
Bei Codeaufgaben gibt es keinen solchen Lehrer. Es gibt aber etwas fast so
Gutes: **die versteckten Tests**.

Also dreht man die Richtung um. Statt ein Modell zu fragen, was richtig wäre,
lässt man den Agenten dieselbe Aufgabe mehrfach lösen und **führt aus**, was
dabei herauskam. Nur Verläufe, deren Tests bestehen, werden Trainingsdaten.
Das ist das Label, das `destillation.py` seit Wochen kennt und das noch nie
befüllt wurde: GOLD_EXECUTION — nicht plausibel, sondern ausgeführt.

Drei Regeln, alle aus den Puzzle-Versuchen gelernt:

1. **Nur bestandene Verläufe.** Ein Verlauf, dessen Tests scheitern, ist kein
   Trainingsdatum, auch wenn er klug aussieht.
2. **Fehlerhafte Einzelschritte fliegen raus, die Erholung bleibt drin.** In
   einem bestandenen Verlauf ist der Schritt NACH einem Fehlgriff das
   Wertvollste, was man lernen kann — genau das fehlte den Puzzle-Daten der
   ersten Runde. Der Fehlgriff selbst wird nicht gelernt.
3. **Was geprüft wird, wird nicht trainiert.** Aufgaben des Prüfsatzes sind
   gesperrt; das Modul weigert sich, sie aufzunehmen.

    python3 ablehnung.py bericht <ordner>     Ausbeute einer Sammlung ansehen
"""

import json
import os
import re
import sys
import time

LABEL = "GOLD_EXECUTION"

# Ergebnisse, die einen Schritt als Fehlgriff ausweisen. Der Schritt davor wird
# nicht gelernt — der danach schon.
FEHLGRIFF = (
    "Ergebnis: Ungültige Antwort",
    "JSON nicht lesbar",
    "Kein JSON-Objekt gefunden",
    "abgelehnt",
    "nicht erlaubt",
    "Fehler:",
)


def _ist_fehlgriff(ergebnis):
    text = (ergebnis or "")[:400]
    return any(m.lower() in text.lower() for m in FEHLGRIFF)


def beispiele_aus_lauf(nachrichten, ergebnis_je_schritt=None, deckel=6000,
                       aufgabe=""):
    """Aus einem Verlauf Trainingsbeispiele schneiden: Vorgeschichte → Aktion.

    Jede Assistentennachricht ist eine Entscheidung; alles davor ist der
    Zusammenhang, in dem sie fiel. `ergebnis_je_schritt` sagt, was das Werkzeug
    danach geantwortet hat — daran wird erkannt, ob der Schritt ein Fehlgriff
    war. Fehlgriffe werden übersprungen, ihre Erholung nicht."""
    aus = []
    schritt = 0
    for i, m in enumerate(nachrichten):
        if m.get("role") != "assistant":
            continue
        schritt += 1
        antwort = m.get("content") or ""
        if not antwort.strip():
            continue
        folge = (ergebnis_je_schritt or {}).get(schritt)
        if folge is None and i + 1 < len(nachrichten):
            folge = nachrichten[i + 1].get("content") if \
                nachrichten[i + 1].get("role") == "user" else None
        if _ist_fehlgriff(folge):
            continue                      # der Fehlgriff selbst wird nicht gelernt
        vorher = []
        for v in nachrichten[:i]:
            inhalt = v.get("content") or ""
            if len(inhalt) > deckel:
                inhalt = inhalt[:deckel] + "\n… (gekürzt)"
            vorher.append({"role": v.get("role"), "content": inhalt})
        if len(vorher) < 2:
            continue                      # ohne Systemzeile und Aufgabe kein Beispiel
        beispiel = {"messages": vorher + [{"role": "assistant", "content": antwort}]}
        if aufgabe:
            # Herkunft je Beispiel. Ohne sie laesst sich aus einem fertigen
            # Datensatz kein Pruefsatz mehr herausschneiden — am 21.09.2026
            # fehlte genau das, und ein sauberer Rueckschnitt war unmoeglich.
            beispiel["aufgabe"] = aufgabe
        aus.append(beispiel)
    return aus


def sammeln(aufgaben, loese, versuche=4, gesperrt=(), melden=lambda *a: None,
            grenze_sekunden=None):
    """Jede Aufgabe mehrfach lösen lassen, nur Bestandenes behalten.

    `loese(aufgabe_id)` → dict mit „geloest" (bool) und „nachrichten" (Liste),
    also genau das, was `stufe2.loese` liefert. Injiziert, damit dieselbe Logik
    im Test ohne Modell läuft.

    Rückgabe: (beispiele, bericht). Der Bericht nennt Ausbeute je Aufgabe —
    eine Aufgabe, die nie besteht, ist ein Befund und kein Rauschen."""
    gesperrt = set(gesperrt)
    verboten = [a for a in aufgaben if a in gesperrt]
    if verboten:
        raise ValueError(
            "Diese Aufgaben stehen im Prüfsatz und dürfen nicht trainiert werden: %s"
            % ", ".join(sorted(verboten)[:5]))
    beispiele, bericht = [], {"aufgaben": {}, "versuche": 0, "bestanden": 0,
                              "beispiele": 0, "label": LABEL}
    start = time.time()
    for nr, aufgabe in enumerate(aufgaben, 1):
        je_aufgabe = {"versuche": 0, "bestanden": 0, "beispiele": 0}
        for versuch in range(versuche):
            if grenze_sekunden and time.time() - start > grenze_sekunden:
                melden("Zeitgrenze erreicht — Sammlung endet bei Aufgabe %d von %d"
                       % (nr, len(aufgaben)))
                bericht["abgebrochen"] = True
                bericht["aufgaben"][aufgabe] = je_aufgabe
                return beispiele, _abschluss(bericht, beispiele, start)
            je_aufgabe["versuche"] += 1
            bericht["versuche"] += 1
            try:
                e = loese(aufgabe)
            except Exception as fehler:
                melden("  %s Versuch %d: Fehler (%s)" % (aufgabe, versuch + 1, fehler))
                continue
            if not e.get("geloest"):
                continue
            je_aufgabe["bestanden"] += 1
            bericht["bestanden"] += 1
            neue = beispiele_aus_lauf(e.get("nachrichten") or [], aufgabe=aufgabe)
            beispiele += neue
            je_aufgabe["beispiele"] += len(neue)
        bericht["aufgaben"][aufgabe] = je_aufgabe
        melden("  %2d/%d %s: %d von %d bestanden, %d Beispiele"
               % (nr, len(aufgaben), aufgabe, je_aufgabe["bestanden"],
                  je_aufgabe["versuche"], je_aufgabe["beispiele"]))
    return beispiele, _abschluss(bericht, beispiele, start)


def _abschluss(bericht, beispiele, start):
    bericht["beispiele"] = len(beispiele)
    bericht["sekunden"] = time.time() - start
    bericht["quote"] = (bericht["bestanden"] / bericht["versuche"]
                        if bericht["versuche"] else None)
    bericht["ohne_erfolg"] = sorted(a for a, w in bericht["aufgaben"].items()
                                    if not w["bestanden"])
    return bericht


def schreiben(beispiele, bericht, ordner, anteil_valid=0.03):
    """train/valid ablegen, samt Herkunft — ohne Herkunft kein Label."""
    os.makedirs(ordner, exist_ok=True)
    schnitt = max(1, int(len(beispiele) * (1 - anteil_valid)))
    for name, teil in (("train", beispiele[:schnitt]), ("valid", beispiele[schnitt:])):
        with open(os.path.join(ordner, name + ".jsonl"), "w", encoding="utf-8") as f:
            for b in teil:
                f.write(json.dumps(b, ensure_ascii=False) + "\n")
    with open(os.path.join(ordner, "herkunft.json"), "w", encoding="utf-8") as f:
        json.dump({"quelle": "ablehnung.py (Ablehnungs-Stichprobe)",
                   "lehrer": "die versteckten Tests der Aufgabe — ausgefuehrt, nicht geschaetzt",
                   "label": LABEL, "training_erlaubt": True,
                   "versuche": bericht["versuche"], "bestanden": bericht["bestanden"],
                   "quote": bericht["quote"], "beispiele": len(beispiele),
                   "aufgaben_ohne_erfolg": bericht["ohne_erfolg"]},
                  f, ensure_ascii=False, indent=1)
    return {"train": schnitt, "valid": len(beispiele) - schnitt, "ordner": ordner}


def bericht_text(bericht):
    z = ["# Ablehnungs-Stichprobe", ""]
    z.append("Versuche: %d · bestanden: %d (%.0f %%) · Beispiele: %d"
             % (bericht["versuche"], bericht["bestanden"],
                100 * (bericht["quote"] or 0), bericht["beispiele"]))
    if bericht.get("ohne_erfolg"):
        z.append("")
        z.append("**Aufgaben ohne einen einzigen Erfolg (%d):** %s"
                 % (len(bericht["ohne_erfolg"]), ", ".join(bericht["ohne_erfolg"][:12])))
        z.append("")
        z.append("_Das ist ein Befund, kein Rauschen: Diese Aufgaben liegen über dem, "
                 "was das Modell derzeit kann — aus ihnen entsteht kein Trainingsdatum, "
                 "und sie gehören in den Prüfsatz, nicht in die Statistik des Erfolgs._")
    return "\n".join(z)


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) >= 2 and argv[0] == "bericht":
        pfad = os.path.join(argv[1], "herkunft.json")
        with open(pfad, encoding="utf-8") as f:
            h = json.load(f)
        print("Quelle: %s\nLehrer: %s\nLabel: %s" % (h["quelle"], h["lehrer"], h["label"]))
        print("Versuche %d · bestanden %d (%.0f %%) · Beispiele %d"
              % (h["versuche"], h["bestanden"], 100 * (h["quote"] or 0), h["beispiele"]))
        if h.get("aufgaben_ohne_erfolg"):
            print("Ohne Erfolg: %s" % ", ".join(h["aufgaben_ohne_erfolg"][:10]))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
