#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Update-Test: alte Version mit Daten füllen, neue Version darauf starten, Sicherung einspielen.

    python3 werkzeuge/update_test.py [--alt v0.1048]

1. Die alte Version (Git-Tag) startet mit leerem Speicher; über ihre API entstehen
   typische Daten: Einrichtung, Chat mit Nachrichten, Wissen, Prompt, Agent,
   Einstellung, Zugangsschlüssel, Zeitplan, Datei im Workspace.
2. Die neue Version (dieser Arbeitsstand) startet auf DEMSELBEN Speicher: Alles muss
   noch da sein, die Einrichtung darf nicht erneut verlangt werden, der Schlüssel gilt.
3. Sicherung (/api/backup) in einen leeren Speicher auspacken, neu starten, vergleichen.

Eigener Wegwerf-Speicher, eigene Ports, der echte Ordner wird nie angefasst.
Gibt 0 zurück, wenn alles stimmt.
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ergebnis = {"gut": [], "schlecht": []}


def pruefe(name, bedingung, zusatz=""):
    (ergebnis["gut"] if bedingung else ergebnis["schlecht"]).append(name)
    print("  %s %s%s" % ("✓" if bedingung else "✗", name, "" if bedingung or not zusatz else "  — %s" % zusatz), flush=True)


class Instanz:
    def __init__(self, code, speicher, port):
        self.basis = "http://127.0.0.1:%d" % port
        self.p = subprocess.Popen([sys.executable, os.path.join(code, "server.py")], cwd=code,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  env=dict(os.environ, PORT=str(port), HOST="127.0.0.1", STORAGE_DIR=speicher,
                                           DOWOS_MESH_PORT=str(47800 + port % 100)))
        for _ in range(60):
            try:
                self.ruf("/api/version")
                return
            except Exception:
                time.sleep(0.5)
        raise SystemExit("Server startet nicht: %s" % code)

    def ruf(self, pfad, rumpf=None, token=None, roh=False):
        kopf = {"Content-Type": "application/json"}
        if token:
            kopf["Authorization"] = "Bearer " + token
        r = urllib.request.Request(self.basis + pfad, headers=kopf,
                                   data=json.dumps(rumpf).encode() if rumpf is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=60) as a:
                daten = a.read()
                return daten if roh else json.loads(daten.decode() or "{}")
        except urllib.error.HTTPError as e:
            if e.code == 404 and pfad == "/api/version":
                return {}
            return {"http": e.code}

    def stopp(self):
        self.p.terminate()
        try:
            self.p.wait(10)
        except subprocess.TimeoutExpired:
            self.p.kill()


def bestand(i, token):
    """Was ein Nutzer wiederfinden will — in vergleichbarer Form."""
    sitzungen = i.ruf("/api/sessions")
    sitzungen = sitzungen if isinstance(sitzungen, list) else sitzungen.get("sessions", [])
    erste = next((s for s in sitzungen if s.get("title") == "Update-Test"), {})
    nachrichten = i.ruf("/api/sessions/%s/messages" % erste.get("id")) if erste else []
    nachrichten = nachrichten if isinstance(nachrichten, list) else nachrichten.get("messages", [])
    namen = lambda liste: sorted(x.get("name", "") for x in (liste if isinstance(liste, list) else []))
    return {
        "einrichtung_noetig": i.ruf("/api/einrichtung").get("noetig"),
        "sitzung": bool(erste), "nachrichten": [m.get("content") for m in nachrichten],
        "wissen": "Hausordnung-Test" in namen(i.ruf("/api/knowledge")),
        "prompt": "Zusammenfassen-Test" in namen(i.ruf("/api/prompts")),
        "agent": "Prüfer-Test" in namen(i.ruf("/api/agents")),
        "einstellung": i.ruf("/api/settings").get("TEMPERATURE"),
        "zeitplan": "Briefing-Test" in [p.get("name") for p in i.ruf("/api/zeitplan").get("plaene", [])],
        "datei": "update" in (i.ruf("/api/sandbox/workspaces").get("workspaces") or []),
        "schluessel_gilt": i.ruf("/api/extern/info", token=token).get("http") is None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alt", default="v0.1048")
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="dowos-update-")
    try:
        alt_code = os.path.join(tmp, "alt")
        os.makedirs(alt_code)
        archiv = subprocess.run(["git", "archive", a.alt], cwd=APP, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", alt_code], input=archiv, check=True)
        speicher = os.path.join(tmp, "daten")
        print("1. Alte Version %s füllen" % a.alt, flush=True)
        alt = Instanz(alt_code, speicher, 3421)
        alt.ruf("/api/einrichtung", {"modell": "qwen3:4b", "schalter": {"SANDBOX_ENABLED": True}})
        s = alt.ruf("/api/sessions", {"title": "Update-Test"})
        sid = s.get("id") or s.get("session_id")
        for text in ("Erste Frage mit Umlaut: Grüße", "Zweite Nachricht ✓"):
            alt.ruf("/api/sessions/%s/messages" % sid, {"role": "user", "content": text})
        alt.ruf("/api/knowledge", {"name": "Hausordnung-Test", "content": "Ruhezeit ab 22 Uhr.", "folder": ""})
        alt.ruf("/api/prompts", {"name": "Zusammenfassen-Test", "content": "Fasse zusammen: {{text}}"})
        alt.ruf("/api/agents", {"name": "Prüfer-Test", "system_prompt": "Du prüfst.", "description": "Test"})
        alt.ruf("/api/settings", {"TEMPERATURE": "0.4", "AUSGANG_STUFE": "urteil"})
        token = alt.ruf("/api/tokens", {"name": "Update-Gerüst", "rolle": "harness"}).get("token")
        alt.ruf("/api/zeitplan/neu", {"name": "Briefing-Test", "was": "briefing", "art": "taeglich", "uhrzeit": "07:30"})
        alt.ruf("/api/sandbox/save", {"workspace": "update", "name": "notiz.txt", "content": "bleibt"})
        vorher = bestand(alt, token)
        alt.stopp()
        print("   vorher: %s" % json.dumps(vorher, ensure_ascii=False), flush=True)
        pruefe("alte Version hat alle Testdaten", vorher["sitzung"] and vorher["wissen"] and vorher["datei"]
               and vorher["schluessel_gilt"] and len(vorher["nachrichten"]) == 2, vorher)

        print("2. Neue Version auf denselben Daten", flush=True)
        neu = Instanz(APP, speicher, 3422)
        nachher = bestand(neu, token)
        for k, v in vorher.items():
            pruefe("nach dem Update: %s" % k, nachher.get(k) == v, "vorher %r, nachher %r" % (v, nachher.get(k)))
        print("3. Sicherung und Wiederherstellen", flush=True)
        zipdaten = neu.ruf("/api/backup", roh=True)
        neu.stopp()
        wieder = os.path.join(tmp, "wieder")
        os.makedirs(wieder)
        with zipfile.ZipFile(io.BytesIO(zipdaten)) as z:
            namen = z.namelist()
            z.extractall(wieder)
        pruefe("Sicherung enthält die Datenbank", any(n.endswith("dowos.db") for n in namen), namen[:5])
        # Liegt der Speicher im Zip unter einem Unterordner, dorthin zeigen
        db = next((os.path.join(wieder, n) for n in namen if n.endswith("dowos.db")), None)
        w = Instanz(APP, os.path.dirname(db) if db else wieder, 3423)
        nach_sicherung = bestand(w, token)
        w.stopp()
        for k, v in vorher.items():
            pruefe("nach dem Wiederherstellen: %s" % k, nach_sicherung.get(k) == v,
                   "vorher %r, danach %r" % (v, nach_sicherung.get(k)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nUPDATE-TEST: %d gut, %d schlecht" % (len(ergebnis["gut"]), len(ergebnis["schlecht"])))
    for s in ergebnis["schlecht"]:
        print("  ✗", s)
    return 0 if not ergebnis["schlecht"] else 1


if __name__ == "__main__":
    sys.exit(main())
