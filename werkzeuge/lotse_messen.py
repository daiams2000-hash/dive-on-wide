#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Den Lotsen messen: dieselben Fragen an mehrere Modelle, automatisch bewertet.

    python3 werkzeuge/lotse_messen.py --modelle ollama@@qwen3.5-4b:q8,ollama@@gemma4:12b
    python3 werkzeuge/lotse_messen.py --basis http://127.0.0.1:3000 --stunden 2

Fragen: pruefstand/lotse_fragen.json (nur Messmaterial, nie Training). Bewertung ohne Modell:
- 'muss': jede Gruppe braucht einen Treffer,
- 'rechnung': kein Modell nennen, das laut Dive on Wides eigener Rechnung auf dem gedachten Rechner nicht passt,
  und mindestens eines nennen, das passt,
- 'ehrlich': die Antwort sagt, dass die Doku dazu nichts sagt (oder verneint), statt etwas zu erfinden.
Ergebnis je Modell nach storage/datenwert/lotse/<modell>.jsonl (Wiederaufnahme), Bericht daneben.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import hardware  # noqa: E402
import lotse  # noqa: E402

EHRLICH = re.compile(r"steht nichts|nicht in der doku|keine angabe|keine information|nicht dokumentiert|weiß ich nicht|"
                     r"kann (dow\.os )?(keine|nicht)|nicht möglich|not (in|covered|documented)|no information|"
                     r"gibt es (kein|nicht)|kostenlos|free|open source|quelloffen|MIT", re.I)


def melden(t):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), t), flush=True)


def bewerten(eintrag, antwort, version):
    a = antwort or ""
    klein = a.lower()
    gruende = []
    for gruppe in eintrag.get("muss", []):
        gruppe = [g.replace("VERSION", version) for g in gruppe]
        if not any(g.lower() in klein for g in gruppe):
            gruende.append("fehlt: " + " | ".join(gruppe))
    for verboten in eintrag.get("darf_nicht", []):
        if verboten.lower() in klein:
            gruende.append("erfunden: " + verboten)
    if eintrag.get("ehrlich") and not EHRLICH.search(a):
        gruende.append("nicht ehrlich: sagt nicht, dass es das nicht gibt / nicht dokumentiert ist")
    if eintrag.get("rechnung"):
        katalog = hardware.katalog_laden()
        passend, zu_gross = set(), set()
        for _, hw in lotse.gedachter_rechner(eintrag["frage"]):
            for m in katalog:
                s = hardware.stufe(int(m["groesse_gb"] * 1e9), hw)
                (passend if s in ("passt", "knapp") else zu_gross).add(m["name"])
        zu_gross -= passend
        genannt_zu_gross = [n for n in zu_gross if n.lower() in klein]
        if genannt_zu_gross:
            gruende.append("empfiehlt, was nicht passt: " + ", ".join(sorted(genannt_zu_gross)))
        if not any(n.lower() in klein for n in passend):
            gruende.append("nennt kein passendes Modell")
    return not gruende, gruende


def fragen_an(basis, frage, modell, frist=600):
    r = urllib.request.Request(basis + "/api/lotse", data=json.dumps({"frage": frage, "modell": modell}).encode(),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=frist) as a:
        return json.load(a)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelle", default="ollama@@qwen3.5-4b:q8,ollama@@gemma4:12b,ollama@@qwen3.6-35b-a3b-text:ud-q3kxl")
    ap.add_argument("--basis", default="http://127.0.0.1:3000")
    ap.add_argument("--stunden", type=float, default=2)
    a = ap.parse_args()
    ende = time.time() + a.stunden * 3600
    fragen = json.load(open(os.path.join(APP, "pruefstand", "lotse_fragen.json"), encoding="utf-8"))["fragen"]
    version = json.load(urllib.request.urlopen(a.basis + "/api/health", timeout=20)).get("version", "?")
    ablage = os.path.join(APP, "storage", "datenwert", "lotse")
    os.makedirs(ablage, exist_ok=True)
    zeilen = ["# Lotse — Messung %s (Dive on Wide %s, %d Fragen)" % (time.strftime("%d.%m.%Y %H:%M"), version, len(fragen)), "",
              "| Modell | bestanden | s je Frage |", "|---|---|---|"]
    for modell in [m.strip() for m in a.modelle.split(",") if m.strip()]:
        datei = os.path.join(ablage, re.sub(r"[^\w.-]+", "_", modell.split("@@")[-1]) + ".jsonl")
        fertig = {}
        if os.path.isfile(datei):
            for z in open(datei, encoding="utf-8"):
                d = json.loads(z)
                fertig[d["frage"]] = d
        melden("Modell %s: %d Fragen, %d schon gemessen" % (modell, len(fragen), len(fertig)))
        fehler_folge = 0
        for i, e in enumerate(fragen, 1):
            if e["frage"] in fertig:
                continue
            if time.time() > ende:
                melden("Zeitgrenze — Rest bleibt offen (Wiederaufnahme möglich)")
                break
            t = time.time()
            try:
                d = fragen_an(a.basis, e["frage"], modell)
                antwort, fehler = d.get("antwort", ""), d.get("error")
            except Exception as x:
                antwort, fehler = "", str(x)[:200]
            ok, gruende = bewerten(e, antwort, version) if not fehler else (False, ["Fehler: %s" % fehler])
            fehler_folge = (fehler_folge + 1) if fehler else 0
            if fehler_folge >= 3:
                # Kleine-Hardware-Test 05.10.2026: Ollama war abgestürzt, alle 26 Fragen „scheiterten“ in 0 s
                melden("Drei Fehler in Folge (%s) — Messung für dieses Modell abgebrochen, nicht gewertet" % fehler)
                break
            zeile = {"frage": e["frage"], "ok": ok, "gruende": gruende, "sekunden": round(time.time() - t, 1),
                     "antwort": antwort[:3000]}
            if fehler:
                melden("  %2d/%d Fehler (nicht gewertet): %s" % (i, len(fragen), fehler))
                continue
            fertig[e["frage"]] = zeile
            with open(datei, "a", encoding="utf-8") as f:
                f.write(json.dumps(zeile, ensure_ascii=False) + "\n")
            melden("  %2d/%d %s %s%s" % (i, len(fragen), "✓" if ok else "✗", e["frage"][:60],
                                         "" if ok else "  — " + "; ".join(gruende)[:120]))
        gemessen = [fertig[e["frage"]] for e in fragen if e["frage"] in fertig]
        if gemessen:
            n_ok = sum(1 for g in gemessen if g["ok"])
            zeilen.append("| %s | %d/%d | %.0f |" % (modell.split("@@")[-1], n_ok, len(gemessen),
                                                     sum(g["sekunden"] for g in gemessen) / len(gemessen)))
    with open(os.path.join(ablage, "bericht.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(zeilen) + "\n")
    print("\n".join(zeilen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
