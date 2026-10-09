#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fremde Trainingskonfigurationen lesen — Axolotl, LLaMA-Factory, mlx-lm, Unsloth.

Wer heute feintunt, hat meist schon eine Konfiguration: eine Axolotl-YAML, eine
LLaMA-Factory-Datei, ein mlx-lm-JSON. Damit der TÜV und die Rezepte für diese
Leute überhaupt in Frage kommen, müssen sie ihre Datei hineingeben können, statt
alles neu zu tippen.

Übersetzt wird, was eine Entsprechung hat. Und — das ist der wichtigere Teil —
**was keine hat, wird genannt, nicht verschwiegen.** Eine Konfiguration, die
stillschweigend die Hälfte verliert, ist schlimmer als gar keine Übersetzung:
Man trainiert dann etwas anderes, als man glaubt.

    python3 fremd.py <konfiguration>        → Rezept auf stdout, Verluste auf stderr
"""

import json
import os
import re
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)

import rezept  # noqa: E402

# Feld in der fremden Datei → Feld im Rezept. Mehrere Namen je Ziel, weil die
# Werkzeuge dieselbe Sache verschieden nennen.
ABBILDUNG = {
    "basis": ("base_model", "model_name_or_path", "model", "base_model_config"),
    "daten.max_laenge": ("sequence_len", "cutoff_len", "max_seq_length", "max_seq", "max_length"),
    "daten.val_anteil": ("val_set_size", "val_size", "val_split", "val_set_ratio"),
    "training.epochen": ("num_epochs", "num_train_epochs", "epochs"),
    "training.lr": ("learning_rate", "lr"),
    "training.batch": ("micro_batch_size", "per_device_train_batch_size", "batch_size"),
    "training.grad_accum": ("gradient_accumulation_steps", "grad_accumulation_steps", "grad_accum"),
    "training.lora.rang": ("lora_r", "lora_rank", "r", "rank"),
    "training.lora.schichten": ("num_layers", "lora_layers"),
    "training.seed": ("seed", "data_seed"),
    "training.iters": ("max_steps", "iters"),
}

# Was Dive on Wide bewusst anders macht oder nicht kann — beim Übersetzen benennen.
UNBEKANNT_ERKLAERT = {
    "deepspeed": "Dive on Wide trainiert auf einem Rechner, nicht auf einem Cluster.",
    "fsdp": "kein verteiltes Training.",
    "flash_attention": "MLX bringt seine eigene Aufmerksamkeit mit.",
    "bf16": "MLX entscheidet die Genauigkeit selbst.",
    "fp16": "MLX entscheidet die Genauigkeit selbst.",
    "load_in_4bit": "Die Quantisierung steckt im Modellordner, nicht im Rezept.",
    "load_in_8bit": "Die Quantisierung steckt im Modellordner, nicht im Rezept.",
    "optimizer": "Dive on Wide trainiert mit adamw.",
    "lr_scheduler": "Dive on Wide fährt einen Kosinus-Plan in Optimizer-Schritten.",
    "warmup_steps": "wird aus den Optimizer-Schritten abgeleitet.",
    "lora_target_modules": "mlx-lm wählt die Zielschichten über die Zahl der Schichten.",
    "target_modules": "mlx-lm wählt die Zielschichten über die Zahl der Schichten.",
    "wandb_project": "Dive on Wide protokolliert lokal, nicht in einem fremden Dienst.",
    "hub_model_id": "Dive on Wide lädt nichts von selbst hoch.",
    "push_to_hub": "Dive on Wide lädt nichts von selbst hoch.",
}


def erkennen(daten, pfad=""):
    """Welches Werkzeug hat das geschrieben?"""
    name = os.path.basename(pfad).lower()
    if "dataset_info" in name:
        return "llama-factory-datensätze"
    if any(k in daten for k in ("finetuning_type", "template", "dataset_dir", "stage")):
        return "llama-factory"
    if any(k in daten for k in ("micro_batch_size", "val_set_size", "sequence_len", "datasets")):
        return "axolotl"
    if any(k in daten for k in ("fine_tune_type", "adapter_path", "lora_parameters")):
        return "mlx-lm"
    if "max_seq_length" in daten and "model" in daten:
        return "unsloth"
    return "unbekannt"


def _tief_setzen(baum, pfad, wert):
    teile = pfad.split(".")
    knoten = baum
    for t in teile[:-1]:
        knoten = knoten.setdefault(t, {})
    knoten[teile[-1]] = wert


def _daten_quelle(daten):
    """Wo liegen die Trainingsdaten? Jedes Werkzeug sagt es anders."""
    d = daten.get("datasets")
    if isinstance(d, list) and d:
        erster = d[0]
        if isinstance(erster, dict):
            return str(erster.get("path") or erster.get("data_files") or "")
        return str(erster)
    for schluessel in ("dataset", "data", "train_file", "data_path", "dataset_dir"):
        wert = daten.get(schluessel)
        if isinstance(wert, str) and wert:
            return wert
    return ""


def uebersetzen(daten, pfad=""):
    """Fremde Konfiguration → (Rezept, Bericht über Übernommenes und Verlorenes)."""
    werkzeug = erkennen(daten, pfad)
    roh = {"name": os.path.splitext(os.path.basename(pfad))[0] or "Import",
           "daten": {}, "training": {"lora": {}}}
    uebernommen, offen = [], []
    flach = dict(daten)
    for ziel, namen in ABBILDUNG.items():
        for n in namen:
            if n in flach and flach[n] not in (None, "", []):
                wert = flach[n]
                if ziel == "daten.val_anteil" and isinstance(wert, (int, float)) and wert > 1:
                    # Manche geben eine Anzahl statt eines Anteils an
                    offen.append("%s = %s (Anzahl statt Anteil — bitte selbst setzen)" % (n, wert))
                    break
                _tief_setzen(roh, ziel, wert)
                uebernommen.append("%s → %s = %s" % (n, ziel, wert))
                break
    quelle = _daten_quelle(daten)
    if quelle:
        _tief_setzen(roh, "daten.quelle", quelle)
        uebernommen.append("Daten → daten.quelle = %s" % quelle)
    benutzt = {n for namen in ABBILDUNG.values() for n in namen} | {
        "datasets", "dataset", "data", "train_file", "data_path", "dataset_dir"}
    for schluessel, wert in sorted(flach.items()):
        if schluessel in benutzt or wert in (None, "", [], {}):
            continue
        grund = UNBEKANNT_ERKLAERT.get(schluessel)
        offen.append("%s = %s%s" % (schluessel, json.dumps(wert, ensure_ascii=False)[:40],
                                    " — %s" % grund if grund else ""))
    if not roh.get("basis"):
        offen.append("Kein Basismodell gefunden — im Rezept nachtragen.")
        roh["basis"] = "HIER-BASISMODELL-EINTRAGEN"
    bericht = {"werkzeug": werkzeug, "uebernommen": uebernommen, "nicht_uebernommen": offen}
    return roh, bericht


def laden(pfad):
    """Konfiguration lesen — JSON oder die YAML-Teilmenge von Dive on Wide."""
    with open(pfad, encoding="utf-8") as f:
        text = f.read()
    try:
        daten = json.loads(text)
    except ValueError:
        daten = rezept.yaml_lesen(text)
    if not isinstance(daten, dict):
        raise rezept.Fehler("Die Datei enthält keine Konfiguration, sondern %s." % type(daten).__name__)
    return daten


def als_yaml(baum, tiefe=0):
    """Das Rezept wieder als lesbares YAML ausgeben."""
    zeilen = []
    for schluessel, wert in baum.items():
        raum = "  " * tiefe
        if isinstance(wert, dict):
            if not wert:
                continue
            zeilen.append("%s%s:" % (raum, schluessel))
            zeilen.append(als_yaml(wert, tiefe + 1))
        elif isinstance(wert, bool):
            zeilen.append("%s%s: %s" % (raum, schluessel, "true" if wert else "false"))
        else:
            zeilen.append("%s%s: %s" % (raum, schluessel, wert))
    return "\n".join(z for z in zeilen if z)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--hilfe", "--help"):
        print(__doc__)
        return 0
    try:
        daten = laden(argv[0])
    except (OSError, rezept.Fehler, ValueError) as e:
        print("Nicht lesbar: %s" % e, file=sys.stderr)
        return 2
    roh, bericht = uebersetzen(daten, argv[0])
    print("# Aus %s übernommen (%s)" % (os.path.basename(argv[0]), bericht["werkzeug"]))
    print(als_yaml(roh))
    print("\n# Nicht übernommen:", file=sys.stderr)
    for zeile in bericht["nicht_uebernommen"]:
        print("#   %s" % zeile, file=sys.stderr)
    try:
        rezept.pruefen(roh)
        print("# Das Rezept ist gültig.", file=sys.stderr)
    except rezept.Fehler as e:
        print("# Noch nicht gültig: %s" % e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
