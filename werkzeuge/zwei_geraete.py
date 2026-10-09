#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zwei-Geräte-Test: Ein Gerät rechnet, das andere fragt — über echtes Netz.

`mesh_inferenz.py` startet beide Knoten auf EINEM Rechner. Das belegt den Weg,
aber nicht das, worum es geht: zwei Geräte, echte Netzlatenz, ein echtes WLAN
mit seinen Eigenheiten. Hier spielt jedes Gerät seine eigene Rolle.

Auf Gerät B (hat ein Modell in Ollama):

    python3 werkzeuge/zwei_geraete.py rechnen

Auf Gerät A (im selben Netz):

    python3 werkzeuge/zwei_geraete.py fragen

Finden sich die beiden nicht von selbst — in Gäste- und Firmen-WLANs ist
Multicast oft gesperrt —, gibt Gerät B beim Start ein Ticket aus. Das kommt
dann auf Gerät A hinter `--ticket`:

    python3 werkzeuge/zwei_geraete.py fragen --ticket "<die Zeile von Gerät B>"

Am Ende druckt jede Seite einen Bericht. Den bitte vollständig zurückschicken.
"""
import argparse
import json
import os
import platform
import sys
import time
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import mesh.knoten as knoten          # noqa: E402
import mesh.ressourcen as ressourcen  # noqa: E402
import mesh.transport as transport    # noqa: E402

OLLAMA = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
FRAGE = ("Rechne: 17 mal 23. Antworte mit NICHTS ausser der Zahl, "
         "ohne Punkt, ohne Erklaerung.")
ERWARTET = "391"


def ollama(pfad, rumpf=None, frist=300):
    daten = json.dumps(rumpf).encode() if rumpf is not None else None
    req = urllib.request.Request(OLLAMA + pfad, data=daten,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=frist) as r:
        return json.loads(r.read().decode("utf-8"))


def knoten_starten(modelle, llm=None):
    netz = transport.UdpNetz()
    k = knoten.Knoten(knoten.KLAUSE, netz=netz,
                      statthalter=ressourcen.Statthalter(zustimmung=True, hoechstens_gb=8.0),
                      modelle=lambda: list(modelle), llm=llm)
    if not k.starten():
        raise SystemExit("Der Knoten startete nicht: %s\n"
                         "Haeufigste Ursache: eine Firewall blockiert UDP." % (netz.fehler or "unbekannt"))
    return k


def kopf(rolle):
    return ["=== Dive on Wide Zwei-Geraete-Test: %s ===" % rolle,
            "Zeit     : %s" % time.strftime("%d.%m.%Y %H:%M"),
            "System   : %s %s, Python %s" % (platform.system(), platform.release(),
                                            platform.python_version()),
            "Adressen : %s" % ", ".join(transport.eigene_adressen() or ["(keine)"])]


# --------------------------------------------------------------- rechnen ---
def rechnen(a):
    try:
        liste = ollama("/api/tags", frist=10).get("models", [])
        vorhanden = [m["name"] for m in liste]
    except Exception as e:
        raise SystemExit("Ollama ist auf diesem Geraet nicht erreichbar (%s). "
                         "Ohne Modell kann es nichts rechnen." % e)
    if not vorhanden:
        raise SystemExit("Ollama laeuft, hat aber kein Modell. Zum Beispiel: ollama pull qwen3:8b")
    # Nicht einfach das erste Modell der Liste: Das kann ein 0,5-B-Winzling sein,
    # der 17 mal 23 nicht rechnen kann — dann sieht ein funktionierendes Netz
    # wie ein kaputtes aus. Genommen wird das groesste, das noch die Haelfte des
    # Arbeitsspeichers frei laesst (dieselbe Regel wie in der Einrichtung).
    gesamt, _ = ressourcen.speicher()
    passend = [m for m in liste if m.get("size") and (not gesamt or m["size"] * 1.2 <= gesamt / 2)]
    vorschlag = max(passend, key=lambda m: m["size"])["name"] if passend else vorhanden[0]
    modell = a.modell or vorschlag
    if modell not in vorhanden:
        raise SystemExit("Modell %r gibt es hier nicht. Vorhanden: %s" % (modell, ", ".join(vorhanden)))
    auftraege = []

    def llm(m, prompt):
        t0 = time.time()
        antwort = ollama("/api/chat", {"model": m, "stream": False, "options": {"temperature": 0},
                                       "messages": [{"role": "user", "content": prompt}]})
        text = (antwort.get("message") or {}).get("content", "")
        auftraege.append({"modell": m, "sekunden": round(time.time() - t0, 1), "antwort": text[:40]})
        print("  Auftrag gerechnet: %s in %.1f s -> %r" % (m, time.time() - t0, text[:40]), flush=True)
        return text

    k = knoten_starten([modell], llm=llm)
    try:
        ticket = k.ticket_erzeugen()
    except Exception as e:
        ticket = "(kein Ticket: %s)" % e
    print("\n".join(kopf("rechnen")))
    print("Modell   : %s" % modell)
    print("\nFalls Geraet A dieses Geraet nicht von selbst findet, dort eingeben:\n")
    print('  python3 werkzeuge/zwei_geraete.py fragen --ticket "%s"\n' % ticket)
    print("Wartet %d Minuten auf Auftraege. Abbrechen mit Strg-C.\n" % a.minuten, flush=True)
    ende = time.time() + a.minuten * 60
    try:
        while time.time() < ende:
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        k.stoppen()
    print("\n=== Bericht rechnen ===")
    print("Nachbarn gesehen : %d" % len(k.nachbarn or {}))
    print("Auftraege        : %d" % len(auftraege))
    for x in auftraege:
        print("  %s" % json.dumps(x, ensure_ascii=False))
    print("Wegen Ueberlast abgesagt: %d" % getattr(k, "abgewiesen_ueberlast", 0))


# ---------------------------------------------------------------- fragen ---
def fragen(a):
    k = knoten_starten([])
    zeilen = kopf("fragen")
    try:
        gefunden, wie = None, ""
        for i in range(a.sekunden):
            time.sleep(1)
            if k.nachbarn:
                gefunden, wie = i + 1, "von selbst (Multicast)"
                break
            if a.ticket and i == 3:
                try:
                    k.ticket_einloesen(a.ticket)
                    print("Ticket eingeloest, warte auf Antwort ...", flush=True)
                except Exception as e:
                    zeilen.append("Ticket UNGUELTIG: %s" % e)
                    break
        if gefunden is None:
            zeilen.append("ERGEBNIS  : FEHLGESCHLAGEN — kein Nachbar in %d s." % a.sekunden)
            zeilen.append("Hinweis   : Laeuft auf Geraet B `zwei_geraete.py rechnen`? Ist Multicast "
                          "gesperrt, das Ticket von Geraet B mit --ticket angeben.")
            return 1
        if a.ticket:
            wie = "ueber Multicast oder das Ticket"
        zeilen.append("Nachbar   : gefunden nach %d s, %s" % (gefunden, wie))
        time.sleep(2)
        karte = {n: len(e["knoten"]) for n, e in k.modell_karte().items() if e["knoten"]}
        zeilen.append("Katalog   : %s" % (", ".join(sorted(karte)) or "(leer)"))
        if not karte:
            zeilen.append("ERGEBNIS  : FEHLGESCHLAGEN — Nachbar gefunden, aber er bietet kein Modell an.")
            return 1
        modell = a.modell if a.modell in karte else sorted(karte)[0]
        messungen = []
        for runde in range(a.runden):
            t0 = time.time()
            auf = k.auftrag_verteilen(FRAGE, modell, hoechstens=1)
            antwort = None
            while time.time() - t0 < 300:
                time.sleep(0.5)
                lage = k.auftrag_lage(auf) or {}
                if lage.get("ergebnisse"):
                    antwort = lage["ergebnisse"][0]
                    break
                if lage.get("vergeblich"):
                    break
            dauer = time.time() - t0
            text = ((antwort or {}).get("text") or (antwort or {}).get("fehler") or "").strip()
            messungen.append((dauer, text))
            print("  Runde %d: %.1f s -> %r" % (runde + 1, dauer, text[:50]), flush=True)
        richtig = sum(1 for _, t in messungen if ERWARTET in t)
        # Kam eine Antwort, ist der Weg ueber das Netz belegt — auch wenn sie
        # falsch ist. Das muss getrennt dastehen, sonst haelt man ein schwaches
        # Modell fuer ein kaputtes Netz.
        angekommen = sum(1 for _, t in messungen if t)
        zeilen.append("Modell    : %s (auf dem anderen Geraet)" % modell)
        zeilen.append("Runden    : %d, richtig %d" % (len(messungen), richtig))
        zeilen.append("Dauer     : %s s" % ", ".join("%.1f" % d for d, _ in messungen))
        zeilen.append("Netz      : %s" % ("TRAEGT — %d von %d Antworten kamen an" % (angekommen, len(messungen))
                                          if angekommen else "KEINE Antwort kam zurueck"))
        if richtig == len(messungen):
            urteil = "BESTANDEN — echte Inferenz ueber zwei Geraete"
        elif angekommen:
            urteil = ("NETZ BESTANDEN, MODELL NICHT — die Antworten kamen an, das Modell auf "
                      "Geraet B hat falsch gerechnet. Dort ein groesseres Modell waehlen: "
                      "zwei_geraete.py rechnen --modell <name>")
        else:
            urteil = "FEHLGESCHLAGEN — der Auftrag kam nicht zurueck"
        zeilen.append("ERGEBNIS  : %s" % urteil)
        return 0 if angekommen == len(messungen) else 1
    finally:
        k.stoppen()
        print("\n" + "\n".join(zeilen), flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    unter = ap.add_subparsers(dest="rolle", required=True)
    r = unter.add_parser("rechnen", help="dieses Geraet stellt sein Modell zur Verfuegung")
    r.add_argument("--modell", default="")
    r.add_argument("--minuten", type=int, default=15)
    f = unter.add_parser("fragen", help="dieses Geraet laesst das andere rechnen")
    f.add_argument("--ticket", default="")
    f.add_argument("--modell", default="")
    f.add_argument("--sekunden", type=int, default=60)
    f.add_argument("--runden", type=int, default=3)
    a = ap.parse_args(argv)
    return rechnen(a) if a.rolle == "rechnen" else fragen(a)


if __name__ == "__main__":
    sys.exit(main() or 0)
