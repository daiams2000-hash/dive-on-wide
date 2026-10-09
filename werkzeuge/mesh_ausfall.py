#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mesh-Ausfall: Was passiert, wenn ein Knoten mitten im Auftrag verschwindet?

Die Abnahme nannte diesen Fall bis zum 23.09.2026 ausdruecklich als
ungeprueft — und es ist der Fall, der beim Online-Gang zuerst eintritt: ein
Laptop klappt zu, ein WLAN bricht weg, ein Prozess wird beendet.

Zwei Faelle, beide mit echten Knoten und echtem UDP:

1. **Mehrfachvergabe traegt.** B und C koennen beide rechnen, B stirbt direkt
   nach der Vergabe. Kommt trotzdem eine Antwort? Genau dafuer geht derselbe
   Auftrag an mehrere Knoten (ARCHITEKTUR.md 1.6).
2. **Einzelvergabe ist ehrlich.** Nur B kann rechnen, B stirbt. Es KANN keine
   Antwort geben — die Frage ist, ob der Auftrag das zeigt oder stumm
   „laeuft noch" behauptet, bis jemand aufgibt.

    python3 werkzeuge/mesh_ausfall.py [--sekunden 25]
"""
import argparse
import os
import sys
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import mesh.knoten as knoten          # noqa: E402
import mesh.ressourcen as ressourcen  # noqa: E402
import mesh.transport as transport    # noqa: E402

MODELL = "ausfall-modell"


def starten(name, gb, modelle, llm=None):
    netz = transport.UdpNetz()
    k = knoten.Knoten("klause", netz=netz,
                      statthalter=ressourcen.Statthalter(zustimmung=True, hoechstens_gb=gb),
                      modelle=lambda: list(modelle), llm=llm)
    if not k.starten():
        raise SystemExit("%s startete nicht: %s" % (name, netz.fehler or "unbekannt"))
    print("  %s gestartet" % name, flush=True)
    return k


def warte_nachbarn(knoten_liste, wieviele, sekunden):
    for i in range(sekunden):
        time.sleep(1)
        if all(len(k.nachbarn or {}) >= wieviele for k in knoten_liste):
            return i + 1
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sekunden", type=int, default=25)
    a = ap.parse_args(argv)
    befunde, maengel = [], []

    # ---------------------------------------------------------- Fall 1 ---
    print("Fall 1: B und C koennen rechnen, B stirbt sofort nach der Vergabe", flush=True)
    gerechnet = []

    def langsam(m, prompt):
        time.sleep(1.5)                   # damit B wirklich mitten im Auftrag stirbt
        gerechnet.append(("C", m))
        return "Antwort von C"

    def nie(m, prompt):
        time.sleep(30)                    # B antwortet nie — er stirbt vorher
        return "kommt nie"

    A = starten("A (verteilt)", 2.0, [])
    B = starten("B (stirbt)", 4.0, [MODELL], llm=nie)
    C = starten("C (rechnet)", 4.0, [MODELL], llm=langsam)
    try:
        s = warte_nachbarn([A], 2, a.sekunden)
        if s is None:
            print("FEHLGESCHLAGEN: A sieht nicht beide Knoten — meist ist Multicast gesperrt.")
            return 1
        befunde.append("A sieht beide Rechenknoten nach %d s" % s)
        auf = A.auftrag_verteilen("Rechne etwas", MODELL, hoechstens=3)
        lage = A.auftrag_lage(auf)
        befunde.append("Auftrag an %d Knoten vergeben" % lage["gefragt"])
        if lage["gefragt"] < 2:
            maengel.append("Der Auftrag ging nur an %d Knoten — die Mehrfachvergabe "
                           "greift nicht, obwohl zwei Knoten das Modell haben."
                           % lage["gefragt"])
        B.stoppen()
        befunde.append("B beendet, waehrend sein Auftrag offen ist")
        for i in range(a.sekunden):
            time.sleep(1)
            lage = A.auftrag_lage(auf) or {}
            if lage.get("ergebnisse"):
                befunde.append("Antwort nach %d s trotz ausgefallenem Knoten: %r"
                               % (i + 1, lage["ergebnisse"][0].get("text", "")[:40]))
                break
        else:
            maengel.append("Keine Antwort in %d s, obwohl C rechnen konnte — "
                           "ein ausgefallener Knoten blockiert den ganzen Auftrag."
                           % a.sekunden)
        lage = A.auftrag_lage(auf) or {}
        befunde.append("Stand: %d von %d offen, %d Ergebnis(se)"
                       % (lage.get("offen", -1), lage.get("gefragt", -1),
                          len(lage.get("ergebnisse") or [])))
    finally:
        for k in (A, B, C):
            try:
                k.stoppen()
            except Exception:
                pass

    # ---------------------------------------------------------- Fall 2 ---
    print("\nFall 2: nur B kann rechnen, B stirbt — bleibt der Auftrag stumm offen?",
          flush=True)
    A2 = starten("A2 (verteilt)", 2.0, [])
    B2 = starten("B2 (stirbt)", 4.0, [MODELL], llm=nie)
    try:
        if warte_nachbarn([A2], 1, a.sekunden) is None:
            print("FEHLGESCHLAGEN: A2 sieht B2 nicht.")
            return 1
        auf2 = A2.auftrag_verteilen("Rechne etwas", MODELL, hoechstens=3)
        B2.stoppen()
        # Der Ausfall kann nicht sofort auffallen: Ein Knoten gilt erst als weg,
        # wenn sein Lebenszeichen ausbleibt (NACHBAR_VERFALL). Gemessen wird
        # deshalb, WIE LANGE es dauert — eine behauptete Zahl waere wertlos.
        grenze = knoten.NACHBAR_VERFALL + 15
        erkannt, t0 = None, time.time()
        while time.time() - t0 < grenze:
            if (A2.auftrag_lage(auf2) or {}).get("vergeblich"):
                erkannt = time.time() - t0
                break
            time.sleep(1)
        lage2 = A2.auftrag_lage(auf2) or {}
        if erkannt is None:
            maengel.append(
                "Der Auftrag steht nach %.0f s immer noch offen, ohne jeden Hinweis: "
                "Es gibt kein Merkmal, an dem ein Aufrufer erkennt, dass niemand mehr "
                "antworten wird. Wer darauf wartet, wartet fuer immer." % grenze)
        else:
            befunde.append("Ausfall nach %.0f s als vergeblich erkannt "
                           "(Verfallsfrist %.0f s) — %d verstummt, %d Ergebnis(se)"
                           % (erkannt, knoten.NACHBAR_VERFALL,
                              lage2.get("verstummt", -1),
                              len(lage2.get("ergebnisse") or [])))
    finally:
        for k in (A2, B2):
            try:
                k.stoppen()
            except Exception:
                pass

    print("\n--- Befunde ---")
    for b in befunde:
        print("  · %s" % b)
    if maengel:
        print("\n--- Maengel ---")
        for m in maengel:
            print("  ! %s" % m)
        return 1
    print("\nBESTANDEN — ein ausgefallener Knoten haelt das Netz nicht auf")
    return 0


if __name__ == "__main__":
    sys.exit(main())
