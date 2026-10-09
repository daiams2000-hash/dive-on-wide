#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fehlerbuch — was kostet die Läufe wirklich: das Modell oder das Gerüst?

Jeder Werkbank-Lauf hinterlässt ein Schrittprotokoll: Werkzeug, Argumente,
Ergebnis, Zeit. Darin steht die Antwort auf die Frage, die man sonst nach
Gefühl beantwortet — woran scheitern die Läufe?

Zwei Auswertungen, beide ohne Modell und ohne GPU:

  1. Fehlerbuch      Welcher Fehlschlag kommt wie oft vor, welchem Teil des
                     Gerüsts gehört er (Werkzeug, Prompt, Sandbox, Format, Netz)?
  2. Strategieprobe  Hätte eine andere Abbruchregel Schritte gespart, ohne einen
                     erfolgreichen Lauf zu verlieren? Das lässt sich am
                     aufgezeichneten Baum nachrechnen — solange die Regel nur
                     bestimmt, WANN abgebrochen wird, nicht WAS das Modell
                     antwortet. Alles, was den Prompt ändert, ändert die
                     Antworten und ist damit nicht nachspielbar; das sagt diese
                     Auswertung auch.

    python3 fehlerbuch.py <ordner mit *.schritte.jsonl> [--json]
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict

# Fehlermuster → (Kurzname, Teil des Gerüsts, was man dagegen tut)
MUSTER = [
    (re.compile(r"'alt' kommt \d+-mal vor", re.I), "ersetzen trifft nicht", "Werkzeug",
     "Kleine Modelle treffen den Alt-Text nicht. Ganze Datei schreiben lassen oder "
     "ersetzen nachsichtiger machen."),
    (re.compile(r"ModuleNotFoundError|No module named", re.I), "fehlendes Paket", "Sandbox",
     "In der Sandbox ist kein Netz. Im Prompt sagen, dass mit der Standardbibliothek "
     "gearbeitet wird."),
    (re.compile(r"command not found|externally-managed-environment|pip install", re.I),
     "Installationsversuch", "Sandbox",
     "Der Agent versucht nachzuinstallieren. Gehört in den Prompt, nicht in die Fehlersuche."),
    (re.compile(r"\(keine Treffer\)|Suche ist gerade nicht verfügbar|"
                r"(?:HTTP\s*(?:Error\s*)?|status code[: ]*)\s*(?:403|429|202)", re.I),
     "Suche liefert nichts", "Netz",
     "Suchquelle gesperrt oder gedrosselt. Fächer aus mehreren Quellen benutzen."),
    (re.compile(r"kein gültiges JSON|invalid JSON|Erwartet wird ein JSON", re.I),
     "Antwort ist kein JSON", "Format",
     "Das Modell hält das Antwortformat nicht ein. Format vereinfachen oder Antwortlänge deckeln."),
    (re.compile(r"Insufficient Memory|OOM|out of memory", re.I), "Speicher reicht nicht", "Rechner",
     "Kleineres Modell, kürzerer Kontext, nur ein schwerer Auftrag zur Zeit."),
    (re.compile(r"Datei .* gibt es nicht|No such file|nicht gefunden", re.I), "Pfad falsch", "Werkzeug",
     "Der Agent rät Pfade. Vor dem Zugriff auflisten lassen."),
    (re.compile(r"Zeitlimit|timed out|timeout", re.I), "Zeitlimit", "Rechner",
     "Befehl zu lang. Frist erhöhen oder Aufgabe kleiner schneiden."),
    (re.compile(r"exit=[1-9]", re.I), "Befehl endete mit Fehler", "Aufgabe",
     "Normal beim Arbeiten — nur auffällig, wenn es sich wiederholt."),
]


def _lesen(pfad):
    schritte = []
    with open(pfad, encoding="utf-8", errors="replace") as f:
        for zeile in f:
            zeile = zeile.strip()
            if not zeile:
                continue
            try:
                schritte.append(json.loads(zeile))
            except ValueError:
                continue
    return schritte


def protokolle_finden(ordner):
    ordner = os.path.abspath(os.path.expanduser(ordner))
    if os.path.isfile(ordner):
        return [ordner]
    aus = []
    for wurzel, _o, dateien in os.walk(ordner):
        for d in sorted(dateien):
            if d.endswith(".schritte.jsonl") or (d.endswith(".jsonl") and "schritt" in d):
                aus.append(os.path.join(wurzel, d))
    return aus


def einordnen(text):
    """Einen Fehlertext einem Muster zuordnen."""
    for muster, name, teil, rat in MUSTER:
        if muster.search(text or ""):
            return name, teil, rat
    return None, None, None


def auswerten(pfade):
    """Fehlerbuch über alle Läufe."""
    laeufe, fehler, werkzeuge = [], Counter(), Counter()
    teile = Counter()
    raete = {}
    beispiele = defaultdict(list)
    for pfad in pfade:
        schritte = _lesen(pfad)
        if not schritte:
            continue
        werkzeug_schritte = [s for s in schritte if s.get("werkzeug")]
        ende = [s for s in schritte if s.get("art") == "ende"]
        fertig = any(s.get("werkzeug") == "fertig" for s in schritte)
        lauf = {"datei": os.path.basename(pfad), "schritte": len(werkzeug_schritte),
                "fertig": fertig, "beendet": bool(ende), "fehlschlaege": 0}
        for s in werkzeug_schritte:
            werkzeuge[s["werkzeug"]] += 1
            # Nur das ERGEBNIS eines Werkzeugs einordnen. Der Gedanke des Agenten
            # („wie installiere ich X, wenn es nicht verfügbar ist?") sieht sonst
            # aus wie ein Fehlschlag und verfälscht die ganze Buchhaltung.
            if s["werkzeug"] in ("frage", "plan", "fertig", "merken", "erinnern"):
                continue
            ergebnis = str(s.get("ergebnis") or s.get("antwort") or "")
            name, teil, rat = einordnen(ergebnis)
            if name:
                fehler[name] += 1
                teile[teil] += 1
                raete[name] = (teil, rat)
                lauf["fehlschlaege"] += 1
                if len(beispiele[name]) < 3:
                    beispiele[name].append("%s: %s" % (s["werkzeug"], " ".join(ergebnis.split())[:110]))
        laeufe.append(lauf)
    return {"laeufe": laeufe, "fehler": fehler, "werkzeuge": werkzeuge, "teile": teile,
            "raete": raete, "beispiele": dict(beispiele)}


def strategie_probe(pfade, grenzen=(2, 3, 4)):
    """Hätte „nach k gleichen Fehlversuchen abbrechen" Schritte gespart?

    Nachspielbar ist das, weil die Regel nur bestimmt, wann abgebrochen wird —
    die Antworten des Modells bleiben dieselben. Gezählt wird je Lauf, an welchem
    Schritt die Regel gegriffen hätte und wie viele Schritte danach noch kamen.
    Läufe, die nach dem Eingriff noch erfolgreich wurden, werden getrennt
    ausgewiesen: Das sind die Fälle, in denen die Regel geschadet hätte.
    """
    aus = {}
    for k in grenzen:
        gespart, geschadet, betroffen = 0, 0, 0
        for pfad in pfade:
            schritte = [s for s in pfad and _lesen(pfad) or [] if s.get("werkzeug")]
            if not schritte:
                continue
            wiederholt, letzter = 0, None
            eingriff = None
            for i, s in enumerate(schritte):
                ergebnis = str(s.get("ergebnis") or s.get("antwort") or "")
                name, _teil, _rat = einordnen(ergebnis)
                kennung = (s["werkzeug"], name)
                if name and kennung == letzter:
                    wiederholt += 1
                elif name:
                    wiederholt, letzter = 1, kennung
                else:
                    wiederholt, letzter = 0, None
                if wiederholt >= k:
                    eingriff = i + 1
                    break
            if eingriff is None:
                continue
            betroffen += 1
            rest = schritte[eingriff:]
            gespart += len(rest)
            if any(s["werkzeug"] == "fertig" for s in rest):
                geschadet += 1
        aus[k] = {"betroffene_laeufe": betroffen, "gesparte_schritte": gespart,
                  "verlorene_erfolge": geschadet}
    return aus


def bericht_text(a, strategie=None):
    z = ["# Fehlerbuch\n"]
    laeufe = a["laeufe"]
    fertig = sum(1 for l in laeufe if l["fertig"])
    schritte = sum(l["schritte"] for l in laeufe)
    z.append("%d Läufe, %d Werkzeugschritte, %d mit „fertig“ beendet (%.0f %%).\n"
             % (len(laeufe), schritte, fertig, 100.0 * fertig / max(1, len(laeufe))))
    if a["fehler"]:
        z.append("## Woran es scheitert")
        z.append("| Fehlschlag | Teil des Gerüsts | Anzahl | Anteil der Schritte | Was hilft |")
        z.append("|---|---|---|---|---|")
        for name, anzahl in a["fehler"].most_common():
            teil, rat = a["raete"][name]
            z.append("| %s | %s | %d | %.0f %% | %s |"
                     % (name, teil, anzahl, 100.0 * anzahl / max(1, schritte), rat))
        z.append("")
        z.append("## Nach Teil des Gerüsts")
        for teil, anzahl in a["teile"].most_common():
            z.append("- **%s**: %d Fehlschläge" % (teil, anzahl))
        z.append("")
        z.append("## Beispiele")
        for name, liste in a["beispiele"].items():
            z.append("- **%s**" % name)
            for b in liste[:2]:
                z.append("  - `%s`" % b)
        z.append("")
    else:
        z.append("Keine bekannten Fehlermuster gefunden.\n")
    if a["werkzeuge"]:
        z.append("## Welche Werkzeuge benutzt werden")
        for w, n in a["werkzeuge"].most_common(10):
            z.append("- %s: %d" % (w, n))
        z.append("")
    if strategie:
        z.append("## Strategieprobe: nach k gleichen Fehlversuchen abbrechen")
        z.append("| k | betroffene Läufe | gesparte Schritte | dabei verlorene Erfolge |")
        z.append("|---|---|---|---|")
        for k, w in sorted(strategie.items()):
            z.append("| %d | %d | %d | %d |" % (k, w["betroffene_laeufe"], w["gesparte_schritte"],
                                                w["verlorene_erfolge"]))
        z.append("")
        z.append("Nachgespielt am aufgezeichneten Verlauf. Gültig ist das nur für Regeln, die den "
                 "**Zeitpunkt** des Abbruchs ändern — wer den Prompt ändert, ändert die Antworten "
                 "und muss echt nachlaufen lassen.")
    return "\n".join(z) + "\n"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--hilfe", "--help"):
        print(__doc__)
        return 0
    pfade = protokolle_finden(argv[0])
    if not pfade:
        print("Keine Schrittprotokolle unter %s" % argv[0])
        return 2
    a = auswerten(pfade)
    s = strategie_probe(pfade)
    if "--json" in argv:
        print(json.dumps({"fehler": dict(a["fehler"]), "teile": dict(a["teile"]),
                          "werkzeuge": dict(a["werkzeuge"]), "laeufe": a["laeufe"],
                          "strategie": s}, ensure_ascii=False, indent=1))
    else:
        print(bericht_text(a, s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
