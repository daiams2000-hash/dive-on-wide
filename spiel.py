#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Spiel — ein Übungsplatz, auf dem sich Lernen wirklich messen lässt.

Der Datenwert-Test scheiterte bisher nicht am Verfahren, sondern am Gegenstand:
12 Agentenaufgaben, Grundmodell schon bei 7/12, jede Messung Stunden. Ein
Fortschritt wäre darin nicht von Rauschen zu unterscheiden.

Ein Schiebepuzzle löst das. Sokoban ist klein genug für ein paar Zeilen Text,
hart genug für ein 4-B-Modell (Sackgassen, Reihenfolge, Rückwärtsdenken) und —
das ist der Punkt — **vollständig entscheidbar**:

* Der **Lehrer ist ein Algorithmus** (Breitensuche), kein fremdes Modell. Jede
  Lösung ist bewiesen optimal, nicht nur plausibel. Keine Lizenzfrage, keine
  API-Kosten, beliebig viele Beispiele.
* Der **Schiedsrichter ist ein Simulator**. „Gelöst" ist keine Meinung: Der
  Zug ist gültig oder nicht, das Puzzle ist gelöst oder nicht.
* Eine Messung ist **ein Modellaufruf je Puzzle**, nicht dreißig Agentenschritte.
  200 Prüffälle dauern Minuten statt Stunden — erst damit werden mehrere Seeds
  bezahlbar, und erst mit mehreren Seeds gibt es ein Konfidenzintervall.

Darstellung (ASCII, wie überall in der Sokoban-Welt):

    #  Wand        .  Ziel          $  Kiste
    @  Spieler     *  Kiste auf Ziel   +  Spieler auf Ziel

    python3 spiel.py zeigen                       ein Puzzle ansehen
    python3 spiel.py erzeugen --anzahl 600 …      Datensatz bauen
    python3 spiel.py orakel                       beweisen, dass der Lehrer stimmt
"""

import argparse
import collections
import hashlib
import json
import os
import random
import sys

WAND, ZIEL, KISTE, SPIELER, KISTE_ZIEL, SPIELER_ZIEL, LEER = "#", ".", "$", "@", "*", "+", " "
ZUEGE = {"U": (-1, 0), "D": (1, 0), "L": (0, -1), "R": (0, 1)}

REGELN = """Du löst ein Sokoban-Puzzle. Zeichen: # Wand, @ Spieler, $ Kiste, . Ziel, \
* Kiste auf Ziel, + Spieler auf Ziel.
Du schiebst Kisten, indem du dich gegen sie bewegst; ziehen geht nicht, und zwei \
Kisten hintereinander lassen sich nicht schieben. Gelöst ist das Puzzle, wenn jede \
Kiste auf einem Ziel steht.
Antworte mit NUR einer Zugfolge aus den Buchstaben U D L R (oben, unten, links, \
rechts), ohne Leerzeichen, ohne Erklärung."""


# ------------------------------------------------------------------ Brett ---

class Brett:
    """Ein Sokoban-Stand: Wände fest, Spieler und Kisten beweglich."""

    def __init__(self, waende, ziele, kisten, spieler, hoehe, breite):
        self.waende = frozenset(waende)
        self.ziele = frozenset(ziele)
        self.kisten = frozenset(kisten)
        self.spieler = spieler
        self.hoehe, self.breite = hoehe, breite

    @classmethod
    def aus_text(cls, text):
        waende, ziele, kisten, spieler = set(), set(), set(), None
        zeilen = [z for z in text.strip("\n").splitlines() if z.strip()]
        for r, zeile in enumerate(zeilen):
            for c, ch in enumerate(zeile):
                p = (r, c)
                if ch == WAND:
                    waende.add(p)
                elif ch == ZIEL:
                    ziele.add(p)
                elif ch == KISTE:
                    kisten.add(p)
                elif ch == KISTE_ZIEL:
                    kisten.add(p); ziele.add(p)
                elif ch == SPIELER:
                    spieler = p
                elif ch == SPIELER_ZIEL:
                    spieler = p; ziele.add(p)
        if spieler is None:
            raise ValueError("Kein Spieler im Brett.")
        return cls(waende, ziele, kisten, spieler,
                   len(zeilen), max(len(z) for z in zeilen))

    def text(self):
        zeilen = []
        for r in range(self.hoehe):
            zeile = []
            for c in range(self.breite):
                p = (r, c)
                if p in self.waende:
                    zeile.append(WAND)
                elif p == self.spieler:
                    zeile.append(SPIELER_ZIEL if p in self.ziele else SPIELER)
                elif p in self.kisten:
                    zeile.append(KISTE_ZIEL if p in self.ziele else KISTE)
                elif p in self.ziele:
                    zeile.append(ZIEL)
                else:
                    zeile.append(LEER)
            zeilen.append("".join(zeile).rstrip())
        return "\n".join(zeilen)

    def geloest(self):
        return self.kisten <= self.ziele

    def zustand(self):
        return (self.spieler, self.kisten)

    def kennung(self):
        """Fingerabdruck des Puzzles — für Dubletten und die Verseuchungsprüfung."""
        return hashlib.sha256(self.text().encode("utf-8")).hexdigest()[:16]

    def ziehen(self, zug):
        """Einen Zug ausführen. Rückgabe: neues Brett — oder None, wenn ungültig."""
        dr, dc = ZUEGE[zug]
        r, c = self.spieler
        neu = (r + dr, c + dc)
        if neu in self.waende:
            return None
        kisten = set(self.kisten)
        if neu in kisten:
            dahinter = (neu[0] + dr, neu[1] + dc)
            if dahinter in self.waende or dahinter in kisten:
                return None
            kisten.discard(neu)
            kisten.add(dahinter)
        return Brett(self.waende, self.ziele, kisten, neu, self.hoehe, self.breite)


def zug_lesen(antwort):
    """Genau einen Zug aus der Modellantwort — oder None.

    Nicht: irgendeinen Buchstaben aus dem Text fischen. „keine Ahnung" wurde am
    18.09.2026 als Zug U gewertet, weil in „AhnUng" ein U steckt — damit wäre
    Ratlosigkeit als Zug in die Messung eingegangen. Eine Antwort ist ein Zug,
    wenn sie einer ist; sonst ist sie unlesbar, und das gehört gezählt."""
    text = (antwort or "").strip()
    if not text:
        return None
    kopf = text.splitlines()[0].strip().strip(".,:;!?*`\"' ")
    if len(kopf) == 1 and kopf.upper() in ZUEGE:
        return kopf.upper()
    if len(kopf) <= 3 and kopf[:1].upper() in ZUEGE:
        return kopf[:1].upper()
    return None


def spielen(brett, zugfolge, grenze=200):
    """Eine Zugfolge ausführen. Rückgabe: (endbrett, gueltige_zuege, fehler).

    Der Schiedsrichter. „Gelöst" ist damit keine Behauptung, sondern ein Fakt:
    entweder stehen am Ende alle Kisten auf Zielen oder nicht."""
    stand, gemacht = brett, 0
    for ch in (zugfolge or "")[:grenze]:
        if ch in " \n\t,-":
            continue
        zug = ch.upper()
        if zug not in ZUEGE:
            return stand, gemacht, "unbekanntes Zeichen %r" % ch
        naechster = stand.ziehen(zug)
        if naechster is None:
            return stand, gemacht, "Zug %d (%s) ist nicht möglich" % (gemacht + 1, zug)
        stand, gemacht = naechster, gemacht + 1
        if stand.geloest():
            break
    return stand, gemacht, ""


# ----------------------------------------------------------------- Orakel ---

def _sackgasse(brett, kiste):
    """Einfache Eckenprüfung: eine Kiste in einer Ecke ohne Ziel ist verloren.

    Nur eine Abkürzung für die Suche — sie darf nichts ausschließen, was noch
    lösbar wäre, deshalb bleibt sie bewusst konservativ."""
    if kiste in brett.ziele:
        return False
    r, c = kiste
    oben = (r - 1, c) in brett.waende
    unten = (r + 1, c) in brett.waende
    links = (r, c - 1) in brett.waende
    rechts = (r, c + 1) in brett.waende
    return (oben or unten) and (links or rechts)


def loesen(brett, grenze=200000):
    """Breitensuche: die kürzeste Zugfolge — oder None, wenn es keine gibt.

    Das ist der Lehrer. Weil er beweisbar optimal ist, ist jedes Trainings-
    beispiel überprüfbar richtig; ein Modell als Lehrer könnte das nicht."""
    if brett.geloest():
        return ""
    start = brett.zustand()
    schlange = collections.deque([(brett, "")])
    gesehen = {start}
    besucht = 0
    while schlange:
        stand, weg = schlange.popleft()
        besucht += 1
        if besucht > grenze:
            return None
        for zug in "UDLR":
            naechster = stand.ziehen(zug)
            if naechster is None:
                continue
            z = naechster.zustand()
            if z in gesehen:
                continue
            if any(_sackgasse(naechster, k) for k in naechster.kisten):
                gesehen.add(z)
                continue
            if naechster.geloest():
                return weg + zug
            gesehen.add(z)
            schlange.append((naechster, weg + zug))
    return None


# ---------------------------------------------------------------- Erzeugen ---

def erzeugen(zufall, hoehe=6, breite=6, kisten=2, versuche=400,
             laenge=(5, 18)):
    """Ein lösbares Puzzle bauen — oder None, wenn es in `versuche` nicht klappt.

    Erzeugt wird vorwärts und dann geprüft: Der Löser sagt, ob es lösbar ist und
    wie lang die kürzeste Lösung ist. Nur Puzzle im gewünschten Längenband
    kommen durch — so ist der Schwierigkeitsgrad eine Zahl, kein Gefühl."""
    for _ in range(versuche):
        felder = [(r, c) for r in range(1, hoehe - 1) for c in range(1, breite - 1)]
        waende = set()
        for r in range(hoehe):
            for c in range(breite):
                if r in (0, hoehe - 1) or c in (0, breite - 1):
                    waende.add((r, c))
        for _ in range(zufall.randint(0, max(1, (hoehe * breite) // 8))):
            p = zufall.choice(felder)
            waende.add(p)
        frei = [p for p in felder if p not in waende]
        if len(frei) < 2 * kisten + 1:
            continue
        gewaehlt = zufall.sample(frei, 2 * kisten + 1)
        ziele = set(gewaehlt[:kisten])
        kiste_pos = set(gewaehlt[kisten:2 * kisten])
        spieler = gewaehlt[-1]
        if kiste_pos & ziele:
            continue
        brett = Brett(waende, ziele, kiste_pos, spieler, hoehe, breite)
        if any(_sackgasse(brett, k) for k in brett.kisten):
            continue
        weg = loesen(brett, grenze=40000)
        if weg is None or not (laenge[0] <= len(weg) <= laenge[1]):
            continue
        return brett, weg
    return None


def datensatz(anzahl, seed=1, formen=((6, 6, 1), (6, 6, 2), (7, 7, 2)),
              laenge=(5, 18), melden=lambda *a: None):
    """`anzahl` verschiedene Puzzle mit bewiesener Lösung, ohne Dubletten."""
    zufall = random.Random(seed)
    aus, gesehen = [], set()
    leerlauf = 0
    while len(aus) < anzahl and leerlauf < anzahl * 50:
        h, b, k = formen[len(aus) % len(formen)]
        erg = erzeugen(zufall, h, b, k, laenge=laenge)
        if not erg:
            leerlauf += 1
            continue
        brett, weg = erg
        kennung = brett.kennung()
        if kennung in gesehen:
            leerlauf += 1
            continue
        gesehen.add(kennung)
        aus.append({"kennung": kennung, "brett": brett.text(), "loesung": weg,
                    "zuege": len(weg), "kisten": k, "groesse": "%dx%d" % (h, b)})
        if len(aus) % 50 == 0:
            melden("  %d von %d Puzzles" % (len(aus), anzahl))
    return aus


REGELN_SCHRITT = """Du löst ein Sokoban-Puzzle, Zug für Zug. Zeichen: # Wand, \
@ Spieler, $ Kiste, . Ziel, * Kiste auf Ziel, + Spieler auf Ziel.
Du schiebst Kisten, indem du dich gegen sie bewegst; ziehen geht nicht, und zwei \
Kisten hintereinander lassen sich nicht schieben. Gelöst ist das Puzzle, wenn jede \
Kiste auf einem Ziel steht.
Antworte mit GENAU EINEM Buchstaben: U (oben), D (unten), L (links) oder R (rechts). \
Kein weiteres Zeichen, keine Erklärung."""


REGELN_KURZ = ("Sokoban. # Wand, @ du, $ Kiste, . Ziel, * Kiste auf Ziel, "
               "+ du auf Ziel. Antworte mit einem Buchstaben: U D L R.")
"""Dieselbe Aufgabe in einem Viertel der Token.

Gemessen am 19.09.2026: Von 174 Token eines Trainingsbeispiels waren 137 die
immer gleiche Systemzeile — 79 %. Das Brett sind 30 Token, die Antwort einer.
Wer die Regeln kürzt, trainiert bei gleicher Datenmenge gut doppelt so schnell.
Die Schieberegeln stehen hier nicht mehr: Sie sind in jedem Beispiel enthalten,
und das Modell soll sie aus den Daten lernen, nicht aus der Ermahnung. Für
einen Vergleich muss dieselbe Zeile auch bei der Messung stehen — sonst misst
man die Anweisung statt das Modell."""


def schritte_eines_puzzles(puzzle, regeln=None):
    """Aus einem Puzzle und seiner optimalen Lösung: jeder Zug ein Beispiel.

    Ein 4-B-Modell kann keine zwölfzügige Folge vorausplanen — gemessen am
    18.09.2026: 0 von 48 Puzzles, auch bei Drei-Zug-Aufgaben, bei durchweg
    sauberem Antwortformat. Es kann aber sehr wohl den nächsten Zug wählen.
    Deshalb ist die Aufgabe „Brett → ein Buchstabe", und der Lehrer liefert zu
    jedem Zwischenstand den beweisbar optimalen Zug."""
    brett = Brett.aus_text(puzzle["brett"])
    aus = []
    for zug in puzzle["loesung"]:
        aus.append({"messages": [
            {"role": "system", "content": regeln or REGELN_SCHRITT},
            {"role": "user", "content": brett.text()},
            {"role": "assistant", "content": zug}]})
        brett = brett.ziehen(zug)
        if brett is None:
            raise ValueError("Die hinterlegte Lösung passt nicht zum Brett.")
    return aus


def messen_schrittweise(chat, puzzles, grenze=None, melden=lambda *a: None,
                        regeln=None):
    """Jedes Puzzle wird gespielt: Brett hin, ein Zug zurück, bis gelöst.

    `grenze` ist das Zugbudget je Puzzle (Standard: doppelt so viele Züge, wie
    das Orakel braucht — wer dreimal so lange irrt, hat es nicht verstanden)."""
    erg = {"n": len(puzzles), "geloest": 0, "optimal": 0, "zuege": 0,
           "ungueltige_zuege": 0, "unlesbar": 0, "fehler": {}}
    for i, p in enumerate(puzzles, 1):
        brett = Brett.aus_text(p["brett"])
        budget = grenze or max(4, p["zuege"] * 2)
        gemacht = 0
        while gemacht < budget and not brett.geloest():
            try:
                antwort = chat([{"role": "system", "content": regeln or REGELN_SCHRITT},
                                {"role": "user", "content": brett.text()}])
            except Exception as e:
                erg["fehler"]["Modellfehler"] = erg["fehler"].get("Modellfehler", 0) + 1
                melden("  %d/%d Modellfehler: %s" % (i, len(puzzles), e))
                break
            zug = zug_lesen(antwort)
            if zug is None:
                erg["unlesbar"] += 1
                break
            naechster = brett.ziehen(zug)
            gemacht += 1
            if naechster is None:
                # Ein ungültiger Zug beendet die Episode. Vorher blieb der Stand
                # stehen — bei Temperatur 0 antwortet das Modell auf dasselbe
                # Brett aber genau gleich, lief also bis zum Budget gegen
                # dieselbe Wand. Gemessen am 18.09.2026: 1404 „ungültige Züge"
                # bei 80 Puzzles waren in Wahrheit 80 Fehler, jeder rund 18-mal
                # gezählt. Eine Zahl, die einen Fehler vervielfacht, misst nicht.
                erg["ungueltige_zuege"] += 1
                erg["fehler"]["ungueltiger Zug"] = erg["fehler"].get("ungueltiger Zug", 0) + 1
                break
            brett = naechster
        erg["zuege"] += gemacht
        if brett.geloest():
            erg["geloest"] += 1
            if gemacht <= p["zuege"]:
                erg["optimal"] += 1
        melden("  %d/%d %s (%d Züge, Orakel %d)"
               % (i, len(puzzles), "✅" if brett.geloest() else "—", gemacht, p["zuege"]))
    erg["quote"] = erg["geloest"] / erg["n"] if erg["n"] else None
    return erg


def zustaende_sammeln(brett, zufall, abweichungen=2, grenze=60):
    """Zustände auf dem Optimalpfad — und daneben. Jeder mit dem Zug des Orakels.

    Warum daneben: Trainiert man nur den Optimalpfad, sieht das Modell nie einen
    Stand, der nach einem eigenen Fehler entsteht. Ein einziger Fehltritt führt
    dann in eine Gegend, die es nicht kennt, und der Rest der Episode ist
    verloren. Gemessen am 18.09.2026: Ein Adapter, der nur Optimalpfade gesehen
    hatte, löste 0 von 80 Puzzles und traf 29 % der Züge — wie das Grundmodell.

    Deshalb weicht diese Sammlung absichtlich ab: ein zufälliger gültiger Zug,
    danach fragt das Orakel erneut, was von HIER aus richtig ist. Weil der
    Lehrer ein Algorithmus ist, kostet das nichts — ein Modell-Lehrer müsste
    für jeden dieser Zustände neu befragt werden."""
    paare, gesehen = [], set()

    def aufnehmen(stand):
        if stand.geloest() or len(paare) >= grenze:
            return None
        text = stand.text()
        if text in gesehen:
            return None
        weg = loesen(stand, grenze=40000)
        if not weg:
            return None                       # Sackgasse: es gibt keinen richtigen Zug
        gesehen.add(text)
        paare.append((text, weg[0]))
        return weg

    weg = aufnehmen(brett)
    if not weg:
        return paare
    # 1. der Optimalpfad
    stand = brett
    for zug in weg:
        stand = stand.ziehen(zug)
        if stand is None or stand.geloest():
            break
        aufnehmen(stand)
    # 2. Abstecher: einmal falsch abbiegen und von dort weiter lernen
    for _ in range(abweichungen):
        stand = brett
        schritte = zufall.randint(0, max(0, len(weg) - 1))
        for zug in weg[:schritte]:
            stand = stand.ziehen(zug)
            if stand is None or stand.geloest():
                break
        if stand is None or stand.geloest():
            continue
        gueltige = [z for z in "UDLR" if stand.ziehen(z) is not None]
        if not gueltige:
            continue
        stand = stand.ziehen(zufall.choice(gueltige))
        neuer_weg = aufnehmen(stand)
        for zug in (neuer_weg or "")[:6]:
            stand = stand.ziehen(zug)
            if stand is None or stand.geloest():
                break
            aufnehmen(stand)
    return paare


def datensatz_zustaende(puzzles, seed=1, abweichungen=2, ausser=(), regeln=None):
    """Aus Puzzles einzelne Lernbeispiele (Brett → ein Zug) machen.

    `ausser` sind Bretter, die nicht ins Training dürfen — die Zustände des
    Prüfsatzes. Ohne diese Sperre könnte dasselbe Brett über ein anderes Puzzle
    doch im Training landen, und die Messung wäre wertlos."""
    zufall = random.Random(seed)
    verboten = set(ausser)
    aus, gesehen = [], set()
    for p in puzzles:
        brett = Brett.aus_text(p["brett"])
        for text, zug in zustaende_sammeln(brett, zufall, abweichungen):
            if text in verboten or text in gesehen:
                continue
            gesehen.add(text)
            aus.append({"messages": [
                {"role": "system", "content": regeln or REGELN_SCHRITT},
                {"role": "user", "content": text},
                {"role": "assistant", "content": zug}]})
    return aus


def zustaende_des_pruefsatzes(puzzles):
    """Jeder Zwischenstand der zurückgehaltenen Puzzles — die Sperrliste."""
    aus = []
    for p in puzzles:
        stand = Brett.aus_text(p["brett"])
        for zug in p["loesung"]:
            aus.append(stand.text())
            stand = stand.ziehen(zug)
            if stand is None:
                break
    return aus


def als_nachrichten(puzzle):
    """Ein Puzzle im Chat-Format des Trainings — Frage, bewiesene Antwort."""
    return {"messages": [
        {"role": "system", "content": REGELN},
        {"role": "user", "content": puzzle["brett"]},
        {"role": "assistant", "content": puzzle["loesung"]}]}


def schreiben(puzzles, ordner, anteil_valid=0.1, pruefsatz=0):
    """train.jsonl, valid.jsonl und einen zurückgehaltenen Prüfsatz ablegen.

    Geteilt wird nach dem Fingerabdruck des Bretts, nicht zufällig: Dasselbe
    Puzzle kann so nicht in Training und Prüfung zugleich landen."""
    os.makedirs(ordner, exist_ok=True)
    sortiert = sorted(puzzles, key=lambda p: p["kennung"])
    pruef = sortiert[:pruefsatz]
    rest = sortiert[pruefsatz:]
    schnitt = max(1, int(len(rest) * (1 - anteil_valid)))
    teile = {"train": rest[:schnitt], "valid": rest[schnitt:]}
    beispiele = {}
    for name, liste in teile.items():
        with open(os.path.join(ordner, name + ".jsonl"), "w", encoding="utf-8") as f:
            n = 0
            for p in liste:
                for beispiel in schritte_eines_puzzles(p):
                    f.write(json.dumps(beispiel, ensure_ascii=False) + "\n")
                    n += 1
            beispiele[name] = n
    if pruef:
        with open(os.path.join(ordner, "pruefsatz.jsonl"), "w", encoding="utf-8") as f:
            for p in pruef:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(os.path.join(ordner, "herkunft.json"), "w", encoding="utf-8") as f:
        json.dump({"quelle": "spiel.py (Sokoban)", "lehrer": "Breitensuche (optimal)",
                   "training_erlaubt": True,
                   "hinweis": "Der Lehrer ist ein Algorithmus, kein Modell. Jede "
                              "Lösung ist bewiesen kürzest — nachprüfbar mit "
                              "`python3 spiel.py orakel`.",
                   "puzzles": len(puzzles), "pruefsatz": len(pruef),
                   "train": len(teile["train"]), "valid": len(teile["valid"])},
                  f, ensure_ascii=False, indent=1)
    return {"train": beispiele["train"], "valid": beispiele["valid"],
            "puzzles_train": len(teile["train"]), "puzzles_valid": len(teile["valid"]),
            "pruefsatz": len(pruef), "ordner": ordner}


# ----------------------------------------------------------------- Messen ---

def messen(chat, puzzles, melden=lambda *a: None):
    """Wie viele Puzzle löst dieses Modell? Ein Aufruf je Puzzle, ein Urteil.

    Gemessen werden drei Dinge, weil sie Verschiedenes bedeuten:
    `gueltig` — die Antwort war überhaupt eine Zugfolge,
    `geloest` — das Puzzle ist am Ende gelöst,
    `optimal` — und zwar mit der kürzest möglichen Zahl an Zügen."""
    erg = {"n": len(puzzles), "gueltig": 0, "geloest": 0, "optimal": 0,
           "zuege_modell": 0, "zuege_orakel": 0, "fehler": {}}
    for i, p in enumerate(puzzles, 1):
        brett = Brett.aus_text(p["brett"])
        try:
            antwort = chat([{"role": "system", "content": REGELN},
                            {"role": "user", "content": p["brett"]}])
        except Exception as e:
            erg["fehler"]["Modellfehler"] = erg["fehler"].get("Modellfehler", 0) + 1
            melden("  %d/%d Modellfehler: %s" % (i, len(puzzles), e))
            continue
        zugfolge = "".join(ch for ch in (antwort or "").upper() if ch in "UDLR")
        if zugfolge:
            erg["gueltig"] += 1
        stand, gemacht, fehler = spielen(brett, zugfolge)
        if fehler:
            erg["fehler"][fehler.split(" ")[0]] = erg["fehler"].get(fehler.split(" ")[0], 0) + 1
        if stand.geloest():
            erg["geloest"] += 1
            erg["zuege_modell"] += gemacht
            erg["zuege_orakel"] += p["zuege"]
            if gemacht <= p["zuege"]:
                erg["optimal"] += 1
        melden("  %d/%d %s (%d Züge, Orakel %d)"
               % (i, len(puzzles), "✅" if stand.geloest() else "—", gemacht, p["zuege"]))
    erg["quote"] = erg["geloest"] / erg["n"] if erg["n"] else None
    return erg


def pruefsatz_lesen(pfad):
    aus = []
    with open(pfad, encoding="utf-8") as f:
        for z in f:
            z = z.strip()
            if z:
                aus.append(json.loads(z))
    return aus


# -------------------------------------------------------------------- CLI ---

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    unter = ap.add_subparsers(dest="befehl", required=True)
    unter.add_parser("zeigen", help="ein Puzzle mit Lösung ansehen")
    e = unter.add_parser("erzeugen", help="Datensatz mit bewiesenen Lösungen bauen")
    e.add_argument("--anzahl", type=int, default=600)
    e.add_argument("--seed", type=int, default=1)
    e.add_argument("--pruefsatz", type=int, default=100)
    e.add_argument("--ziel", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "storage", "training", "daten", "sokoban_v1"))
    o = unter.add_parser("orakel", help="beweisen, dass Lehrer und Schiedsrichter stimmen")
    o.add_argument("--anzahl", type=int, default=50)
    a = ap.parse_args(argv)

    if a.befehl == "zeigen":
        erg = erzeugen(random.Random(7))
        if not erg:
            print("Kein Puzzle erzeugt — das sollte nicht passieren.")
            return 1
        brett, weg = erg
        print(brett.text())
        print("\nKürzeste Lösung (%d Züge): %s" % (len(weg), weg))
        ende, gemacht, fehler = spielen(brett, weg)
        print("Nachgespielt: %s%s" % ("gelöst" if ende.geloest() else "NICHT gelöst",
                                      " (%s)" % fehler if fehler else ""))
        return 0

    if a.befehl == "erzeugen":
        print("Erzeuge %d Puzzles (Lehrer: Breitensuche) …" % a.anzahl)
        puzzles = datensatz(a.anzahl, a.seed, melden=lambda t: print(t, flush=True))
        bericht = schreiben(puzzles, a.ziel, pruefsatz=a.pruefsatz)
        print("Fertig: %(train)d Zug-Beispiele aus %(puzzles_train)d Puzzles, "
              "%(valid)d zur Validierung, %(pruefsatz)d Puzzles zurückgehalten "
              "→ %(ordner)s" % bericht)
        laengen = [p["zuege"] for p in puzzles]
        if laengen:
            print("Lösungslängen: %d bis %d Züge, im Mittel %.1f"
                  % (min(laengen), max(laengen), sum(laengen) / len(laengen)))
        return 0

    # orakel: Der Lehrer muss beweisbar stimmen, sonst ist alles darauf wertlos.
    zufall = random.Random(99)
    gut = schlecht = 0
    for _ in range(a.anzahl):
        erg = erzeugen(zufall)
        if not erg:
            continue
        brett, weg = erg
        ende, gemacht, fehler = spielen(brett, weg)
        kuerzer = loesen(brett)
        if ende.geloest() and not fehler and len(kuerzer) == len(weg):
            gut += 1
        else:
            schlecht += 1
            print("FEHLER im Orakel:\n%s\nWeg %r" % (brett.text(), weg))
    print("%d Puzzles geprüft: %d in Ordnung, %d fehlerhaft" % (gut + schlecht, gut, schlecht))
    return 0 if not schlecht else 1


if __name__ == "__main__":
    sys.exit(main())
