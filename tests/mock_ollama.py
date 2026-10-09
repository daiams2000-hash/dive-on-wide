#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mock-Ollama für die Dive-on-Wide-Testsuite.

Verhält sich wie ein echter Ollama-Server (/api/tags, /api/chat mit und ohne
Streaming), antwortet aber deterministisch. So laufen die Tests in Sekunden
statt Minuten und ohne geladenes Modell.

Besonderheit: Der Mock erkennt am System-Prompt, welche Art Antwort erwartet
wird (JSON für Skill-/Agenten-Fabrik und Coding-Agent) und liefert passendes,
gültiges JSON — damit werden auch die Parser-Pfade echt getestet.
"""

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CODER_OK = json.dumps({
    "filename": "main.py", "runtime": "python",
    "code": "print('Hallo aus der Sandbox')\nprint(6 * 7)",
    "erklaerung": "Gibt einen Gruß und das Produkt aus.",
}, ensure_ascii=False)

CODER_BROKEN = json.dumps({
    "filename": "main.py", "runtime": "python",
    "code": "print(nicht_definiert)",
    "erklaerung": "Absichtlich fehlerhaft (Testfall für die Selbstkorrektur).",
}, ensure_ascii=False)

SKILL_JSON = json.dumps({
    "name": "Testskill", "trigger_word": "testskill",
    "description": "Von der Fabrik erzeugter Testskill.",
    "steps": [{"name": "Schritt A", "system_prompt": "Tu etwas."},
              {"name": "Schritt B", "system_prompt": "Tu mehr."}],
}, ensure_ascii=False)

AGENT_JSON = json.dumps({
    "name": "Testagent", "emoji": "🧪", "description": "Von der Fabrik erzeugt.",
    "system_prompt": "Du bist ein Testagent.",
}, ensure_ascii=False)


# Ein Werkbank-Lauf wie von einem echten Modell: lesen, Fehler beheben,
# prüfen, fertig. Der Workspace dazu wird im Test angelegt.
WERKBANK_SKRIPT = [
    {"gedanke": "Code ansehen", "werkzeug": "lesen", "argumente": {"pfad": "rechnen.py"}},
    {"gedanke": "Ganzzahldivision ist der Fehler", "werkzeug": "ersetzen",
     "argumente": {"pfad": "rechnen.py", "alt": "x // 2", "neu": "x / 2"}},
    {"gedanke": "prüfen", "werkzeug": "ausfuehren",
     "argumente": {"befehl": "python3 -c \"from rechnen import halbiere; print(halbiere(3))\""}},
    {"gedanke": "geprüft", "werkzeug": "fertig",
     "argumente": {"zusammenfassung": "halbiere teilt jetzt richtig"}},
]


class MockOllama(BaseHTTPRequestHandler):
    # Steuerung durch die Tests
    fail_mode = False        # True → simuliert nicht erreichbaren Server
    coder_fail_first = False  # True → erster Coding-Versuch schlägt fehl
    fabrik_antwort = None     # Text, den die Aufgabenfabrik als Entwurf bekommt
    _coder_calls = 0
    _planner_calls = 0       # zählt Planner-Aufrufe (Computer-Use)
    _lock = threading.Lock()
    calls = []               # Aufzeichnung aller Anfragen für Assertions

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if MockOllama.fail_mode:
            self.send_response(500)
            self.end_headers()
            return
        if self.path == "/api/tags":
            return self._json({"models": [
                {"name": "qwen2.5-coder:14b", "size": 9_000_000_000,
                 "details": {"family": "qwen2"}},
                {"name": "llama3:8b", "size": 4_700_000_000,
                 "details": {"family": "llama"}},
            ]})
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if MockOllama.fail_mode:
            self.send_response(500)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else {}
        if self.path == "/api/pull":
            # Nachgebautes Laden: Fortschritt als Zeilenstrom, wie Ollama ihn schickt.
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for z in ({"status": "pulling manifest"}, {"status": "pulling abc", "total": 1000, "completed": 500},
                      {"status": "pulling abc", "total": 1000, "completed": 1000}, {"status": "success"}):
                self.wfile.write((json.dumps(z) + "\n").encode())
            return
        if self.path == "/api/show":
            # Fähigkeiten wie bei Ollama. Andere Tests (Computer-Use) brauchen Bildverständnis —
            # nur ein Modell, das ausdrücklich „ohne-bild“ heißt, hat keines.
            koennen = ["completion"] if "ohne-bild" in str(body.get("model")) else ["completion", "vision"]
            return self._json({"capabilities": koennen})
        msgs = body.get("messages", [])
        system = msgs[0]["content"] if msgs else ""
        user = msgs[-1]["content"] if msgs else ""
        with MockOllama._lock:
            MockOllama.calls.append({"model": body.get("model"), "system": system,
                                     "user": user, "stream": body.get("stream"),
                                     "bilder": sum(len(m.get("images") or []) for m in msgs)})

        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for token in ["Mock", "-", "Antwort", " im", " Stream"]:
                self.wfile.write(
                    (json.dumps({"message": {"role": "assistant", "content": token},
                                 "done": False}) + "\n").encode("utf-8"))
                self.wfile.flush()
            self.wfile.write((json.dumps({"done": True}) + "\n").encode("utf-8"))
            return

        if MockOllama.fabrik_antwort and "Übungsaufgabe für einen Coding-Agenten" in system:
            return self._json({"message": {"role": "assistant", "content": MockOllama.fabrik_antwort}})
        if "Git-Commit-Nachricht" in system:
            return self._json({"message": {"role": "assistant", "content": "Division in halbiere korrigieren\n\nGanzzahldivision lieferte 1 statt 1.5."}})
        if "wiederverwendbaren Skill im SKILL.md-Format" in system:
            return self._json({"message": {"role": "assistant", "content":
                "NAME: halbieren-reparieren\nBESCHREIBUNG: Wenn Divisionen falsch runden.\n---\n1. Datei lesen\n2. // durch / ersetzen\n3. Tests laufen lassen"}})
        if "Werkbank-Agent von Dive on Wide" in system:
            # Die Werkbank führt ein Gespräch über viele Schritte: Die Zahl der
            # bisherigen Modellantworten bestimmt den nächsten Schritt.
            n = sum(1 for m in msgs if m.get("role") == "assistant")
            schritt = WERKBANK_SKRIPT[min(n, len(WERKBANK_SKRIPT) - 1)]
            return self._json({"message": {"role": "assistant",
                                           "content": json.dumps(schritt, ensure_ascii=False)}})
        return self._json({"message": {"role": "assistant",
                                       "content": self._answer(system, user)}})

    def _answer(self, system, user):
        if "LANGSAM" in user:
            time.sleep(1.5)          # Testfenster für den Lauf-Abbruch
        if "Orchestrator von Dive on Wide" in system:
            # Plant einen schlanken 2-Schritt-Ablauf (Agent → Denkschritt).
            return json.dumps({"plan": "Ziel in zwei Schritten lösen",
                "schritte": [
                    {"typ": "agent", "rolle": "Analyst, sachlich",
                     "anweisung": "Sammle die Kernpunkte."},
                    {"typ": "think", "anweisung": "Ordne das Ergebnis."}]},
                ensure_ascii=False)
        if "steuerst den Computer" in system:
            # Computer-Use-Planner: erst eine Klick-Aktion, dann fertig.
            with MockOllama._lock:
                MockOllama._planner_calls += 1
                n = MockOllama._planner_calls
            if n == 1:
                return json.dumps({"gedanke": "Ich klicke den Testknopf",
                                   "aktion": "klick", "ziel": "der Testknopf"},
                                  ensure_ascii=False)
            return json.dumps({"gedanke": "Erledigt", "aktion": "fertig",
                               "fertig_text": "Aufgabe erledigt."},
                              ensure_ascii=False)
        if "Coding-Agent" in system and "KORRIGIERTE" in system:
            return CODER_OK                      # Korrekturlauf klappt immer
        if "Coding-Agent" in system:
            if MockOllama.coder_fail_first:
                with MockOllama._lock:
                    MockOllama._coder_calls += 1
                    first = MockOllama._coder_calls == 1
                return CODER_BROKEN if first else CODER_OK
            return CODER_OK
        if "Skill-Fabrik" in system:
            return SKILL_JSON
        if "Agenten-Fabrik" in system:
            return AGENT_JSON
        if "Websuchanfrage" in system:
            return "testanfrage lokale ki"
        return "MOCK: " + user[:120].replace("\n", " ")


class MockGrounding(BaseHTTPRequestHandler):
    """Mock des Grounding-Servers (OpenAI-kompatibel, wie mlx_vlm/vLLM).

    Antwortet im LocateAnything-Format mit einer festen, 0-1000-normierten
    Box — so lässt sich „Zeig mir wo“ komplett ohne Modell testen."""

    def log_message(self, *a):
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            return self._json({"data": [{"id": "nvidia/LocateAnything-3B"}]})
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path == "/v1/chat/completions":
            return self._json({"choices": [{"message": {
                "role": "assistant",
                "content": "<ref>testknopf</ref><box><250><250><750><750></box>",
            }}]})
        self.send_response(404)
        self.end_headers()


def serve_grounding(port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), MockGrounding)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def serve(port=11434):
    srv = ThreadingHTTPServer(("127.0.0.1", port), MockOllama)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11434
    serve(port)
    print("Mock-Ollama läuft auf Port %d" % port)
    while True:
        time.sleep(1)
