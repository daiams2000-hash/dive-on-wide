#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Welche Testgruppe besteht nur im Rudel?

    python3 werkzeuge/test_isolation.py      Rueckgabe 0: alle bestehen allein

`--nur <gruppe>` ist das Werkzeug, mit dem jemand an einer Stelle arbeitet.
Faellt eine Gruppe allein durch, waehrend der Gesamtlauf gruen ist, glaubt
dieser Jemand, etwas kaputt gemacht zu haben — und sucht an der falschen
Stelle. Das ist kein Schoenheitsfehler, das ist eine Falle.

Mit Fortschritt und harter Grenze.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BERICHT = os.path.join(tempfile.gettempdir(), "dowos-test-isolation.json")
START = time.time()
GRENZE = 45 * 60


def melden(t):
    print("[%5.0f s] %s" % (time.time() - START, t), flush=True)


def main():
    aus = subprocess.run([sys.executable, "tests/run_tests.py", "--liste"],
                         capture_output=True, cwd=APP)
    gruppen = re.findall(r"^\s+(\w+)\s+(\d+) Tests",
                         aus.stdout.decode("utf-8", "replace"), re.M)
    melden("%d Gruppen zu pruefen" % len(gruppen))
    ergebnis = []
    for nr, (g, anzahl) in enumerate(gruppen, 1):
        if time.time() - START > GRENZE:
            melden("Zeitgrenze erreicht bei Gruppe %d von %d" % (nr, len(gruppen)))
            break
        t0 = time.time()
        r = subprocess.run([sys.executable, "tests/run_tests.py", "--nur", g],
                           capture_output=True, cwd=APP,
                           timeout=900)
        text = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
        schlecht = re.findall(r"^\s+• \[(\w+)\] (.+)$", text, re.M)
        e = {"gruppe": g, "tests": int(anzahl), "code": r.returncode,
             "sekunden": round(time.time() - t0, 1),
             "fehlgeschlagen": [s[1][:90] for s in schlecht]}
        ergebnis.append(e)
        melden("%2d/%d %-14s %3s Tests %6.1fs  %s"
               % (nr, len(gruppen), g, anzahl, e["sekunden"],
                  "OK" if r.returncode == 0 else "FAELLT DURCH (%d)" % len(schlecht)))
        for f in e["fehlgeschlagen"][:4]:
            melden("        · %s" % f)
        with open(BERICHT, "w") as f:
            json.dump(ergebnis, f, ensure_ascii=False, indent=1)
    kaputt = [e for e in ergebnis if e["code"] != 0]
    print("\n=== Ergebnis ===")
    print("%d von %d Gruppen bestehen allein nicht." % (len(kaputt), len(ergebnis)))
    for e in kaputt:
        print("  %-14s %d Fehler: %s" % (e["gruppe"], len(e["fehlgeschlagen"]),
                                         "; ".join(e["fehlgeschlagen"][:3])))
    return 0 if not kaputt else 1


if __name__ == "__main__":
    sys.exit(main())
