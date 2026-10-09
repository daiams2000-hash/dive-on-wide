# -*- coding: utf-8 -*-
"""GGUF-Dateien als Modell: Dive on Wide startet llama-server bei Bedarf und beendet ihn im Leerlauf.

Warum nicht einfach Ollama? Für Hybridmodelle (Qwen3.8) legt llama.cpp je Agentenschritt
Kontext-Checkpoints an — unter Ollama nicht begrenzbar. qwen3.8-27B lief so am 25.09.2026
den Mac voll. Direkt in llama-server mit `--ctx-checkpoints 4 --cache-ram 0` blieb dasselbe
Modell stabil; die 4-Bit-Fassung (15,3 GiB) passt auf einen Mac mit 24 GB nur mit kleinen
Stapeln und 8-Bit-Kontextspeicher (gemessen 03.10.2026: 16,3 GiB bis 15 000 Token, kein Auslagern).

Anders als Ollama hält ein laufender llama-server seinen Speicher fest. Deshalb:
- gestartet wird erst, wenn das Modell gebraucht wird; vorher gibt Ollama seine Modelle frei,
- nach `leerlauf` Sekunden ohne Anfrage wird er beendet,
- fragt jemand ein Ollama-Modell an, wird ein unbenutzter GGUF-Dienst vorher beendet.
"""
import contextlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request

# Gemessen auf Mac mini M4 Pro, 24 GB (03.10.2026): ohne -ub/-b/-ctk/-ctv „Insufficient Memory“
# schon bei 1 100 Token; mit ihnen stabil bis 15 000 Token.
STANDARD_SCHALTER = ["-ngl", "99", "-np", "1", "--ctx-checkpoints", "4", "--cache-ram", "0",
                     "--jinja", "-fa", "on", "-ub", "256", "-b", "256", "-ctk", "q8_0", "-ctv", "q8_0"]


def programm_finden():
    """llama-server: im PATH, von Homebrew oder dort, wo der Installer es ablegt."""
    for kandidat in (shutil.which("llama-server"), "/opt/homebrew/bin/llama-server", "/usr/local/bin/llama-server",
                     os.path.expanduser("~/.dowos/llama-rpc/bin/llama-server"),
                     os.path.expanduser("~/.dowos/llama-rpc/bin/llama-server.exe")):
        if kandidat and os.path.isfile(kandidat) and os.access(kandidat, os.X_OK):
            return kandidat
    return None


def freier_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def modellname(pfad):
    return os.path.splitext(os.path.basename(pfad))[0]


class Dienst:
    def __init__(self, ordner, programm=programm_finden, vor_start=None, leerlauf=300, frist=240):
        self.ordner = ordner
        self._programm = programm
        self.vor_start = vor_start          # z. B. Ollama-Modelle entladen
        self.leerlauf = leerlauf
        self.frist = frist
        self._laeufe = {}                   # Anbieter-ID -> {proc, port, zuletzt, aktiv, pfad}
        self._sperre = threading.RLock()
        self._waechter = None

    # ------------------------------------------------------------------ Zustand
    def laeuft(self, pid):
        with self._sperre:
            l = self._laeufe.get(pid)
            return bool(l and l["proc"].poll() is None)

    def lage(self):
        with self._sperre:
            return {pid: {"port": l["port"], "aktiv": l["aktiv"], "seit_s": int(time.time() - l["zuletzt"])}
                    for pid, l in self._laeufe.items() if l["proc"].poll() is None}

    def befehl(self, prov, port):
        g = prov["gguf"]
        prog = self._programm()
        if not prog:
            raise RuntimeError("llama-server fehlt. macOS: brew install llama.cpp — sonst das offizielle Paket von "
                               "github.com/ggml-org/llama.cpp/releases (siehe install.py --alles).")
        aufruf = [prog, "-m", g["pfad"], "-c", str(int(g.get("kontext") or 16384))]
        aufruf += list(g.get("schalter") or STANDARD_SCHALTER)
        if not g.get("denken"):
            aufruf += ["--chat-template-kwargs", '{"enable_thinking":false}']
        return aufruf + ["--host", "127.0.0.1", "--port", str(port)]

    # ------------------------------------------------------------------ Start und Ende
    def _starten(self, prov):
        pfad = prov["gguf"]["pfad"]
        if not os.path.isfile(pfad):
            raise RuntimeError("Die Modelldatei fehlt: %s" % pfad)
        for andere in [p for p, l in self._laeufe.items() if p != prov["id"] and l["aktiv"] == 0]:
            self.anhalten(andere)           # zwei große Dateimodelle passen nie gleichzeitig
        if self.vor_start:
            try:
                self.vor_start()
            except Exception:
                pass
        port = freier_port()
        os.makedirs(self.ordner, exist_ok=True)
        log = os.path.join(self.ordner, "gguf-%s.log" % prov["id"])
        proc = subprocess.Popen(self.befehl(prov, port), stdout=open(log, "w"), stderr=subprocess.STDOUT)
        ende = time.time() + self.frist
        while time.time() < ende:
            if proc.poll() is not None:
                raise RuntimeError("llama-server beendete sich beim Laden (Code %s). %s"
                                   % (proc.returncode, _letzte_zeilen(log)))
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=3) as a:
                    if json.load(a).get("status") == "ok":
                        break
            except Exception:
                pass
            time.sleep(1)
        else:
            proc.terminate()
            raise RuntimeError("llama-server kam in %d s nicht hoch. %s" % (self.frist, _letzte_zeilen(log)))
        self._laeufe[prov["id"]] = {"proc": proc, "port": port, "zuletzt": time.time(), "aktiv": 0, "pfad": pfad}
        with open(self._pid_datei(prov["id"]), "w") as f:
            f.write(str(proc.pid))
        self._waechter_starten()

    def _pid_datei(self, pid):
        return os.path.join(self.ordner, "gguf-%s.pid" % pid)

    def aufraeumen(self):
        """Beim Start: Reste eines früheren Laufs beenden (Dive on Wide hart beendet → llama-server hielt 16 GB fest)."""
        beendet = []
        if not os.path.isdir(self.ordner):
            return beendet
        for datei in os.listdir(self.ordner):
            if not (datei.startswith("gguf-") and datei.endswith(".pid")):
                continue
            pfad = os.path.join(self.ordner, datei)
            try:
                alt = int(open(pfad).read().strip())
                if _ist_llama_server(alt):
                    os.kill(alt, 15)
                    beendet.append(alt)
            except (OSError, ValueError):
                pass
            try:
                os.remove(pfad)
            except OSError:
                pass
        return beendet

    def anhalten(self, pid):
        with self._sperre:
            l = self._laeufe.pop(pid, None)
        try:
            os.remove(self._pid_datei(pid))
        except OSError:
            pass
        if l and l["proc"].poll() is None:
            l["proc"].terminate()
            try:
                l["proc"].wait(15)
            except subprocess.TimeoutExpired:
                l["proc"].kill()

    def alle_anhalten(self):
        for pid in list(self._laeufe):
            self.anhalten(pid)

    def platz_machen(self):
        """Vor einer Ollama-Anfrage: unbenutzte Dateimodelle beenden, damit beide nicht um den Speicher ringen."""
        with self._sperre:
            frei = [p for p, l in self._laeufe.items() if l["aktiv"] == 0]
        for p in frei:
            self.anhalten(p)
        return frei

    @contextlib.contextmanager
    def benutzt(self, prov):
        """Für die Dauer einer Anfrage: Dienst läuft, wird nicht beendet. Liefert den Anbieter mit echter Adresse."""
        with self._sperre:
            l = self._laeufe.get(prov["id"])
            if not l or l["proc"].poll() is not None:
                self._laeufe.pop(prov["id"], None)
                self._starten(prov)
                l = self._laeufe[prov["id"]]
            l["aktiv"] += 1
        try:
            yield dict(prov, base_url="http://127.0.0.1:%d" % l["port"])
        finally:
            with self._sperre:
                l["aktiv"] -= 1
                l["zuletzt"] = time.time()

    def leerlauf_pruefen(self, jetzt=None):
        jetzt = jetzt or time.time()
        with self._sperre:
            alt = [p for p, l in self._laeufe.items()
                   if l["aktiv"] == 0 and (jetzt - l["zuletzt"] > self.leerlauf or l["proc"].poll() is not None)]
        for p in alt:
            self.anhalten(p)
        return alt

    def _waechter_starten(self):
        if self._waechter and self._waechter.is_alive():
            return

        def schleife():
            while self._laeufe:
                time.sleep(min(30, max(1, self.leerlauf / 4)))
                self.leerlauf_pruefen()
        self._waechter = threading.Thread(target=schleife, daemon=True, name="gguf-leerlauf")
        self._waechter.start()


def _prozess_befehl(pid):
    """Womit ein Prozess läuft (leer, wenn es ihn nicht gibt)."""
    if os.name == "nt":
        return subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True, text=True).stdout
    return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout


def _ist_llama_server(pid):
    """Nur einen Prozess beenden, der wirklich ein llama-server ist — PIDs werden wiederverwendet."""
    return "llama-server" in _prozess_befehl(pid)


def _letzte_zeilen(pfad, n=4):
    try:
        zeilen = [z.strip() for z in open(pfad, encoding="utf-8", errors="replace").read().splitlines() if z.strip()]
        return " | ".join(zeilen[-n:])[-400:]
    except OSError:
        return ""
