#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TÜV für KI-Training — prüft Trainingsdaten, bevor jemand eine GPU anwirft.

Es gibt viele Werkzeuge, die ein Modell feintunen. Am Ende liefern sie alle
dasselbe: einen Adapter und eine Verlustkurve. Die Frage, auf die es ankommt,
beantwortet keines davon — **ist der Datensatz sein Training wert, und misst der
Prüfsatz danach noch etwas?**

Dieses Modul beantwortet den Teil, der ohne GPU geht:

    1. Gesundheit    Format, Dubletten, leere Antworten, Rollenfolge, Längen
    2. Leckage       Überschneidung zwischen Trainings- und Prüfteil
    3. Verseuchung   Überschneidung zwischen Trainingsdaten und Prüfsatz —
                     der stillste Weg, sich selbst zu belügen
    4. Herkunft      Wer hat die Daten erzeugt, und darf man damit trainieren?
    5. Siegel        Prüfsummen, mit denen ein Dritter dasselbe Urteil nachrechnet

Und es sagt ausdrücklich, was es **nicht** geprüft hat. „Wirkt der Datensatz?"
beantwortet erst der Datenwert-Test mit echter Rechenzeit (datenwert.py); bis
dahin steht hier `null` und nicht 0.

Absichtlich nur Standardbibliothek und ohne Dive-on-Wide-Server: Der TÜV soll auch
Datensätze prüfen, die mit Axolotl, LLaMA-Factory oder Unsloth entstanden sind,
auf jedem Betriebssystem, ohne Installation.

    python3 tuev.py <datensatz> [--pruefsatz <ordner|datei>] [--bericht aus.md]
"""

import hashlib
import json
import os
import re
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)

import rezept  # noqa: E402  — Formaterkennung und Daten-Doktor, reine Standardbibliothek

VERSION = 1
URTEILE = ("geeignet", "mit Auflagen", "nicht geeignet")


# ------------------------------------------------------------- Verseuchung ---

WORT = re.compile(r"[\wäöüßÄÖÜ]+", re.UNICODE)


def schindeln(text, n=8):
    """Überlappende Wortketten als Fingerabdrücke — die übliche Art, Textüberschneidung
    zu messen, ohne Texte zu vergleichen.

    Acht Wörter sind lang genug, dass zufällige Übereinstimmung selten ist, und
    kurz genug, dass eine leicht umformulierte Aufgabe noch auffällt.
    """
    worte = [w.lower() for w in WORT.findall(text or "")]
    if len(worte) < n:
        return {hashlib.blake2b(" ".join(worte).encode("utf-8"), digest_size=8).hexdigest()} if worte else set()
    return {hashlib.blake2b(" ".join(worte[i:i + n]).encode("utf-8"), digest_size=8).hexdigest()
            for i in range(len(worte) - n + 1)}


def verseuchung(trainingstexte, pruefsatztexte, n=8, schwelle=0.12):
    """Wie viel vom Prüfsatz steckt schon in den Trainingsdaten?

    Das ist der Fehler, der nie auffällt: Ein Modell, das die Prüfaufgaben im
    Training gesehen hat, sieht großartig aus und kann nichts. Gemessen wird je
    Prüfaufgabe der Anteil ihrer Wortketten, der auch im Training vorkommt.
    """
    training = set()
    for t in trainingstexte:
        training |= schindeln(t, n)
    treffer, anteile = [], []
    for i, t in enumerate(pruefsatztexte):
        s = schindeln(t, n)
        if not s:
            continue
        anteil = len(s & training) / float(len(s))
        anteile.append(anteil)
        if anteil >= schwelle:
            treffer.append({"nummer": i, "anteil": round(anteil, 3), "auszug": " ".join((t or "").split())[:120]})
    return {"geprueft": len(anteile), "verseucht": len(treffer),
            "anteil_verseucht": round(len(treffer) / float(len(anteile)), 4) if anteile else None,
            "hoechster_anteil": round(max(anteile), 3) if anteile else None,
            "schwelle": schwelle, "beispiele": treffer[:5],
            "n": n}


# ---------------------------------------------------------------- Herkunft ---

def herkunft_lesen(ordner):
    """Manifest eines Destillations-Exports oder Bericht eines Rezepts lesen.

    Interessiert vor allem eines: Darf mit diesen Daten überhaupt trainiert
    werden? Ausgaben mancher Anbieter dürfen das ausdrücklich nicht.
    """
    aus = {"manifest": None, "lehrer": [], "training_erlaubt": None, "labels": {}, "hinweise": []}
    pfad = os.path.join(ordner, "manifest.json")
    if os.path.isfile(pfad):
        try:
            with open(pfad, encoding="utf-8") as f:
                m = json.load(f)
            aus["manifest"] = pfad
            aus["lehrer"] = sorted({str(x) for x in (m.get("lehrer") or m.get("teacher") or [])})
            aus["labels"] = m.get("labels") or m.get("label_verteilung") or {}
            erlaubt = m.get("training_erlaubt")
            aus["training_erlaubt"] = erlaubt
            if erlaubt is False:
                aus["hinweise"].append("Das Manifest verbietet Training mit diesen Daten "
                                       "(training_erlaubt = false). Nicht trainieren.")
        except (OSError, ValueError) as e:
            aus["hinweise"].append("Manifest nicht lesbar: %s" % e)
    else:
        aus["hinweise"].append("Kein manifest.json — Herkunft der Daten ist nicht belegt. "
                               "Wer den Datensatz erzeugt hat und ob damit trainiert werden darf, "
                               "steht nirgends.")
    bericht = os.path.join(ordner, "bericht.md")
    if os.path.isfile(bericht):
        aus["bericht"] = bericht
    return aus


# -------------------------------------------------------------------- Lauf ---

def _texte_aus(brauchbar, nur_frage=True):
    """Die Texte, auf die es bei Verseuchung ankommt: die Aufgabenstellung."""
    aus = []
    for b in brauchbar:
        nachrichten = b.get("messages") or []
        if nur_frage:
            teile = [m["content"] for m in nachrichten if m.get("role") in ("user", "system", "text")]
        else:
            teile = [m["content"] for m in nachrichten]
        aus.append("\n".join(teile))
    return aus


def pruefsatz_texte(pfad, grenze=400):
    """Prüfaufgaben einlesen — als Ordner mit Aufgaben oder als jsonl."""
    pfad = os.path.abspath(os.path.expanduser(pfad))
    if os.path.isfile(pfad):
        eintraege = rezept.daten_lesen(pfad, grenze=grenze)
        aus = []
        for _d, _n, beispiel, fehler in eintraege:
            if fehler or not isinstance(beispiel, dict):
                continue
            try:
                aus.append("\n".join(m["content"] for m in rezept.nach_nachrichten(beispiel)))
            except rezept.Fehler:
                continue
        return aus
    texte = []
    if os.path.isdir(pfad):
        for name in sorted(os.listdir(pfad))[:grenze]:
            ordner = os.path.join(pfad, name)
            if not os.path.isdir(ordner):
                continue
            stueck = []
            for datei in ("issue.md", "aufgabe.md", "AUFGABE.md", "task.md", "README.md"):
                voll = os.path.join(ordner, datei)
                if os.path.isfile(voll):
                    try:
                        with open(voll, encoding="utf-8", errors="replace") as f:
                            stueck.append(f.read())
                    except OSError:
                        pass
            if stueck:
                texte.append("\n".join(stueck))
    return texte


def siegel(brauchbar, befund_daten, urteil):
    """Prüfsumme über den Inhalt — dasselbe Material ergibt dasselbe Siegel.

    Damit kann ein Dritter nachrechnen, dass ein Bericht zu einem Datensatz
    gehört, ohne den Datensatz zu bekommen.
    """
    abdruecke = sorted(b["abdruck"] for b in brauchbar)
    inhalt = hashlib.sha256(("\n".join(abdruecke)).encode("utf-8")).hexdigest()
    rahmen = json.dumps({"version": VERSION, "beispiele": len(brauchbar),
                         "urteil": urteil, "inhalt": inhalt,
                         "laenge": befund_daten.get("laenge")}, sort_keys=True, ensure_ascii=False)
    return {"inhalt_sha256": inhalt, "bericht_sha256": hashlib.sha256(rahmen.encode("utf-8")).hexdigest(),
            "version": VERSION}


def pruefen(datensatz, pruefsatz=None, max_laenge=4096, zaehler=None, melden=None):
    """Der ganze TÜV für einen Datensatz. Ohne GPU, ohne Netz, ohne Dive on Wide."""
    melden = melden or (lambda *a: None)
    pfad = os.path.abspath(os.path.expanduser(datensatz))
    if not os.path.exists(pfad):
        raise rezept.Fehler("Gibt es nicht: %s" % pfad)
    ordner = pfad if os.path.isdir(pfad) else os.path.dirname(pfad)

    teile = {}
    if os.path.isdir(pfad):
        for name in ("train", "valid", "test"):
            voll = os.path.join(pfad, name + ".jsonl")
            if os.path.isfile(voll):
                teile[name] = voll
    if not teile:
        teile["train"] = pfad

    berichte, brauchbar_je_teil = {}, {}
    for name, datei in teile.items():
        melden("lese %s" % os.path.basename(datei))
        eintraege = rezept.daten_lesen(datei)
        b, brauchbar = rezept.befund(eintraege, "auto", max_laenge, zaehler)
        berichte[name] = b
        brauchbar_je_teil[name] = brauchbar

    haupt = berichte.get("train") or list(berichte.values())[0]
    alle_brauchbar = [b for liste in brauchbar_je_teil.values() for b in liste]

    leck = None
    if "train" in brauchbar_je_teil and "valid" in brauchbar_je_teil:
        leck = rezept.leckage_pruefen(brauchbar_je_teil["train"], brauchbar_je_teil["valid"])

    verseucht = None
    if pruefsatz:
        melden("prüfe Verseuchung gegen den Prüfsatz")
        texte = pruefsatz_texte(pruefsatz)
        if texte:
            verseucht = verseuchung(_texte_aus(brauchbar_je_teil.get("train", alle_brauchbar)), texte)
            verseucht["pruefsatz"] = os.path.abspath(os.path.expanduser(pruefsatz))
            verseucht["aufgaben"] = len(texte)
        else:
            verseucht = {"fehler": "Im Prüfsatz wurden keine Aufgabentexte gefunden."}

    herkunft = herkunft_lesen(ordner)

    # --- Urteil: Sperren zuerst, dann Auflagen
    sperren, auflagen = [], []
    if herkunft.get("training_erlaubt") is False:
        sperren.append("Die Herkunft verbietet Training mit diesen Daten.")
    if haupt["urteil"] == "nicht geeignet":
        sperren.append(haupt.get("grund") or "Die Daten sind unbrauchbar.")
    if leck and leck["anzahl"]:
        sperren.append("%d von %d Prüfbeispielen stehen auch im Training — der Val-Loss misst dann "
                       "Auswendiglernen." % (leck["anzahl"], len(brauchbar_je_teil.get("valid", []))))
    if verseucht and verseucht.get("verseucht"):
        anteil = verseucht.get("anteil_verseucht") or 0
        satz = ("%d von %d Prüfaufgaben stecken schon in den Trainingsdaten (%.0f %%)."
                % (verseucht["verseucht"], verseucht["geprueft"], anteil * 100))
        (sperren if anteil >= 0.05 else auflagen).append(satz)
    for p in haupt.get("probleme", []):
        auflagen.append("%s (%dx): %s" % (p["art"], p["anzahl"], p["rat"]))
    if herkunft.get("manifest") is None:
        auflagen.append("Herkunft nicht belegt (kein manifest.json).")

    urteil = "nicht geeignet" if sperren else ("mit Auflagen" if auflagen else "geeignet")

    aus = {"datensatz": pfad, "teile": {k: berichte[k]["brauchbar"] for k in berichte},
           "gesundheit": berichte, "leckage": leck, "verseuchung": verseucht, "herkunft": herkunft,
           "urteil": urteil, "sperren": sperren, "auflagen": auflagen,
           "nicht_gemessen": [
               "Wirkung auf den Schüler (Datenwert-Test, ≥ 3 Seeds) — braucht Rechenzeit",
               "Transfer auf fremde Aufgaben (Prüfstand)",
               "Qualität der Antworten inhaltlich — der TÜV prüft Form, Herkunft und Überschneidung, "
               "nicht die Richtigkeit einzelner Antworten"],
           "siegel": siegel(alle_brauchbar, haupt, urteil)}
    return aus


# ----------------------------------------------------------------- Bericht ---

def bericht_text(p):
    z = []
    z.append("# TÜV-Bericht: %s\n" % os.path.basename(p["datensatz"].rstrip("/")))
    z.append("**Urteil: %s**\n" % p["urteil"].upper())
    if p["sperren"]:
        z.append("## Sperren — damit wird nicht trainiert")
        z += ["- %s" % s for s in p["sperren"]]
        z.append("")
    if p["auflagen"]:
        z.append("## Auflagen — geht, sollte aber Absicht sein")
        z += ["- %s" % a for a in p["auflagen"]]
        z.append("")
    z.append("## Umfang")
    for teil, anzahl in p["teile"].items():
        b = p["gesundheit"][teil]
        z.append("- **%s**: %d von %d Zeilen brauchbar · Formate %s · Längen (%s): Median %s, 95 %% %s, max %s"
                 % (teil, b["brauchbar"], b["gelesen"],
                    ", ".join("%s: %d" % kv for kv in sorted(b.get("formate", {}).items())),
                    b.get("token_art"), (b.get("laenge") or {}).get("median"),
                    (b.get("laenge") or {}).get("p95"), (b.get("laenge") or {}).get("max")))
    if p["leckage"] is not None:
        z.append("\n## Leckage Training ↔ Prüfteil")
        z.append("- %d Überschneidungen%s" % (p["leckage"]["anzahl"],
                 (" (z. B. %s)" % ", ".join(p["leckage"]["beispiele"][:3])) if p["leckage"]["beispiele"] else ""))
    if p["verseuchung"]:
        v = p["verseuchung"]
        z.append("\n## Verseuchung des Prüfsatzes")
        if v.get("fehler"):
            z.append("- %s" % v["fehler"])
        else:
            z.append("- Prüfsatz: `%s` (%d Aufgaben)" % (v.get("pruefsatz"), v.get("aufgaben", 0)))
            z.append("- %d von %d Aufgaben überschneiden sich mit den Trainingsdaten "
                     "(Schwelle %.0f %% gemeinsamer %d-Wort-Ketten, höchster Wert %s)"
                     % (v["verseucht"], v["geprueft"], v["schwelle"] * 100, v["n"], v["hoechster_anteil"]))
            for b in v.get("beispiele", [])[:3]:
                z.append("  - %.0f %%: „%s …“" % (b["anteil"] * 100, b["auszug"][:90]))
    h = p["herkunft"]
    z.append("\n## Herkunft")
    z.append("- Manifest: %s" % (h["manifest"] or "**fehlt**"))
    if h["lehrer"]:
        z.append("- Lehrer: %s" % ", ".join(h["lehrer"]))
    if h["training_erlaubt"] is not None:
        z.append("- Training erlaubt: **%s**" % ("ja" if h["training_erlaubt"] else "NEIN"))
    for hinweis in h["hinweise"]:
        z.append("- %s" % hinweis)
    z.append("\n## Was hier NICHT geprüft wurde")
    z += ["- %s" % n for n in p["nicht_gemessen"]]
    z.append("\n## Siegel")
    z.append("```\nInhalt  %s\nBericht %s\nVersion %d\n```"
             % (p["siegel"]["inhalt_sha256"], p["siegel"]["bericht_sha256"], p["siegel"]["version"]))
    z.append("\nDerselbe Datensatz ergibt dasselbe Siegel — damit lässt sich ein Bericht einem "
             "Datensatz zuordnen, ohne ihn weiterzugeben.")
    return "\n".join(z) + "\n"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--hilfe", "--help"):
        print(__doc__)
        return 0
    datensatz = argv[0]
    pruefsatz = argv[argv.index("--pruefsatz") + 1] if "--pruefsatz" in argv else None
    ziel = argv[argv.index("--bericht") + 1] if "--bericht" in argv else None
    als_json = "--json" in argv
    try:
        p = pruefen(datensatz, pruefsatz, melden=lambda *a: None if als_json else print("…", *a))
    except rezept.Fehler as e:
        print("Abbruch: %s" % e)
        return 2
    text = bericht_text(p)
    if ziel:
        with open(ziel, "w", encoding="utf-8") as f:
            f.write(text)
        print("Bericht geschrieben: %s" % ziel)
    if als_json:
        print(json.dumps(p, ensure_ascii=False, indent=1))
    elif not ziel:
        print(text)
    return 0 if p["urteil"] != "nicht geeignet" else 3


if __name__ == "__main__":
    sys.exit(main())
