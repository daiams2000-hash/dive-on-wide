#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mesh-Abnahme: zwei Knoten auf einer Maschine, echte Arbeit dazwischen.

Das Mesh hat 100 Tests — aber bis zum 22.09.2026 liefen nie zwei Knoten
gleichzeitig. Genau dort liegt der Unterschied zwischen „getestet" und
„belegt": Multicast, Nachbarschaft, Modellkatalog und Auftragsverteilung
bekommt man in Einzeltests nicht zu fassen.

Dieses Werkzeug startet zwei Knoten, lässt sie sich finden, verteilt einen
Auftrag von A nach B und prüft, dass B ihn wirklich gerechnet hat.

    python3 werkzeuge/mesh_abnahme.py [--sekunden 20]

Es braucht kein zweites Gerät und kein Modell — das „LLM" von B ist ein Echo,
geprüft wird der Weg durchs Netz.
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

MODELL = "abnahme-modell"


def knoten_starten(name, gb, llm=None):
    netz = transport.UdpNetz()
    statt = ressourcen.Statthalter(zustimmung=True, hoechstens_gb=gb)
    # Die Modellliste sind NAMEN. Ein Dict wird stillschweigend zu seinem
    # str() — dann findet kein anderer Knoten das Modell wieder.
    k = knoten.Knoten("klause", netz=netz, statthalter=statt,
                      modelle=lambda: [MODELL], llm=llm)
    if not k.starten():
        raise SystemExit("%s startete nicht: %s" % (name, netz.fehler or "unbekannt"))
    print("  %s gestartet" % name, flush=True)
    return k


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sekunden", type=int, default=20,
                    help="wie lange auf Nachbarschaft und Ergebnis gewartet wird")
    a = ap.parse_args(argv)

    gerechnet = []

    def llm_von_b(modell, prompt):       # Reihenfolge: Modell zuerst
        gerechnet.append((modell, prompt))
        return "Echo von B: " + prompt[:60]

    print("Mesh-Abnahme startet zwei Knoten …", flush=True)
    A = knoten_starten("A (verteilt)", 2.0)
    B = knoten_starten("B (rechnet)", 6.0, llm=llm_von_b)
    schritte = []
    try:
        # 1. Finden sie sich?
        for i in range(a.sekunden):
            time.sleep(1)
            na, nb = len(A.nachbarn or {}), len(B.nachbarn or {})
            if na and nb:
                schritte.append("Nachbarschaft nach %d s (A sieht %d, B sieht %d)" % (i + 1, na, nb))
                break
            if i and i % 5 == 0:
                print("  … %d s, A sieht %d, B sieht %d" % (i, na, nb), flush=True)
        else:
            print("FEHLGESCHLAGEN: Die Knoten haben sich in %d s nicht gefunden." % a.sekunden)
            print("Häufigster Grund: Multicast ist im Netz oder in der Firewall gesperrt.")
            return 1

        # 2. Kennt A das Modell von B?
        try:
            auf_id = A.auftrag_verteilen("Hallo aus der Abnahme", MODELL, hoechstens=2)
        except ValueError as e:
            print("FEHLGESCHLAGEN: %s" % e)
            return 1
        schritte.append("Auftrag verteilt (%s)" % auf_id[:12])

        # 3. Kommt ein Ergebnis zurück, und hat B wirklich gerechnet?
        for i in range(a.sekunden):
            time.sleep(1)
            lage = A.auftrag_lage(auf_id) or {}
            if lage.get("ergebnisse"):
                erg = lage["ergebnisse"][0]
                schritte.append("Ergebnis nach %d s von %s: %r"
                                % (i + 1, erg.get("von", "?")[:12], (erg.get("text") or "")[:50]))
                break
        else:
            print("FEHLGESCHLAGEN: kein Ergebnis in %d s." % a.sekunden)
            return 1
        if not gerechnet:
            print("FEHLGESCHLAGEN: Ergebnis da, aber B hat nie gerechnet — es kam von woanders.")
            return 1
        schritte.append("B hat gerechnet: Modell %r, Prompt %r"
                        % (gerechnet[0][0], gerechnet[0][1][:40]))
    finally:
        for k in (A, B):
            try:
                k.stoppen()
            except Exception:
                pass

    print("\nBESTANDEN")
    for s in schritte:
        print("  · %s" % s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
