# -*- coding: utf-8 -*-
"""Wiederholung — einen aufgezeichneten Lauf ohne Modell nachspielen.

Wie das „keyless snapshot replay“ von DeepSeek Harness: Die Antworten des
Modells stehen im Schrittprotokoll. Spielt man sie auf einer Kopie des
Startstands erneut in die Werkbank, muss jedes Werkzeug dasselbe liefern wie
damals. Tut es das nicht, hat sich die Werkbank verändert — ein Werkzeug, eine
Regel, die Sandbox — oder das Projekt hing an etwas außerhalb seiner Dateien.

    dowos wiederholen <lauf>          Rückgabe 0: gleich · 1: Abweichungen

Das Projekt selbst wird nie angefasst: Gespielt wird in einem temporären Ordner,
aufgebaut aus dem Checkpunkt „Vor dem Lauf“. Nicht wiederholbar sind Schritte, die
etwas außerhalb brauchen (Websuche, MCP, externe Agenten) — sie werden als
solche gemeldet, nicht als Fehler der Werkbank. Freigaben werden so beantwortet
wie im aufgezeichneten Lauf.
"""

import os
import re
import shutil
import tempfile

import checkpunkte as checkpunkte_modul
import schrittprotokoll
import werkbank

EXTERN = ("websuche", "webseite", "mcp", "extern")
ZEIT_RE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:s|ms|sec|seconds)\b")


def modellfolge(protokoll):
    """Die Antworten in der Reihenfolge, in der das Modell gefragt wurde.

    Im Protokoll steht ein Unteragent vor dem delegieren-Schritt, der ihn
    gestartet hat (der Schritt endet erst, wenn der Unteragent fertig ist) —
    gefragt wurde aber zuerst der Hauptagent. Gleichzeitige Unteragenten werden
    beim Nachspielen nacheinander gefragt (parallel=1), Agent für Agent."""
    folge = []
    for s in protokoll["schritte"]:
        folge.append(s)
        folge.extend(schrittprotokoll.unteragenten_zu(protokoll, s["schritt"]))
    return folge


def normalisieren(text, ordner, kopie):
    text = str(text or "").replace(kopie, ordner)
    text = re.sub(r" \(\d+ Unteragenten gleichzeitig\)", "", text)     # nachgespielt wird nacheinander
    return ZEIT_RE.sub("#s", text)


def wiederholen(ablage, lauf, checkpunkte_basis, stufe=None, regeln_fuer=None):
    """`regeln_fuer(kopie) -> Regeln` liefert die HEUTIGEN Regeln, angewendet
    auf den alten Stand — genau das ist die Frage der Wiederholung.

    Ohne das lief die Wiederholung ganz ohne Regeln, waehrend der
    aufgezeichnete Lauf welche hatte: Ein Schritt, den damals ein Verbot
    stoppte, waere jetzt einfach durchgelaufen, und eine geaenderte oder
    entfernte Regel blieb unsichtbar — obwohl die Beschreibung dieses Moduls
    „eine Regel" ausdruecklich als Grund fuer Abweichungen nennt.
    Injiziert statt hier gebaut, damit die Wiederholung ohne Vertrauensspeicher
    und ohne Einstellungen pruefbar bleibt."""
    p = schrittprotokoll.lesen(ablage, lauf)
    meta, beginn = p["meta"], p["beginn"]
    ordner = meta.get("ordner") or ""
    start = beginn.get("checkpunkt_start")
    if not start:
        raise ValueError("Dieser Lauf hat keinen Startstand (Rechte „nur lesen“ oder ohne Checkpunkte) — nicht wiederholbar.")
    folge = modellfolge(p)
    kopie = os.path.realpath(tempfile.mkdtemp(prefix="dowos-wiederholung-"))
    try:
        checkpunkte_modul.Checkpunkte(checkpunkte_basis, ordner).auspacken(start, kopie)
        zeiger = {"i": 0, "letzter": None}

        def chat(nachrichten):
            if zeiger["i"] >= len(folge):
                zeiger["letzter"] = None
                return '{"werkzeug":"fertig","argumente":{"zusammenfassung":"(Aufzeichnung zu Ende)"}}'
            zeiger["letzter"] = folge[zeiger["i"]]
            zeiger["i"] += 1
            return zeiger["letzter"].get("roh") or ""

        def freigabe(text):
            # So entscheiden wie damals: Abgelehnt wurde, wo das Ergebnis „Abgelehnt“ lautet.
            alt = (zeiger["letzter"] or {}).get("antwort") or ""
            return ": Abgelehnt" not in alt.split("\n", 1)[0]

        neu = []
        vorher = beginn["nachrichten"] if beginn.get("fortsetzung") else None
        wb = werkbank.Werkbank(kopie, stufe or beginn.get("stufe") or meta.get("stufe") or "projekt")
        ergebnis = werkbank.arbeiten(beginn.get("aufgabe", ""), wb, chat, freigabe=freigabe,
                                     regeln=regeln_fuer(kopie) if regeln_fuer else None,
                                     politik=meta.get("freigabe") or "nie", max_schritte=len(p["schritte"]) + 1,
                                     budget=10 ** 9, vorher=vorher, planmodus=bool(beginn.get("planmodus")),
                                     ereignisse=lambda e: neu.append(e) if e.get("art") == "schritt" else None, parallel=1)
        neu_protokoll = {"schritte": [e for e in neu if e.get("unteragent_von") is None], "unteragenten": {}}
        for e in neu:
            if e.get("unteragent_von") is not None:
                neu_protokoll["unteragenten"].setdefault(str(e["unteragent_von"]), []).append(e)
        neu_folge = modellfolge(neu_protokoll)
        abweichungen, nicht_wiederholbar = [], []
        for i, alt in enumerate(folge):
            jetzt = neu_folge[i] if i < len(neu_folge) else None
            wo = "Schritt %s%s" % (alt["schritt"], " (Unteragent)" if alt.get("unteragent_von") is not None else "")
            if jetzt is None:
                abweichungen.append({"wo": wo, "werkzeug": alt.get("werkzeug"), "grund": "in der Wiederholung nicht erreicht"})
                continue
            if alt.get("werkzeug") in EXTERN:
                nicht_wiederholbar.append({"wo": wo, "werkzeug": alt.get("werkzeug")})
                continue
            a = normalisieren(alt.get("antwort"), ordner, kopie)
            b = normalisieren(jetzt.get("antwort"), ordner, kopie)
            if a != b:
                abweichungen.append({"wo": wo, "werkzeug": alt.get("werkzeug"), "damals": a[:1500], "jetzt": b[:1500]})
        return {"lauf": lauf, "schritte": len(folge), "gleich": not abweichungen, "abweichungen": abweichungen,
                "nicht_wiederholbar": nicht_wiederholbar, "beendet_damals": (p["ende"] or {}).get("beendet"),
                "beendet_jetzt": ergebnis["beendet"]}
    finally:
        shutil.rmtree(kopie, ignore_errors=True)
