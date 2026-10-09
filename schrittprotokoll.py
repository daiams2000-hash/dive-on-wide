# -*- coding: utf-8 -*-
"""Schrittprotokoll eines Werkbank-Laufs — geschrieben, während er entsteht.

Nach dem Vorbild des Session-Logs von DeepSeek Harness: Alles, was das Modell
zu sehen bekommt, steht in einer Datei, Zeile für Zeile angehängt — der
System-Prompt, die Aufgabe, jede rohe Antwort und jedes Werkzeugergebnis. Daraus
lässt sich jeder Stand wiederherstellen:

- **ansehen**: Schritt für Schritt, mit Zeit, Kontextgröße und Checkpunkt
- **abzweigen**: bei Schritt N mit einer neuen Anweisung weitermachen, auf
  Wunsch mit dem Projekt auf dem Stand dieses Schritts
- **fortsetzen nach Abbruch**: Ein abgebrochener oder abgestürzter Lauf hat
  kein fertiges Ergebnis, aber sein Protokoll — er lässt sich dort aufnehmen.

Die Datei `<lauf>.schritte.jsonl` liegt neben dem Ergebnis `<lauf>.json`.
Sie ist unverdichtet: Gekürzt wird nur, was an das Modell geht, nicht, was hier steht.
"""

import json
import os
import re
import threading

KENNUNG_RE = re.compile(r"^[\w-]{1,80}$")


def pfad(ablage, lauf):
    if not KENNUNG_RE.match(str(lauf or "")):
        raise ValueError("Ungültige Lauf-Kennung.")
    return os.path.join(ablage, "%s.schritte.jsonl" % lauf)


class Schreiber:
    """Als `ereignisse` an werkbank.arbeiten geben. Schreibfehler halten den Lauf nicht auf."""

    def __init__(self, ablage, lauf, **meta):
        os.makedirs(ablage, exist_ok=True)
        self.pfad = pfad(ablage, lauf)
        self.fehler = ""
        self._schloss = threading.Lock()          # gleichzeitige Unteragenten schreiben in dieselbe Datei
        self({"art": "meta", **meta})

    def __call__(self, ereignis):
        try:
            with self._schloss, open(self.pfad, "a", encoding="utf-8") as f:
                f.write(json.dumps(ereignis, ensure_ascii=False, default=str) + "\n")
        except OSError as e:
            self.fehler = str(e)


def lesen(ablage, lauf):
    """{"meta", "beginn", "schritte", "unteragenten", "verdichtet", "ende"} — oder LookupError."""
    datei = pfad(ablage, lauf)
    if not os.path.isfile(datei):
        raise LookupError("Zu diesem Lauf gibt es kein Schrittprotokoll.")
    p = {"meta": {}, "beginn": None, "schritte": [], "unteragenten": {}, "verdichtet": [], "ende": None, "segmente": []}
    with open(datei, encoding="utf-8") as f:
        for zeile in f:
            try:
                e = json.loads(zeile)
            except ValueError:          # letzte Zeile halb geschrieben: Absturz mitten im Schreiben
                continue
            art = e.get("art")
            if e.get("unteragent_von") is not None:
                if art == "schritt":
                    p["unteragenten"].setdefault(str(e["unteragent_von"]), []).append(e)
                continue
            if art == "meta":
                p["meta"].update({k: v for k, v in e.items() if k != "art"})
            elif art == "beginn":
                # Ein zweiter Beginn in derselben Datei: Fortsetzung im selben Lauf (etwa nach einer automatisch
                # beantworteten Rückfrage). Sein Beginn enthält das Gespräch bis dahin; die Schritte davor
                # wandern in „segmente“, damit Schrittnummern eindeutig bleiben.
                if p["beginn"] is not None:
                    p["segmente"].append({"beginn": p["beginn"], "schritte": p["schritte"], "ende": p["ende"]})
                    p["schritte"], p["ende"] = [], None
                p["beginn"] = e
            elif art == "schritt":
                p["schritte"].append(e)
            elif art == "verdichtet":
                p["verdichtet"].append(e)
            elif art == "ende":
                p["ende"] = e
    if not p["beginn"]:
        raise LookupError("Das Schrittprotokoll ist leer.")
    return p


def nachrichten_bis(protokoll, schritt):
    """Das Gespräch so, wie es nach Schritt `schritt` stand — unverdichtet.

    Geeignet als `vorher` für werkbank.arbeiten: Die neue Anweisung wird dort angehängt."""
    try:
        schritt = int(schritt)
    except (TypeError, ValueError):
        raise ValueError("Schritt muss eine Zahl sein.")
    vorhanden = [s["schritt"] for s in protokoll["schritte"]]
    if schritt < 0 or (schritt > 0 and schritt not in vorhanden):
        raise ValueError("Schritt %s gibt es in diesem Lauf nicht (vorhanden: 0–%d)." % (schritt, max(vorhanden or [0])))
    nachrichten = [dict(n) for n in protokoll["beginn"]["nachrichten"]]
    for s in protokoll["schritte"]:
        if s["schritt"] > schritt:
            break
        nachrichten.append({"role": "assistant", "content": s.get("roh") or ""})
        if s.get("antwort") is not None:
            nachrichten.append({"role": "user", "content": s["antwort"]})
    return nachrichten


def alle_schritte(protokoll):
    """Die Schritte aller Segmente in Reihenfolge."""
    return [s for seg in protokoll.get("segmente", []) for s in seg["schritte"]] + protokoll["schritte"]


def unteragenten_zu(protokoll, schritt):
    """Die Schritte der Unteragenten eines delegieren-Schritts, Agent für Agent.
    Ein einzelner Unteragent heißt „5“, mehrere „5#1“, „5#2“ …"""
    schluessel = sorted((k for k in protokoll["unteragenten"] if k == str(schritt) or k.startswith("%s#" % schritt)),
                        key=lambda k: int(k.partition("#")[2] or 0))
    return [s for k in schluessel for s in protokoll["unteragenten"][k]]


def checkpunkt_bis(protokoll, schritt):
    """Der jüngste Checkpunkt bis einschließlich `schritt` — der Projektstand zu diesem Zeitpunkt."""
    kennung = protokoll["beginn"].get("checkpunkt_start")
    for s in protokoll["schritte"]:
        if s["schritt"] > int(schritt):
            break
        if s.get("checkpunkt"):
            kennung = s["checkpunkt"]
    return kennung


def unterbrochen(ablage, aktive=()):
    """Läufe mit Protokoll, aber ohne Ergebnis, die gerade nicht laufen: abgebrochen oder abgestürzt."""
    aus = []
    try:
        namen = os.listdir(ablage)
    except OSError:
        return aus
    for n in namen:
        if not n.endswith(".schritte.jsonl"):
            continue
        lauf = n[:-len(".schritte.jsonl")]
        if lauf in aktive or os.path.exists(os.path.join(ablage, lauf + ".json")):
            continue
        try:
            p = lesen(ablage, lauf)
        except (LookupError, ValueError, OSError):
            continue
        if p["ende"]:                   # zu Ende gelaufen, nur das Ergebnis fehlt (etwa Prüfstand) — nicht unterbrochen
            continue
        aus.append({"id": lauf, "aufgabe": p["beginn"].get("aufgabe", "")[:300], "schritte": len(p["schritte"]),
                    "zeit": os.path.getmtime(os.path.join(ablage, n)), **{k: p["meta"].get(k) for k in
                                                                         ("ordner", "modell", "stufe", "freigabe", "quelle")}})
    return sorted(aus, key=lambda x: -x["zeit"])
