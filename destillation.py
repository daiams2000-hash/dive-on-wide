#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Destillation — Spitzenmodelle als Lehrer, geprüfte Datensätze je Thema für lokale Modelle.

Umsetzung von doku/DESTILLATION.md (aus dem Destillations-Verlauf). Ein Auftrag:

    Themen ─► Aufgabenfabrik (Lehrer oder lokal) ─► Lehrer löst als Werkbank-Agent
          ─► unabhängiges Orakel (orakel.py) ─► Tore G0–G7 ─► Label ─► Datensatz je Thema

Grundsätze, die hier Code sind:
- Das Label ist eine **berechnete Folge der Tore**, nie eine Aussage des Lehrers.
- Drei Ebenen: RAW_FACT (Prozesse), VERIFIED_DERIVATION (Orakel), MODEL_INTERPRETATION (Lehrer).
- Nicht gemessen heißt `null` mit Status UNVERIFIED — nie 0.
- Das Budget ist eine harte Grenze (lehrer.Budget); ist es aufgebraucht, endet der Auftrag sauber.
- Jede Probe trägt Digests mit kanonischer Eingabedefinition und die Provenance jedes Lehreraufrufs.

Aufruf (die Oberfläche macht dasselbe):
    python3 destillation.py starten <ordner-des-auftrags>
Der Ordner enthält auftrag.json; API-Schlüssel kommen über die Umgebung (DOWOS_API_KEYS).
"""

import collections
import hashlib
import json
import os
import random
import re
import shutil
import sys
import tempfile
import time

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
sys.path.insert(0, os.path.join(HIER, "pruefstand"))
import lehrer as lehrer_modul  # noqa: E402
import orakel  # noqa: E402
import schrittprotokoll  # noqa: E402
import werkbank  # noqa: E402

LABELS = ("GOLD_CAUSAL", "GOLD_EXECUTION", "SILVER", "BRONZE", "REJECT", "UNVERIFIED")
TRAINIERBAR = ("GOLD_CAUSAL", "GOLD_EXECUTION")
AUTO_ANTWORT = ("(Automatische Antwort des Prüfstands) Niemand kann deine Frage beantworten. Entscheide selbst "
                "anhand des Issues, des Codes und der Tests, triff eine vernünftige Annahme und löse die Aufgabe.")
TESTBEFEHL = "python -m unittest discover -s tests -t ."
SCHEMA_VERSION = 1


def _jetzt():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _json_schreiben(pfad, daten):
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    with open(pfad + ".tmp", "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)
    os.replace(pfad + ".tmp", pfad)


def _json_lesen(pfad, vorgabe=None):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return vorgabe


# ----------------------------------------------------------------- Auftrag ---

def auftrag_pruefen(daten):
    """Normalisiert einen Auftrag. Wirft ValueError mit klarem Grund."""
    themen = [" ".join(str(t).split())[:120] for t in (daten.get("themen") or []) if str(t).strip()]
    if not themen:
        raise ValueError("Mindestens ein Thema angeben.")
    lehrer = []
    for l in daten.get("lehrer") or []:
        if not l.get("modell"):
            raise ValueError("Jeder Lehrer braucht ein Modell.")
        preis = l.get("preis") or {}
        if l.get("art", "api") not in ("api", "lokal", "abo"):
            raise ValueError("Lehrer-Art: api | lokal | abo")
        if l.get("art", "api") == "api" and (preis.get("eingabe") in (None, "") or preis.get("ausgabe") in (None, "")):
            raise ValueError("Für „%s“ fehlt der Preis je 1 Mio. Token — ohne Preis keine Budgetgrenze." % l["modell"])
        lehrer.append({"modell": str(l["modell"]), "name": str(l.get("name") or l["modell"])[:80],
                       "basis_url": str(l.get("basis_url") or ""), "anbieter": str(l.get("anbieter") or ""),
                       "ollama": bool(l.get("ollama")), "art": l.get("art", "api"),
                       "preis": {k: preis.get(k) for k in ("eingabe", "ausgabe", "cache_eingabe", "waehrung")},
                       "max_tokens": max(512, min(int(l.get("max_tokens") or 8192), 128000)),
                       "kontext": max(8000, min(int(l.get("kontext") or 60000), 1_000_000)),
                       "max_aufrufe": int(l["max_aufrufe"]) if l.get("max_aufrufe") not in (None, "") else None,
                       "befehl": str(l.get("befehl") or ""), "aufwand": l.get("aufwand") or None})
    if not lehrer:
        raise ValueError("Mindestens einen Lehrer angeben.")
    try:
        anzahl = max(1, min(int(daten.get("anzahl") or 1), 10000))
        budget = float(daten["budget"]) if daten.get("budget") not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("anzahl und budget müssen Zahlen sein.")
    if budget is not None and budget <= 0:
        raise ValueError("Das Budget muss größer als 0 sein.")
    quelle = daten.get("aufgaben_quelle") or "lehrer"
    if quelle not in ("lehrer", "vorhanden"):
        raise ValueError("aufgaben_quelle: lehrer | vorhanden")
    return {"name": " ".join(str(daten.get("name") or themen[0]).split())[:80], "themen": themen, "lehrer": lehrer,
            "anzahl": anzahl, "budget": budget, "waehrung": str(daten.get("waehrung") or "USD"),
            "aufgaben_quelle": quelle, "aufgaben_ordner": str(daten.get("aufgaben_ordner") or ""),
            "max_schritte": max(5, min(int(daten.get("max_schritte") or 30), 200)),
            "anteil_je_lehrer": max(0.1, min(float(daten.get("anteil_je_lehrer") or 0.7), 1.0)),
            "anteil_je_cluster": max(0.05, min(float(daten.get("anteil_je_cluster") or 0.4), 1.0)),
            "ablation": daten.get("ablation", True) is not False, "seed": int(daten.get("seed") or 1),
            "verteilung": daten.get("verteilung") if daten.get("verteilung") in ("gleich", "lernluecke") else "lernluecke",
            "schueler_quoten": {str(k): float(v) for k, v in (daten.get("schueler_quoten") or {}).items()
                                if isinstance(v, (int, float)) and 0 <= v <= 1},
            "angelegt": _jetzt(), "schema": SCHEMA_VERSION}


def lehrer_bauen(eintrag, budget, protokoll, schluessel):
    if eintrag.get("art") == "abo":
        return lehrer_modul.AboLehrer(eintrag["name"], eintrag["modell"].split("@@")[-1],
                                      befehl=lehrer_modul.claude_befehl_finden(eintrag.get("befehl") or ""),
                                      max_aufrufe=eintrag.get("max_aufrufe"), protokoll=protokoll,
                                      aufwand=eintrag.get("aufwand"))
    preis = None
    if eintrag["preis"].get("eingabe") not in (None, ""):
        preis = lehrer_modul.Preis(eintrag["preis"]["eingabe"], eintrag["preis"]["ausgabe"],
                                   eintrag["preis"].get("cache_eingabe"), eintrag["preis"].get("waehrung") or "USD")
    return lehrer_modul.Lehrer(eintrag["name"], eintrag["basis_url"], eintrag["modell"].split("@@")[-1],
                               api_key=schluessel.get(eintrag["modell"].split("@@")[0], ""), preis=preis, budget=budget,
                               max_tokens=eintrag["max_tokens"], anbieter=eintrag["anbieter"], protokoll=protokoll,
                               ollama=eintrag["ollama"])


# -------------------------------------------------------------------- Tore ---

def label_berechnen(gates):
    """Das Label folgt allein aus den Toren (DESTILLATION.md, Abschnitt 2)."""
    st = lambda g: (gates.get(g) or {}).get("status")
    if st("G0_infrastruktur") != "PASS":
        return "UNVERIFIED"
    if st("G2_konsistenz") == "FAIL" or (gates.get("G3_orakel") or {}).get("grund") == "Manipulation festgestellt":
        return "REJECT"
    if (gates.get("G2_konsistenz") or {}).get("behauptung_widerspricht"):
        return "REJECT"
    if all(st(g) == "PASS" for g in ("G1_ausfuehrung", "G2_konsistenz", "G3_orakel", "G4_robustheit", "G5_kausal",
                                     "G6_transfer", "G7_schueler")):
        return "GOLD_CAUSAL"
    if all(st(g) == "PASS" for g in ("G1_ausfuehrung", "G2_konsistenz", "G3_orakel", "G4_robustheit", "G5_kausal")):
        return "GOLD_EXECUTION"
    if all(st(g) == "PASS" for g in ("G1_ausfuehrung", "G2_konsistenz", "G3_orakel")):
        return "SILVER"
    if all(st(g) == "PASS" for g in ("G1_ausfuehrung", "G2_konsistenz")):
        return "BRONZE"
    return "UNVERIFIED"


def _befehle(schritte):
    return [s for s in schritte if s.get("werkzeug") == "ausfuehren" and str(s.get("antwort") or "").startswith("Ergebnis von ausfuehren:\nexit=")]


def _exit(schritt):
    m = re.match(r"Ergebnis von ausfuehren:\nexit=(-?\d+)", schritt.get("antwort") or "")
    return int(m.group(1)) if m else None


def probe_bewerten(aufgabe_dir, arbeitsordner, protokoll, ergebnis, ablation=True):
    """Tore G0–G7 für eine gelöste (oder nicht gelöste) Aufgabe. Alles aus Artefakten, nichts aus Behauptungen."""
    befund = orakel.bewerten(aufgabe_dir, arbeitsordner, ablation=ablation)
    gates = dict(befund["gates"])
    schritte = schrittprotokoll.alle_schritte(protokoll)
    befehle = _befehle(schritte)
    ende = protokoll.get("ende") or {}
    gates["G1_ausfuehrung"] = orakel.gate("PASS" if befehle and ende else "FAIL", befehle=len(befehle),
                                          beendet=ende.get("beendet"),
                                          grund=None if befehle and ende else "kein ausgeführter Befehl oder Lauf nicht beendet")
    # G2 — Behauptung ↔ Beobachtung und Artefakte.
    nachgerechnet = orakel.baum_digest(orakel._ohne_tests(orakel.baum_lesen(arbeitsordner)[0]))
    ausgabe_digest = befund["digests"]["output_artifact_sha256"]
    ausgabe_digest.update(recomputed_value=nachgerechnet, match=nachgerechnet == ausgabe_digest["value"])
    tatsaechlich = set(befund.get("geaendert") or [])
    behauptet = set(p for p in (ergebnis.get("geaendert") or []) if not str(p).startswith("tests/"))
    ueber_shell = bool(befehle)
    unerklaert = sorted(tatsaechlich - behauptet) if not ueber_shell else []
    fertig_behauptet = ende.get("beendet") == "fertig"
    g3 = (gates.get("G3_orakel") or {}).get("status")
    widerspruch = fertig_behauptet and g3 == "FAIL"
    gates["G2_konsistenz"] = orakel.gate(
        "PASS" if ausgabe_digest["match"] and not unerklaert else "FAIL",
        digests_stimmen=ausgabe_digest["match"], behauptet=sorted(behauptet), tatsaechlich=sorted(tatsaechlich),
        unerklaerte_aenderungen=unerklaert, behauptung_widerspricht=widerspruch,
        grund=("„fertig“ behauptet, das Orakel widerspricht" if widerspruch else None))
    if gates.get("G3_orakel", {}).get("status") is None:
        gates["G3_orakel"] = orakel.gate("UNVERIFIED", grund="Orakel lief nicht")
    gates["G6_transfer"] = orakel.gate("UNVERIFIED", wert=None, grund="Transfer-Test nicht ausgeführt (Phase 2)")
    gates["G7_schueler"] = orakel.gate("UNVERIFIED", wert=None, grund="Schüler-A/B nicht ausgeführt (Phase 2)")
    # Kategorie — negative Evidenz ist eine eigene Klasse, kein Abfall.
    rot_vorher = any((_exit(s) or 0) != 0 for s in befehle[:-1]) if len(befehle) > 1 else False
    if g3 == "PASS":
        kategorie = "verified_recovery" if rot_vorher else "verified_success"
    elif g3 == "FAIL" and fertig_behauptet:
        kategorie = "verified_failure"
    elif g3 == "FAIL" and ende.get("beendet") in ("frage", "limit"):
        kategorie = "verified_bailout" if ende.get("beendet") == "frage" else "verified_failure"
    else:
        kategorie = "unverified"
    befund["gates"] = gates
    befund["kategorie"] = kategorie
    befund["label"] = label_berechnen(gates)
    return befund


def audit(probe):
    """Die zehn Selbstprüfungsfragen des Verlaufs — soweit sie sich aus den Artefakten beantworten lassen."""
    g = probe["gates"]
    fragen = [
        ("Behauptung ohne Evidenz-Referenz?", False, "Label und Kategorie werden nur aus Toren berechnet"),
        ("Ereignis ohne echte Prozess-Evidenz?", g["G1_ausfuehrung"]["status"] != "PASS", "G1"),
        ("Digest ohne Eingabedefinition?", any(not d.get("canonical_input_definition") for d in probe["integritaet"].values()),
         "alle Digests tragen eine Definition"),
        ("Ergebnis nur aus Modelltext?", g["G3_orakel"]["status"] not in ("PASS", "FAIL"), "Bestehen kommt aus dem Orakel"),
        ("Test durch den Agenten veränderbar?", any(b["art"] == "test_manipulation" for b in probe["sicherheit"]),
         "Tests kommen aus dem Aufgabenspeicher; Änderungen des Agenten sind Befunde"),
        ("Kausalschluss nur Korrelation?", g["G5_kausal"]["status"] != "PASS", "G5: Basis scheitert, Patch besteht, Ablation"),
        ("Behaupteter ≠ beobachteter Zustand?", bool(g["G2_konsistenz"].get("unerklaerte_aenderungen"))
         or bool(g["G2_konsistenz"].get("behauptung_widerspricht")), "G2"),
        ("Transfer durch identische Oberfläche?", None, "Transfer nicht gemessen"),
        ("Schüler-Uplift nicht reproduzierbar?", None, "Uplift nicht gemessen"),
        ("Grund, die Probe NICHT in GOLD aufzunehmen?", probe["label"] not in ("GOLD_CAUSAL",),
         "GOLD_CAUSAL verlangt G6 und G7" if probe["label"] != "GOLD_CAUSAL" else ""),
    ]
    return [{"frage": f, "positiv": p, "begruendung": b} for f, p, b in fragen]


def thema_waehlen(auftrag, zustand, rnd):
    """Welches Thema bekommt die nächste Aufgabe? (Thompson-Sampling statt PID, wie im Verlauf empfohlen.)

    Wert eines Themas = erwartete Ausbeute der Fabrik × erwartete GOLD-Quote des Lehrers × Lernlücke des
    Schülers × Bedarf. Ausbeuten werden aus Beta-Verteilungen gezogen: Wenig Erfahrung heißt breite Streuung,
    also wird ein neues Thema auch ausprobiert. Lernlücke = 1 − gemessene Lösungsquote des Schülers
    (Datenwert-Test, Variante A); ohne Messung 1. Bedarf sinkt mit jeder GOLD-Probe, damit kein Thema alles frisst."""
    themen = auftrag["themen"]
    if auftrag.get("verteilung") == "gleich" or len(themen) == 1:
        return rnd.choice(themen), None
    gold, versuche = collections.Counter(), collections.Counter()
    for p in (zustand.get("proben") or {}).values():
        versuche[p.get("thema")] += 1
        gold[p.get("thema")] += int(p.get("label") in TRAINIERBAR)
    fabrik = zustand.get("fabrik_themen") or {}
    werte = {}
    for t in themen:
        f = fabrik.get(t) or {}
        f_ok, f_alle = f.get("angenommen", 0), f.get("versuche", 0)
        ausbeute = rnd.betavariate(f_ok + 1, max(0, f_alle - f_ok) + 1)
        lehrer = rnd.betavariate(gold[t] + 1, max(0, versuche[t] - gold[t]) + 1)
        luecke = 1.0 - auftrag.get("schueler_quoten", {}).get(t, 0.0)
        werte[t] = ausbeute * lehrer * max(0.05, luecke) / (1 + gold[t])
    gewaehlt = max(werte, key=werte.get)
    return gewaehlt, {t: round(v, 4) for t, v in werte.items()}


def cluster_signatur(schritte):
    """Struktur der Lösung statt Text: Folge der Werkzeuge (Wiederholungen zusammengefasst)."""
    folge = []
    for s in schritte:
        w = s.get("werkzeug") or "unlesbar"
        if not folge or folge[-1] != w:
            folge.append(w)
    return ">".join(folge)[:200]


# ---------------------------------------------------------------- Lösen ---

def loesen(aufgabe_dir, chat, proben_dir, probe_id, max_schritte, kontext, melden=print):
    """Der Lehrer löst die Aufgabe als Werkbank-Agent in einer Kopie. Gibt (Arbeitsordner, Ergebnis, Protokoll)."""
    arbeit = os.path.realpath(tempfile.mkdtemp(prefix="dowos-destillation-"))
    shutil.copytree(os.path.join(aufgabe_dir, "repo"), arbeit, dirs_exist_ok=True)
    with open(os.path.join(aufgabe_dir, "ISSUE.md"), encoding="utf-8") as f:
        issue = f.read().strip()
    schreiber = schrittprotokoll.Schreiber(proben_dir, probe_id, aufgabe=os.path.basename(aufgabe_dir), zeit=time.time())
    wb = werkbank.Werkbank(arbeit, "projekt")
    e = werkbank.arbeiten(issue, wb, chat, politik="nie", max_schritte=max_schritte, budget=kontext,
                          testbefehl=TESTBEFEHL, ereignisse=schreiber, parallel=1,
                          melden=lambda t, z="done": None)
    rueckfragen = 0
    while e["beendet"] == "frage" and rueckfragen < 2 and e["schritte"] < max_schritte:
        weiter = werkbank.arbeiten(AUTO_ANTWORT, wb, chat, politik="nie", max_schritte=max_schritte - e["schritte"],
                                   budget=kontext, vorher=e["nachrichten"], ereignisse=schreiber, parallel=1)
        for k in ("schritte", "sekunden", "ungueltig", "abgelehnt"):
            weiter[k] = e[k] + weiter[k]
        weiter["geaendert"] = list(dict.fromkeys(e["geaendert"] + weiter["geaendert"]))
        rueckfragen += 1
        e = weiter
    e["rueckfragen"] = rueckfragen
    return arbeit, e, schrittprotokoll.lesen(proben_dir, probe_id)


# -------------------------------------------------------------- Auftrag ---

class Destillation:
    def __init__(self, ordner, schluessel=None, melden=print):
        self.ordner = ordner
        self.auftrag = _json_lesen(os.path.join(ordner, "auftrag.json"))
        if not self.auftrag:
            raise ValueError("auftrag.json fehlt oder ist unlesbar.")
        self.zustand = _json_lesen(os.path.join(ordner, "zustand.json"), {"ausgegeben": 0.0, "proben": {}, "aufgaben": [],
                                                                         "ereignisse": []})
        self.budget = lehrer_modul.Budget(self.auftrag.get("budget"), self.zustand.get("ausgegeben", 0.0))
        self.schluessel = schluessel or {}
        self.melden = melden
        self.aufgaben_dir = self.auftrag.get("aufgaben_ordner") if self.auftrag["aufgaben_quelle"] == "vorhanden" \
            else os.path.join(ordner, "aufgaben")
        self.proben_dir = os.path.join(ordner, "proben")

    def speichern(self):
        self.zustand["ausgegeben"] = round(self.budget.ausgegeben, 6)
        self.zustand["budget"] = self.budget.stand()
        self.zustand["aktualisiert"] = _jetzt()
        _json_schreiben(os.path.join(self.ordner, "zustand.json"), self.zustand)

    def _protokoll(self, eintrag):
        with open(os.path.join(self.ordner, "lehrer_aufrufe.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")

    def ereignis(self, text):
        self.zustand.setdefault("ereignisse", []).append({"zeit": _jetzt(), "text": text})
        self.zustand["ereignisse"] = self.zustand["ereignisse"][-200:]
        self.melden(text)

    def aufgaben(self):
        if not os.path.isdir(self.aufgaben_dir):
            return []
        return sorted(n for n in os.listdir(self.aufgaben_dir)
                      if os.path.isfile(os.path.join(self.aufgaben_dir, n, "ISSUE.md")))

    def lauf(self):
        if not werkbank.sandbox_art():
            self.ereignis("❌ G0: keine Sandbox — ohne Sandbox wird kein fremder Code ausgeführt. Auftrag beendet (UNVERIFIED).")
            self.zustand["ende"] = "infrastruktur"
            self.speichern()
            return self.zustand
        lehrer_liste = [lehrer_bauen(l, self.budget, self._protokoll, self.schluessel) for l in self.auftrag["lehrer"]]
        rnd = random.Random(self.auftrag["seed"])
        try:
            while True:
                offen = [a for a in self.aufgaben() if not all(("%s__%d" % (a, i)) in self.zustand["proben"]
                                                              for i in range(len(lehrer_liste)))]
                fertig = len({p["aufgabe"] for p in self.zustand["proben"].values()
                              if all(("%s__%d" % (p["aufgabe"], i)) in self.zustand["proben"] for i in range(len(lehrer_liste)))})
                if fertig >= self.auftrag["anzahl"]:
                    break
                if not offen:
                    if self.auftrag["aufgaben_quelle"] != "lehrer":
                        self.ereignis("Keine weiteren Aufgaben im Ordner — %d von %d bearbeitet." % (fertig, self.auftrag["anzahl"]))
                        break
                    fb = self.zustand.get("fabrik") or {}
                    if fb.get("versuche", 0) >= self.auftrag["anzahl"] * 6:
                        self.ereignis("❌ Aufgabenfabrik: %d Entwürfe, nur %d angenommen — Auftrag beendet, Themen oder Lehrer prüfen."
                                      % (fb.get("versuche", 0), fb.get("angenommen", 0)))
                        self.zustand["ende"] = "fabrik"
                        self.speichern()
                        return self.zustand
                    self._aufgabe_erzeugen(lehrer_liste[0], rnd)
                    continue
                aufgabe = offen[0]
                for i, lehrer in enumerate(lehrer_liste):
                    probe_id = "%s__%d" % (aufgabe, i)
                    if probe_id not in self.zustand["proben"]:
                        self._probe(aufgabe, i, lehrer, probe_id)
                        self.speichern()
            self.zustand["ende"] = "fertig"
            self.ereignis("Bericht: Auftrag fertig — %d Proben, %.4f %s ausgegeben"
                          % (len(self.zustand["proben"]), self.budget.ausgegeben, self.auftrag["waehrung"]))
        except lehrer_modul.BudgetErschoepft as e:
            self.zustand["ende"] = "budget"
            self.ereignis("Bericht: Budget erreicht — sauber beendet. %s" % e)
        except lehrer_modul.LehrerFehler as e:
            self.zustand["ende"] = "lehrer"
            self.ereignis("Bericht: Lehrer nicht nutzbar — %s" % e)
        except lehrer_modul.KontingentErschoepft as e:
            self.zustand["ende"] = "kontingent"
            self.ereignis("Bericht: Kontingent erreicht — sauber beendet, später fortsetzbar. %s" % e)
        self.speichern()
        return self.zustand

    def _aufgabe_erzeugen(self, lehrer, rnd):
        import fabrik
        vorher = set(self.aufgaben())
        thema, werte = thema_waehlen(self.auftrag, self.zustand, rnd)
        if werte:
            self.zustand["verteilung_letzte"] = {"thema": thema, "werte": werte}
        bericht = fabrik.entwerfen(lehrer, 1, self.aufgaben_dir, seed=rnd.randint(1, 10 ** 9), themen=[thema],
                                   melden=lambda t: self.melden("  Fabrik · " + t.strip()), max_versuche=1)
        neu = sorted(set(self.aufgaben()) - vorher)
        ft = self.zustand.setdefault("fabrik_themen", {}).setdefault(thema, {"versuche": 0, "angenommen": 0})
        ft["versuche"] += bericht["versuche"]
        ft["angenommen"] += len(bericht["angenommen"])
        if neu:
            # Wer eine Aufgabe geschrieben hat, bestimmt mit, wofür sie verwendet werden darf.
            meta_pfad = os.path.join(self.aufgaben_dir, "meta.json")
            meta = _json_lesen(meta_pfad, {}) or {}
            for kennung in neu:
                meta.setdefault(kennung, {}).update(erzeugt_von=lehrer.name, lehrer_art=getattr(lehrer, "art", "api"),
                                                   training_erlaubt=getattr(lehrer, "training_erlaubt", True))
            _json_schreiben(meta_pfad, meta)
        self.zustand["fabrik"] = dict(collections.Counter(self.zustand.get("fabrik") or {}) +
                                      collections.Counter({"versuche": bericht["versuche"], "angenommen": len(bericht["angenommen"])}))
        self.speichern()

    def _probe(self, aufgabe, i, lehrer, probe_id):
        aufgabe_dir = os.path.join(self.aufgaben_dir, aufgabe)
        meta = (_json_lesen(os.path.join(self.aufgaben_dir, "meta.json"), {}) or {}).get(aufgabe, {})
        vorher = dict(lehrer.summe)
        t0 = time.time()
        arbeit = None
        try:
            arbeit, ergebnis, protokoll = loesen(aufgabe_dir, lehrer, self.proben_dir, probe_id,
                                                 self.auftrag["max_schritte"], self.auftrag["lehrer"][i]["kontext"])
            befund = probe_bewerten(aufgabe_dir, arbeit, protokoll, ergebnis, ablation=self.auftrag["ablation"])
        except (lehrer_modul.BudgetErschoepft, lehrer_modul.KontingentErschoepft):
            raise
        except lehrer_modul.LehrerFehler as e:
            if getattr(e, "abbruch", False):
                raise
            self.ereignis("❌ %s (%s): Lehrerfehler — %s" % (aufgabe, lehrer.name, e))
            self.zustand["proben"][probe_id] = {"aufgabe": aufgabe, "lehrer": lehrer.name, "label": "UNVERIFIED",
                                                "kategorie": "unverified", "fehler": str(e)[:300]}
            return
        finally:
            if arbeit:
                shutil.rmtree(arbeit, ignore_errors=True)
        kosten = {k: lehrer.summe[k] - vorher[k] for k in ("ein", "aus", "kosten", "aufrufe", "geschaetzt")}
        probe = self.probe_bauen(aufgabe, aufgabe_dir, meta, lehrer, i, probe_id, befund, protokoll, ergebnis, kosten,
                                 time.time() - t0)
        _json_schreiben(os.path.join(self.proben_dir, probe_id + ".json"), probe)
        self.zustand["proben"][probe_id] = {"aufgabe": aufgabe, "lehrer": lehrer.name, "label": probe["final_label"],
                                            "kategorie": probe["kategorie"], "thema": meta.get("thema", ""),
                                            "kosten": round(kosten["kosten"], 6), "schritte": ergebnis["schritte"],
                                            "cluster": probe["diversitaet"]["cluster"]}
        self.ereignis("%s %s (%s): %s · %s · %d Schritte · %.4f %s" % (
            "✅" if probe["final_label"] in TRAINIERBAR else "❌", aufgabe, lehrer.name, probe["final_label"],
            probe["kategorie"], ergebnis["schritte"], kosten["kosten"], self.auftrag["waehrung"]))

    def probe_bauen(self, aufgabe, aufgabe_dir, meta, lehrer, i, probe_id, befund, protokoll, ergebnis, kosten, sekunden):
        eintrag = self.auftrag["lehrer"][i]
        schritte = schrittprotokoll.alle_schritte(protokoll)
        system = protokoll["beginn"]["nachrichten"][0]["content"] if protokoll["beginn"].get("nachrichten") else ""
        schritt_digest = orakel.sha256(open(schrittprotokoll.pfad(self.proben_dir, probe_id), "rb").read())
        probe = {
            "schema": SCHEMA_VERSION, "sample_id": probe_id, "erstellt": _jetzt(),
            "task": {"id": aufgabe, "thema": meta.get("thema"), "art": meta.get("art"), "quelle": meta.get("quelle"),
                     "estimated_difficulty": meta.get("stufe"), "estimate_source": "model" if meta.get("quelle") == "fabrik" else "heuristic",
                     "measured_difficulty": None, "measured_difficulty_status": "UNVERIFIED"},
            "execution": {"schritte": ergebnis["schritte"], "beendet": ergebnis["beendet"], "rueckfragen": ergebnis.get("rueckfragen"),
                          "sekunden": round(sekunden, 1), "unlesbar": ergebnis.get("ungueltig"),
                          "schrittprotokoll": os.path.basename(schrittprotokoll.pfad(self.proben_dir, probe_id))},
            "evidence": {"raw_facts": befund["fakten"], "verified_derivations": befund["ableitungen"],
                         "model_interpretation": {"ebene": "MODEL_INTERPRETATION", "zusammenfassung": ergebnis.get("zusammenfassung"),
                                                  "gedanken": [s.get("gedanke") for s in schritte if s.get("gedanke")][:60]}},
            "gates": befund["gates"],
            "causal_certificate": [
                {"level": 1, "claim": "Die Ausführung fand statt", "result": _stufe(befund["gates"]["G1_ausfuehrung"]),
                 "evidence_refs": ["schrittprotokoll:" + probe_id]},
                {"level": 2, "claim": "Behauptung passt zur Beobachtung", "result": _stufe(befund["gates"]["G2_konsistenz"]),
                 "evidence_refs": ["output_artifact_sha256"]},
                {"level": 3, "claim": "Die Änderung verursacht das Bestehen", "result": _stufe(befund["gates"]["G5_kausal"]),
                 "evidence_refs": [f["execution_id"] for f in befund["fakten"]]},
                {"level": 4, "claim": "Das Ergebnis übersteht unabhängige Störungen", "result": _stufe(befund["gates"]["G4_robustheit"]),
                 "evidence_refs": [f["execution_id"] for f in befund["fakten"] if f.get("variante", {}).get("seed") != "0"]}],
            "counterfactuals": (befund["gates"].get("G5_kausal") or {}).get("ablationen"),
            "transfer": {"value": None, "status": "UNVERIFIED"},
            "student_ab_test": {"value": None, "status": "UNVERIFIED"},
            "integritaet": dict(befund["digests"], execution_record_sha256=orakel.digest_eintrag(
                schritt_digest, "SHA-256 über die Bytes der Datei %s.schritte.jsonl" % probe_id)),
            "sicherheit": befund["sicherheit"],
            "quality": {"correctness": _stufe(befund["gates"]["G3_orakel"]),
                        "robustness": _stufe(befund["gates"]["G4_robustheit"]),
                        "efficiency": {"schritte": ergebnis["schritte"], "token_ein": kosten["ein"], "token_aus": kosten["aus"],
                                       "kosten": round(kosten["kosten"], 6), "waehrung": self.auftrag["waehrung"]},
                        "safety": "FAIL" if befund["sicherheit"] else "PASS",
                        "maintainability": {"geaenderte_dateien": len(befund.get("geaendert") or [])}},
            "provenance": {"lehrer": eintrag["name"], "modell_ref": eintrag["modell"], "anbieter": eintrag["anbieter"],
                           "modell_antwortet": sorted({a.get("modell_antwortet") for a in _aufrufe(self.ordner, lehrer.name,
                                                                                               kosten["aufrufe"]) if a.get("modell_antwortet")}),
                           "system_prompt_sha256": orakel.sha256(system), "tool_schema_sha256": orakel.sha256(werkbank.SYSTEM),
                           "decoding": {"temperature": lehrer.temperatur, "max_tokens": lehrer.max_tokens},
                           "harness": "dowos-werkbank", "token_geschaetzt": bool(kosten["geschaetzt"]),
                           "lehrer_art": eintrag.get("art", "api"),
                           # Abo-Lehrer: Die Bedingungen des Anbieters schränken das Training mit den Ausgaben ein.
                           # Auch eine Aufgabe, die ein Abo-Lehrer geschrieben hat, macht die Probe nicht trainierbar.
                           "training_erlaubt": getattr(lehrer, "training_erlaubt", True) and meta.get("training_erlaubt", True) is not False,
                           "aufgabe_erzeugt_von": meta.get("erzeugt_von")},
            "diversitaet": {"cluster": cluster_signatur(schritte), "lehrer": eintrag["name"]},
            "kategorie": befund["kategorie"], "final_label": befund["label"],
            "rejection_reasons": [g + ": " + str(v.get("grund")) for g, v in befund["gates"].items()
                                  if v.get("status") in ("FAIL", "UNVERIFIED") and v.get("grund")],
            "training_value": {"estimated": None, "measured": None, "status": "UNVERIFIED"},
        }
        probe["audit"] = audit({"gates": befund["gates"], "integritaet": probe["integritaet"], "sicherheit": befund["sicherheit"],
                                "label": befund["label"]})
        probe["evidence_chain"] = [
            {"stufe": "RAW_FACT", "inhalt": "%d Orakelläufe, %d ausgeführte Befehle des Agenten" % (len(befund["fakten"]),
                                                                                            len(_befehle(schritte)))},
            {"stufe": "VERIFIED_DERIVATION", "inhalt": "Orakel: %s" % befund["gates"]["G3_orakel"]["status"]},
            {"stufe": "MODEL_INTERPRETATION", "inhalt": (ergebnis.get("zusammenfassung") or "")[:300]},
            {"stufe": "CAUSAL_CERTIFICATE", "inhalt": "L3 %s · L4 %s" % (_stufe(befund["gates"]["G5_kausal"]),
                                                                        _stufe(befund["gates"]["G4_robustheit"]))},
            {"stufe": "TRANSFER", "inhalt": "UNVERIFIED"}, {"stufe": "STUDENT_UPLIFT", "inhalt": "UNVERIFIED"},
            {"stufe": "FINAL_DATA_LABEL", "inhalt": befund["label"]}]
        return probe


def _stufe(gate):
    return {"PASS": "PASS", "FAIL": "FAIL"}.get((gate or {}).get("status"), "UNVERIFIED")


def _aufrufe(ordner, name, anzahl):
    try:
        with open(os.path.join(ordner, "lehrer_aufrufe.jsonl"), encoding="utf-8") as f:
            zeilen = [json.loads(z) for z in f if z.strip()]
    except (OSError, ValueError):
        return []
    return [z for z in zeilen if z.get("lehrer") == name][-anzahl:] if anzahl else []


# ------------------------------------------------------------------ Export ---

def exportieren(ordner, ziel, labels=TRAINIERBAR, valid_anteil=0.1, max_token=4096, seed=17, holdout_anteil=0.2):
    """Datensatz je Thema aus den Proben eines Auftrags: nur trainierbare Labels, mit Quoten je Lehrer und Cluster.

    Negative Evidenz (verified_failure/bailout) geht in negativ.jsonl — als Kontrast, nie als Nachahmungsziel.
    **Kontrollaufgaben**: Je Thema wird ein Anteil ganzer Aufgaben zurückgehalten (holdout.json) — auf ihnen misst
    der Datenwert-Test (G7), ob ein Schüler durch den Datensatz besser wird. Beispiele derselben Aufgabe landen
    nie teils im Training und teils in der Messung."""
    import trajektorien
    auftrag = _json_lesen(os.path.join(ordner, "auftrag.json"), {})
    proben_dir = os.path.join(ordner, "proben")
    proben = [p for p in (_json_lesen(os.path.join(proben_dir, n)) for n in sorted(os.listdir(proben_dir))
                          if n.endswith(".json")) if p] if os.path.isdir(proben_dir) else []
    nach_thema = collections.defaultdict(list)
    negativ = []
    gesperrt = 0
    for p in proben:
        if p["provenance"].get("training_erlaubt") is False:
            gesperrt += 1                    # nur Systemtest — nie Trainingsdaten, auch nicht als Kontrast
            continue
        if p["final_label"] in labels and p["kategorie"] in ("verified_success", "verified_recovery"):
            nach_thema[p["task"].get("thema") or "ohne Thema"].append(p)
        elif p["kategorie"] in ("verified_failure", "verified_bailout"):
            negativ.append(p)
    rnd = random.Random(seed)
    holdout = {}
    alle_je_thema = collections.defaultdict(set)
    for p in proben:
        alle_je_thema[p["task"].get("thema") or "ohne Thema"].add(p["task"]["id"])
    for thema, ids in alle_je_thema.items():
        ids = sorted(ids)
        n = int(round(len(ids) * holdout_anteil)) if len(ids) >= 3 else 0
        holdout[thema] = sorted(random.Random("%s-%s" % (seed, thema)).sample(ids, n)) if n else []
    for thema in list(nach_thema):
        nach_thema[thema] = [p for p in nach_thema[thema] if p["task"]["id"] not in holdout.get(thema, [])]
    bericht = {"themen": {}, "ziel": ziel, "labels": list(labels), "quoten": {"je_lehrer": auftrag.get("anteil_je_lehrer", 0.7),
                                                                              "je_cluster": auftrag.get("anteil_je_cluster", 0.4)}}
    os.makedirs(ziel, exist_ok=True)
    manifest = []
    for thema, liste in sorted(nach_thema.items()):
        rnd.shuffle(liste)
        gewaehlt, je_lehrer, je_cluster = [], collections.Counter(), collections.Counter()
        grenze_l = max(1, int(len(liste) * auftrag.get("anteil_je_lehrer", 0.7) + 0.999)) if len(set(p["provenance"]["lehrer"] for p in liste)) > 1 else len(liste)
        grenze_c = max(1, int(len(liste) * auftrag.get("anteil_je_cluster", 0.4) + 0.999)) if len(set(p["diversitaet"]["cluster"] for p in liste)) > 1 else len(liste)
        for p in liste:
            l, c = p["provenance"]["lehrer"], p["diversitaet"]["cluster"]
            if je_lehrer[l] >= grenze_l or je_cluster[c] >= grenze_c:
                continue
            je_lehrer[l] += 1
            je_cluster[c] += 1
            gewaehlt.append(p)
        beispiele, verworfen = [], collections.Counter()
        for p in gewaehlt:
            pr = schrittprotokoll.lesen(proben_dir, p["sample_id"])
            letzter = pr["schritte"][-1]["schritt"] if pr["schritte"] else 0
            nachrichten = schrittprotokoll.nachrichten_bis(pr, letzter)
            sauber, grund = trajektorien.aufbereiten(nachrichten)
            if not sauber:
                verworfen[grund] += 1
                continue
            teile, zu_lang = trajektorien.schritt_beispiele(sauber, max_token)
            verworfen["zu lang"] += zu_lang
            beispiele += [{"messages": b} for b in teile]
            manifest.append({"sample_id": p["sample_id"], "thema": thema, "label": p["final_label"],
                             "output_artifact_sha256": p["integritaet"]["output_artifact_sha256"]["value"],
                             "execution_record_sha256": p["integritaet"]["execution_record_sha256"]["value"]})
        name = re.sub(r"[^\w-]+", "_", thema).strip("_")[:60] or "thema"
        rnd.shuffle(beispiele)
        n_valid = max(1, int(len(beispiele) * valid_anteil)) if len(beispiele) > 1 else 0
        tdir = os.path.join(ziel, name)
        os.makedirs(tdir, exist_ok=True)
        for teil, daten in (("valid", beispiele[:n_valid]), ("train", beispiele[n_valid:])):
            with open(os.path.join(tdir, teil + ".jsonl"), "w", encoding="utf-8") as f:
                for b in daten:
                    f.write(json.dumps(b, ensure_ascii=False) + "\n")
        _json_schreiben(os.path.join(tdir, "holdout.json"), {
            "thema": thema, "aufgaben_ordner": auftrag.get("aufgaben_ordner") if auftrag.get("aufgaben_quelle") == "vorhanden"
            else os.path.join(ordner, "aufgaben"), "aufgaben": holdout.get(thema, []), "auftrag": os.path.basename(ordner),
            "auftrag_ordner": os.path.abspath(ordner),
            "proben_im_training": [m["sample_id"] for m in manifest if m["thema"] == thema]})
        # Keine Prüfstand-Aufgabe ist im Datensatz — alle 20 bleiben für Messungen frei (Transfer, Schülerkette).
        with open(os.path.join(tdir, "zurueckgehalten.txt"), "w", encoding="utf-8") as f:
            f.write("# Prüfstand-Aufgaben, die nicht im Datensatz sind\n" + "\n".join(_pruefstand_ids()) + "\n")
        bericht["themen"][thema] = {"proben": len(liste), "gewaehlt": len(gewaehlt), "beispiele": len(beispiele),
                                    "holdout": holdout.get(thema, []),
                                    "je_lehrer": dict(je_lehrer), "cluster": len(je_cluster), "verworfen": dict(verworfen),
                                    "ordner": tdir}
    with open(os.path.join(ziel, "negativ.jsonl"), "w", encoding="utf-8") as f:
        for p in negativ:
            f.write(json.dumps({"sample_id": p["sample_id"], "kategorie": p["kategorie"], "label": p["final_label"],
                                "thema": p["task"].get("thema"), "schrittprotokoll": p["execution"]["schrittprotokoll"]},
                               ensure_ascii=False) + "\n")
    _json_schreiben(os.path.join(ziel, "manifest.json"), {"proben": manifest, "bericht": bericht, "erstellt": _jetzt()})
    bericht["negativ"] = len(negativ)
    bericht["gesperrt"] = gesperrt
    return bericht


def archivieren(ordner, ziel_basis):
    """Sichert einen Auftrag vollständig (Aufgaben, Proben, Schrittprotokolle, Lehreraufrufe, Zustand) mit SHA-256 je Datei.

    Damit bleibt, wofür Lehrer-Kontingent oder Budget schon verbraucht wurde: Aufgaben im Prüfstand-Format
    (als Prüfsatz für andere Schüler: stufe2.py --aufgaben-ordner <archiv>/aufgaben), Proben für erneute
    Auswertungen. Trainingssperren bleiben in den Proben und in manifest.json erhalten."""
    if not os.path.isdir(ordner):
        raise LookupError("Auftrag nicht gefunden.")
    ziel_basis = os.path.expanduser(ziel_basis)
    if not os.path.isdir(os.path.dirname(ziel_basis.rstrip("/")) or "/"):
        raise ValueError("Archivort „%s“ ist nicht erreichbar (SSD angesteckt?)." % ziel_basis)
    ziel = os.path.join(ziel_basis, os.path.basename(ordner.rstrip("/")))
    os.makedirs(ziel, exist_ok=True)
    dateien, gesperrt = [], 0
    for w, _, namen in os.walk(ordner):
        for n in sorted(namen):
            if n.endswith(".tmp") or "__pycache__" in w or "/_verworfen" in w:
                continue
            quelle = os.path.join(w, n)
            rel = os.path.relpath(quelle, ordner)
            os.makedirs(os.path.dirname(os.path.join(ziel, rel)), exist_ok=True)
            shutil.copy2(quelle, os.path.join(ziel, rel))
            with open(quelle, "rb") as f:
                dateien.append({"pfad": rel, "sha256": orakel.sha256(f.read()), "bytes": os.path.getsize(quelle)})
    for p in (_json_lesen(os.path.join(ordner, "proben", d["pfad"].split("/", 1)[1])) for d in dateien
              if d["pfad"].startswith("proben/") and d["pfad"].endswith(".json")):
        if p and p.get("provenance", {}).get("training_erlaubt") is False:
            gesperrt += 1
    manifest = {"auftrag": os.path.basename(ordner.rstrip("/")), "archiviert": _jetzt(), "dateien": dateien,
                "proben_ohne_trainingserlaubnis": gesperrt,
                "hinweis": "Proben mit training_erlaubt=false dürfen nicht als Trainingsdaten verwendet werden "
                           "(Bedingungen des Lehrer-Anbieters). Aufgaben eignen sich als Prüfsatz."}
    _json_schreiben(os.path.join(ziel, "archiv_manifest.json"), manifest)
    return {"ziel": ziel, "dateien": len(dateien), "bytes": sum(d["bytes"] for d in dateien), "gesperrt": gesperrt}


def _pruefstand_ids():
    basis = os.path.join(HIER, "pruefstand", "stufe2", "aufgaben")
    try:
        return sorted(n for n in os.listdir(basis) if os.path.isfile(os.path.join(basis, n, "ISSUE.md")))
    except OSError:
        return []


# ------------------------------------------------------------ Statistik ---

def lauf_statistik(*ablagen):
    """Gemessene Werte aus Schrittprotokollen für die Kostenschätzung."""
    st = {"schritte": [], "kontext_summe": [], "aus_je_schritt": []}
    for ablage in ablagen:
        if not ablage or not os.path.isdir(ablage):
            continue
        for w, _, namen in os.walk(ablage):
            for n in namen:
                if not n.endswith(".schritte.jsonl"):
                    continue
                try:
                    p = schrittprotokoll.lesen(w, n[:-len(".schritte.jsonl")])
                except (LookupError, ValueError, OSError):
                    continue
                if not p["schritte"] or not p["ende"]:
                    continue
                st["schritte"].append(len(p["schritte"]))
                st["kontext_summe"].append(sum(s.get("kontext") or 0 for s in p["schritte"]))
                st["aus_je_schritt"].append(sum(len(s.get("roh") or "") for s in p["schritte"]) / 3.2 / len(p["schritte"]))
    return st


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    unter = ap.add_subparsers(dest="befehl", required=True)
    s = unter.add_parser("starten")
    s.add_argument("ordner")
    e = unter.add_parser("exportieren")
    e.add_argument("ordner")
    e.add_argument("--ziel", required=True)
    e.add_argument("--mit-silber", action="store_true")
    a = ap.parse_args(argv)
    if a.befehl == "exportieren":
        print(json.dumps(exportieren(a.ordner, a.ziel, TRAINIERBAR + (("SILVER",) if a.mit_silber else ())),
                         ensure_ascii=False, indent=1))
        return 0
    schluessel = json.loads(os.environ.get("DOWOS_API_KEYS") or "{}")
    d = Destillation(a.ordner, schluessel, melden=lambda t: print(t, flush=True))
    z = d.lauf()
    return 0 if z.get("ende") in ("fertig", "budget", "kontingent") else 1


if __name__ == "__main__":
    sys.exit(main())
