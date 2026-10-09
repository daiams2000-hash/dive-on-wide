#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API-Fuzz: kaputte, übergroße und feindliche Eingaben an jede harmlose Schnittstelle.

Startet eine Wegwerf-Instanz (eigener Speicher, Scheinmodell statt Ollama) und
schickt ihr so lange Unsinn, bis die Zeit um ist. Ein Befund ist alles, was eine
fremde Anfrage NICHT mit einer ordentlichen Antwort beantworten darf:

  - Status 500 oder eine Python-Ausnahme im Serverprotokoll
  - keine Antwort binnen 30 Sekunden, abgerissene Verbindung
  - der Server lebt danach nicht mehr
  - eine Datei landet außerhalb des Speichers (Kanarienvogel-Ordner)

Nur eine FREIGABELISTE wird angefasst: nichts, was Befehle ausführt, Trainings
startet, ins lokale Netz sendet oder fremde Dienste anschreibt.

    python3 werkzeuge/fuzz.py [--minuten 60] [--port 3097]
"""
import argparse
import http.client
import json
import os
import random
import shutil
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP, "tests"))

GET_EINFACH = ["/api/health", "/api/sessions", "/api/artifacts", "/api/agents", "/api/pipelines", "/api/prompts",
               "/api/skills", "/api/templates", "/api/knowledge", "/api/dashboard", "/api/models", "/api/steptypes",
               "/api/settings", "/api/providers", "/api/tokens", "/api/werkbank/laeufe", "/api/werkbank/regeln",
               "/api/werkbank/gedaechtnis", "/api/werkbank/aktiv", "/api/werkbank/unterbrochen", "/api/training",
               "/api/destillation", "/api/mcp", "/api/einrichtung", "/api/ausgang", "/api/extern/info", "/api/mesh",
               "/api/mesh/verteilt/lage", "/api/zeitplan", "/api/rezept", "/api/sandbox/workspaces", "/api/ps",
               "/api/tags", "/api/werkbank/webhooks", "/api/mesh/kontakte", "/api/mesh/faeden", "/api/mesh/modelle",
               # neu seit 07.10.2026
               "/api/lotse", "/api/lotse?ansicht=werkbank&sprache=en", "/api/lotse?ansicht=%00&sprache=xx",
               "/api/anbieter/suchen", "/api/werkbank/agenten"]
GET_MIT_ID = ["/api/artifacts/%s", "/api/sessions/%s/messages", "/api/sessions/%s", "/api/rezept/%s",
              "/api/training/%s", "/api/destillation/%s", "/api/mcp/%s", "/api/werkbank/laeufe/%s",
              "/api/werkbank/%s", "/api/mesh/faden/%s"]
POST = ["/api/sessions", "/api/artifacts", "/api/chat", "/api/generate", "/api/orchestrator", "/api/agents",
        "/api/pipelines", "/api/prompts", "/api/skills", "/api/templates", "/api/knowledge",
        "/api/notifications/read_all", "/api/tokens", "/api/rezept/plan", "/api/meta/create", "/api/brain/sync",
        "/api/extern/auftrag", "/api/web/fetch", "/api/werkbank/gedaechtnis", "/api/werkbank/regeln",
        "/api/einrichtung", "/api/show", "/api/sandbox/save", "/api/mesh/chat", "/api/mesh/auftrag",
        "/api/destillation/schaetzen", "/api/destillation/bedingungen", "/api/providers/test",
        # neu seit 07.10.2026
        "/api/lotse", "/api/schwarm", "/api/zeitplan/neu", "/api/zeitplan/jetzt", "/api/zeitplan/weg"]
DELETE = ["/api/sessions/%s", "/api/artifacts/%s", "/api/agents/%s", "/api/prompts/%s", "/api/skills/%s",
          "/api/tokens/%s", "/api/werkbank/regeln/%s"]
FELDER = ["title", "name", "content", "prompt", "model", "ziel", "text", "url", "messages", "id", "session_id",
          "steps", "query", "aufgabe", "ordner", "pfad", "path", "filename", "code", "workspace", "inhalt",
          "werkzeug", "regel", "system_prompt", "description", "tags", "stream", "temperature", "max_tokens",
          "rolle", "provider", "base_url", "api_key", "images", "notiz", "modell", "anweisung", "questions",
          "frage", "verlauf", "sprache", "agent_id", "wissen", "web", "angeheftet", "planer", "arbeiter", "runden",
          "testbefehl", "aufbauen_auf", "was", "art", "uhrzeit", "eingabe", "intervall_min", "wochentage"]


def ids(z, kanarien):
    return z.choice(["0", "-1", "999999999999999999999", "../../../../etc/passwd", "..%2F..%2Fsettings",
                     "%00", "a" * 5000, "ä€😀", "1 OR 1=1", "<script>", "null", "NaN", "",
                     "../../../../" + kanarien.lstrip("/") + "/x", "%2e%2e%2f%2e%2e%2f", " ", "\\..\\..\\"])


def wert(z, tiefe=0):
    r = z.random()
    if r < 0.08: return None
    if r < 0.16: return z.choice([0, -1, 2 ** 63, -2 ** 63, 1e308, -1e-308, True, False])
    if r < 0.24: return "x" * z.choice([0, 1, 10000, 200000])
    if r < 0.32: return z.choice(["ä€😀‮\u0000", "../../../../etc/passwd", "${jndi:ldap://x}", "' OR 1=1 --",
                                  "<img src=x onerror=alert(1)>", "http://127.0.0.1:9/", "file:///etc/passwd",
                                  "{{7*7}}", "\r\nX-Injected: 1"])
    if r < 0.40 and tiefe < 3: return [wert(z, tiefe + 1) for _ in range(z.randint(0, 5))]
    if r < 0.48 and tiefe < 3: return {z.choice(FELDER): wert(z, tiefe + 1) for _ in range(z.randint(0, 4))}
    return z.choice(["hallo", "qwen2.5-coder:14b", "Fasse zusammen", "1", "test"])


def rumpf(z, kanarien):
    art = z.random()
    if art < 0.08: return b"das ist kein json", "kein-json"
    if art < 0.14: return json.dumps(z.choice([[], [1, 2], None, 42, "text", True])).encode(), "json-kein-objekt"
    if art < 0.18: return ("[" * 20000 + "]" * 20000).encode(), "tief-verschachtelt"
    if art < 0.21: return json.dumps({"content": "x" * (5 * 1024 * 1024)}).encode(), "fuenf-mb"
    if art < 0.24:
        return json.dumps({"filename": "../../../../" + kanarien.lstrip("/") + "/ausbruch.txt", "path":
                           "../../../../" + kanarien.lstrip("/") + "/ausbruch.txt", "content": "x",
                           "workspace": "../../../../" + kanarien.lstrip("/")}).encode(), "pfad-ausbruch"
    return json.dumps({z.choice(FELDER): wert(z) for _ in range(z.randint(1, 6))}).encode(), "felder"


class Ergebnis:
    def __init__(self):
        self.befunde, self.anfragen, self.lock = {}, 0, threading.Lock()

    def befund(self, art, methode, pfad, nutzlast, detail):
        schluessel = (art, methode, re.sub(r"/[^/]{20,}", "/<lang>", pfad), nutzlast)
        with self.lock:
            if schluessel not in self.befunde:
                self.befunde[schluessel] = {"art": art, "methode": methode, "pfad": pfad[:200],
                                            "nutzlast": nutzlast, "detail": str(detail)[:400], "anzahl": 0}
                print("  BEFUND %-12s %s %s [%s] %s" % (art, methode, pfad[:80], nutzlast, str(detail)[:120]), flush=True)
            self.befunde[schluessel]["anzahl"] += 1


def anfrage(port, schluessel, methode, pfad, koerper=None, kopf_extra=None, frist=30):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=frist)
    kopf = {"Authorization": "Bearer " + schluessel, "Content-Type": "application/json"}
    kopf.update(kopf_extra or {})
    try:
        c.request(methode, pfad, body=koerper, headers=kopf)
        a = c.getresponse()
        return a.status, a.read(2_000_000)
    finally:
        c.close()


def roh(port, daten, frist=10):
    """Eine handgemachte HTTP-Anfrage (falscher Content-Length usw.)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=frist)
    try:
        s.sendall(daten)
        return s.recv(200)
    finally:
        s.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--minuten", type=float, default=60)
    ap.add_argument("--port", type=int, default=3097)
    ap.add_argument("--startwert", type=int, default=1)
    ap.add_argument("--behalten", action="store_true",
                    help="Wegwerf-Instanz nicht entfernen (sonst bleibt nur der Befundbericht)")
    a = ap.parse_args()

    import mock_ollama
    mock_port = a.port + 1
    mock_ollama.serve(mock_port)
    ablage = tempfile.mkdtemp(prefix="dowos-fuzz-")
    speicher = os.path.join(ablage, "storage")
    kanarien = tempfile.mkdtemp(prefix="dowos-kanarien-")
    os.makedirs(speicher)
    log_pfad = os.path.join(ablage, "server.log")
    umgebung = dict(os.environ, PORT=str(a.port), STORAGE_DIR=speicher,
                    OLLAMA_BASE_URL="http://127.0.0.1:%d" % mock_port, DEFAULT_MODEL="qwen2.5-coder:14b",
                    SANDBOX_ENABLED="0", COMPUTER_USE_ENABLED="0")
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=APP, env=umgebung,
                            stdout=open(log_pfad, "w"), stderr=subprocess.STDOUT)
    print("Fuzz: Server PID %d, Port %d, Speicher %s, Kanarien %s" % (proc.pid, a.port, ablage, kanarien), flush=True)
    schluessel = ""
    for _ in range(90):
        time.sleep(1)
        m = re.search(r"dow_[A-Za-z0-9_-]+", open(log_pfad, errors="replace").read())
        if m:
            schluessel = m.group(0); break
    if not schluessel:
        print("Kein Zugangsschluessel — Abbruch"); proc.kill(); return 2
    e = Ergebnis()
    z = random.Random(a.startwert)
    ende = time.time() + a.minuten * 60
    log_stand = os.path.getsize(log_pfad)

    def pruefen_nach(methode, pfad, nutzlast):
        nonlocal log_stand
        if proc.poll() is not None:
            e.befund("server-tot", methode, pfad, nutzlast, "Exitcode %s" % proc.returncode)
            return False
        groesse = os.path.getsize(log_pfad)
        if groesse > log_stand:
            with open(log_pfad, errors="replace") as f:
                f.seek(log_stand)
                neu = f.read()
            log_stand = groesse
            # Ausnahmen nach Herkunft, nicht nach der letzten Protokollzeile: Sie
            # koennen aus Hintergrund-Faeden stammen, die zu einer frueheren
            # Anfrage gehoeren.
            for rahmen, ausnahme in re.findall(r"Traceback \(most recent call last\):\n(.*?)\n(\w[\w.]*(?:Error|Exception)[^\n]*)", neu, re.S):
                eigene = [f for f in re.findall(r'File "([^"]+)", line (\d+), in (\w+)', rahmen) if "/app/" in f[0]]
                ort = "%s:%s %s" % (os.path.basename(eigene[-1][0]), eigene[-1][1], eigene[-1][2]) if eigene else "?"
                e.befund("ausnahme", "LOG", ort, "-", ausnahme)
        for wurzel, _, dateien in os.walk(kanarien):
            if dateien:
                e.befund("pfad-ausbruch", methode, pfad, nutzlast, os.path.join(wurzel, dateien[0]))
        return True

    # Handgemachte Anfragen zuerst — einmal je Fall.
    for name, daten in (("content-length-negativ", b"POST /api/sessions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nContent-Length: -5\r\n\r\n{}" % schluessel.encode()),
                        ("content-length-zu-gross", b"POST /api/sessions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nContent-Length: 1000\r\n\r\n{}" % schluessel.encode()),
                        ("content-length-text", b"POST /api/sessions HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer %s\r\nContent-Length: abc\r\n\r\n{}" % schluessel.encode()),
                        ("kaputte-zeile", b"\x00\xff GET / HTTP/9.9\r\n\r\n"),
                        ("riesiger-kopf", b"GET /api/health HTTP/1.1\r\nX: " + b"a" * 100000 + b"\r\n\r\n")):
        t = time.time()
        try:
            # Wer weniger schickt als angekuendigt, wird nach der Frist des Servers (60 s) getrennt.
            antwort = roh(a.port, daten, frist=75 if name == "content-length-zu-gross" else 10)
            if b" 500 " in antwort[:20]:
                e.befund("status-500", "RAW", name, name, antwort[:80])
        except socket.timeout:
            e.befund("haengt", "RAW", name, name, "keine Antwort in 10 s")
        except OSError as fehler:
            pass                            # Verbindung abgelehnt/geschlossen ist eine ordentliche Antwort
        pruefen_nach("RAW", name, name)
        # Haengt ein Handler-Thread weiter? Dann ist die Gesundheit das Mass.
        try:
            anfrage(a.port, schluessel, "GET", "/api/health", frist=10)
        except Exception as fehler:
            e.befund("server-antwortet-nicht", "RAW", name, name, fehler)

    try:
        return _schleife(a, z, e, ende, proc, schluessel, kanarien, pruefen_nach, ablage)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def _schleife(a, z, e, ende, proc, schluessel, kanarien, pruefen_nach, ablage):
    runde = 0
    while time.time() < ende and proc.poll() is None:
        runde += 1
        wahl = z.random()
        if wahl < 0.2:
            methode, pfad, koerper, nutzlast = "GET", z.choice(GET_EINFACH) + z.choice(["", "?limit=-1", "?q=%00", "?x=" + "a" * 3000]), None, "query"
        elif wahl < 0.4:
            methode, pfad, koerper, nutzlast = "GET", z.choice(GET_MIT_ID) % ids(z, kanarien), None, "id"
        elif wahl < 0.9:
            koerper, nutzlast = rumpf(z, kanarien)
            methode, pfad = "POST", z.choice(POST)
        else:
            methode, pfad, koerper, nutzlast = "DELETE", z.choice(DELETE) % ids(z, kanarien), None, "id"
        pfad = urllib.parse.quote(pfad, safe="/?=&%.:-_~")
        e.anfragen += 1
        t = time.time()
        try:
            status, antwort = anfrage(a.port, schluessel, methode, pfad, koerper)
            if status >= 500 and status not in (502, 503, 504):   # Vorgelagertes Modell nicht erreichbar: richtige Antwort
                e.befund("status-%d" % status, methode, pfad, nutzlast, antwort[:200])
            if time.time() - t > 20:
                e.befund("langsam", methode, pfad, nutzlast, "%.0f s" % (time.time() - t))
        except (socket.timeout, TimeoutError):
            e.befund("haengt", methode, pfad, nutzlast, "keine Antwort in 30 s")
        except (ConnectionError, http.client.HTTPException, OSError) as fehler:
            if nutzlast == "fuenf-mb" and isinstance(fehler, (BrokenPipeError, ConnectionResetError)):
                pass                        # zu gross, ungelesen abgewiesen — richtig
            else:
                e.befund("verbindung", methode, pfad, nutzlast, "%s: %s" % (type(fehler).__name__, fehler))
        if not pruefen_nach(methode, pfad, nutzlast):
            break
        if runde % 500 == 0:
            print("  %d Anfragen, %d Befunde, %.0f min" % (e.anfragen, len(e.befunde), (time.time() - (ende - a.minuten * 60)) / 60), flush=True)

    lebt = proc.poll() is None
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    # Die Wegwerf-Instanz speichert, was sie annimmt — im Nachtlauf 2,4 GB
    # Artefakte zu je 5 MB. Aufbewahrt wird nur der Befundbericht.
    ziel = os.path.join(APP, "storage", "fuzz")
    os.makedirs(ziel, exist_ok=True)
    bericht = os.path.join(ziel, "befunde_%s.json" % time.strftime("%Y%m%d_%H%M"))
    json.dump(sorted(e.befunde.values(), key=lambda b: (b["art"], b["pfad"])), open(bericht, "w"),
              ensure_ascii=False, indent=1)
    print("FUZZ: %d Anfragen, %d verschiedene Befunde, Server am Ende %s. Bericht: %s"
          % (e.anfragen, len(e.befunde), "lebend" if lebt else "TOT", bericht), flush=True)
    for b in sorted(e.befunde.values(), key=lambda b: -b["anzahl"])[:40]:
        print("  %4dx %-12s %-6s %-45s [%s] %s" % (b["anzahl"], b["art"], b["methode"], b["pfad"][:45], b["nutzlast"], b["detail"][:100]))
    if not a.behalten:
        shutil.rmtree(ablage, ignore_errors=True)
        shutil.rmtree(kanarien, ignore_errors=True)
    return 0 if not e.befunde and lebt else 1


if __name__ == "__main__":
    sys.exit(main())
