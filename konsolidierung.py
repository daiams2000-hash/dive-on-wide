#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Konsolidierung — die Nachtschicht: aus vielen Läufen wird dauerhaftes Wissen.

Dive on Wide merkt sich heute zweierlei: kurze Notizen (`merken`) und den Volltext
aller früheren Werkbank-Läufe (`erinnern`). Beides entsteht aber nur, wenn
jemand im Lauf daran denkt. Was fehlt, ist der Schritt danach: **hinsetzen,
die letzten Läufe durchsehen und daraus festhalten, was beim nächsten Mal
Zeit spart.** Genau das macht dieses Modul — zu einer Uhrzeit, zu der niemand
zusieht, und mit drei Regeln, die es von einem „autonomen Agenten" unterscheiden:

1. **Die Fakten kommen ohne Modell.** Wie viele Läufe, wie viele fertig, welche
   Werkzeuge, welche Dateien immer wieder, woran es scheiterte — das wird
   gezählt, nicht erzählt. Selbst wenn das Modell Unsinn liefert, ist der
   Bericht wahr.
2. **Das Modell darf nur vorschlagen.** Jeder Vorschlag wird geprüft: Länge,
   Dubletten gegen den Bestand, keine Fragen, keine Floskeln. Was durchfällt,
   steht mit Grund im Bericht — nicht im Gedächtnis.
3. **Übernommen wird nur mit Erlaubnis.** Ohne `uebernehmen=True` schreibt die
   Nachtschicht nichts; sie legt einen Bericht hin. Gelöscht wird nie etwas:
   Widersprüche werden benannt, nicht ausgeführt.

    python3 konsolidierung.py [--ordner storage/werkbank] [--tage 7]
"""

import json
import os
import re
import sys
import time

# Eine Notiz ist ein kurzer, dauerhafter Satz — kein Absatz. Dieselbe Grenze wie
# im Gedächtnis selbst, hier noch einmal geprüft, weil Modelle gern ausholen.
NOTIZ_GRENZE = 160
HOECHSTENS = 5                      # Vorschläge je Nacht; mehr merkt sich niemand
FLOSKELN = ("als ki", "als sprachmodell", "ich habe", "ich werde", "zusammenfassend",
            "es scheint", "vielleicht", "möglicherweise", "könnte sein")

PROMPT = """Du siehst die Auswertung mehrerer abgeschlossener Arbeitsläufe eines
Coding-Agenten. Deine Aufgabe: Finde bis zu %d Erkenntnisse, die beim NÄCHSTEN
Lauf konkret Zeit sparen — Dinge über dieses Projekt und diese Arbeitsweise, die
man sich dauerhaft merken sollte.

REGELN:
- Jede Erkenntnis ist EIN kurzer Aussagesatz (höchstens %d Zeichen).
- Nur, was in den Daten wirklich steht. Erfinde nichts, rate nicht.
- Keine Allgemeinplätze ("Tests sind wichtig"), nichts über einen einzelnen Lauf
  ("Lauf 3 war schnell") — nur Wiederkehrendes.
- Keine Fragen, keine Höflichkeit, keine Einleitung.
- Gibt es nichts Belastbares, schreibe genau: KEINE

FORMAT: eine Zeile je Erkenntnis, beginnend mit "- ".""" % (HOECHSTENS, NOTIZ_GRENZE)


# ------------------------------------------------------------------ Lesen ---

def laeufe_lesen(werkbank_dir, seit=0.0, grenze=200):
    """Abgeschlossene Werkbank-Läufe seit `seit`, neueste zuletzt."""
    aus = []
    try:
        namen = [n for n in os.listdir(werkbank_dir) if n.endswith(".json")]
    except OSError:
        return aus
    for name in namen:
        pfad = os.path.join(werkbank_dir, name)
        try:
            with open(pfad, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict) or "ergebnis" not in d:
            continue
        zeit = float(d.get("zeit") or 0)
        if zeit <= seit:
            continue
        e = d.get("ergebnis") or {}
        aend = e.get("aenderungen") or {}
        aus.append({
            "id": name[:-5], "zeit": zeit, "aufgabe": d.get("aufgabe") or "",
            "ordner": d.get("ordner") or "", "modell": d.get("modell") or "",
            "beendet": e.get("beendet") or "?", "schritte": e.get("schritte") or 0,
            "sekunden": e.get("sekunden") or 0, "ungueltig": e.get("ungueltig") or 0,
            "werkzeuge": e.get("werkzeuge") or {},
            "zusammenfassung": e.get("zusammenfassung") or "",
            "dateien": list(aend.get("neu") or []) + list(aend.get("geaendert") or []),
        })
    aus.sort(key=lambda x: x["zeit"])
    return aus[-grenze:]


def kennzahlen(laeufe):
    """Gezählt, nicht erzählt: der harte Kern jedes Berichts."""
    k = {"laeufe": len(laeufe), "fertig": 0, "abgebrochen": 0, "unlesbar": 0,
         "sekunden": 0.0, "schritte": 0, "werkzeuge": {}, "dateien": {},
         "modelle": {}, "ordner": {}, "ausgaenge": {}}
    for l in laeufe:
        k["ausgaenge"][l["beendet"]] = k["ausgaenge"].get(l["beendet"], 0) + 1
        if l["beendet"] == "fertig":
            k["fertig"] += 1
        else:
            k["abgebrochen"] += 1
        k["unlesbar"] += int(l["ungueltig"] or 0)
        k["sekunden"] += float(l["sekunden"] or 0)
        k["schritte"] += int(l["schritte"] or 0)
        for w, n in (l["werkzeuge"] or {}).items():
            k["werkzeuge"][w] = k["werkzeuge"].get(w, 0) + int(n or 0)
        for d in l["dateien"]:
            k["dateien"][d] = k["dateien"].get(d, 0) + 1
        for feld, schluessel in (("modell", "modelle"), ("ordner", "ordner")):
            wert = l.get(feld) or "?"
            k[schluessel][wert] = k[schluessel].get(wert, 0) + 1
    k["quote"] = (k["fertig"] / k["laeufe"]) if k["laeufe"] else None
    return k


def _ordnen(zaehler, wie_viele=6):
    return sorted(zaehler.items(), key=lambda kv: (-kv[1], kv[0]))[:wie_viele]


def stoff(k, laeufe, fehlerbefund=None, notizen=()):
    """Was das Modell zu sehen bekommt — kompakt, ohne Dateiinhalte."""
    zeilen = ["== Zahlen =="]
    zeilen.append("Läufe: %d · fertig: %d · abgebrochen: %d · unlesbare Antworten: %d"
                  % (k["laeufe"], k["fertig"], k["abgebrochen"], k["unlesbar"]))
    if k["laeufe"]:
        zeilen.append("Schritte je Lauf im Mittel: %.1f · Zeit gesamt: %.0f min"
                      % (k["schritte"] / k["laeufe"], k["sekunden"] / 60.0))
    zeilen.append("Werkzeuge: " + ", ".join("%s %d" % (w, n) for w, n in _ordnen(k["werkzeuge"])))
    if k["dateien"]:
        zeilen.append("Häufig angefasste Dateien: "
                      + ", ".join("%s (%dx)" % (os.path.basename(d), n)
                                  for d, n in _ordnen(k["dateien"])))
    if fehlerbefund:
        zeilen.append("\n== Woran es scheiterte ==\n" + str(fehlerbefund)[:1200])
    if notizen:
        zeilen.append("\n== Schon gemerkt (nicht wiederholen) ==\n"
                      + "\n".join("- " + n for n in list(notizen)[-20:]))
    zeilen.append("\n== Aufgaben und Ergebnisse ==")
    for l in laeufe[-25:]:
        zeilen.append("- [%s, %d Schritte] %s → %s"
                      % (l["beendet"], l["schritte"], (l["aufgabe"] or "")[:110],
                         (l["zusammenfassung"] or "")[:140]))
    return "\n".join(zeilen)


# ------------------------------------------------------------ Vorschläge ---

def _normal(text):
    return re.sub(r"[^a-z0-9äöüß ]+", "", (text or "").lower()).strip()


def vorschlaege_lesen(antwort):
    """Aus der Modellantwort die Zeilen holen, die Vorschläge sein wollen."""
    aus = []
    for zeile in (antwort or "").splitlines():
        zeile = zeile.strip()
        if not zeile:
            continue
        m = re.match(r"^(?:[-*•]|\d+[.)])\s+(.*)$", zeile)
        if m:
            aus.append(" ".join(m.group(1).split()))
    return aus


def vorschlaege_pruefen(roh, vorhandene=(), grenze=HOECHSTENS):
    """Prüft, was das Modell vorschlägt. Rückgabe: (angenommen, abgelehnt).

    Abgelehnt wird mit Grund — der Bericht soll zeigen, was die Nachtschicht
    aussortiert hat, sonst prüft niemand nach, ob sie streng genug ist."""
    bekannt = {_normal(n) for n in vorhandene}
    angenommen, abgelehnt = [], []
    for text in roh:
        text = " ".join((text or "").split())
        n = _normal(text)
        if not n:
            abgelehnt.append((text, "leer"))
        elif text.strip().upper() == "KEINE":
            continue
        elif len(text) > NOTIZ_GRENZE:
            abgelehnt.append((text[:80] + "…", "zu lang (%d Zeichen)" % len(text)))
        elif len(n) < 15:
            abgelehnt.append((text, "zu unbestimmt"))
        elif text.rstrip().endswith("?"):
            abgelehnt.append((text, "eine Frage, keine Erkenntnis"))
        elif any(f in text.lower()[:40] for f in FLOSKELN):
            abgelehnt.append((text, "Floskel statt Fakt"))
        elif n in bekannt:
            abgelehnt.append((text, "steht schon im Gedächtnis"))
        elif len(angenommen) >= grenze:
            abgelehnt.append((text, "über der Höchstzahl von %d" % grenze))
        else:
            bekannt.add(n)
            angenommen.append(text)
    return angenommen, abgelehnt


# --------------------------------------------------------------- Bericht ---

def bericht_text(k, angenommen, abgelehnt, seit, bis, uebernommen=False,
                 fehlerbefund="", hinweis=""):
    z = ["# Nachtschicht: Konsolidierung", ""]
    z.append("Zeitraum: %s bis %s" % (
        time.strftime("%d.%m.%Y %H:%M", time.localtime(seit)) if seit else "Anfang",
        time.strftime("%d.%m.%Y %H:%M", time.localtime(bis))))
    z.append("")
    if not k["laeufe"]:
        z.append("In diesem Zeitraum gab es keine abgeschlossenen Läufe. "
                 "Es gibt nichts zu konsolidieren — und deshalb steht hier auch "
                 "keine erfundene Erkenntnis.")
        return "\n".join(z)
    z.append("## Gezählt")
    z.append("")
    z.append("| | |")
    z.append("|---|---|")
    z.append("| Läufe | %d |" % k["laeufe"])
    z.append("| davon fertig | %d (%.0f %%) |" % (k["fertig"], 100 * (k["quote"] or 0)))
    z.append("| abgebrochen oder am Limit | %d |" % k["abgebrochen"])
    z.append("| unlesbare Modellantworten | %d |" % k["unlesbar"])
    z.append("| Schritte je Lauf (Mittel) | %.1f |" % (k["schritte"] / k["laeufe"]))
    z.append("| Arbeitszeit gesamt | %.0f min |" % (k["sekunden"] / 60.0))
    z.append("")
    if k["werkzeuge"]:
        z.append("**Werkzeuge:** " + ", ".join("`%s` %d×" % (w, n)
                                               for w, n in _ordnen(k["werkzeuge"])))
    if k["dateien"]:
        z.append("**Immer wieder angefasst:** "
                 + ", ".join("`%s` (%d×)" % (os.path.basename(d), n)
                             for d, n in _ordnen(k["dateien"], 5)))
    if k["modelle"]:
        z.append("**Modelle:** " + ", ".join("%s (%d)" % (m, n)
                                             for m, n in _ordnen(k["modelle"], 4)))
    z.append("")
    if fehlerbefund:
        z.append("## Woran es scheiterte")
        z.append("")
        z.append(str(fehlerbefund)[:1500])
        z.append("")
    z.append("## Vorgeschlagene Notizen")
    z.append("")
    if angenommen:
        for t in angenommen:
            z.append("- %s" % t)
        z.append("")
        z.append("**%s**" % ("In das Gedächtnis übernommen." if uebernommen else
                             "Nicht übernommen — die Nachtschicht darf nur vorschlagen. "
                             "Einschalten unter Einstellungen → Rhythmus, oder von Hand "
                             "im Werkbank-Gedächtnis eintragen."))
    else:
        z.append("_Keine. Das ist ein gültiges Ergebnis: Wiederholtes Wissen entsteht "
                 "nicht jede Nacht._")
    if abgelehnt:
        z.append("")
        z.append("## Aussortiert")
        z.append("")
        for t, grund in abgelehnt[:10]:
            z.append("- %s — _%s_" % (t, grund))
    if hinweis:
        z.append("")
        z.append("> %s" % hinweis)
    return "\n".join(z)


# ----------------------------------------------------------------- Lauf ---

def _zustand_pfad(werkbank_dir):
    return os.path.join(werkbank_dir, "konsolidierung.json")


def zustand_lesen(werkbank_dir):
    try:
        with open(_zustand_pfad(werkbank_dir), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"zuletzt": 0.0, "laeufe_gesamt": 0}


def zustand_schreiben(werkbank_dir, zustand):
    try:
        os.makedirs(werkbank_dir, exist_ok=True)
        with open(_zustand_pfad(werkbank_dir), "w", encoding="utf-8") as f:
            json.dump(zustand, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def laufen(werkbank_dir, frage_modell=None, gedaechtnis=None, seit=None,
           uebernehmen=False, fehlerbefund="", melden=lambda *a: None,
           fenster_fortschreiben=True):
    """Eine Nachtschicht. Gibt Bericht, Vorschläge und Zahlen zurück.

    `frage_modell(nachrichten) -> text` wird hineingereicht, damit dieselbe Logik
    im Test ohne Modell läuft. Ohne Modell bleibt der Faktenteil — und der ist
    der Teil, auf den man sich verlassen kann.

    `fenster_fortschreiben=False` schaut nur hin, ohne den Zeitraum zu
    verbrauchen. Das braucht die Kommandozeile: Gemessen am 18.09.2026 hat ein
    Blick in die Zahlen der nächtlichen Konsolidierung die Läufe weggenommen —
    sie sah danach 0 Läufe und schrieb einen leeren Bericht."""
    zustand = zustand_lesen(werkbank_dir)
    seit = zustand.get("zuletzt", 0.0) if seit is None else seit
    jetzt = time.time()
    laeufe = laeufe_lesen(werkbank_dir, seit)
    k = kennzahlen(laeufe)
    melden("Konsolidierung: %d Läufe seit %s"
           % (k["laeufe"], time.strftime("%d.%m. %H:%M", time.localtime(seit)) if seit else "Anfang"))
    notizen = []
    if gedaechtnis is not None:
        try:
            notizen = [n["text"] for n in gedaechtnis.notizen(None)]
        except Exception:
            notizen = []
    angenommen, abgelehnt, hinweis = [], [], ""
    if k["laeufe"] and frage_modell:
        try:
            antwort = frage_modell([
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": stoff(k, laeufe, fehlerbefund, notizen)}])
            angenommen, abgelehnt = vorschlaege_pruefen(
                vorschlaege_lesen(antwort), notizen)
        except Exception as e:
            hinweis = ("Das Modell war nicht erreichbar (%s). Die Zahlen oben sind "
                       "trotzdem gemessen — sie brauchen kein Modell." % e)
            melden("Konsolidierung ohne Modell: %s" % e)
    uebernommen = 0
    if angenommen and uebernehmen and gedaechtnis is not None:
        for t in angenommen:
            try:
                if gedaechtnis.merken(t, None):
                    uebernommen += 1
            except Exception:
                pass
        melden("Konsolidierung: %d Notizen übernommen" % uebernommen)
    bericht = bericht_text(k, angenommen, abgelehnt, seit, jetzt,
                           bool(uebernommen), fehlerbefund, hinweis)
    if fenster_fortschreiben:
        zustand_schreiben(werkbank_dir,
                          {"zuletzt": jetzt,
                           "laeufe_gesamt": zustand.get("laeufe_gesamt", 0) + k["laeufe"],
                           "zuletzt_uebernommen": uebernommen})
    return {"bericht": bericht, "kennzahlen": k, "angenommen": angenommen,
            "abgelehnt": abgelehnt, "uebernommen": uebernommen, "laeufe": k["laeufe"]}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ordner", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "storage", "werkbank"),
        help="Ablage der Werkbank-Läufe")
    ap.add_argument("--tage", type=float, default=0,
                    help="Zeitraum in Tagen (0 = seit der letzten Konsolidierung)")
    a = ap.parse_args(argv)
    seit = (time.time() - a.tage * 86400) if a.tage else None
    # Ohne Modell: nur die gemessenen Zahlen. Genau das soll die Kommandozeile
    # können, ohne dass ein Ollama läuft.
    erg = laufen(a.ordner, frage_modell=None, seit=seit, melden=lambda *x: None,
                 fenster_fortschreiben=False)
    print(erg["bericht"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
