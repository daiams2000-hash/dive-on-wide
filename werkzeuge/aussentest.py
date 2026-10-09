#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Außentest: Was geht auf DIESEM Rechner? Ein Befehl, ein Bericht.

Für jeden Rechner, auf dem Dive on Wide zum ersten Mal läuft — ein anderer Mac, ein
Linux-Rechner, Windows. Im entpackten Dive-on-Wide-Ordner:

    python3 werkzeuge/aussentest.py            (Windows: py werkzeuge\\aussentest.py)

Dauert mit Testlauf einige Minuten; `--schnell` lässt den Testlauf weg. Am Ende
steht ein Bericht auf dem Schirm und in einer Datei daneben. Den bitte
vollständig zurückschicken — er enthält keine Rechnernamen, keine Pfade aus
dem Heimatordner und keine Schlüssel.

Jede Prüfung ist für sich gekapselt: Scheitert eine, laufen die anderen weiter,
und der Bericht sagt, was gescheitert ist und warum.
"""
import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
HEIM = os.path.expanduser("~")
OLLAMA = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
ZEILEN = []


def neutral(text):
    """Heimatordner und Rechnername aus allem entfernen, was hinausgeht."""
    text = str(text).replace(HEIM, "~")
    name = socket.gethostname()
    if name:
        text = text.replace(name, "<rechner>")
    return re.sub(r"dow_[A-Za-z0-9_-]{8,}", "dow_<schluessel>", text)


def sag(text=""):
    text = neutral(text)
    ZEILEN.append(text)
    print(text, flush=True)


def pruefung(titel):
    def huelle(fn):
        def lauf(*a, **k):
            sag("")
            sag("## " + titel)
            try:
                fn(*a, **k)
            except Exception as e:
                sag("  FEHLER in dieser Pruefung: %s: %s" % (type(e).__name__, str(e)[:200]))
        return lauf
    return huelle


@pruefung("System")
def system():
    sag("  Betriebssystem : %s %s (%s)" % (platform.system(), platform.release(), platform.machine()))
    sag("  Python         : %s" % platform.python_version())
    sag("  Prozessoren    : %s" % os.cpu_count())
    try:
        from mesh import ressourcen
        gesamt, frei = ressourcen.speicher()
        sag("  Arbeitsspeicher: %s gesamt, %s verfuegbar" % (
            "%.1f GB" % (gesamt / 1e9) if gesamt else "unbekannt",
            "%.1f GB" % (frei / 1e9) if frei else "unbekannt"))
    except Exception as e:
        sag("  Arbeitsspeicher: nicht ermittelbar (%s)" % e)
    # Die VERSION-Datei liegt beim Betreuer eine Ebene ueber dem Programm und
    # reist NICHT im Paket mit. Die Nummer steht aber sicher im Server selbst —
    # ohne sie waere ein zurueckgeschickter Bericht keiner Fassung zuzuordnen.
    try:
        quelle = open(os.path.join(APP, "server.py"), encoding="utf-8").read()
        m = re.search(r'server_version = "DowOS/Alpha-([0-9.]+)"', quelle)
        version = m.group(1) if m else "(nicht gefunden)"
    except OSError:
        version = "(server.py fehlt)"
    sag("  Dive-on-Wide-Version : %s" % version)
    frei = shutil.disk_usage(APP).free / 1e9
    sag("  Platz frei     : %.0f GB" % frei)


@pruefung("Port 3000")
def port():
    s = socket.socket()
    try:
        s.bind(("0.0.0.0", 3000))
        sag("  frei — Dive on Wide kann dort starten")
    except OSError as e:
        sag("  BELEGT (%s) — Dive on Wide mit PORT=3001 starten" % e.strerror)
    finally:
        s.close()


@pruefung("Ollama (das lokale Modell)")
def ollama():
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5) as r:
            modelle = json.loads(r.read().decode()).get("models", [])
    except Exception as e:
        sag("  NICHT ERREICHBAR (%s). Installieren: https://ollama.com" % e)
        return
    sag("  erreichbar, %d Modell(e)" % len(modelle))
    for m in modelle[:8]:
        faehig = "?"
        try:
            req = urllib.request.Request(OLLAMA + "/api/show", data=json.dumps({"model": m["name"]}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                faehig = ",".join(json.loads(r.read().decode()).get("capabilities") or [])
        except Exception:
            pass
        sag("    - %-40s %5.1f GB  %s" % (m["name"][:40], (m.get("size") or 0) / 1e9, faehig))
    if not modelle:
        sag("  Kein Modell geladen. Zum Beispiel: ollama pull qwen3:8b")


@pruefung("Sandbox (Grenze fuer Befehle des Agenten)")
def sandbox():
    import werkbank as W
    art = W.sandbox_art()
    sag("  Art: %s" % (art or "KEINE"))
    ordner = tempfile.mkdtemp(prefix="dowos-aussentest-")
    wb = W.Werkbank(ordner, "projekt")
    if not art:
        sag("  Ohne Sandbox muss JEDER Befehl freigegeben werden: %s"
            % ("ja, geprueft" if W.braucht_freigabe("ausfuehren", "nie", wb, args={"befehl": "echo"})
               else "NEIN — das waere ein Fehler, bitte melden"))
        return
    ausserhalb = os.path.join(tempfile.gettempdir(), "dowos_aussentest_ausbruch.txt")
    for befehl, was, soll in (("echo drin > a.txt && cat a.txt", "Schreiben im Projekt", True),
                              ("echo raus > %s" % ausserhalb, "Schreiben ausserhalb", False),
                              ("curl -s -m 5 -o /dev/null https://example.com", "Netz", False)):
        e = str(wb.werkzeug("ausfuehren", {"befehl": befehl}))
        erlaubt = e.startswith("exit=0")
        sag("  %-22s %s (%s)" % (was, "erlaubt" if erlaubt else "gesperrt",
                                 "richtig" if erlaubt == soll else "FALSCH — bitte melden"))
    if os.path.exists(ausserhalb):
        sag("  ACHTUNG: Die Datei ausserhalb ist entstanden — die Sandbox haelt nicht!")
        os.remove(ausserhalb)
    shutil.rmtree(ordner, ignore_errors=True)


@pruefung("Computer-Use (Maus und Tastatur)")
def computer():
    import steuerung
    r = steuerung.rueckseite(steuerung.schirm_waehlen("auto"))
    sag("  Rueckseite: %s — %s" % (r.name, r.beschreibung))


@pruefung("Netzwerk (Mesh)")
def mesh():
    from mesh import transport
    sag("  eigene Adressen: %s" % (", ".join(transport.eigene_adressen() or []) or "keine"))
    netz = transport.UdpNetz()
    from mesh import knoten, ressourcen
    k = knoten.Knoten(knoten.KLAUSE, netz=netz,
                      statthalter=ressourcen.Statthalter(zustimmung=True, hoechstens_gb=1.0),
                      modelle=lambda: [])
    if k.starten():
        sag("  Knoten startet, UDP-Empfang auf Port %s" % getattr(netz, "unicast_port", "?"))
        k.stoppen()
        sag("  Der eigentliche Test braucht zwei Geraete: werkzeuge/zwei_geraete.py")
    else:
        sag("  Knoten startet NICHT: %s (meist blockiert eine Firewall UDP)" % (netz.fehler or "unbekannt"))


@pruefung("Verteilte Inferenz (llama.cpp mit RPC)")
def verteilt():
    from mesh import verteilt as v
    l = v.rpc_lage()
    sag("  kann mitrechnen: %s · kann anfuehren: %s" % (l.get("kann_mitrechnen"), l.get("kann_fuehren")))
    for h in l.get("hinweise") or []:
        sag("  Hinweis: %s" % h)


@pruefung("Fremde Agenten (optional)")
def fremde():
    for name in ("claude", "codex"):
        sag("  %-6s %s" % (name, "gefunden" if shutil.which(name) else "nicht installiert"))


@pruefung("Testlauf")
def testlauf(schnell):
    if schnell:
        sag("  uebersprungen (--schnell)")
        return
    t0 = time.time()
    p = subprocess.run([sys.executable, os.path.join(APP, "tests", "run_tests.py")],
                       cwd=APP, capture_output=True, timeout=45 * 60)
    text = re.sub(r"\x1b\[[0-9;]*m", "", p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace"))
    zusammen = [z for z in text.splitlines() if "Tests bestanden" in z or "fehlgeschlagen" in z]
    sag("  %s (%.0f min)" % (zusammen[-1].strip() if zusammen else "kein Ergebnis gefunden",
                             (time.time() - t0) / 60))
    fehler = [z.strip() for z in text.splitlines() if z.strip().startswith("•")]
    for z in fehler[:40]:
        sag("    %s" % z)
    if len(fehler) > 40:
        sag("    ... und %d weitere" % (len(fehler) - 40))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--schnell", action="store_true", help="ohne den Testlauf")
    a = ap.parse_args(argv)
    sag("# Dive on Wide Aussentest — %s" % time.strftime("%d.%m.%Y %H:%M"))
    for p in (system, port, ollama, sandbox, computer, mesh, verteilt, fremde):
        p()
    testlauf(a.schnell)
    datei = "aussentest_%s_%s.txt" % (platform.system().lower(), time.strftime("%Y%m%d_%H%M"))
    with open(datei, "w", encoding="utf-8") as f:
        f.write("\n".join(ZEILEN) + "\n")
    print("\nBericht gespeichert: %s — bitte vollstaendig zurueckschicken." % os.path.abspath(datei))


if __name__ == "__main__":
    main()
