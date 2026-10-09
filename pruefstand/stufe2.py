#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prüfstand Stufe 2 — löst der Werkbank-Agent echte kleine Projekte?

20 Mini-Projekte (4 leicht, 11 mittel, 5 schwer): Fehler beheben, Funktionen
ergänzen, ein Umbau über drei Module, ein Leistungsproblem. Bei 5 Aufgaben steht
der Fehler nur im Issue, die sichtbaren Tests sind grün. Bewertet wird mit
**versteckten Tests**, die der Agent nie sieht.

Die Aufgaben stammen aus `~/llm/work/eval_stufe2` (dort erzeugt von `bau.py`).
Hier laufen sie gegen den Agenten, den Dive on Wide wirklich benutzt — `werkbank.py`
mit Sandbox, Rechten und Kontextverdichtung. Jede Änderung am Agenten bleibt so
messbar.

    python3 pruefstand/stufe2.py --orakel                    # Bewertung selbst prüfen, ohne Modell
    python3 pruefstand/stufe2.py --modelle gemma4-base       # alle 20 Aufgaben
    python3 pruefstand/stufe2.py --modelle a,b --aufgaben 01,02 --lauf vergleich

Ergebnisse (JSONL, Bericht, vollständige Verläufe) landen in
`pruefstand/ergebnisse/` — erfolgreiche Verläufe sind später Trainingsdaten.
Ein unterbrochener Lauf setzt mit demselben `--lauf` dort fort, wo er stand.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HIER))
import werkbank  # noqa: E402

AUFGABEN = os.path.join(HIER, "stufe2", "aufgaben")
ERGEBNISSE = os.path.join(HIER, "ergebnisse")
TESTBEFEHL = "python -m unittest discover -s tests -t ."


def aufgaben_ids(filter_=""):
    # Nur Ordner mit ISSUE.md sind Aufgaben — die Fabrik legt daneben _verworfen/ ab.
    ids = sorted(d for d in os.listdir(AUFGABEN) if os.path.isfile(os.path.join(AUFGABEN, d, "ISSUE.md")))
    if filter_:
        ids = [i for i in ids if any(i.startswith(x.strip()) for x in filter_.split(","))]
    return ids


def meta():
    with open(os.path.join(AUFGABEN, "meta.json"), encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ Modell ---

def ollama_chat(basis, modell, num_ctx=16384, temperatur=0.2):
    """chat(nachrichten) für die Werkbank — wie im Referenz-Agenten: Temperatur 0,2, ohne Denken."""
    zustand = {"think": False}

    def chat(nachrichten):
        nutzlast = {"model": modell, "messages": nachrichten, "stream": False,
                    "options": {"num_ctx": num_ctx, "temperature": temperatur}}
        if zustand["think"] is not None:
            nutzlast["think"] = zustand["think"]
        req = urllib.request.Request(basis.rstrip("/") + "/api/chat", data=json.dumps(nutzlast).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                d = json.load(r)
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            if zustand["think"] is not None and "think" in text.lower():
                zustand["think"] = None           # Modell kennt den Schalter nicht
                return chat(nachrichten)
            raise RuntimeError("Ollama: %s %s" % (e.code, text[:200]))
        chat.tokens += d.get("eval_count", 0)
        return d.get("message", {}).get("content", "")
    chat.tokens = 0
    return chat


def openai_chat(basis, modell, api_key="", temperatur=0.2, adapter=""):
    """Dasselbe für OpenAI-kompatible Server — vor allem `mlx_lm server` mit einem
    frisch trainierten Adapter, ohne ihn erst verschmelzen und nach Ollama bringen
    zu müssen:  python -m mlx_lm server --model M --port 8080  und  adapter=A.

    Der Adapter muss in jeder Anfrage stehen: mlx_lm server (0.31) übergeht
    `--adapter-path` beim Laden für eine Anfrage und nimmt das nackte Grundmodell."""
    def chat(nachrichten):
        # Bilder (Ollama: "images") als Inhaltsteile, wie die OpenAI-Schnittstelle sie erwartet.
        nachrichten = [dict({k: v for k, v in n.items() if k != "images"},
                            content=[{"type": "text", "text": n.get("content", "")}] + [
                                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b}} for b in n["images"]])
                       if n.get("images") else n for n in nachrichten]
        nutzlast = {"model": modell, "messages": nachrichten, "temperature": temperatur, "max_tokens": 4096}
        if adapter:
            nutzlast["adapters"] = adapter
        kopf = {"Content-Type": "application/json"}
        if api_key:
            kopf["Authorization"] = "Bearer " + api_key
        req = urllib.request.Request(basis.rstrip("/") + "/v1/chat/completions", data=json.dumps(nutzlast).encode(),
                                     headers=kopf)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                d = json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError("Server: %s %s" % (e.code, e.read().decode("utf-8", "replace")[:200]))
        chat.tokens += (d.get("usage") or {}).get("completion_tokens", 0)
        return ((d.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    chat.tokens = 0
    return chat


def zurueckgehaltene(datensatz):
    """Aufgaben aus zurueckgehalten.txt eines exportierten Datensatzes."""
    with open(os.path.join(os.path.expanduser(datensatz), "zurueckgehalten.txt"), encoding="utf-8") as f:
        return [z.strip() for z in f if z.strip() and not z.startswith("#")]


def entladen(basis, modell):
    try:
        urllib.request.urlopen(urllib.request.Request(
            basis.rstrip("/") + "/api/generate", data=json.dumps({"model": modell, "keep_alive": 0}).encode(),
            headers={"Content-Type": "application/json"}), timeout=60).read()
    except Exception:
        pass


# --------------------------------------------------------------- Bewertung ---

def test_hashes(ordner):
    h = {}
    for wurzel, _, dateien in os.walk(os.path.join(ordner, "tests")):
        for d in dateien:
            if d.endswith(".py"):
                p = os.path.join(wurzel, d)
                with open(p, "rb") as f:
                    h[os.path.relpath(p, ordner)] = hashlib.sha256(f.read()).hexdigest()
    return h


def bewerten(ordner, aufgabe_id):
    """(gelöst, Ausgabe, Zahlen) — mit den versteckten Tests, außerhalb jeder Reichweite des Agenten.

    Früher liefen die versteckten Tests ohne Sandbox im Arbeitsordner des Agenten — also in seiner
    Trust-Domain und mit seinem Code ungeschützt. Jetzt prüft das Orakel (orakel.py): frische Kopie,
    nur Quelldateien des Agenten, Tests aus dem Aufgabenspeicher, Sandbox, Isolationsmodus."""
    import orakel
    quelle = orakel._ohne_tests(orakel.baum_lesen(ordner)[0])
    versteckt = orakel.baum_lesen(os.path.join(AUFGABEN, aufgabe_id), "versteckt")[0]
    fakt, ableitung = orakel.testlauf(quelle, versteckt, testordner="tests_versteckt", frist=120)
    if not ableitung:
        return False, "Orakel nicht verfügbar: %s" % fakt.get("grund"), {"tests": 0, "fehlschlaege": None}
    ausgabe = (fakt.get("stdout_auszug") or "") + (fakt.get("stderr_auszug") or "")
    if fakt.get("timeout"):
        return False, "Zeitüberschreitung bei den versteckten Tests", {"tests": 0, "fehlschlaege": None}
    return ableitung["bestanden"], ausgabe[-1200:], {
        "tests": ableitung.get("tests") or 0, "fehlschlaege": len(ableitung.get("gescheitert") or [])}


def projekt_anlegen(aufgabe_id, mit_loesung=False):
    ordner = tempfile.mkdtemp(prefix="dowos_stufe2_%s_" % aufgabe_id)
    shutil.copytree(os.path.join(AUFGABEN, aufgabe_id, "repo"), ordner, dirs_exist_ok=True)
    if mit_loesung:
        shutil.copytree(os.path.join(AUFGABEN, aufgabe_id, "loesung"), ordner, dirs_exist_ok=True)
    return ordner


def orakel(ids=None):
    """Beweist die Bewertung: unverändert löst nichts, die Referenzlösung alles.

    Gibt (unverändert gelöst, Referenz gelöst, Anzahl) zurück."""
    ids = ids or aufgaben_ids()
    roh = ref = 0
    for i in ids:
        for mit in (False, True):
            ordner = projekt_anlegen(i, mit)
            try:
                geloest = bewerten(ordner, i)[0]
            finally:
                shutil.rmtree(ordner, ignore_errors=True)
            if mit:
                ref += geloest
            else:
                roh += geloest
    return roh, ref, len(ids)


# -------------------------------------------------------------------- Lauf ---

# Auf dem Prüfstand sitzt niemand, der Rückfragen beantwortet. Früher endete die
# Aufgabe damit (gemessen: 2 von 20 beim Grundmodell Qwen3-4B). Jetzt bekommt der
# Agent diese Antwort und arbeitet weiter — die Frage kostet ihn Schritte, nicht die Aufgabe.
AUTO_ANTWORT = ("(Automatische Antwort des Prüfstands) Niemand kann deine Frage beantworten. Entscheide selbst "
                "anhand des Issues, des Codes und der Tests, triff eine vernünftige Annahme und löse die Aufgabe.")


def loese(chat, aufgabe_id, max_schritte=30, budget=11000, stufe="projekt", melden=None, rueckfragen=2):
    ordner = projekt_anlegen(aufgabe_id)
    try:
        vorher = test_hashes(ordner)
        with open(os.path.join(AUFGABEN, aufgabe_id, "ISSUE.md"), encoding="utf-8") as f:
            issue = f.read().strip()
        tokens_vorher = getattr(chat, "tokens", 0)
        wb = werkbank.Werkbank(ordner, stufe)
        e = werkbank.arbeiten(issue, wb, chat, politik="nie", max_schritte=max_schritte, budget=budget,
                              melden=melden, testbefehl=TESTBEFEHL)
        e["rueckfragen"] = 0
        while e["beendet"] == "frage" and e["rueckfragen"] < rueckfragen and e["schritte"] < max_schritte:
            weiter = werkbank.arbeiten(AUTO_ANTWORT, wb, chat, politik="nie", max_schritte=max_schritte - e["schritte"],
                                       budget=budget, melden=melden, vorher=e["nachrichten"])
            for k in ("schritte", "sekunden", "ungueltig", "abgelehnt"):
                weiter[k] = e[k] + weiter[k]
            weiter["verlauf"] = e["verlauf"] + [dict(v, schritt=v["schritt"] + e["schritte"]) for v in weiter["verlauf"]]
            weiter["geaendert"] = list(dict.fromkeys(e["geaendert"] + weiter["geaendert"]))
            weiter["rueckfragen"] = e["rueckfragen"] + 1
            e = weiter
        geloest, ausgabe, zahlen = bewerten(ordner, aufgabe_id)
        e.update({"geloest": geloest, "versteckte_tests": zahlen, "test_ausgabe": ausgabe[-600:],
                  "tests_veraendert": test_hashes(ordner) != vorher,
                  "tokens": getattr(chat, "tokens", 0) - tokens_vorher})
        return e
    finally:
        shutil.rmtree(ordner, ignore_errors=True)


def bericht(zeilen, lauf):
    m = meta()
    modelle = list(dict.fromkeys(z["modell"] for z in zeilen))
    L = ["# Prüfstand Stufe 2 · %s\n" % lauf,
         "Werkbank-Agent von Dive on Wide, Rechtestufe „projekt“, Sandbox %s, Temperatur 0,2, ohne Denken. "
         "Bewertet mit versteckten Tests.\n" % (werkbank.sandbox_art() or "keine"),
         "| Modell | Gelöst | leicht | mittel | schwer | nur im Issue | Ø Schritte | Ø Minuten | unlesbar | Tests verändert |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for mod in modelle:
        r = {z["aufgabe"]: z["ergebnis"] for z in zeilen if z["modell"] == mod}

        def quote(filt):
            s = [a for a in r if a in m and filt(m[a])]
            return "%d/%d" % (sum(r[a]["geloest"] for a in s), len(s)) if s else "–"
        n = max(1, len(r))
        L.append("| **%s** | **%d/%d** | %s | %s | %s | %s | %.1f | %.1f | %d | %d |" % (
            mod, sum(e["geloest"] for e in r.values()), len(r),
            quote(lambda x: x["stufe"] == "leicht"), quote(lambda x: x["stufe"] == "mittel"),
            quote(lambda x: x["stufe"] == "schwer"), quote(lambda x: not x["fehler_sichtbar"]),
            sum(e["schritte"] for e in r.values()) / n, sum(e["sekunden"] for e in r.values()) / n / 60,
            sum(e["ungueltig"] for e in r.values()), sum(e["tests_veraendert"] for e in r.values())))
    L += ["\n## Je Aufgabe\n", "| Aufgabe | Stufe | " + " | ".join(modelle) + " |",
          "|---|---|" + "---|" * len(modelle)]
    for a in sorted(m):
        zellen = []
        for mod in modelle:
            e = next((z["ergebnis"] for z in zeilen if z["modell"] == mod and z["aufgabe"] == a), None)
            zellen.append("–" if e is None else "%s %dS·%.0fm·%s" % (
                "✅" if e["geloest"] else "❌", e["schritte"], e["sekunden"] / 60, e["beendet"]))
        L.append("| %s | %s | %s |" % (a, m[a]["stufe"], " | ".join(zellen)))
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Prüfstand Stufe 2 für den Werkbank-Agenten")
    ap.add_argument("--modelle", default="")
    ap.add_argument("--aufgaben", default="", help="Präfixe, kommagetrennt, z. B. 01,02,18")
    ap.add_argument("--lauf", default=time.strftime("stufe2_%Y%m%d_%H%M"))
    ap.add_argument("--max-schritte", type=int, default=30)
    ap.add_argument("--rueckfragen", type=int, default=2,
                    help="so oft wird eine Rückfrage automatisch beantwortet (0: Rückfrage beendet die Aufgabe wie früher)")
    ap.add_argument("--ollama", default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))
    ap.add_argument("--num-ctx", type=int, default=16384)
    ap.add_argument("--openai", help="OpenAI-kompatibler Server statt Ollama, z. B. http://127.0.0.1:8080 (mlx_lm server)")
    ap.add_argument("--adapter", help="LoRA-Adapter-Ordner, den mlx_lm server je Anfrage laden soll")
    ap.add_argument("--zurueckgehalten", metavar="DATENSATZ",
                    help="nur die Aufgaben, die dieser Trainingsdatensatz zurückgehalten hat — "
                         "ein damit trainiertes Modell darf nur dort gemessen werden")
    ap.add_argument("--orakel", action="store_true", help="nur die Bewertung prüfen, ohne Modell")
    ap.add_argument("--aufgaben-ordner", help="andere Aufgaben im selben Aufbau, z. B. aus der Fabrik")
    ap.add_argument("--ergebnisse", help="wohin Ergebnisse und Verläufe geschrieben werden")
    a = ap.parse_args()
    global AUFGABEN, ERGEBNISSE
    if a.aufgaben_ordner:
        AUFGABEN = os.path.abspath(os.path.expanduser(a.aufgaben_ordner))
    if a.ergebnisse:
        ERGEBNISSE = os.path.abspath(os.path.expanduser(a.ergebnisse))
    if a.orakel:
        roh, ref, n = orakel(aufgaben_ids(a.aufgaben))
        print("Orakel: unverändert gelöst %d/%d (soll 0) · Referenzlösung gelöst %d/%d (soll %d) · Sandbox: %s"
              % (roh, n, ref, n, n, werkbank.sandbox_art() or "keine"))
        return 0 if roh == 0 and ref == n else 1
    modelle = [x.strip() for x in a.modelle.split(",") if x.strip()]
    if not modelle:
        ap.error("--modelle fehlt (oder --orakel)")
    os.makedirs(ERGEBNISSE, exist_ok=True)
    roh_pfad = os.path.join(ERGEBNISSE, a.lauf + ".jsonl")
    erledigt = set()
    if os.path.exists(roh_pfad):
        with open(roh_pfad, encoding="utf-8") as f:
            erledigt = {(z["modell"], z["aufgabe"]) for z in map(json.loads, f)}
    budget = int(a.num_ctx * 0.67)
    ids = aufgaben_ids(a.aufgaben)
    if a.zurueckgehalten:
        erlaubt = set(zurueckgehaltene(a.zurueckgehalten))
        ids = [i for i in ids if i in erlaubt]
        print("Nur zurückgehaltene Aufgaben: %d" % len(ids), flush=True)
    for modell in modelle:
        print("\n=== %s" % modell, flush=True)
        # Der Schlüssel kommt aus der Umgebung, nie aus der Befehlszeile: die steht in `ps` und im Lauf-Protokoll.
        chat = openai_chat(a.openai, modell, os.environ.get("DOWOS_API_KEY", ""), adapter=a.adapter or "") if a.openai \
            else ollama_chat(a.ollama, modell, a.num_ctx)
        verlaeufe = os.path.join(ERGEBNISSE, a.lauf + "_verlaeufe", re.sub(r"[^\w.-]+", "_", modell))
        os.makedirs(verlaeufe, exist_ok=True)
        for i in ids:
            if (modell, i) in erledigt:
                continue
            try:
                e = loese(chat, i, a.max_schritte, budget, rueckfragen=a.rueckfragen)
            except Exception as fehler:          # Modell weg, Ollama abgestürzt …
                e = {"geloest": False, "beendet": "modellfehler: %s" % fehler, "schritte": 0, "sekunden": 0,
                     "ungueltig": 0, "tests_veraendert": False, "nachrichten": [], "verlauf": []}
            with open(os.path.join(verlaeufe, i + ".json"), "w", encoding="utf-8") as f:
                json.dump(e, f, ensure_ascii=False, indent=1)
            kurz = {k: v for k, v in e.items() if k not in ("nachrichten", "verlauf")}
            with open(roh_pfad, "a", encoding="utf-8") as f:
                f.write(json.dumps({"modell": modell, "aufgabe": i, "ergebnis": kurz}, ensure_ascii=False) + "\n")
            print("  %s %-22s %2d Schritte %5.1f min  %-12s unlesbar %d%s" % (
                "✅" if e["geloest"] else "❌", i, e["schritte"], e["sekunden"] / 60, e["beendet"],
                e["ungueltig"], "  rückgefragt %d×" % e["rueckfragen"] if e.get("rueckfragen") else ""), flush=True)
        if not a.openai:
            entladen(a.ollama, modell)
    with open(roh_pfad, encoding="utf-8") as f:
        zeilen = [json.loads(z) for z in f]
    ziel = os.path.join(ERGEBNISSE, a.lauf + ".md")
    with open(ziel, "w", encoding="utf-8") as f:
        f.write(bericht(zeilen, a.lauf))
    print("\nBericht: %s" % ziel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
