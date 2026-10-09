#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ein trainierter Schüler wird ein gewöhnliches Ollama-Modell.

    1. Adapter einbacken       mlx_lm fuse --dequantize  (Grundmodell + LoRA → HF-Ordner)
    2. nach GGUF               llama.cpp convert_hf_to_gguf.py (F16)
    3. quantisieren            llama-quantize (Q8_0, Q4_K_M) — bei F16 entfällt das
    4. in Ollama übernehmen    ollama create NAME -f Modelfile

Ollama kopiert die GGUF-Datei in seinen eigenen Speicher. Danach wäre jede
Zwischendatei eine zweite Kopie desselben Modells — der Arbeitsordner wird
deshalb nach Erfolg entfernt. Nach einem Fehler bleibt er zur Untersuchung
liegen; der nächste Versuch legt einen neuen an.

Ausgabe wie die anderen Aufträge der Trainings-Werkbank: ✅/❌ je Stufe, am Ende
eine Zeile „Bericht: …“.
"""

import argparse
import os
import shutil
import subprocess
import sys

STUFEN = 4


def groesse(pfad):
    return sum(os.path.getsize(os.path.join(w, d)) for w, _, ds in os.walk(pfad) for d in ds)


def ausfuehren(befehl, umgebung=None):
    print("   $ " + " ".join(befehl), flush=True)
    p = subprocess.run(befehl, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=umgebung)
    for zeile in p.stdout.splitlines()[-15:]:
        print("   " + zeile, flush=True)
    return p.returncode == 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--python", required=True, help="Python mit mlx_lm")
    ap.add_argument("--basis", required=True)
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--name", required=True, help="Name des Ollama-Modells")
    ap.add_argument("--quant", default="Q8_0", choices=("Q8_0", "Q4_K_M", "F16"))
    ap.add_argument("--arbeit", required=True, help="Arbeitsordner für Zwischendateien")
    ap.add_argument("--llama-cpp", required=True, help="Ordner mit convert_hf_to_gguf.py")
    ap.add_argument("--quantize", default="", help="llama-quantize")
    ap.add_argument("--ollama", default="ollama")
    a = ap.parse_args(argv)

    os.makedirs(a.arbeit, exist_ok=True)
    # Einbacken entpackt 4-Bit-Gewichte (×4), die F16-GGUF ist noch einmal so groß.
    noetig = groesse(a.basis) * 9 + 2 * 1024 ** 3
    frei = shutil.disk_usage(a.arbeit).free
    if frei < noetig:
        print("❌ Zu wenig Speicherplatz: frei %.1f GB, nötig etwa %.1f GB." % (frei / 1e9, noetig / 1e9), flush=True)
        return 1

    fused = os.path.join(a.arbeit, "eingebacken")
    f16 = os.path.join(a.arbeit, "modell-f16.gguf")
    fertig = f16 if a.quant == "F16" else os.path.join(a.arbeit, "modell-%s.gguf" % a.quant.lower())

    if not ausfuehren([a.python, "-m", "mlx_lm", "fuse", "--model", a.basis, "--adapter-path", a.adapter,
                       "--save-path", fused, "--dequantize"]) or not os.path.isfile(os.path.join(fused, "config.json")):
        print("❌ 1/%d Adapter einbacken fehlgeschlagen" % STUFEN, flush=True)
        return 1
    print("✅ 1/%d Adapter eingebacken (%.1f GB)" % (STUFEN, groesse(fused) / 1e9), flush=True)

    if not ausfuehren([a.python, os.path.join(a.llama_cpp, "convert_hf_to_gguf.py"), fused,
                       "--outfile", f16, "--outtype", "f16"]) or not os.path.isfile(f16):
        print("❌ 2/%d Umwandlung nach GGUF fehlgeschlagen" % STUFEN, flush=True)
        return 1
    print("✅ 2/%d GGUF geschrieben (%.1f GB)" % (STUFEN, os.path.getsize(f16) / 1e9), flush=True)
    shutil.rmtree(fused)                     # ab hier nicht mehr gebraucht — Platz für die Quantisierung

    if a.quant != "F16":
        if not a.quantize or not ausfuehren([a.quantize, f16, fertig, a.quant]) or not os.path.isfile(fertig):
            print("❌ 3/%d Quantisierung %s fehlgeschlagen" % (STUFEN, a.quant), flush=True)
            return 1
        os.remove(f16)
    print("✅ 3/%d %s (%.1f GB)" % (STUFEN, a.quant, os.path.getsize(fertig) / 1e9), flush=True)

    modelfile = os.path.join(a.arbeit, "Modelfile")
    with open(modelfile, "w", encoding="utf-8") as f:
        f.write("FROM %s\nPARAMETER temperature 0.2\n" % os.path.abspath(fertig))
    if not ausfuehren([a.ollama, "create", a.name, "-f", modelfile]):
        print("❌ 4/%d ollama create fehlgeschlagen — läuft Ollama?" % STUFEN, flush=True)
        return 1
    print("✅ 4/%d In Ollama übernommen: %s" % (STUFEN, a.name), flush=True)
    shutil.rmtree(a.arbeit)                  # Ollama hat seine eigene Kopie
    print("Bericht: Ollama-Modell %s aus %s" % (a.name, os.path.basename(a.adapter.rstrip("/"))), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
