# -*- coding: utf-8 -*-
"""Eine Roadmap Paket für Paket abarbeiten — und nie weiter, solange Tests rot sind.

Gelernt im Frostwerk-Test (28.09.2026): Die Werkbank baute Paket auf Paket, obwohl die Tests
längst rot waren; nach sechs Stunden schlugen 15 von 57 fehl, und jede Reparatur machte drei
andere Tests kaputt. Im Folgetest („Eisrutsch“) hielt ein Treiber nach jedem Paket an: Tests
laufen lassen, bei Rot bis zu drei Reparaturläufe mit der Testausgabe, danach Halt mit Begründung.
Das ist hier der Ablauf — unabhängig vom Server, damit er sich ohne Modell prüfen lässt.
"""
import json
import os
import re
import time

MAX_REPARATUREN = 3
ANWEISUNGSDATEIEN = ("DOWOS.md", "AGENTS.md")


def pakete_lesen(text):
    """Pakete aus einer Roadmap: JSON-Liste, nummerierte Liste oder Überschriften.

    Jedes Paket: {"titel", "aufgabe", "fertig_wenn"}. Ohne Modell, damit die Roadmap des Nutzers
    genau so abgearbeitet wird, wie sie dasteht."""
    text = (text or "").strip()
    if text.startswith("["):
        try:
            liste = json.loads(text)
            return [{"titel": str(p.get("titel") or p.get("title") or "Paket %d" % i).strip(),
                     "aufgabe": str(p.get("aufgabe") or p.get("task") or p.get("titel") or "").strip(),
                     "fertig_wenn": str(p.get("fertig_wenn") or p.get("done_when") or "").strip()}
                    for i, p in enumerate(liste, 1) if isinstance(p, dict)]
        except ValueError:
            pass
    kopf = re.compile(r"^\s*(?:#{1,4}\s*)?(?:(?:Paket|Arbeitspaket|Schritt|Phase|Package|Step)\s*)?(\d+)[.):]\s*(.+)$", re.I)
    pakete, aktuell = [], None
    for zeile in text.splitlines():
        m = kopf.match(zeile)
        if m and (not aktuell or not zeile.startswith((" ", "\t"))):
            aktuell = {"titel": m.group(2).strip(" *#"), "zeilen": []}
            pakete.append(aktuell)
        elif aktuell is not None and zeile.strip():
            aktuell["zeilen"].append(zeile.strip(" -*\t"))
    aus = []
    for p in pakete:
        fertig = [z for z in p["zeilen"] if re.match(r"(fertig,? wenn|abschlusskriterium|done when|akzeptanz)", z, re.I)]
        rest = [z for z in p["zeilen"] if z not in fertig]
        aus.append({"titel": p["titel"], "aufgabe": "\n".join([p["titel"]] + rest).strip(),
                    "fertig_wenn": " ".join(re.sub(r"^[^:]*:\s*", "", z) for z in fertig).strip()})
    return aus


def testbefehl(ordner, standard):
    """`Tests: <befehl>` aus DOWOS.md oder AGENTS.md — sonst der Standard der Werkbank."""
    for name in ANWEISUNGSDATEIEN:
        pfad = os.path.join(ordner, name)
        if not os.path.isfile(pfad):
            continue
        for zeile in open(pfad, encoding="utf-8", errors="replace"):
            # „- Tests: `python3 -m unittest …` — vor „fertig“ müssen ALLE grün sein“: Befehl in Backticks, Rest egal
            # (Eisrutsch-Nacht 06.10.2026: die Zeile ging durch, die Roadmap nahm den Standardbefehl)
            m = re.match(r"^\s*[-*]?\s*(?:Tests?|Testbefehl|Test command)\s*:\s*`([^`]+)`", zeile, re.I) or \
                re.match(r"^\s*[-*]?\s*(?:Tests?|Testbefehl|Test command)\s*:\s*([^`]+?)\s*$", zeile, re.I)
            if m:
                return m.group(1).strip()
    return standard


def auswerten(ausgabe):
    """(grün, Anzahl Tests, Grund) aus der Ausgabe von unittest oder pytest (Werkbank: „exit=N“ vorn)."""
    text = ausgabe or ""
    code = re.match(r"exit=(-?\d+)", text)
    code = int(code.group(1)) if code else None
    if "Zeitüberschreitung" in text[:80]:
        return False, 0, "Die Tests liefen in eine Zeitüberschreitung."
    m = re.search(r"Ran (\d+) tests?", text)
    if m:
        n = int(m.group(1))
        if n == 0:
            return False, 0, "Es gibt noch keine Tests — ohne Tests lässt sich nicht prüfen, ob das Paket fertig ist."
        ok = re.search(r"^OK\b", text, re.M) is not None and code in (0, None)
        fehl = re.search(r"FAILED \(([^)]*)\)", text)
        return ok, n, "" if ok else "Tests rot: %s" % (fehl.group(1) if fehl else "siehe Ausgabe")
    bestanden = re.search(r"(\d+) passed", text)
    gescheitert = re.search(r"(\d+) (failed|error)", text)
    if bestanden or gescheitert:
        n = sum(int(x) for x in re.findall(r"(\d+) (?:passed|failed|errors?)", text))
        ok = not gescheitert and code in (0, None)
        return ok, n, "" if ok else "Tests rot: %s %s" % (gescheitert.group(1), gescheitert.group(2))
    if "no tests ran" in text or "collected 0 items" in text:
        return False, 0, "Es gibt noch keine Tests — ohne Tests lässt sich nicht prüfen, ob das Paket fertig ist."
    if code == 0:
        return True, None, ""
    # Kein bekanntes Testformat: die letzte Fehlerzeile sagt meist, warum (Tests nicht gefunden, Importfehler)
    zeilen = [z.strip() for z in text.splitlines()[1:] if z.strip()]
    return False, None, "Testbefehl endete mit Code %s: %s" % (code, zeilen[-1][:200] if zeilen else "keine Ausgabe")


def reparatur_auftrag(ausgabe, befehl):
    return ("Die Tests sind rot — bevor es mit dem nächsten Paket weitergeht, müssen alle grün sein. "
            "Ausgabe von `%s`:\n\n%s\n\nPrüfe zuerst, ob der Code oder der Test falsch ist (erwartete Werte "
            "ausrechnen statt abzählen). Dann die Ursache beheben. Vorhandene Tests nur ändern, wenn sie "
            "nachweislich falsch sind, und das begründen." % (befehl, (ausgabe or "")[-1800:]))


class Fahrplan:
    """Zustand einer Roadmap in einer JSON-Datei: lässt sich nach Halt oder Neustart fortsetzen."""

    def __init__(self, pfad):
        self.pfad = pfad
        self.z = json.load(open(pfad, encoding="utf-8")) if os.path.isfile(pfad) else {}

    def sichern(self):
        os.makedirs(os.path.dirname(self.pfad), exist_ok=True)
        tmp = self.pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.z, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.pfad)

    def abarbeiten(self, pakete, arbeite, teste, melden=lambda t: None, max_reparaturen=MAX_REPARATUREN,
                   kopf="", befehl=""):
        """arbeite(aufgabe, vorgaenger) -> Lauf-ID; teste() -> Ausgabe. Hält beim ersten Paket, das rot bleibt."""
        self.z.setdefault("pakete", pakete)
        self.z.setdefault("ergebnisse", {})
        pakete, erg = self.z["pakete"], self.z["ergebnisse"]
        self.z["status"] = "laeuft"
        self.sichern()
        n = len(pakete)
        for i, p in enumerate(pakete, 1):
            schon = erg.get(str(i))
            if schon and schon.get("gruen"):
                continue
            t0 = time.time()
            laeufe = list((schon or {}).get("laeufe") or [])
            if schon:
                # Fortsetzen nach Halt: Vielleicht hat der Nutzer selbst repariert — erst prüfen.
                melden("Paket %d/%d wird fortgesetzt: erst die Tests" % (i, n))
                ausgabe = teste()
                gruen, anzahl, grund = auswerten(ausgabe)
                runde = 0
            else:
                melden("Paket %d/%d: %s" % (i, n, p["titel"][:80]))
                auftrag = ("%sArbeitspaket %d von %d: %s\n%s%s" % (kopf, i, n, p["titel"], p["aufgabe"],
                           "\nFertig, wenn: " + p["fertig_wenn"] if p.get("fertig_wenn") else ""))
                laeufe.append(arbeite(auftrag, None))
                ausgabe = teste()
                gruen, anzahl, grund = auswerten(ausgabe)
                runde = 0
            while not gruen and runde < max_reparaturen:
                runde += 1
                melden("Paket %d: %s — Reparatur %d von %d" % (i, grund or "rot", runde, max_reparaturen))
                laeufe.append(arbeite(reparatur_auftrag(ausgabe, befehl), laeufe[-1] if laeufe else None))
                ausgabe = teste()
                gruen, anzahl, grund = auswerten(ausgabe)
            erg[str(i)] = {"titel": p["titel"], "gruen": gruen, "tests": anzahl, "reparaturen": runde,
                           "laeufe": laeufe, "minuten": round((time.time() - t0) / 60, 1),
                           "grund": grund, "ausgabe": (ausgabe or "")[-1500:]}
            self.sichern()
            if not gruen:
                self.z.update(status="angehalten", halt_bei=i)
                self.sichern()
                melden("Halt bei Paket %d: %s" % (i, grund))
                return self.z
            melden("Paket %d grün (%s Tests)" % (i, anzahl if anzahl is not None else "?"))
        self.z.update(status="fertig", halt_bei=None)
        self.sichern()
        return self.z

    def bericht(self):
        z = self.z
        zeilen = ["# Roadmap: %s" % {"fertig": "alle Pakete grün ✅", "angehalten": "angehalten ⏸",
                                      "laeuft": "läuft"}.get(z.get("status"), z.get("status", "")), "",
                  "| # | Paket | Tests | Reparaturen | Minuten |", "|---|---|---|---|---|"]
        for i, p in enumerate(z.get("pakete", []), 1):
            e = z.get("ergebnisse", {}).get(str(i))
            if not e:
                zeilen.append("| %d | %s | — | — | — |" % (i, p["titel"]))
                continue
            zeilen.append("| %d | %s | %s %s | %d | %.1f |" % (i, p["titel"], "✅" if e["gruen"] else "❌",
                          e["tests"] if e["tests"] is not None else "?", e["reparaturen"], e["minuten"]))
        if z.get("status") == "angehalten":
            e = z["ergebnisse"][str(z["halt_bei"])]
            zeilen += ["", "**Angehalten bei Paket %d:** %s" % (z["halt_bei"], e["grund"]), "",
                       "Die weiteren Pakete bauen darauf auf und würden die Fehler nur stapeln. Repariere "
                       "selbst oder gib einen gezielten Auftrag — „Fortsetzen“ prüft zuerst die Tests.", "",
                       "```", e["ausgabe"][-1200:], "```"]
        return "\n".join(zeilen) + "\n"
