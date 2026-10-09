#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DowBench — den Harness von Dive on Wide mit einem Modell durch alle Kategorien schicken.

    python3 werkzeuge/dowbench.py --modell qwen3.6-35b-a3b-text:ud-q3kxl
    python3 werkzeuge/dowbench.py --modell gemma4:12b --kategorie injektion,regeln
    python3 werkzeuge/dowbench.py --pruefen          # nur Selbstpruefung der Aufgaben, kein Modell

Jede Aufgabe laeuft als echter Werkbank-Lauf (Sandbox, Grundschutz, Regeln des
Besitzers, keine Freigaben, Temperatur 0). Ergebnisse je Aufgabe nach
storage/dowbench/<modell>.jsonl (Wiederaufnahme), Bericht daneben als .md.
Fortschritt nach jeder Aufgabe, harte Grenze --stunden (Standard 4).
Aufbau und Vorbilder: docs/DOWBENCH.md.
"""
import argparse
import json
import os
import re
import sys
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [APP, os.path.join(APP, "pruefstand"), os.path.join(APP, "pruefstand", "dowbench")]
import bank  # noqa: E402


def melden(t):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), t), flush=True)


def bericht(modell, zeilen, datei):
    z = ["# DowBench v1 — %s" % modell, "_Stand %s, %d Aufgaben_" % (time.strftime("%d.%m.%Y %H:%M"), len(zeilen)), "",
         "| Kategorie | Vorbild | Aufgaben | Nutzen | sicher | **bestanden** | Min je Aufgabe |", "|---|---|---|---|---|---|---|"]
    vorbild = {"code": "SWE-bench", "terminal": "Terminal-Bench", "injektion": "AgentDojo", "regeln": "tau-bench"}
    gesamt = [0, 0, 0, 0, 0.0]
    for kat in bank.KATEGORIEN:
        r = [x for x in zeilen if x["kategorie"] == kat]
        if not r:
            continue
        n, nutzen = len(r), sum(x["nutzen"] for x in r)
        sicher = sum(not x["verstoesse"] for x in r)
        best = sum(x["nutzen"] and not x["verstoesse"] for x in r)
        minuten = sum((x.get("sekunden") or 0) for x in r) / 60 / n
        for i, w in enumerate((n, nutzen, sicher, best, minuten * n)):
            gesamt[i] += w
        z.append("| %s | %s | %d | %d (%.0f %%) | %d | **%d (%.0f %%)** | %.1f |"
                 % (kat, vorbild[kat], n, nutzen, 100 * nutzen / n, sicher, best, 100 * best / n, minuten))
    if gesamt[0]:
        z.append("| **gesamt** | | %d | %d (%.0f %%) | %d | **%d (%.0f %%)** | %.1f |"
                 % (gesamt[0], gesamt[1], 100 * gesamt[1] / gesamt[0], gesamt[2], gesamt[3], 100 * gesamt[3] / gesamt[0],
                    gesamt[4] / gesamt[0]))
    verstoesse = [(x["aufgabe"], v) for x in zeilen for v in x["verstoesse"]]
    if verstoesse:
        z += ["", "## Sicherheitsverstoesse"] + ["- `%s`: %s" % (a, v) for a, v in verstoesse]
    open(datei, "w", encoding="utf-8").write("\n".join(z) + "\n")
    return "\n".join(z)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--modell", help="Ollama-Modell")
    ap.add_argument("--ollama", default=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    ap.add_argument("--openai", default="", help="statt Ollama ein OpenAI-kompatibler Server, z. B. llama-server "
                                                 "(http://127.0.0.1:8114) — für GGUF-Dateien, die unter Ollama nicht laufen")
    ap.add_argument("--kategorie", default=",".join(bank.KATEGORIEN))
    ap.add_argument("--stunden", type=float, default=4.0)
    ap.add_argument("--pruefen", action="store_true", help="nur die Selbstpruefung der Aufgaben")
    a = ap.parse_args()
    kategorien = tuple(k.strip() for k in a.kategorie.split(",") if k.strip())

    if a.pruefen:
        schlecht = 0
        for kennung, kat in bank.aufgaben(tuple(k for k in kategorien if k != "code")):
            r = bank.selbstpruefung(kennung)
            schlecht += not r["gueltig"]
            melden("%-26s %-9s %s" % (kennung, kat, "gueltig" if r["gueltig"] else "UNGUELTIG %s" % r))
        return 1 if schlecht else 0
    if not a.modell:
        ap.error("--modell fehlt")

    import stufe2
    chat = (stufe2.openai_chat(a.openai, a.modell, temperatur=0.0) if a.openai
            else stufe2.ollama_chat(a.ollama, a.modell, num_ctx=16384, temperatur=0.0))
    ablage = os.path.join(APP, "storage", "dowbench")
    os.makedirs(ablage, exist_ok=True)
    name = re.sub(r"[^\w.-]+", "_", a.modell)
    datei = os.path.join(ablage, name + ".jsonl")
    fertig = {json.loads(z)["aufgabe"]: json.loads(z) for z in open(datei)} if os.path.isfile(datei) else {}
    liste = bank.aufgaben(kategorien)
    melden("DowBench v1 mit %s: %d Aufgaben (%s), %d schon gemessen, Grenze %.1f h"
           % (a.modell, len(liste), ",".join(kategorien), len(fertig), a.stunden))
    ende = time.time() + a.stunden * 3600
    for nr, (kennung, kat) in enumerate(liste, 1):
        if kennung in fertig:
            continue
        if time.time() > ende:
            melden("Zeitgrenze erreicht — Bericht ist unvollstaendig")
            break
        t0 = time.time()
        try:
            r = bank.loese(chat, kennung)
        except Exception as e:
            r = {"nutzen": False, "verstoesse": [], "fehler": "%s: %s" % (type(e).__name__, str(e)[:200])}
        r.update(aufgabe=kennung, kategorie=kat, sekunden=r.get("sekunden") or round(time.time() - t0, 1))
        with open(datei, "a") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        fertig[kennung] = r
        melden("%2d/%d %-26s Nutzen %-5s %s%s" % (nr, len(liste), kennung, r["nutzen"],
                                                 "sicher" if not r["verstoesse"] else "VERSTOSS: %s" % r["verstoesse"][0],
                                                 (" · Fehler " + r["fehler"]) if r.get("fehler") else ""))
    zeilen = [fertig[k] for k, _ in liste if k in fertig]
    print("\n" + bericht(a.modell, zeilen, os.path.join(ablage, name + ".md")), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
