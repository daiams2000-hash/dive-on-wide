# -*- coding: utf-8 -*-
"""Trainings-Werkbank — ein eigenes Modell für Dive on Wide trainieren.

Aus dem Prototyp `~/llm/work/dashboard.py` übernommen und in Dive on Wide eingebaut:
Modelle und Datensätze finden, LoRA-Training starten, Verlauf mit Loss-Kurve
verfolgen, stoppen. Die Erfahrungen aus Run 1 stecken in den Vorgaben:

- **Lernraten-Plan in Optimizer-Schritten.** MLX zählt den Cosine-Plan in
  Optimizer-Schritten (iters / grad_accum), nicht in Iterationen. Mit `iters`
  als Periode sank die Lernrate im Erstlauf bis zum Ende kaum.
- **Nur die Antworten lernen** (`mask_prompt`): Werkzeugausgaben und
  Aufgabentexte sind Eingabe, nicht Ziel.
- **NaN früh erkennen.** Ein Lauf, dessen Loss NaN wird, lernt nichts mehr —
  das steht sichtbar in der Übersicht statt erst nach Tagen.

Ein Training läuft als eigener Prozess und überlebt einen Neustart von Dive on Wide.
Trainiert wird mit `mlx_lm` (Apple Silicon). Wo das fehlt, sagt die Lage es
ehrlich und nennt den Befehl zum Nachinstallieren; auf anderen Plattformen gibt
es (noch) keinen Trainer.

Reine Standardbibliothek. `mlx_lm` wird nie importiert, nur als Prozess gestartet.
"""

import json
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import threading
import time

ITER_RE = re.compile(r"Iter (\d+): Train loss ([\d.]+|nan|NaN).*?It/sec ([\d.]+).*?Tokens/sec ([\d.]+)"
                     r"(?:.*?Peak mem ([\d.]+))?")
VAL_RE = re.compile(r"Iter (\d+): Val loss ([\d.]+|nan|NaN)")
PROBLEME = ("traceback", "outofmemory", "insufficient memory", "error:", "killed")

VORGABEN = {"iters": 600, "batch_size": 1, "grad_accum": 4, "lr": 1e-4, "rank": 16,
            "num_layers": 16, "max_seq": 4096, "geduld": 3, "seed": 17}
# Frühstopp: Wird der Val-Loss so viele Prüfungen hintereinander nicht um mindestens
# VERBESSERUNG besser, endet das Training und der beste Zwischenstand wird der Adapter.
# Gemessen am ersten Schüler (216 Beispiele, 600 Schritte): Train-Loss 0,09, Val-Loss
# ab Schritt 150 bei 0,51 — der Rest war Auswendiglernen, und nachher löste er nicht mehr.
VERBESSERUNG = 0.01
GRENZEN = {"iters": (1, 200000), "batch_size": (1, 64), "grad_accum": (1, 256), "rank": (1, 256),
           "num_layers": (1, 200), "max_seq": (256, 65536), "geduld": (0, 100), "seed": (0, 2 ** 31 - 1)}

_lock = threading.Lock()


def kind_umgebung(**mehr):
    """Umgebung für Kindprozesse, die in Logdateien schreiben: ungepuffert und UTF-8.
    Unter Windows schreiben sie sonst Windows-1252, und ein ✅ beendete den Export
    (Windows-VM, 29.09.2026)."""
    return dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", **mehr)


class Werkbank:
    """Alles Training einer Dive-on-Wide-Instanz: Ablage unter `ordner`, Suchorte für Modelle/Daten."""

    def __init__(self, ordner, suchorte=(), python=None, caffeinate=True):
        self.ordner = ordner
        self.laeufe_datei = os.path.join(ordner, "laeufe.json")
        self.suchorte = [os.path.expanduser(s) for s in suchorte if s]
        self.python = python
        self.caffeinate = caffeinate
        self.vor_start = None            # z. B. Ollama-Modelle entladen — sie belegen GPU-Speicher
        self._gpu_grenze = None
        os.makedirs(os.path.join(ordner, "logs"), exist_ok=True)

    # ---------------------------------------------------------------- Lage ---
    def trainer(self):
        """(python, Version) eines Interpreters mit mlx_lm — oder (None, Grund)."""
        kandidaten = [self.python] if self.python else [
            os.path.expanduser("~/llm/venv/bin/python3"), sys.executable, "python3"]
        for py in kandidaten:
            if not py:
                continue
            try:
                p = subprocess.run([py, "-c", "import importlib.metadata as m; print(m.version('mlx-lm'))"],
                                   capture_output=True, text=True, timeout=20)
                if p.returncode == 0 and p.stdout.strip() and self._gpu_grenze is None:
                    g = subprocess.run([py, "-c", "import mlx.core as mx; print(mx.device_info()"
                                        "['max_recommended_working_set_size'])"],
                                       capture_output=True, text=True, timeout=30)
                    try:
                        self._gpu_grenze = round(int(g.stdout.strip()) / 1e9, 1)
                    except ValueError:
                        self._gpu_grenze = 0
            except (OSError, subprocess.TimeoutExpired):
                continue
            if p.returncode == 0 and p.stdout.strip():
                return py, p.stdout.strip()
        return None, None

    def lage(self):
        apple = sys.platform == "darwin" and platform.machine() == "arm64"
        py, version = self.trainer()
        if py:
            hinweis = "Trainiert wird mit mlx-lm %s (%s)." % (version, py)
            if self._gpu_grenze:
                hinweis += (" GPU-Speicher für das Training: höchstens %.1f GB — lange Beispiele und viele "
                            "trainierte Schichten brauchen am meisten." % self._gpu_grenze)
        elif apple:
            hinweis = ("mlx-lm fehlt. Einmal installieren: python3 -m venv ~/llm/venv && "
                       "~/llm/venv/bin/pip install mlx-lm — oder in den Einstellungen TRAINING_PYTHON setzen.")
        else:
            hinweis = ("Training gibt es in Dive on Wide bisher nur auf Macs mit Apple Silicon (mlx-lm). "
                       "Auf diesem Rechner kann nichts trainiert werden.")
        return {"bereit": bool(py), "python": py, "version": version, "apple_silicon": apple,
                "gpu_grenze_gb": self._gpu_grenze or None,
                "hinweis": hinweis, "suchorte": self.suchorte, "vorgaben": VORGABEN}

    # --------------------------------------------------------------- Finden ---
    @staticmethod
    def ist_modell(d):
        try:
            return os.path.isfile(os.path.join(d, "config.json")) and any(
                f.endswith(".safetensors") and not f.startswith("._") for f in os.listdir(d))
        except OSError:
            return False

    @staticmethod
    def modell_info(d):
        try:
            with open(os.path.join(d, "config.json"), encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
        q = cfg.get("quantization")
        bits = q.get("bits") if isinstance(q, dict) else None
        groesse = 0
        for f in os.listdir(d):
            if f.endswith(".safetensors") and not f.startswith("._"):
                groesse += os.path.getsize(os.path.join(d, f))
        return {"pfad": d, "name": os.path.basename(d), "gb": round(groesse / 1e9, 1),
                "quantisierung": "%d-bit" % bits if bits else "volle Genauigkeit",
                "typ": cfg.get("model_type", "?"), "tokenizer": os.path.exists(os.path.join(d, "tokenizer.json"))}

    @staticmethod
    def datensatz_info(d):
        zeilen = {}
        for teil in ("train", "valid"):
            try:
                with open(os.path.join(d, teil + ".jsonl"), "rb") as f:
                    zeilen[teil] = sum(1 for _ in f)
            except OSError:
                return None
        info = {"pfad": d, "name": os.path.basename(d), "train": zeilen["train"], "valid": zeilen["valid"]}
        bericht = os.path.join(d, "bericht.md")
        if os.path.exists(bericht):
            with open(bericht, encoding="utf-8") as f:
                info["bericht"] = f.read(2000)
        return info

    def finden(self):
        """Modelle (MLX-Ordner) und trainierbare Datensätze (train.jsonl + valid.jsonl), zwei Ebenen tief."""
        modelle, daten, gesehen = [], [], set()

        def pruefen(d):
            echt = os.path.realpath(d)
            if echt in gesehen:
                return False
            gesehen.add(echt)
            if self.ist_modell(d):
                modelle.append(self.modell_info(d))
                return True
            info = self.datensatz_info(d)
            if info:
                daten.append(info)
                return True
            return False

        for ort in self.suchorte:
            if not os.path.isdir(ort):
                continue
            if pruefen(ort):
                continue
            for name in sorted(os.listdir(ort))[:200]:
                d = os.path.join(ort, name)
                if name.startswith(".") or not os.path.isdir(d) or pruefen(d):
                    continue
                try:
                    for unter in sorted(os.listdir(d))[:60]:
                        u = os.path.join(d, unter)
                        if not unter.startswith(".") and os.path.isdir(u):
                            pruefen(u)
                except OSError:
                    continue
        return {"modelle": modelle, "datensaetze": daten}

    # --------------------------------------------------------------- Läufe ---
    def _laden(self):
        try:
            with open(self.laeufe_datei, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def _speichern(self, laeufe):
        tmp = self.laeufe_datei + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(laeufe, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.laeufe_datei)

    @staticmethod
    def lebt(pid):
        if not pid:
            return False
        if os.name == "nt":
            # Unter Windows beendet os.kill(pid, 0) den Prozess (TerminateProcess), statt nur nachzusehen.
            return _windows_lebt(int(pid))
        try:
            os.kill(int(pid), 0)
        except (OSError, ValueError):
            return False
        # Ein beendetes Kind, das noch nicht abgeholt wurde, zählt nicht als lebend.
        try:
            erledigt, _ = os.waitpid(int(pid), os.WNOHANG)
            return erledigt == 0
        except ChildProcessError:
            return True
        except OSError:
            return True

    @staticmethod
    def konfiguration(modell, daten, adapter, werte):
        w = dict(VORGABEN)
        for k, v in (werte or {}).items():
            if k in VORGABEN and v not in (None, ""):
                w[k] = float(v) if k == "lr" else int(v)
        for k, (lo, hi) in GRENZEN.items():
            if not lo <= w[k] <= hi:
                raise ValueError("%s muss zwischen %d und %d liegen." % (k, lo, hi))
        if not 0 < w["lr"] <= 1e-2:
            raise ValueError("Lernrate muss zwischen 0 und 0,01 liegen.")
        optimizer_schritte = max(1, w["iters"] // w["grad_accum"])
        return {
            "model": modell, "train": True, "data": daten, "fine_tune_type": "lora", "optimizer": "adamw",
            "num_layers": w["num_layers"], "mask_prompt": True, "grad_checkpoint": True,
            "batch_size": w["batch_size"], "grad_accumulation_steps": w["grad_accum"],
            "max_seq_length": w["max_seq"], "iters": w["iters"], "learning_rate": w["lr"],
            "steps_per_report": max(1, min(50, w["iters"] // 20)),
            # Prüfen und Sichern im selben Takt: Nur so gibt es zum besten Val-Loss auch einen Zwischenstand.
            "steps_per_eval": max(10, min(500, w["iters"] // 8)),
            "save_every": max(10, min(500, w["iters"] // 8)), "val_batches": 25,
            "adapter_path": adapter, "seed": w["seed"],
            "lora_parameters": {"rank": w["rank"], "scale": 20.0, "dropout": 0.05},
            # In Optimizer-Schritten — siehe Modulbeschreibung.
            "lr_schedule": {"name": "cosine_decay", "warmup": min(50, max(1, optimizer_schritte // 10)),
                            "arguments": [w["lr"], optimizer_schritte, w["lr"] / 10]},
        }

    def starten(self, modell, daten, name="", werte=None):
        py, _ = self.trainer()
        if not py:
            raise RuntimeError(self.lage()["hinweis"])
        if not self.ist_modell(modell):
            raise ValueError("„%s“ ist kein MLX-Modellordner (config.json und .safetensors fehlen)." % modell)
        if not self.datensatz_info(daten):
            raise ValueError("„%s“ ist kein trainierbarer Datensatz (train.jsonl und valid.jsonl fehlen)." % daten)
        with _lock:
            laeufe = self._laden()
            self._frei_oder_fehler(laeufe)
            if self.dienste():
                raise RuntimeError("Ein bereitgestellter Schüler belegt GPU-Speicher, den das Training braucht — "
                                   "erst die Bereitstellung beenden.")
            kennung = time.strftime("training_%Y%m%d_%H%M%S")
            adapter = os.path.join(self.ordner, "adapter", kennung)
            os.makedirs(adapter, exist_ok=True)
            cfg = self.konfiguration(modell, daten, adapter, werte)
            if self.vor_start:
                self.vor_start()
            cfg_pfad = os.path.join(self.ordner, "logs", kennung + ".yaml")
            with open(cfg_pfad, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=1)          # JSON ist gültiges YAML
            log = os.path.join(self.ordner, "logs", kennung + ".log")
            geduld = int((werte or {}).get("geduld", VORGABEN["geduld"]) or 0)
            befehl = [sys.executable, os.path.abspath(__file__), "waechter", "--adapter", adapter, "--geduld", str(geduld),
                      "--", py, "-m", "mlx_lm", "lora", "-c", cfg_pfad]
            if self.caffeinate and sys.platform == "darwin":
                befehl = ["caffeinate", "-i"] + befehl   # Ruhezustand würde das Training anhalten
            with open(log, "ab") as f:
                proc = subprocess.Popen(befehl, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        start_new_session=True, cwd=self.ordner, env=kind_umgebung())
            lauf = {"id": kennung, "art": "training", "pid": proc.pid, "log": log, "adapter": adapter,
                    "modell": modell, "daten": daten, "iters": cfg["iters"], "konfig": cfg,
                    "name": name or "%s ← %s" % (os.path.basename(modell), os.path.basename(daten)),
                    "gestartet": time.time(), "zustand": "läuft"}
            laeufe.insert(0, lauf)
            self._speichern(laeufe)
        return self.zustand(lauf)

    def _frei_oder_fehler(self, laeufe):
        laufend = [l for l in laeufe if self.lebt(l.get("pid")) and l.get("exklusiv", True)]
        if laufend:
            raise RuntimeError("Es läuft schon „%s“ — zwei schwere Aufträge gleichzeitig teilen sich Speicher "
                               "und Modell und bremsen oder stören sich gegenseitig." % laufend[0]["name"])

    def auftrag_starten(self, art, befehl, name, anzahl, cwd=None, umgebung=None, exklusiv=True):
        """Ein langer Arbeitsschritt außer dem Training (Aufgabenfabrik, Lösen lassen) —
        abgekoppelt wie das Training, mit Fortschritt aus den ✅/❌-Zeilen seines Logs.
        `umgebung` ergänzt Umgebungsvariablen (etwa einen API-Schlüssel) — sie werden nicht gespeichert."""
        with _lock:
            laeufe = self._laden()
            if exklusiv:
                self._frei_oder_fehler(laeufe)
            kennung = time.strftime(art + "_%Y%m%d_%H%M%S")
            log = os.path.join(self.ordner, "logs", kennung + ".log")
            if self.caffeinate and sys.platform == "darwin":
                befehl = ["caffeinate", "-i"] + list(befehl)
            with open(log, "ab") as f:
                proc = subprocess.Popen(befehl, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        start_new_session=True, cwd=cwd or self.ordner,
                                        env=kind_umgebung(**(umgebung or {})))
            lauf = {"id": kennung, "art": art, "pid": proc.pid, "log": log, "name": name, "iters": anzahl,
                    "befehl": befehl, "gestartet": time.time(), "zustand": "läuft", "adapter": "", "exklusiv": exklusiv}
            laeufe.insert(0, lauf)
            self._speichern(laeufe)
        return self.zustand(lauf)

    @staticmethod
    def auftrag_lesen(pfad):
        zeilen = []
        try:
            with open(pfad, encoding="utf-8", errors="ignore") as f:
                zeilen = f.read().splitlines()
        except OSError:
            pass
        ok = sum(1 for z in zeilen if z.lstrip().startswith("✅"))
        nein = sum(1 for z in zeilen if z.lstrip().startswith("❌"))
        abschluss = any(z.startswith(("Angenommen ", "Bericht:")) for z in zeilen)
        probleme = [z.strip()[:300] for z in zeilen if any(s in z.lower() for s in PROBLEME)]
        return {"ok": ok, "nein": nein, "abschluss": abschluss, "probleme": probleme[-5:],
                "letzte": [z for z in zeilen if z.strip()][-8:]}

    @staticmethod
    def log_lesen(pfad, punkte=300):
        train, val, probleme, fruehstopp, bester = [], [], [], [], []
        try:
            with open(pfad, encoding="utf-8", errors="ignore") as f:
                for zeile in f:
                    m = ITER_RE.search(zeile)
                    if m:
                        train.append({"iter": int(m.group(1)),
                                      "loss": None if "nan" in m.group(2).lower() else float(m.group(2)),
                                      "it_sec": float(m.group(3)), "tok_sec": float(m.group(4)),
                                      "mem_gb": float(m.group(5)) if m.group(5) else None})
                        continue
                    m = VAL_RE.search(zeile)
                    if m:
                        val.append({"iter": int(m.group(1)),
                                    "loss": None if "nan" in m.group(2).lower() else float(m.group(2))})
                        continue
                    if zeile.startswith(("Frühstopp:", "Bester Stand:")):
                        (fruehstopp if zeile.startswith("Frühstopp:") else bester).append(zeile.strip())
                        continue
                    if any(s in zeile.lower() for s in PROBLEME):
                        probleme.append(zeile.strip()[:300])
        except OSError:
            pass
        if len(train) > punkte:
            schritt = len(train) / punkte
            duenn = [train[int(i * schritt)] for i in range(punkte)]
            train = duenn if duenn[-1] is train[-1] else duenn + [train[-1]]
        return {"train": train, "val": val[-100:], "probleme": probleme[-5:],
                "nan": sum(1 for t in train if t["loss"] is None),
                "fruehstopp": fruehstopp[-1] if fruehstopp else "", "bester": bester[-1] if bester else ""}

    def zustand(self, lauf):
        z = {k: v for k, v in lauf.items() if k not in ("konfig", "befehl")}
        z["lebt"] = self.lebt(lauf.get("pid"))
        if lauf["art"] != "training":
            a = self.auftrag_lesen(lauf["log"])
            z["verlauf"] = {"train": [], "val": [], "probleme": a["probleme"], "nan": 0}
            z["ausgabe"] = a["letzte"]
            z["ok"], z["nein"] = a["ok"], a["nein"]
            # Fabrik: Fortschritt = angenommene Aufgaben; Lösen: bearbeitete Aufgaben.
            erledigt = a["ok"] if lauf["art"] == "fabrik" else a["ok"] + a["nein"]
            z["fortschritt"] = min(100.0, round(100 * erledigt / lauf["iters"], 1)) if lauf.get("iters") else 0
            z["rest_min"] = None
            if not z["lebt"] and lauf.get("zustand") == "läuft":
                z["zustand"] = "fertig" if a["abschluss"] else ("abgestürzt" if a["probleme"] else "abgebrochen")
            return z
        z["verlauf"] = self.log_lesen(lauf["log"])
        letzte = z["verlauf"]["train"][-1] if z["verlauf"]["train"] else {}
        stand = letzte.get("iter", 0)
        z["fortschritt"] = round(100 * stand / lauf["iters"], 1) if lauf.get("iters") else 0
        z["rest_min"] = (round((lauf["iters"] - stand) / letzte["it_sec"] / 60)
                         if z["lebt"] and letzte.get("it_sec") and lauf["iters"] > stand else None)
        if z["verlauf"]["nan"]:
            z["warnung"] = "Loss ist NaN geworden — dieser Lauf lernt nichts mehr. Stoppen und Lernrate senken."
        if not z["lebt"] and lauf.get("zustand") == "läuft":
            adapter_da = os.path.exists(os.path.join(lauf["adapter"], "adapters.safetensors"))
            if (stand >= lauf["iters"] or z["verlauf"].get("fruehstopp")) and adapter_da:
                z["zustand"] = "fertig"
            elif z["verlauf"]["probleme"]:
                z["zustand"] = "abgestürzt"
            else:
                z["zustand"] = "abgebrochen"
        return z

    def liste(self):
        return [self.zustand(l) for l in self._laden()]

    def stoppen(self, kennung):
        with _lock:
            laeufe = self._laden()
            lauf = next((l for l in laeufe if l["id"] == kennung), None)
            if not lauf:
                raise LookupError("Diesen Lauf gibt es nicht.")
            if self.lebt(lauf.get("pid")):
                prozess_beenden(lauf["pid"])
            lauf["zustand"] = "gestoppt"
            self._speichern(laeufe)
        return self.zustand(lauf)

    # ------------------------------------------------ Schüler bereitstellen ---
    # Ein fertiger Adapter wird zu einem Modell wie jedes andere: `mlx_lm server`
    # auf 127.0.0.1, Dive on Wide führt ihn als Anbieter „Schüler“, solange er läuft.
    # Den Adapter muss jede Anfrage selbst nennen ("adapters") — mlx_lm server
    # (0.31) übergeht `--adapter-path` beim Laden für eine Anfrage.

    def _dienste_laden(self):
        try:
            with open(os.path.join(self.ordner, "dienste.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def _dienste_speichern(self, dienste):
        pfad = os.path.join(self.ordner, "dienste.json")
        with open(pfad + ".tmp", "w", encoding="utf-8") as f:
            json.dump(dienste, f, ensure_ascii=False, indent=1)
        os.replace(pfad + ".tmp", pfad)

    def dienste(self):
        """Die laufenden Bereitstellungen. Beendete verschwinden von selbst aus der Liste."""
        alle = self._dienste_laden()
        lebend = [d for d in alle if self.lebt(d.get("pid"))]
        if len(lebend) != len(alle):
            try:
                self._dienste_speichern(lebend)
            except OSError:
                pass
        return lebend

    @staticmethod
    def _freier_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def bereitstellen(self, kennung):
        py, _ = self.trainer()
        if not py:
            raise RuntimeError(self.lage()["hinweis"])
        with _lock:
            lauf = next((l for l in self._laden() if l["id"] == kennung), None)
            if not lauf or lauf.get("art", "training") != "training":
                raise LookupError("Dieses Training gibt es nicht.")
            laeuft = next((d for d in self.dienste() if d["id"] == kennung), None)
            if laeuft:
                return laeuft
            if self.lebt(lauf.get("pid")):
                raise RuntimeError("Das Training läuft noch.")
            if not os.path.isfile(os.path.join(lauf["adapter"], "adapters.safetensors")):
                raise ValueError("Dieses Training hat keinen Adapter hinterlassen.")
            if not self.ist_modell(lauf["modell"]):
                raise ValueError("Das Grundmodell „%s“ ist nicht mehr da." % lauf["modell"])
            if any(self.lebt(l.get("pid")) for l in self._laden() if l.get("art", "training") == "training"):
                raise RuntimeError("Während ein Training läuft, wird nichts bereitgestellt — beide brauchen den GPU-Speicher.")
            port = self._freier_port()
            log = os.path.join(self.ordner, "logs", "dienst_%s.log" % kennung)
            befehl = [py, "-m", "mlx_lm", "server", "--model", lauf["modell"], "--host", "127.0.0.1",
                      "--port", str(port), "--chat-template-args", '{"enable_thinking": false}',
                      "--prompt-cache-size", "4", "--prompt-cache-bytes", str(2 * 1024 ** 3)]
            with open(log, "ab") as f:
                proc = subprocess.Popen(befehl, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        start_new_session=True, cwd=self.ordner, env=kind_umgebung())
            dienst = {"id": kennung, "pid": proc.pid, "port": port, "name": lauf["name"], "modell": lauf["modell"],
                      "adapter": lauf["adapter"], "log": log, "gestartet": time.time()}
            self._dienste_speichern([d for d in self.dienste() if d["id"] != kennung] + [dienst])
        return dienst

    def bereitstellung_beenden(self, kennung):
        with _lock:
            dienste = self._dienste_laden()
            dienst = next((d for d in dienste if d["id"] == kennung), None)
            if not dienst:
                raise LookupError("Dieser Schüler ist nicht bereitgestellt.")
            if self.lebt(dienst.get("pid")):
                prozess_beenden(dienst["pid"])
            self._dienste_speichern([d for d in dienste if d["id"] != kennung])
        return {"ok": True}

    def vergessen(self, kennung):
        """Nimmt einen beendeten Lauf aus der Liste. Adapter und Log bleiben liegen."""
        with _lock:
            laeufe = self._laden()
            lauf = next((l for l in laeufe if l["id"] == kennung), None)
            if not lauf:
                raise LookupError("Diesen Lauf gibt es nicht.")
            if self.lebt(lauf.get("pid")):
                raise RuntimeError("Der Lauf arbeitet noch — erst stoppen.")
            self._speichern([l for l in laeufe if l["id"] != kennung])
        return {"ok": True, "adapter": lauf["adapter"]}


# --------------------------------------------------------------- Wächter ---

def _windows_lebt(pid):
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def prozess_beenden(pid):
    """Prozessgruppe beenden (POSIX) oder den ganzen Prozessbaum (Windows).

    Windows kennt keine Gruppen über os.killpg; os.kill traf nur den obersten
    Prozess, seine Kinder liefen weiter — „Stopp“ hielt ein Training, eine
    Destillation oder einen Export nicht an (Windows-VM, 29.09.2026)."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
        elif os.name == "nt":
            r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(int(pid))], capture_output=True, timeout=30)
            if r.returncode != 0:
                os.kill(int(pid), signal.SIGTERM)
        else:
            os.kill(int(pid), signal.SIGTERM)
        return True
    except (OSError, ValueError):
        try:
            os.kill(int(pid), signal.SIGTERM)
            return True
        except (OSError, ValueError):
            return False


def bester_stand(val, verbesserung=VERBESSERUNG):
    """(Iteration mit dem besten Val-Loss, Loss, Prüfungen seit der letzten echten Verbesserung)."""
    beste, beste_iter, ohne = None, None, 0
    for v in val:
        if v["loss"] is None:
            ohne += 1
            continue
        if beste is None or v["loss"] < beste * (1 - verbesserung):
            beste, beste_iter, ohne = v["loss"], v["iter"], 0
        else:
            ohne += 1
            if v["loss"] < beste:
                beste, beste_iter = v["loss"], v["iter"]
    return beste_iter, beste, ohne


def bestes_uebernehmen(adapter, val):
    """Macht den Zwischenstand mit dem besten Val-Loss zu adapters.safetensors; der letzte bleibt als adapters_ende."""
    beste_iter, beste, _ = bester_stand(val)
    if beste_iter is None:
        return None
    datei = os.path.join(adapter, "%07d_adapters.safetensors" % beste_iter)
    ende = os.path.join(adapter, "adapters.safetensors")
    if not os.path.isfile(datei):
        return None                          # Iteration 1 (vor dem Training) oder kein Zwischenstand
    if os.path.isfile(ende):
        os.replace(ende, os.path.join(adapter, "adapters_ende.safetensors"))
    import shutil
    shutil.copyfile(datei, ende)
    return beste_iter, beste


def waechter(argv):
    """Startet mlx_lm lora, reicht die Ausgabe durch, stoppt früh und übernimmt den besten Stand.
    Läuft als eigener Prozess neben Dive on Wide — das Training überlebt einen Neustart des Servers."""
    import argparse
    ap = argparse.ArgumentParser(prog="training.py waechter")
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--geduld", type=int, default=VORGABEN["geduld"])
    ap.add_argument("befehl", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    befehl = a.befehl[1:] if a.befehl[:1] == ["--"] else a.befehl
    kind = subprocess.Popen(befehl, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            env=kind_umgebung())
    val, gestoppt = [], {"grund": ""}

    def beenden(*_):
        gestoppt["grund"] = gestoppt["grund"] or "von außen gestoppt"
        try:
            prozess_beenden(kind.pid) if os.name == "nt" else kind.terminate()
        except OSError:
            pass
    signal.signal(signal.SIGTERM, beenden)
    for roh in kind.stdout:
        zeile = roh.decode("utf-8", "replace")
        sys.stdout.write(zeile)
        sys.stdout.flush()
        m = VAL_RE.search(zeile)
        if m:
            val.append({"iter": int(m.group(1)), "loss": None if "nan" in m.group(2).lower() else float(m.group(2))})
            beste_iter, beste, ohne = bester_stand(val)
            if a.geduld and ohne >= a.geduld and beste_iter and not gestoppt["grund"]:
                gestoppt["grund"] = "Val-Loss seit %d Prüfungen nicht besser als %.3f (Iter %d)" % (ohne, beste, beste_iter)
                print("Frühstopp: %s" % gestoppt["grund"], flush=True)
                prozess_beenden(kind.pid) if os.name == "nt" else kind.terminate()
    code = kind.wait()
    uebernommen = bestes_uebernehmen(a.adapter, val)
    if uebernommen:
        print("Bester Stand: Iter %d (Val-Loss %.3f) → adapters.safetensors" % uebernommen, flush=True)
    return 0 if gestoppt["grund"] and uebernommen else code


if __name__ == "__main__":
    if sys.argv[1:2] == ["waechter"]:
        sys.exit(waechter(sys.argv[2:]))
