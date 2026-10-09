#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gemessene Werkbank-Guete in den Modellkatalog eintragen.

    python3 werkzeuge/guete_eintragen.py storage/datenwert/vergleich/ergebnisse.jsonl
    python3 werkzeuge/guete_eintragen.py --absturz MODELL "was geschah"

Uebernommen werden nur VOLLSTAENDIGE Messungen (art = "messung") von Modellen,
die unter ihrem Ollama-Namen im Katalog stehen. Abgebrochene Messungen bleiben
draussen: Eine halbe Messung ist kein Vergleich. Mit --absturz wird festgehalten,
dass ein Modell hier unter Last aus dem Speicher lief; der Katalog fuehrt es dann
als „passt nicht“, egal wie gross die Datei ist. Das Ziel ist
storage/modell_guete.json; der Orchestrator liest sie bei jedem Plan.
"""
import json
import os
import sys
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ziel(ziel):
    return ziel or os.path.join(os.environ.get("STORAGE_DIR") or os.path.join(APP, "storage"),
                                "modell_guete.json")


def _lesen(ziel):
    try:
        with open(ziel, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _schreiben(ziel, guete):
    tmp = ziel + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(guete, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ziel)


def absturz(modell, grund, ziel=None):
    """Haelt fest, dass `modell` hier unter Last aus dem Speicher lief."""
    ziel = _ziel(ziel)
    guete = _lesen(ziel)
    guete.setdefault(modell, {})["absturz"] = "%s: %s" % (time.strftime("%Y-%m-%d"), grund)
    _schreiben(ziel, guete)
    return ziel, guete


def eintragen(quelle, ziel=None):
    ziel = _ziel(ziel)
    guete = _lesen(ziel)
    neu = 0
    for zeile in open(quelle, encoding="utf-8"):
        try:
            d = json.loads(zeile)
        except ValueError:
            continue
        modell = d.get("modell") or ""
        if d.get("art") != "messung" or not modell or ("/" in modell or "\\" in modell) and not modell.startswith("hf.co"):
            continue                          # abgebrochen, oder ein Pfad statt Ollama-Name
        # Ein festgehaltener Absturz bleibt: Eine gelungene Messung auf anderem
        # Weg (etwa llama-server mit engeren Grenzen) hebt ihn nicht auf.
        guete[modell] = {k: v for k, v in (guete.get(modell) or {}).items() if k == "absturz"}
        guete[modell].update({"geloest": int(d["geloest"]), "aufgaben": int(d["n"]),
                         "min_je_aufgabe": round(float(d.get("minuten", 0)) / max(1, int(d["n"])), 2),
                         "stand": time.strftime("%Y-%m-%d", time.localtime(d.get("zeit", time.time()))),
                         "pruefung": "werkbank-%d" % int(d["n"])})
        neu += 1
    _schreiben(ziel, guete)
    return ziel, neu, guete


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--absturz":
        ziel, guete = absturz(sys.argv[2], sys.argv[3])
        print("Absturz festgehalten in %s: %s" % (ziel, guete[sys.argv[2]]["absturz"]))
        sys.exit(0)
    if len(sys.argv) != 2:
        print(__doc__); sys.exit(1)
    ziel, neu, guete = eintragen(sys.argv[1])
    print("%d Messung(en) eingetragen in %s" % (neu, ziel))
    for m, g in sorted(guete.items(), key=lambda kv: -kv[1].get("geloest", -1)):
        if "aufgaben" in g:
            print("  %-48s %2d/%d  %.1f min je Aufgabe" % (m, g["geloest"], g["aufgaben"], g["min_je_aufgabe"]))
        if g.get("absturz"):
            print("  %-48s ABSTURZ %s" % (m, g["absturz"]))
