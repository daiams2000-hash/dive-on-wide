#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verteilte Inferenz: EIN Modell ueber MEHRERE Rechenknoten, auf einer Maschine.

`mesh_inferenz.py` belegt, dass ein Auftrag zu einem anderen Knoten wandert und
dort ganz gerechnet wird. Das ist noch keine verteilte Inferenz: Dabei wird ein
Modell in Schichten zerlegt, und jeder Knoten haelt nur einen Teil. Erst das
laesst ein Modell laufen, das auf keinem der Geraete allein Platz haette.

Zwei Geraete braucht es dafuer nicht, um den WEG zu pruefen: Zwei
Rechenknoten auf 127.0.0.1 sind fuer llama.cpp zwei Rueckseiten wie alle
anderen. Was hier nicht geprueft werden kann, ist echte Netzlatenz.

Geprueft wird bis zum Ende:

* Der Plan verteilt die Schichten wirklich auf mehr als einen Knoten.
* Der Hauptprozess antwortet mit einem echten Modellergebnis.
* Die Rechenknoten haben wirklich gearbeitet — sonst haette llama.cpp still
  alles selbst gerechnet, und die Probe waere wertlos.

Der Beleg dafuer steht in den Protokollen der RECHENKNOTEN, nicht in dem des
Hauptprozesses: `llama-server` schreibt die Rueckseiten-Adressen nirgends hin,
aber ein Rechenknoten ohne zugewiesene Schichten uebersetzt auch keine
Metal-Kernel fuer dieses Modell. Die erste Fassung dieser Probe suchte im
falschen Protokoll und meldete deshalb einen Fehlschlag, wo keiner war.

    python3 werkzeuge/mesh_verteilt_probe.py [--modell qwen2.5:0.5b]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
from mesh import verteilt as v   # noqa: E402

PORTS = (50252, 50253)
FUEHRER_PORT = 8771
FRAGE = "Nenne die Hauptstadt von Frankreich. Antworte mit EINEM Wort."
ERWARTET = "paris"


def melden(t):
    print("  %s" % t, flush=True)


def warten_bis(pruefung, sekunden, takt=1.0):
    ende = time.time() + sekunden
    while time.time() < ende:
        if pruefung():
            return True
        time.sleep(takt)
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--modell", default="qwen2.5:0.5b")
    ap.add_argument("--sekunden", type=int, default=120)
    a = ap.parse_args(argv)

    lage = v.rpc_lage()
    if not lage["kann_fuehren"] or not lage["rpc_server"]:
        print("llama.cpp mit RPC fehlt: %s" % (" ".join(lage["hinweise"]) or "unbekannt"))
        print("Bauen mit: werkzeuge/llamacpp_rpc_bauen.sh (dauert ~10 Minuten)")
        return 1
    pfad = v.modell_datei_finden(a.modell)
    if not pfad:
        print("Zu %r wurde keine .gguf-Datei gefunden." % a.modell)
        return 1
    masse = v.modell_masse_lesen(pfad)
    melden("Modell %s: %.2f GB, %d Schichten" % (a.modell, masse["gb"], masse["schichten"]))

    # Zwei Rechenknoten auf dieser Maschine. Fuer llama.cpp sind sie zwei
    # Rueckseiten wie jede andere auch.
    prozesse, protokolle = [], []
    # NICHT in storage/: Das ist der echte Speicher des Nutzers, und eine
    # Probe hat darin nichts abzulegen.
    ablage = tempfile.mkdtemp(prefix="dowos-verteilt-probe-")
    try:
        for port in PORTS:
            befehl = v.rechenknoten_aufruf(port=port)
            befehl[0] = v.rpc_programm() or befehl[0]
            log = os.path.join(ablage, "rpc-%d.log" % port)
            protokolle.append(log)
            prozesse.append(subprocess.Popen(befehl, stdout=open(log, "w"),
                                             stderr=subprocess.STDOUT))
            melden("Rechenknoten auf Port %d gestartet" % port)
        if not warten_bis(lambda: all(p.poll() is None for p in prozesse), 5, 1.0):
            melden("Ein Rechenknoten ist sofort gestorben — siehe %s" % ablage)
            return 1
        time.sleep(3)

        # Ein Plan ueber drei Knoten: der Hauptprozess und die beiden Rueckseiten.
        knoten = [{"id": "hier", "adresse": "127.0.0.1:0", "gb": 4.0}]
        knoten += [{"id": "rpc%d" % p, "adresse": "127.0.0.1:%d" % p, "gb": 4.0}
                   for p in PORTS]
        plan = v.plan_erstellen(masse["gb"], masse["schichten"], knoten)
        verteilung = ["%s: %d Schichten%s" % (k["id"], k["schichten"],
                                              " (fuehrt)" if k["fuehrt"] else "")
                      for k in plan["knoten"]]
        melden("Plan: " + " · ".join(verteilung))
        if len(plan["knoten"]) < 2:
            melden("FEHLGESCHLAGEN: Der Plan nutzt nur einen Knoten — nichts verteilt.")
            return 1

        befehl = v.aufruf_bauen(plan, pfad, port=FUEHRER_PORT, kontext=2048)
        befehl[0] = v.fuehrer_programm() or befehl[0]
        fuehrer_log = os.path.join(ablage, "fuehrer.log")
        melden("Hauptprozess: %s" % " ".join(befehl[:9]))
        fuehrer = subprocess.Popen(befehl, stdout=open(fuehrer_log, "w"),
                                   stderr=subprocess.STDOUT)
        prozesse.append(fuehrer)

        basis = "http://127.0.0.1:%d" % FUEHRER_PORT

        def lebt():
            try:
                urllib.request.urlopen(basis + "/health", timeout=3).read()
                return True
            except Exception:
                return False

        if not warten_bis(lebt, a.sekunden, 2.0):
            melden("FEHLGESCHLAGEN: Der Hauptprozess antwortet nicht. Protokoll:")
            print(open(fuehrer_log, encoding="utf-8", errors="replace").read()[-2500:])
            return 1
        melden("Hauptprozess antwortet")

        rumpf = json.dumps({"messages": [{"role": "user", "content": FRAGE}],
                            "temperature": 0, "max_tokens": 16}).encode()
        req = urllib.request.Request(basis + "/v1/chat/completions", data=rumpf,
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=120) as r:
            antwort = json.loads(r.read().decode("utf-8"))
        text = antwort["choices"][0]["message"]["content"].strip()
        melden("Antwort nach %.1f s: %r" % (time.time() - t0, text[:60]))

        # Haben die Rechenknoten wirklich gearbeitet? Ein Knoten, dem keine
        # Schicht zugewiesen wurde, laedt auch keine Metal-Kernel fuer dieses
        # Modell. Das ist der Beleg — nicht das Protokoll des Hauptprozesses.
        benutzt = 0
        for port, log in zip(PORTS, protokolle):
            # NICHT `text` nennen — das ist die Modellantwort. Beim ersten
            # Versuch ueberschrieb diese Schleife sie, und die Probe verglich
            # das Protokoll mit der erwarteten Antwort.
            knoten_log = open(log, encoding="utf-8", errors="replace").read()
            kernel = knoten_log.count("ggml_metal_library_compile_pipeline: loaded")
            verbunden = "accepted" in knoten_log.lower() or kernel > 0
            melden("Rechenknoten %d: %d Kernel uebersetzt%s"
                   % (port, kernel, "" if verbunden else " — offenbar unbeteiligt"))
            benutzt += 1 if kernel > 0 else 0

        print()
        if ERWARTET not in text.lower():
            print("FEHLGESCHLAGEN: Die Antwort ist falsch (%r statt %r)."
                  % (text[:80], ERWARTET))
            print("  Der Weg hat getragen — das ist ein Modellfehler, kein Netzfehler.")
            return 2
        if benutzt < len(PORTS):
            print("FEHLGESCHLAGEN: %d von %d Rechenknoten haben nichts getan."
                  % (len(PORTS) - benutzt, len(PORTS)))
            print("  Die Antwort koennte ganz hier gerechnet worden sein.")
            return 1
        print("BESTANDEN — ein Modell, ueber %d Knoten gespannt, hat geantwortet"
              % len(plan["knoten"]))
        print("  Schichten: %s" % " · ".join(verteilung))
        print("  Nicht geprueft: echte Netzlatenz. Alle Knoten liefen hier.")
        return 0
    finally:
        # Erst der Hauptprozess, dann die Rechenknoten. Andersherum verliert
        # llama.cpp mitten im Betrieb seine Rueckseiten und schreibt
        # "Remote RPC server crashed" samt Rueckverfolgung — das sieht aus wie
        # ein Fehler und ist doch nur das Ende. Genau diese Meldung hat beim
        # ersten Lauf dieser Probe eine halbe Stunde Suche gekostet.
        for p in reversed(prozesse):
            try:
                p.terminate()
                p.wait(timeout=8)
            except Exception:
                pass
        for p in prozesse:
            try:
                p.wait(timeout=8)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        melden("Alle Prozesse beendet — Protokolle in %s" % ablage)


if __name__ == "__main__":
    sys.exit(main())
