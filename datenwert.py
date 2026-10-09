#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Datenwert-Test — macht ein Datensatz einen Schüler messbar besser?

Der Destillations-Verlauf nennt das den wichtigsten offenen Baustein: den Wert von
Daten nicht behaupten („training_value = 0.91“), sondern messen. Deshalb:

    Variante A   Grundmodell (oder Grundmodell + Basisdatensatz)
    Variante AB  dasselbe + der zu prüfende Datensatz
    — gleiches Trainingsbudget (Iterationen, Lernrate, Rang …), mehrere Seeds —
    gemessen auf Aufgaben, die in keinem Training vorkommen:
      Kontrollaufgaben desselben Themas  → G7 Schüler-Uplift
      Prüfstand (anderes Thema)          → G6 Transfer

Ausgewertet wird gepaart je Seed: Differenz der Lösungsquote, Mittelwert und 95-%-
Konfidenzintervall (t-Verteilung). PASS nur, wenn die untere Grenze über 0 liegt;
FAIL, wenn die obere unter 0 liegt (der Datensatz schadet); sonst UNVERIFIED — nicht
unterscheidbar. Mit einem einzigen Seed gibt es kein Intervall und damit kein PASS.

Das Ergebnis wird in die Proben des Destillations-Auftrags geschrieben (G6/G7) und
ihr Label neu berechnet — erst damit kann eine Probe GOLD_CAUSAL werden.

    python3 datenwert.py starten <ordner-mit-experiment.json>
"""

import collections
import json
import math
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import time
import urllib.request

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
sys.path.insert(0, os.path.join(HIER, "pruefstand"))
import destillation  # noqa: E402
import training  # noqa: E402

T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
       15: 2.131, 20: 2.086, 30: 2.042}


def t_wert(freiheitsgrade):
    for f in sorted(T95, reverse=True):
        if freiheitsgrade >= f:
            return T95[f]
    return None


def auswerten(paare):
    """paare: [(quote_a, quote_ab)] je Seed → Differenz, Intervall, Status."""
    deltas = [ab - a for a, ab in paare if a is not None and ab is not None]
    if not deltas:
        return {"status": "UNVERIFIED", "wert": None, "grund": "nichts gemessen"}
    mittel = statistics.mean(deltas)
    if len(deltas) < 2:
        return {"status": "UNVERIFIED", "wert": round(mittel, 4), "ci95": None, "seeds": 1,
                "grund": "ein Seed — ohne Streuung kein Intervall, kein Beleg"}
    sd = statistics.stdev(deltas)
    halb = t_wert(len(deltas) - 1) * sd / math.sqrt(len(deltas))
    unten, oben = mittel - halb, mittel + halb
    status = "PASS" if unten > 0 else "FAIL" if oben < 0 else "UNVERIFIED"
    return {"status": status, "wert": round(mittel, 4), "ci95": [round(unten, 4), round(oben, 4)], "seeds": len(deltas),
            "deltas": [round(d, 4) for d in deltas],
            "grund": None if status == "PASS" else "Datensatz verschlechtert den Schüler" if status == "FAIL"
            else "Unterschied nicht von Zufall unterscheidbar"}


def daten_zusammenfuehren(ziel, *quellen):
    """train/valid mehrerer Datensätze aneinanderhängen (gleiche Beispiele, andere Mischung ergibt der Trainer)."""
    os.makedirs(ziel, exist_ok=True)
    for teil in ("train", "valid"):
        with open(os.path.join(ziel, teil + ".jsonl"), "w", encoding="utf-8") as aus:
            for q in quellen:
                if not q:
                    continue
                with open(os.path.join(q, teil + ".jsonl"), encoding="utf-8") as f:
                    for zeile in f:
                        if zeile.strip():
                            aus.write(zeile if zeile.endswith("\n") else zeile + "\n")
    return ziel


def teile_nachrichten(nachrichten, laenge, max_seq):
    """Ein zu langes Gespräch in aufeinanderfolgende, passende Gespräche schneiden.

    Der Trainer schneidet sonst stumpf am Ende ab. Bei einem Mehrrunden-Gespräch
    verschwindet damit die letzte Antwort — es bleiben keine Zieltoken übrig, der
    Verlust wird NaN, und weil die Gewichte davon NaN werden, bleibt der ganze
    Lauf NaN. Gemessen am 17.09.2026: max_seq 3072, 141 von 964 Beispielen
    länger als das, Verlust ab Schritt 1 NaN, Adapter Schrott. mlx-lm rät im
    Protokoll selbst dazu: „Consider pre-splitting your data."

    Geschnitten wird nur zwischen Frage-Antwort-Paaren, niemals mitten in einer
    Antwort: jedes entstehende Gespräch behält die Systemzeile und endet mit
    einer vollständigen Antwort. Ein einzelnes Paar, das allein nicht passt,
    lässt sich nicht retten und wird gemeldet statt stillschweigend verstümmelt.

    laenge(nachrichten) → Tokenzahl (der echte Tokenizer des Grundmodells).
    Rückgabe: (gespräche, verworfene_paare)"""
    system = [m for m in nachrichten if m.get("role") == "system"]
    rest = [m for m in nachrichten if m.get("role") != "system"]
    paare, i = [], 0
    while i < len(rest):
        if (i + 1 < len(rest) and rest[i].get("role") == "user"
                and rest[i + 1].get("role") == "assistant"):
            paare.append(rest[i:i + 2]); i += 2
        else:
            paare.append([rest[i]]); i += 1
    aus, aktuell, verworfen = [], [], 0
    for paar in paare:
        if aktuell and laenge(system + aktuell + paar) <= max_seq:
            aktuell = aktuell + paar
            continue
        if aktuell:
            aus.append(system + aktuell)
            aktuell = []
        if laenge(system + paar) > max_seq:
            verworfen += 1
            continue
        aktuell = list(paar)
    if aktuell:
        aus.append(system + aktuell)
    return aus, verworfen


_LAENGEN_SKRIPT = r'''
import json, sys
sys.path.insert(0, %r)
from datenwert import teile_nachrichten
from transformers import AutoTokenizer

auftrag = json.load(open(sys.argv[1]))
tok = AutoTokenizer.from_pretrained(auftrag["modell"])
speicher = {}

def laenge(msgs):
    schluessel = json.dumps(msgs, sort_keys=True)
    if schluessel not in speicher:
        text = tok.apply_chat_template(msgs, tokenize=False)
        speicher[schluessel] = len(tok(text)["input_ids"])
    return speicher[schluessel]

bericht = {}
for teil in ("train", "valid"):
    quelle = auftrag["quelle"] + "/" + teil + ".jsonl"
    hinaus, vorher, geteilt, verworfen, laengste = [], 0, 0, 0, 0
    for zeile in open(quelle, encoding="utf-8"):
        zeile = zeile.strip()
        if not zeile:
            continue
        vorher += 1
        d = json.loads(zeile)
        msgs = d.get("messages") or []
        l = laenge(msgs)
        if l <= auftrag["max_seq"]:
            laengste = max(laengste, l)
            hinaus.append({"messages": msgs})
            continue
        stuecke, weg = teile_nachrichten(msgs, laenge, auftrag["max_seq"])
        geteilt += 1
        verworfen += weg
        for st in stuecke:
            laengste = max(laengste, laenge(st))
            hinaus.append({"messages": st})
    with open(auftrag["ziel"] + "/" + teil + ".jsonl", "w", encoding="utf-8") as f:
        for d in hinaus:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    bericht[teil] = {"vorher": vorher, "nachher": len(hinaus), "geteilt": geteilt,
                     "verworfene_paare": verworfen, "laengstes": laengste}
print("BERICHT " + json.dumps(bericht))
''' % HIER


def daten_passend_machen(quelle, python, modell, max_seq, melden=print):
    """Datensatz so umschreiben, dass jedes Beispiel in max_seq passt.

    Gemessen wird mit dem echten Tokenizer des Grundmodells — im Python des
    Trainers, denn dort liegt er. Ohne diesen Schritt entscheidet der Trainer
    selbst, und zwar mit der Schere (siehe teile_nachrichten)."""
    ziel = quelle.rstrip("/") + "_passend"
    os.makedirs(ziel, exist_ok=True)
    auftrag = os.path.join(ziel, "auftrag.json")
    with open(auftrag, "w", encoding="utf-8") as f:
        json.dump({"quelle": quelle, "ziel": ziel, "modell": modell, "max_seq": int(max_seq)}, f)
    skript = os.path.join(ziel, "laengen.py")
    with open(skript, "w", encoding="utf-8") as f:
        f.write(_LAENGEN_SKRIPT)
    p = subprocess.run([python, skript, auftrag], capture_output=True, text=True)
    zeile = next((z for z in p.stdout.splitlines() if z.startswith("BERICHT ")), "")
    if not zeile:
        raise RuntimeError("Längenprüfung der Daten fehlgeschlagen: %s"
                           % (p.stderr or p.stdout)[-400:])
    bericht = json.loads(zeile[8:])
    for teil, b in bericht.items():
        melden("  Daten %s: %d Beispiele → %d (geteilt: %d, verworfene Paare: %d, "
               "längstes %d Token bei max_seq %d)"
               % (teil, b["vorher"], b["nachher"], b["geteilt"], b["verworfene_paare"],
                  b["laengstes"], int(max_seq)))
    with open(os.path.join(ziel, "bericht.json"), "w", encoding="utf-8") as f:
        json.dump(bericht, f, ensure_ascii=False, indent=2)
    return ziel, bericht


def speicher_frei_machen(melden=print):
    """Geladene Ollama-Modelle freigeben, bevor das Training die GPU braucht.

    Gemessen am 18.09.2026: Ein Training mit max_seq 4096 (Spitze 9,6 GB) starb
    nach 14 Minuten an „[METAL] Insufficient Memory" — auf 24 GB reicht es nicht,
    wenn daneben noch ein Chatmodell im Speicher liegt."""
    basis = (os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
    try:
        with urllib.request.urlopen(basis + "/api/ps", timeout=3) as r:
            geladen = [m.get("name") for m in json.load(r).get("models", [])]
    except Exception:
        return []
    for name in geladen:
        try:
            urllib.request.urlopen(urllib.request.Request(
                basis + "/api/generate", data=json.dumps({"model": name, "keep_alive": 0}).encode(),
                headers={"Content-Type": "application/json"}), timeout=30).read()
        except Exception:
            pass
    if geladen:
        melden("  Speicher freigemacht: %s entladen" % ", ".join(geladen))
    return geladen


# ------------------------------------------------------------- Messung ---

def _server_starten(python, basis, port):
    befehl = [python, "-m", "mlx_lm", "server", "--model", basis, "--host", "127.0.0.1", "--port", str(port),
              "--chat-template-args", '{"enable_thinking": false}', "--prompt-cache-size", "4",
              "--prompt-cache-bytes", str(3 * 1024 ** 3)]
    proc = subprocess.Popen(befehl, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(240):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/v1/models" % port, timeout=2).read()
            return proc
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError("mlx_lm server beendete sich beim Start")
            time.sleep(1)
    proc.kill()
    raise RuntimeError("mlx_lm server kam nicht hoch")


_STOERTOKEN = ("</think>", "<think>", "</tool_call>", "<tool_call>", "<|im_start|>", "<|im_end|>")


def wiederholt(text, mindestens=3, mindestlaenge=25):
    """Dreht sich der Text im Kreis? (derselbe Satz mehrfach)

    Das Kennzeichen eines entgleisten Modells: Es wiederholt einen Satz, bis das
    Token-Limit greift. Gemessen am 18.09.2026 am ersten trainierten Adapter —
    die Antwort war 4096 Token lang, der Satz darin fünfzehnmal, das JSON
    unvollständig und damit unlesbar."""
    saetze = [t.strip() for t in re.split(r"(?<=[.!?])\s+", text or "")
              if len(t.strip()) >= mindestlaenge]
    if not saetze:
        return 0
    haeufigster = max(collections.Counter(saetze).values())
    return haeufigster if haeufigster >= mindestens else 0


def abnahme_proben(daten_ordner, anzahl=3):
    """Abnahme-Proben aus dem Datensatz selbst — die längsten, nicht die ersten.

    Der Kontext ist alles bis zur letzten Antwort: genau der Ausschnitt, auf den
    das Training zielt (mlx-lm lernt mit mask_prompt nur die letzte Nachricht),
    und genau die Länge, bei der der erste trainierte Adapter entgleiste — im
    ersten Schritt antwortete er sauber, ab etwa 2000 Token Kontext drehte er
    sich im Kreis. Kurze Proben hätten das nicht gefunden."""
    pfad = os.path.join(daten_ordner, "valid.jsonl")
    if not os.path.isfile(pfad):
        pfad = os.path.join(daten_ordner, "train.jsonl")
    kandidaten = []
    with open(pfad, encoding="utf-8") as f:
        for zeile in f:
            if not zeile.strip():
                continue
            msgs = (json.loads(zeile).get("messages") or [])
            if not msgs or msgs[-1].get("role") != "assistant":
                continue
            vorlauf = msgs[:-1]
            if vorlauf:
                kandidaten.append((sum(len(m.get("content") or "") for m in vorlauf), vorlauf))
    kandidaten.sort(key=lambda k: -k[0])
    return [v for _, v in kandidaten[:anzahl]]


def mlx_abnehmer(python, basis, daten_ordner, port=8094, anzahl=3):
    """abnehmen(adapter) → Abnahmebericht; startet dafür kurz einen mlx_lm-Server.

    Kostet eine Minute und erspart im Fehlerfall Stunden: Die Messung eines
    Satzes lief am 18.09.2026 über 75 Minuten und endete mit „0 von 12", weil
    der Adapter kein lesbares Protokoll mehr lieferte. Dieselbe Auskunft gibt
    die Abnahme in drei Anfragen."""
    def abnehmen(adapter):
        import stufe2
        proben = abnahme_proben(daten_ordner, anzahl)
        if not proben:
            return {"ok": True, "befunde": [], "antworten": [],
                    "hinweis": "keine Proben im Datensatz gefunden"}
        proc = _server_starten(python, basis, port)
        try:
            chat = stufe2.openai_chat("http://127.0.0.1:%d" % port, basis, adapter=adapter or "")
            return abnahme(chat, proben)
        finally:
            training.prozess_beenden(proc.pid)
            try:
                proc.wait(timeout=30)
            except Exception:
                pass
    return abnehmen


def abnahme(chat, proben):
    """Abnahmefahrt: Antwortet der Adapter überhaupt im Protokoll der Messung?

    Eine Messung kostet je Satz Stunden (gemessen 18.09.2026: 12 Aufgaben, über
    75 Minuten). Ein Adapter, der im ersten Schritt kein lesbares JSON mehr
    liefert, muss vorher auffallen — nicht nach acht Stunden als „0 von 12".
    Geprüft wird nur, was ohne Aufgabenlösung feststellbar ist: lesbares
    Protokoll, keine Endlosschleife, keine Fremdmarken aus der Vorlage.

    Rückgabe: {"ok": bool, "befunde": [Text …], "antworten": [Text …]}"""
    befunde, antworten = [], []
    for nr, nachrichten in enumerate(proben, 1):
        try:
            text = chat(nachrichten) or ""
        except Exception as e:
            befunde.append("Probe %d: Der Modellaufruf scheiterte (%s)" % (nr, e))
            continue
        antworten.append(text)
        kurz = text.strip()
        marken = [t for t in _STOERTOKEN if t in kurz]
        if marken:
            befunde.append("Probe %d: Fremdmarken in der Antwort (%s) — Training und "
                           "Messung benutzen nicht dieselbe Vorlage"
                           % (nr, ", ".join(marken)))
        wdh = wiederholt(kurz)
        if wdh:
            befunde.append("Probe %d: derselbe Satz %dx — die Antwort dreht sich im Kreis"
                           % (nr, wdh))
        anfang = kurz.find("{")
        if anfang < 0:
            befunde.append("Probe %d: kein JSON in der Antwort (Anfang: %r)"
                           % (nr, kurz[:80]))
            continue
        try:
            d = json.loads(kurz[anfang:kurz.rfind("}") + 1])
        except ValueError as e:
            befunde.append("Probe %d: JSON nicht lesbar (%s), Antwortlänge %d Zeichen"
                           % (nr, e, len(kurz)))
            continue
        fehlend = [k for k in ("gedanke", "werkzeug") if k not in d]
        if fehlend:
            befunde.append("Probe %d: im JSON fehlt %s" % (nr, ", ".join(fehlend)))
    return {"ok": not befunde, "befunde": befunde, "antworten": antworten}


def restzeit_text(dauern, offen):
    """„noch 9 Aufgaben ≈ 2 h 10" — aus den bisher gemessenen Dauern.

    Am 18.09.2026 habe ich die Restzeit eines Laufs um den Faktor sechs
    unterschätzt, weil niemand die Kosten einer einzelnen Aufgabe kannte: Eine
    gescheiterte Aufgabe reizt ihr Schrittbudget aus und kostet damit ein
    Vielfaches einer gelösten. Wer wartet, soll die Zahl sehen, nicht raten."""
    if not dauern or offen <= 0:
        return ""
    mittel = sum(dauern) / len(dauern)
    rest = mittel * offen
    if rest < 90:
        wie = "%.0f s" % rest
    elif rest < 5400:
        wie = "%.0f min" % (rest / 60)
    else:
        wie = "%d h %02d" % (int(rest // 3600), int((rest % 3600) // 60))
    return "noch %d Aufgabe%s ≈ %s" % (offen, "" if offen == 1 else "n", wie)


def mlx_bewerter(python, basis, port=8093, max_schritte=30):
    """bewerten(adapter, aufgabensatz) → (gelöst, Anzahl) über mlx_lm server; der Adapter reist je Anfrage mit."""
    import stufe2

    def bewerten(adapter, satz):
        proc = _server_starten(python, basis, port)
        try:
            chat = stufe2.openai_chat("http://127.0.0.1:%d" % port, basis, adapter=adapter or "")
            alt = stufe2.AUFGABEN
            stufe2.AUFGABEN = satz["aufgaben_ordner"]
            try:
                geloest = 0
                # Eine Messung dauert je Satz eine gute Stunde. Ohne Zeile je
                # Aufgabe sieht ein laufender Lauf genauso aus wie ein haengender
                # — dieselbe Falle wie bei den stillen Fehlschlaegen im Server.
                dauern = []
                for nr, aufgabe in enumerate(satz["aufgaben"], 1):
                    t0 = time.time()
                    try:
                        treffer = int(bool(stufe2.loese(chat, aufgabe, max_schritte)["geloest"]))
                        geloest += treffer
                        dauern.append(time.time() - t0)
                        print("    %2d/%d %s %s (%.0f s) %s"
                              % (nr, len(satz["aufgaben"]), "✅" if treffer else "—",
                                 aufgabe, dauern[-1],
                                 restzeit_text(dauern, len(satz["aufgaben"]) - nr)), flush=True)
                    except Exception:            # Modellfehler zählt als nicht gelöst — sichtbar im Log
                        dauern.append(time.time() - t0)
                        print("  ⚠️ %s: Modellfehler (%.0f s)" % (aufgabe, dauern[-1]), flush=True)
            finally:
                stufe2.AUFGABEN = alt
            return geloest, len(satz["aufgaben"])
        finally:
            training.prozess_beenden(proc.pid)
            try:
                proc.wait(timeout=30)
            except Exception:
                pass
    return bewerten


# --------------------------------------------------------- Experiment ---

class Experiment:
    def __init__(self, ordner, werkbank=None, bewerter=None, melden=print, abnehmer=None):
        # Absolut, immer. Der Trainer läuft in seinem eigenen Arbeitsverzeichnis;
        # ein relativer Pfad landete dort als storage/training/storage/… und das
        # Training starb mit "Couldn't find any data file" (gemessen 17.09.2026,
        # erster echter Datenwert-Lauf).
        self.ordner = os.path.abspath(os.path.expanduser(ordner))
        ordner = self.ordner
        self.plan = destillation._json_lesen(os.path.join(ordner, "experiment.json"))
        if not self.plan:
            raise ValueError("experiment.json fehlt.")
        self.zustand = destillation._json_lesen(os.path.join(ordner, "zustand.json"), {"laeufe": {}})
        # Dieselbe Trainings-Ablage wie Dive on Wide: Nur so sieht das Training, ob schon ein anderes die GPU belegt.
        self.wb = werkbank or training.Werkbank(self.plan.get("training_ordner") or os.path.join(ordner, "training"), [],
                                                python=self.plan.get("python"), caffeinate=True)
        self.bewerter = bewerter or mlx_bewerter(self.wb.trainer()[0], self.plan["basis"])
        self.abnehmer = abnehmer            # None: wird je Lauf mit dem Datensatz gebaut
        self.melden = melden

    def speichern(self):
        destillation._json_schreiben(os.path.join(self.ordner, "zustand.json"), self.zustand)

    def _trainieren(self, variante, seed):
        schluessel = "%s-%s" % (variante, seed)
        lauf = self.zustand["laeufe"].setdefault(schluessel, {})
        if lauf.get("adapter") and os.path.isfile(os.path.join(lauf["adapter"], "adapters.safetensors")):
            return lauf["adapter"]
        if variante == "A" and not self.plan.get("daten_a"):
            return None                                        # untrainiertes Grundmodell
        quellen = [self.plan.get("daten_a")] + ([self.plan["daten_b"]] if variante == "AB" else [])
        daten = daten_zusammenfuehren(os.path.join(self.ordner, "daten_%s" % variante), *quellen)
        max_seq = (self.plan.get("werte") or {}).get("max_seq")
        if max_seq and not self.plan.get("ohne_laengenpruefung"):
            daten, bericht = daten_passend_machen(daten, self.wb.trainer()[0],
                                                  self.plan["basis"], max_seq, self.melden)
            self.zustand.setdefault("daten", {})[variante] = bericht
            self.speichern()
        speicher_frei_machen(self.melden)
        z = self.wb.starten(self.plan["basis"], daten, name="Datenwert %s Seed %s" % (variante, seed),
                            werte=dict(self.plan.get("werte") or {}, seed=seed))
        self.melden("  Training %s, Seed %s gestartet" % (variante, seed))
        while self.wb.lebt(z["pid"]):
            time.sleep(self.plan.get("takt", 20))
        ende = next(l for l in self.wb.liste() if l["id"] == z["id"])
        if ende["zustand"] != "fertig":
            raise RuntimeError("Training %s Seed %s: %s" % (variante, seed, ende["zustand"]))
        # Ein Lauf mit NaN gilt als „fertig" — er hat aber nichts gelernt, und sein
        # Adapter ist Schrott. Würde man ihn messen, käme „der Datensatz schadet"
        # heraus, und schuld wäre in Wahrheit die Einstellung. Gemessen am
        # 17.09.2026: max_seq 3072 schnitt bei langen Beispielen die Antwort weg,
        # dadurch gab es keine Zieltoken und der Verlust war ab Schritt 1 NaN.
        nan = (ende.get("verlauf") or {}).get("nan") or 0
        if nan:
            raise RuntimeError(
                "Training %s Seed %s: Der Verlust wurde %dx NaN — dieser Lauf hat nichts gelernt. "
                "Meist ist max_seq kleiner als die längsten Beispiele, dann bleibt keine Antwort "
                "zum Lernen übrig. Ein solcher Adapter darf nicht gemessen werden."
                % (variante, seed, nan))
        lauf.update(training=z["id"], adapter=z["adapter"])
        self.speichern()
        # Abnahmefahrt, bevor Stunden in eine Messung gehen: Antwortet der
        # Adapter überhaupt im Protokoll? Gemessen am 18.09.2026 lief eine
        # Messung 75 Minuten und endete mit 0 von 12 — der Adapter drehte sich
        # ab dem dritten Schritt im Kreis, das JSON war unlesbar. Drei Anfragen
        # hätten das gesagt. Ein durchgefallener Adapter wird nicht mit 0
        # bewertet, sondern gar nicht: 0 wäre eine Messung, das hier ist keine.
        if not self.plan.get("ohne_abnahme"):
            abnehmen = self.abnehmer or mlx_abnehmer(self.wb.trainer()[0],
                                                     self.plan["basis"], daten)
            bericht = abnehmen(z["adapter"])
            lauf["abnahme"] = bericht
            self.speichern()
            if not bericht.get("ok"):
                self.melden("  ⛔ Abnahme %s Seed %s nicht bestanden:" % (variante, seed))
                for b in bericht.get("befunde") or []:
                    self.melden("     %s" % b)
                raise RuntimeError(
                    "Abnahme %s Seed %s nicht bestanden: %s. Dieser Adapter wird nicht "
                    "gemessen — eine Messung würde nur festhalten, dass er kein lesbares "
                    "Protokoll liefert. Erst die Trainingseinstellung ändern (weniger "
                    "Iterationen, kleinere Lernrate, kleinerer Rang), dann erneut."
                    % (variante, seed, "; ".join(bericht.get("befunde") or [])[:400]))
            self.melden("  ✓ Abnahme %s Seed %s bestanden" % (variante, seed))
        return z["adapter"]

    def _messen(self, variante, seed, adapter, satz_name):
        lauf = self.zustand["laeufe"]["%s-%s" % (variante, seed)]
        if lauf.get(satz_name) is not None:
            return lauf[satz_name]
        satz = self.plan.get(satz_name)
        if not satz or not satz.get("aufgaben"):
            lauf[satz_name] = None
            return None
        geloest, n = self.bewerter(adapter, satz)
        lauf[satz_name] = {"geloest": geloest, "n": n, "quote": geloest / n if n else None}
        self.melden("%s %s Seed %s · %s: %d/%d" % ("✅" if satz_name == "kontroll" else "·", variante, seed, satz_name, geloest, n))
        self.speichern()
        return lauf[satz_name]

    def lauf(self):
        for seed in self.plan["seeds"]:
            for variante in ("A", "AB"):
                adapter = self._trainieren(variante, seed)
                for satz in ("kontroll", "transfer"):
                    self._messen(variante, seed, adapter, satz)
        ergebnis = {}
        for satz, gate in (("kontroll", "G7_schueler"), ("transfer", "G6_transfer")):
            paare = []
            for seed in self.plan["seeds"]:
                a = (self.zustand["laeufe"].get("A-%s" % seed) or {}).get(satz)
                ab = (self.zustand["laeufe"].get("AB-%s" % seed) or {}).get(satz)
                if a and ab:
                    paare.append((a["quote"], ab["quote"]))
            ergebnis[gate] = dict(auswerten(paare), messung=satz, gemessen=time.strftime("%Y-%m-%dT%H:%M:%S"),
                                  plan={k: self.plan.get(k) for k in ("basis", "daten_a", "daten_b", "seeds", "werte")})
        self.zustand["ergebnis"] = ergebnis
        self.speichern()
        if self.plan.get("auftrag"):
            n = in_proben_schreiben(self.plan["auftrag"], self.plan.get("proben") or [], ergebnis)
            self.melden("  %d Proben mit G6/G7 aktualisiert" % n)
        self.melden("Bericht: G7 %s (%s) · G6 %s (%s)" % (ergebnis["G7_schueler"]["status"], ergebnis["G7_schueler"].get("wert"),
                                                         ergebnis["G6_transfer"]["status"], ergebnis["G6_transfer"].get("wert")))
        return ergebnis


def in_proben_schreiben(auftrag_ordner, proben_ids, ergebnis):
    """G6/G7 in genau die Proben, die im gemessenen Datensatz waren, und ihr Label neu berechnen."""
    proben_dir = os.path.join(auftrag_ordner, "proben")
    zustand_pfad = os.path.join(auftrag_ordner, "zustand.json")
    zustand = destillation._json_lesen(zustand_pfad, {}) or {}
    n = 0
    for name in sorted(os.listdir(proben_dir)) if os.path.isdir(proben_dir) else []:
        if not name.endswith(".json"):
            continue
        pfad = os.path.join(proben_dir, name)
        p = destillation._json_lesen(pfad)
        if not p or p["sample_id"] not in proben_ids:
            continue
        for gate in ("G6_transfer", "G7_schueler"):
            p["gates"][gate] = dict(ergebnis[gate], status=ergebnis[gate]["status"])
        p["student_ab_test"] = {"value": ergebnis["G7_schueler"].get("wert"), "status": ergebnis["G7_schueler"]["status"],
                                "ci95": ergebnis["G7_schueler"].get("ci95")}
        p["transfer"] = {"value": ergebnis["G6_transfer"].get("wert"), "status": ergebnis["G6_transfer"]["status"]}
        p["training_value"] = {"estimated": None, "measured": ergebnis["G7_schueler"].get("wert"),
                               "status": ergebnis["G7_schueler"]["status"], "einheit": "Δ Lösungsquote auf Kontrollaufgaben"}
        p["final_label"] = destillation.label_berechnen(p["gates"])
        for e in p.get("evidence_chain", []):
            if e["stufe"] == "TRANSFER":
                e["inhalt"] = "%s (Δ %s)" % (ergebnis["G6_transfer"]["status"], ergebnis["G6_transfer"].get("wert"))
            elif e["stufe"] == "STUDENT_UPLIFT":
                e["inhalt"] = "%s (Δ %s, 95 %% %s)" % (ergebnis["G7_schueler"]["status"], ergebnis["G7_schueler"].get("wert"),
                                                       ergebnis["G7_schueler"].get("ci95"))
            elif e["stufe"] == "FINAL_DATA_LABEL":
                e["inhalt"] = p["final_label"]
        destillation._json_schreiben(pfad, p)
        if p["sample_id"] in (zustand.get("proben") or {}):
            zustand["proben"][p["sample_id"]]["label"] = p["final_label"]
        n += 1
    if zustand:
        destillation._json_schreiben(zustand_pfad, zustand)
    return n


def experiment_anlegen(ordner, basis, daten_b, seeds=(1, 2, 3), werte=None, daten_a=None, transfer_ordner=None,
                       transfer_aufgaben=None, python=None, training_ordner=None):
    """Plan aus einem exportierten Themen-Datensatz (mit holdout.json) bauen."""
    holdout = destillation._json_lesen(os.path.join(daten_b, "holdout.json"))
    if not holdout or not holdout.get("aufgaben"):
        raise ValueError("Der Datensatz hat keine Kontrollaufgaben (holdout.json) — mit mehr Aufgaben je Thema exportieren.")
    if not training.Werkbank.datensatz_info(daten_b):
        raise ValueError("„%s“ ist kein trainierbarer Datensatz." % daten_b)
    if len(seeds) < 2:
        raise ValueError("Mindestens zwei Seeds — mit einem gibt es keinen Beleg.")
    transfer_ordner = transfer_ordner or os.path.join(HIER, "pruefstand", "stufe2", "aufgaben")
    if transfer_aufgaben is None:
        transfer_aufgaben = sorted(n for n in os.listdir(transfer_ordner) if os.path.isfile(os.path.join(transfer_ordner, n, "ISSUE.md")))
    plan = {"basis": basis, "daten_a": daten_a, "daten_b": daten_b, "seeds": list(seeds),
            "werte": dict({"iters": 200, "geduld": 0}, **(werte or {})), "python": python, "training_ordner": training_ordner,
            "kontroll": {"aufgaben_ordner": holdout["aufgaben_ordner"], "aufgaben": holdout["aufgaben"]},
            "transfer": {"aufgaben_ordner": transfer_ordner, "aufgaben": transfer_aufgaben},
            "auftrag": holdout.get("auftrag_ordner"), "thema": holdout.get("thema"),
            "proben": holdout.get("proben_im_training") or [], "angelegt": time.strftime("%Y-%m-%dT%H:%M:%S")}
    # Frühstopp aus: Beide Varianten sollen genau dasselbe Trainingsbudget bekommen.
    plan["werte"]["geduld"] = 0
    destillation._json_schreiben(os.path.join(ordner, "experiment.json"), plan)
    return plan


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    unter = ap.add_subparsers(dest="befehl", required=True)
    s = unter.add_parser("starten")
    s.add_argument("ordner")
    a = ap.parse_args(argv)
    e = Experiment(a.ordner, melden=lambda t: print(t, flush=True))
    e.lauf()
    return 0


if __name__ == "__main__":
    sys.exit(main())
