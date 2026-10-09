#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mesh-Inferenz: ein Knoten OHNE Modell lässt einen anderen wirklich denken.

`mesh_abnahme.py` belegt den Weg durchs Netz — aber mit einem Echo als
„Modell". Das lässt die eine Frage offen, um die es beim Netzwerk-Compute
geht: Trägt dieser Weg eine **echte** Antwort eines **echten** Sprachmodells?

Der Aufbau beantwortet das so, dass er nicht schummeln kann:

* Knoten **B** meldet die Modelle des laufenden Ollama und rechnet auch damit.
* Knoten **A** meldet **keine** Modelle. Was A zurückbekommt, kann A nicht
  selbst erzeugt haben.
* Gefragt wird etwas, das ein Echo nicht beantworten kann (eine Rechnung),
  und die Antwort wird auf das erwartete Ergebnis geprüft.

    python3 werkzeuge/mesh_inferenz.py [--modell NAME] [--sekunden 120]

Scheitert es an der Nachbarschaft, ist fast immer Multicast gesperrt; das
sagt die Meldung dann auch.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import mesh.knoten as knoten          # noqa: E402
import mesh.ressourcen as ressourcen  # noqa: E402
import mesh.transport as transport    # noqa: E402

OLLAMA = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
# Eine Frage, die ein Echo nicht bestehen kann: Die Antwort steht nicht drin.
FRAGE = ("Rechne: 17 mal 23. Antworte mit NICHTS ausser der Zahl, "
         "ohne Punkt, ohne Erklaerung.")
ERWARTET = "391"


def ollama(pfad, rumpf=None, frist=180):
    daten = json.dumps(rumpf).encode() if rumpf is not None else None
    req = urllib.request.Request(OLLAMA.rstrip("/") + pfad, data=daten,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=frist) as r:
        return json.loads(r.read().decode("utf-8"))


def modelle_von_ollama():
    return [m["name"] for m in ollama("/api/tags").get("models", [])]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--modell", default="", help="welches Modell B benutzt")
    ap.add_argument("--sekunden", type=int, default=120,
                    help="wie lange auf Nachbarschaft und Antwort gewartet wird")
    a = ap.parse_args(argv)

    try:
        vorhanden = modelle_von_ollama()
    except Exception as e:
        print("Ollama ist nicht erreichbar (%s) — ohne Modell kein Beweis." % e)
        return 1
    modell = a.modell or next((m for m in vorhanden if "0.5b" in m), None) or vorhanden[0]
    print("Ollama: %d Modelle, B rechnet mit %r" % (len(vorhanden), modell), flush=True)

    gerechnet = []

    def llm_von_b(m, prompt):
        gerechnet.append((m, prompt))
        antwort = ollama("/api/chat", {"model": m, "stream": False,
                                       "options": {"temperature": 0},
                                       "messages": [{"role": "user", "content": prompt}]})
        return (antwort.get("message") or {}).get("content", "")

    def starten(name, gb, modelle, llm=None):
        netz = transport.UdpNetz()
        k = knoten.Knoten("klause", netz=netz,
                          statthalter=ressourcen.Statthalter(zustimmung=True, hoechstens_gb=gb),
                          modelle=lambda: list(modelle), llm=llm)
        if not k.starten():
            raise SystemExit("%s startete nicht: %s" % (name, netz.fehler or "unbekannt"))
        print("  %s gestartet (%d Modelle)" % (name, len(modelle)), flush=True)
        return k

    # A hat bewusst NICHTS anzubieten. Alles, was zurueckkommt, kam von aussen.
    A = starten("A (fragt, ohne eigenes Modell)", 2.0, [])
    B = starten("B (rechnet mit Ollama)", 8.0, [modell], llm=llm_von_b)
    schritte = []
    try:
        for i in range(a.sekunden):
            time.sleep(1)
            if A.nachbarn and B.nachbarn:
                schritte.append("Nachbarschaft nach %d s" % (i + 1))
                break
        else:
            print("FEHLGESCHLAGEN: Die Knoten fanden sich in %d s nicht — "
                  "meist ist Multicast gesperrt." % a.sekunden)
            return 1

        # Der Katalog ist der Weg, ueber den ein Mensch in der Oberflaeche ein
        # fremdes Modell ueberhaupt auswaehlen kann. Steht es hier nicht, gibt
        # es das Netz-Modell fuer den Nutzer nicht — auch wenn es rechnet.
        karte = {name: len(e["knoten"]) for name, e in A.modell_karte().items()
                 if e["knoten"]}
        if modell not in karte:
            print("FEHLGESCHLAGEN: A rechnet zwar gleich, sieht %r aber nicht "
                  "im Katalog — in der Oberflaeche waere es unauffindbar." % modell)
            print("  A sieht: %s" % (", ".join(sorted(karte)) or "(nichts)"))
            return 1
        schritte.append("A findet %r im Netzkatalog (%d Geraet(e)) — so waehlt "
                        "es auch ein Mensch aus" % (modell, karte[modell]))

        t0 = time.time()
        try:
            auf_id = A.auftrag_verteilen(FRAGE, modell, hoechstens=2)
        except ValueError as e:
            print("FEHLGESCHLAGEN: A kennt das Modell von B nicht (%s)" % e)
            return 1
        schritte.append("Auftrag verteilt (%s)" % auf_id[:12])

        for i in range(a.sekunden):
            time.sleep(1)
            lage = A.auftrag_lage(auf_id) or {}
            if lage.get("ergebnisse"):
                erg = lage["ergebnisse"][0]
                text = (erg.get("text") or "").strip()
                dauer = time.time() - t0
                schritte.append("Antwort nach %.0f s von %s: %r"
                                % (dauer, (erg.get("von") or "?")[:12], text[:60]))
                if not gerechnet:
                    print("FEHLGESCHLAGEN: Antwort da, aber B hat nie gerechnet.")
                    return 1
                if ERWARTET not in text:
                    print("FEHLGESCHLAGEN: B hat gerechnet, aber die Antwort ist falsch.")
                    print("  erwartet %r, bekommen %r" % (ERWARTET, text[:200]))
                    print("  Das ist ein Modellfehler, kein Netzfehler — der Weg "
                          "durchs Netz hat getragen.")
                    return 2
                schritte.append("Inhalt geprueft: %s steht in der Antwort — "
                                "ein Echo haette das nicht gekonnt" % ERWARTET)
                break
        else:
            print("FEHLGESCHLAGEN: keine Antwort in %d s." % a.sekunden)
            return 1
    finally:
        for k in (A, B):
            try:
                k.stoppen()
            except Exception:
                pass

    print("\nBESTANDEN — echte Inferenz ueber das Mesh")
    for s in schritte:
        print("  · %s" % s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
