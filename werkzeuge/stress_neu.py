#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stresstest der Funktionen vom 07./08.10.2026: Chat mit Wissen und Verdichtung, große Wissensbasis, viele
gleichzeitige Chats, Schwärme und verkettete Rhythmus-Einträge — gegen ein Scheinmodell, damit allein der Server
gemessen wird.

    python3 werkzeuge/stress_neu.py [--port 3298] [--wissen 3000] [--parallel 40]

Gemessen: Fehler, Antwortzeiten (p50/p95), Speicher (RSS), Threads und offene Dateien des Servers vor, während und
nach der Last — ein Leck zeigt sich daran, dass die Zahlen nach der Last nicht zurückgehen. Alles in einem
Wegwerf-Speicher; der echte Ordner wird nicht angefasst.
"""
import argparse
import concurrent.futures
import http.client
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP, "tests"))

WOERTER = ("Solarzelle Akku Spannung Tiefschlaf Firmware Sensor Funk Antenne Gehäuse Platine Lötstelle Kondensator "
           "Tomaten Hochbeet Dünger Gießkanne Kompost Saatgut Ernte Herbst Frost Mulch Regenwasser Schnecken "
           "Ölwechsel Reifen Bremse Zahnriemen Batterie Zündkerze Kühlmittel Kilometer Werkstatt Rechnung "
           "Rezept Mehl Hefe Teig Ofen Temperatur Minuten Butter Zucker Salz Pfanne Nudeln Soße Gewürz").split()


SCHLUESSEL = {"wert": ""}
FEHLERTEXTE = []


def anfrage(port, methode, pfad, koerper=None, frist=120, strom=False):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=frist)
    t0 = time.time()
    c.request(methode, pfad, body=json.dumps(koerper).encode() if koerper is not None else None,
              headers={"Content-Type": "application/json", "Authorization": "Bearer " + SCHLUESSEL["wert"]})
    r = c.getresponse()
    erstes = None
    if strom:
        teile = []
        while True:
            z = r.readline()
            if not z:
                break
            if erstes is None:
                erstes = time.time() - t0
            teile.append(z)
        daten = b"".join(teile)
    else:
        daten = r.read()
    c.close()
    return r.status, daten, time.time() - t0, erstes


def messen(pid):
    rss = int(subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip() or 0)
    threads = len(subprocess.run(["ps", "-M", "-p", str(pid)], capture_output=True, text=True).stdout.splitlines()) - 1
    fds = len(subprocess.run(["lsof", "-p", str(pid)], capture_output=True, text=True).stdout.splitlines()) - 1
    return {"rss_mb": round(rss / 1024, 1), "threads": threads, "fds": fds}


def eintrag_text(z, i):
    return "# Notiz %d\n" % i + " ".join(z.choice(WOERTER) for _ in range(300))


def chat(port, frage, nachrichten=None):
    k = {"model": "qwen2.5-coder:14b", "sprache": "de",
         "messages": nachrichten or [{"role": "system", "content": "Du bist hilfreich."}, {"role": "user", "content": frage}]}
    st, daten, dauer, erstes = anfrage(port, "POST", "/api/chat", k, strom=True)
    if st != 200:
        FEHLERTEXTE.append(daten[:200].decode("utf-8", "replace"))
    meta = {}
    for z in daten.splitlines():
        try:
            d = json.loads(z)
            if "wissen" in d or "kontext" in d:
                meta = d
        except ValueError:
            pass
    return st, dauer, erstes, meta


def p(werte, q):
    return round(sorted(werte)[min(len(werte) - 1, int(len(werte) * q))], 3) if werte else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3298)
    ap.add_argument("--wissen", type=int, default=3000)
    ap.add_argument("--parallel", type=int, default=40)
    a = ap.parse_args()
    import mock_ollama
    mock_port = a.port + 100
    mock_ollama.serve(mock_port)
    speicher = tempfile.mkdtemp(prefix="dowos-stress-")
    umgebung = dict(os.environ, PORT=str(a.port), STORAGE_DIR=speicher, DOWOS_MESH_PORT=str(a.port + 200),
                    OLLAMA_BASE_URL="http://127.0.0.1:%d" % mock_port, DEFAULT_MODEL="qwen2.5-coder:14b")
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=APP, env=umgebung,
                            stdout=open(os.path.join(speicher, "server.log"), "w"), stderr=subprocess.STDOUT)
    befunde, bericht = [], {}
    try:
        import re
        for _ in range(90):
            time.sleep(0.5)
            m = re.search(r"dow_[A-Za-z0-9_-]+", open(os.path.join(speicher, "server.log"), errors="replace").read())
            if m:
                SCHLUESSEL["wert"] = m.group(0)
                break
        st = anfrage(a.port, "GET", "/api/sessions")[0]
        if st != 200:
            print("Zugang klappt nicht (Status %s) — Abbruch" % st)
            return 2
        anfrage(a.port, "POST", "/api/einrichtung", {"schalter": {}})
        bericht["start"] = messen(proc.pid)
        print("Start:", bericht["start"], flush=True)

        # 1. Wissensbasis wachsen lassen, je Stufe die Chat-Zeit messen
        z = random.Random(7)
        bisher = 0
        for stufe in (0, 100, 1000, a.wissen):
            t0 = time.time()
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                stati = list(pool.map(lambda i: anfrage(a.port, "POST", "/api/knowledge",
                                                {"name": "notiz-%d.md" % i, "content": eintrag_text(z, i),
                                                 "folder": "stress"})[0], range(bisher, stufe)))
            if any(x != 200 for x in stati):
                befunde.append("Wissen anlegen: Status %s" % sorted(set(stati)))
            bisher = stufe
            zeiten, erste, treffer = [], [], 0
            for i in range(10):
                st, dauer, erst, meta = chat(a.port, "Bei welcher Spannung geht der Akku mit der Solarzelle in den Tiefschlaf?")
                if st != 200:
                    befunde.append("Chat bei %d Einträgen: Status %s" % (stufe, st))
                zeiten.append(dauer)
                erste.append(erst or dauer)
                treffer += bool(meta.get("wissen"))
            bericht["wissen_%d" % stufe] = {"anlegen_s": round(time.time() - t0, 1), "chat_p50": p(zeiten, .5),
                                            "chat_p95": p(zeiten, .95), "erstes_byte_p50": p(erste, .5),
                                            "mit_wissen": "%d/10" % treffer}
            print("Wissen %5d: %s" % (stufe, bericht["wissen_%d" % stufe]), flush=True)

        # 2. Viele gleichzeitige Chats (mit großer Wissensbasis)
        t0 = time.time()
        fehler, zeiten = [], []

        def ein_chat(i):
            try:
                st, dauer, _, _ = chat(a.port, "Frage %d: Wie oft gieße ich die Tomaten im Hochbeet?" % i)
                return st, dauer
            except Exception as e:
                return str(e)[:80], None
        with concurrent.futures.ThreadPoolExecutor(a.parallel) as pool:
            for st, dauer in pool.map(ein_chat, range(a.parallel * 5)):
                if st != 200:
                    fehler.append(st)
                if dauer:
                    zeiten.append(dauer)
        bericht["parallel"] = {"anfragen": a.parallel * 5, "fehler": len(fehler), "p50": p(zeiten, .5),
                               "p95": p(zeiten, .95), "gesamt_s": round(time.time() - t0, 1), "unter_last": messen(proc.pid)}
        if fehler:
            befunde.append("Parallele Chats: %d Fehler, z. B. %s — %s" % (len(fehler), fehler[:3], FEHLERTEXTE[:2]))
        print("Parallel:", bericht["parallel"], flush=True)

        # 3. Lange Unterhaltung: Verdichtung unter kleinem Kontext, mehrfach hintereinander
        anfrage(a.port, "POST", "/api/settings", {"NUM_CTX": "2048"})
        verlauf = [{"role": "system", "content": "Du bist hilfreich."}]
        verdichtet = 0
        for i in range(12):
            verlauf.append({"role": "user", "content": "Runde %d: " % i + "erzähle mehr " * 120})
            st, dauer, _, meta = chat(a.port, "", verlauf)
            if st != 200:
                befunde.append("Verdichtung Runde %d: Status %s" % (i, st))
            k = meta.get("kontext") or {}
            verdichtet += bool(k.get("verdichtet"))
            verlauf.append({"role": "assistant", "content": "Antwort %d " % i + "text " * 120})
            if k.get("prozent", 0) > 150:
                befunde.append("Kontext nach Verdichtung bei %s %%" % k.get("prozent"))
        bericht["verdichtung"] = {"runden": 12, "verdichtet": verdichtet}
        anfrage(a.port, "POST", "/api/settings", {"NUM_CTX": "16384"})
        print("Verdichtung:", bericht["verdichtung"], flush=True)

        # 4. Fünf Schwärme gleichzeitig (Scheinmodell liefert keinen gültigen Plan → sie müssen sauber enden)
        ids = []
        for i in range(5):
            st, d, _, _ = anfrage(a.port, "POST", "/api/schwarm", {"ziel": "Projekt %d" % i, "runden": 2})
            rid = json.loads(d).get("run_id")
            if not rid:
                befunde.append("Schwarm %d nicht gestartet: %s %s" % (i, st, d[:120]))
            else:
                ids.append(rid)
        ende = time.time() + 120
        offen = list(ids)
        while offen and time.time() < ende:
            offen = [r for r in offen if json.loads(anfrage(a.port, "GET", "/api/runs/" + r)[1]).get("status") == "running"]
            time.sleep(1)
        bericht["schwaerme"] = {"gestartet": len(ids), "haengen": len(offen)}
        if offen:
            befunde.append("%d Schwärme hängen nach 120 s" % len(offen))
        print("Schwärme:", bericht["schwaerme"], flush=True)

        # 5. Rhythmus: Vorgänger gelöscht, dann Nachfolger von Hand starten → darf nicht ins Blaue laufen
        agent = json.loads(anfrage(a.port, "GET", "/api/agents")[1])[0]["id"]
        r = json.loads(anfrage(a.port, "POST", "/api/zeitplan/neu", {"name": "A", "was": "agent", "ziel": agent,
                                                                    "art": "einmal", "eingabe": "x"})[1])
        liste = r.get("plaene", r) if isinstance(r, dict) else r
        aid = [x for x in liste if x["name"] == "A"][-1]["id"]
        r = json.loads(anfrage(a.port, "POST", "/api/zeitplan/neu", {"name": "B", "was": "agent", "ziel": agent, "art": "einmal",
                                                                    "eingabe": "y", "aufbauen_auf": aid})[1])
        liste = r.get("plaene", r) if isinstance(r, dict) else r
        bid = [x for x in liste if x["name"] == "B"][-1]["id"]
        anfrage(a.port, "POST", "/api/zeitplan/weg", {"id": aid})
        anfrage(a.port, "POST", "/api/zeitplan/jetzt", {"id": bid})
        time.sleep(4)
        d = json.loads(anfrage(a.port, "GET", "/api/zeitplan")[1])
        b = [x for x in (d.get("plaene", d) if isinstance(d, dict) else d) if x["id"] == bid]
        status = (b[0].get("letzter_status") if b else "?")
        bericht["rhythmus_vorgaenger_weg"] = status
        if status not in ("warn",):
            befunde.append("Nachfolger ohne Vorgänger lief trotzdem: Status %r" % status)
        print("Rhythmus, Vorgänger gelöscht:", status, flush=True)

        # 6. Ein Vorgänger und neun Nachfolger, alle in derselben Minute fällig, drei Laufplätze:
        #    Kein Festfahren, und jeder Nachfolger startet erst NACH dem Vorgänger (sonst lädt er dessen alten Stand).
        jetzt_uhr = time.strftime("%H:%M", time.localtime(time.time() + 60))   # nächste Minute — die laufende gilt als vorbei
        r = json.loads(anfrage(a.port, "POST", "/api/zeitplan/neu", {"name": "V", "was": "agent", "ziel": agent,
                                                                    "art": "einmal", "uhrzeit": jetzt_uhr, "eingabe": "v"})[1])
        liste = r.get("plaene", r) if isinstance(r, dict) else r
        vid = [x for x in liste if x["name"] == "V"][-1]["id"]
        nach = []
        for i in range(9):
            r = json.loads(anfrage(a.port, "POST", "/api/zeitplan/neu", {"name": "N%d" % i, "was": "agent", "ziel": agent,
                                                                        "art": "einmal", "uhrzeit": jetzt_uhr,
                                                                        "eingabe": "n", "aufbauen_auf": vid})[1])
            liste = r.get("plaene", r) if isinstance(r, dict) else r
            nach.append([x for x in liste if x["name"] == "N%d" % i][-1]["id"])
        ende, fertig = time.time() + 360, False
        while time.time() < ende:
            d = json.loads(anfrage(a.port, "GET", "/api/zeitplan")[1])
            pl = {x["id"]: x for x in (d.get("plaene", d) if isinstance(d, dict) else d)}
            if all(pl.get(i, {}).get("letzter_status") for i in [vid] + nach):
                fertig = True
                break
            time.sleep(2)
        if not fertig:
            befunde.append("Kette V → 9 Nachfolger nach 240 s nicht fertig (Festfahren?)")
        else:
            lauf = lambda rid: json.loads(anfrage(a.port, "GET", "/api/runs/" + rid)[1])
            v_ende = lauf(pl[vid]["letzter_lauf_id"]).get("updated_at", 0)
            zu_frueh = [pl[i]["name"] for i in nach if lauf(pl[i]["letzter_lauf_id"]).get("created_at", 0) < v_ende - 0.5]
            stati = sorted(set(pl[i]["letzter_status"] for i in nach))
            bericht["kette_gleichzeitig"] = {"nachfolger": 9, "zu_frueh": zu_frueh, "stati": stati}
            if zu_frueh:
                befunde.append("Nachfolger vor dem Vorgänger gestartet: %s" % zu_frueh)
            if stati != ["done"]:
                befunde.append("Nachfolger-Status: %s" % stati)
        print("Kette, alle gleichzeitig fällig:", bericht.get("kette_gleichzeitig"), flush=True)

        # 7. Nach der Last: geht der Verbrauch zurück?
        time.sleep(20)
        bericht["nach_last"] = messen(proc.pid)
        print("Nach der Last:", bericht["nach_last"], flush=True)
        if bericht["nach_last"]["threads"] > bericht["start"]["threads"] + 15:
            befunde.append("Threads wachsen: %s → %s" % (bericht["start"]["threads"], bericht["nach_last"]["threads"]))
        if bericht["nach_last"]["fds"] > bericht["start"]["fds"] + 30:
            befunde.append("Offene Dateien wachsen: %s → %s" % (bericht["start"]["fds"], bericht["nach_last"]["fds"]))
        log = open(os.path.join(speicher, "server.log"), encoding="utf-8", errors="replace").read()
        if "Traceback" in log:
            befunde.append("Traceback im Serverprotokoll: " + log[log.index("Traceback"):][:400].replace("\n", " | "))
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()
    print("\nBERICHT", json.dumps(bericht, ensure_ascii=False))
    print("BEFUNDE (%d):" % len(befunde))
    for b in befunde:
        print("  -", b)
    shutil.rmtree(speicher, ignore_errors=True)
    return 1 if befunde else 0


if __name__ == "__main__":
    sys.exit(main())
