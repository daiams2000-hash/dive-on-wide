#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ausgang — was Dive on Wide herausgibt, wenn ein fremdes Gerüst es befehligt.

Dive on Wide läuft lokal, und das ist der Sinn der Sache. Sobald aber ein Harness
(Claude Code, ein anderer Agent, ein Skript) Aufträge schickt, verlässt etwas
den Rechner: der Auftragstext und die Antwort. Alles dazwischen — Dateien,
Schrittprotokolle, besuchte Seiten, die Gedanken des Modells — bleibt hier,
solange es nicht in der Antwort steht.

Daraus folgt das Prinzip dieses Moduls: **Die Seite, die die Daten besitzt,
entscheidet, was hinausgeht — nicht die Seite, die fragt.** Ein Harness kann
nicht versprechen, etwas nicht zu lesen; was in seinem Kontext landet, ist
draußen. Also wird hier gefiltert, und zwar vor dem Senden.

Vier Stufen (Einstellung `AUSGANG_STUFE`):

    aus              Kein Harness-Zugang. Voreinstellung.
    urteil           Nur Maschinenfakten: Zustand, Schritte, Dauer, Werkzeug-
                     zählung, Anzahl geänderter Dateien. Kein Freitext, keine
                     Dateinamen, keine Inhalte.
    zusammenfassung  Dazu der Abschlusssatz des Agenten, die Schrittliste mit
                     Werkzeugnamen und die Namen der geänderten Dateien —
                     keine Dateiinhalte, keine Befehlsausgaben.
    alles            Das vollständige Protokoll, wie die Oberfläche es zeigt.

Jede hinausgehende Antwort läuft zusätzlich durch `filtern` (Schlüssel,
Zugangsdaten, Mailadressen, absolute Pfade) und wird im Ausgangsbuch
vermerkt: Zeit, Empfänger, Pfad, Stufe, Zeichenzahl, was entfernt wurde.
Damit ist die Zusage nicht behauptet, sondern nachlesbar.

    python3 ausgang.py buch [anzahl]      Ausgangsbuch anzeigen
    python3 ausgang.py summe              Was heute hinausgegangen ist
"""

import json
import os
import re
import sys
import time

STUFEN = ("aus", "urteil", "zusammenfassung", "alles")

ERKLAERUNG = {
    "aus": "Kein Zugang für fremde Gerüste. Aufträge von außen werden abgewiesen.",
    "urteil": "Nur Maschinenfakten: fertig oder gescheitert, Schritte, Dauer, "
              "wie viele Dateien sich geändert haben. Kein Freitext, keine Namen.",
    "zusammenfassung": "Dazu der Abschlusssatz des Agenten, die Werkzeuge der "
                       "Schritte und die Namen der geänderten Dateien. Keine "
                       "Dateiinhalte, keine Befehlsausgaben.",
    "alles": "Das vollständige Protokoll inklusive Gedanken und Ergebnissen der "
             "Schritte — so viel, wie die Oberfläche selbst zeigt.",
}

# Was niemals hinausgeht, egal auf welcher Stufe. Der Name zu jedem Muster steht
# hinterher im Ausgangsbuch: „2 Mailadressen entfernt" ist prüfbar, „gefiltert"
# wäre es nicht.
MUSTER = (
    ("privater Schlüssel",
     re.compile(r"-----BEGIN[^-\n]{0,40}PRIVATE KEY-----.*?-----END[^-\n]{0,40}PRIVATE KEY-----",
                re.S)),
    ("API-Schlüssel",
     re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}\b")),
    ("API-Schlüssel",
     re.compile(r"\b(?:ghp_|gho_|github_pat_|glpat-|xoxb-|xoxp-|AIza)[A-Za-z0-9_\-]{10,}\b")),
    ("Dive-on-Wide-Zugangsschlüssel",
     re.compile(r"\bdow_[A-Za-z0-9_\-]{10,}\b")),
    ("Bearer-Token",
     re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{10,}")),
    ("Mailadresse",
     re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b")),
)

# Zuweisungen aus einer .env oder einem Export: Der Name darf stehen bleiben (er
# erklärt, was fehlt), der Wert nicht. Ohne Zeilenanker, denn so ein Paar steht
# auch mitten in einem Satz („… und OPENAI_API_KEY=geheim123 gesetzt").
GEHEIM_ZUWEISUNG = re.compile(
    r"\b((?:export\s+)?[A-Z][A-Z0-9_]*"
    r"(?:KEY|TOKEN|SECRET|PASS|PASSWORD|CREDENTIAL|CREDENTIALS|API)[A-Z0-9_]*\s*[:=]\s*)"
    r"(\S+)")

# Absolute Heimatpfade verraten den Benutzernamen und die Ordnerstruktur.
HEIMPFAD = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\Users\\)[^\s\"'`,;:)\]}]{0,200}")

ENTFERNT = "<entfernt>"


def stufe_lesen(wert, standard="aus"):
    """Eine Einstellung zu einer gültigen Stufe machen — ohne Überraschungen.

    Ein Tippfehler in der Einstellung darf nicht dazu führen, dass mehr
    hinausgeht als gewollt. Unbekanntes wird deshalb zur strengsten Stufe."""
    wert = (wert or "").strip().lower()
    return wert if wert in STUFEN else standard


def filtern(text, arbeitsordner=""):
    """Text so herausgeben, dass keine Zugangsdaten und Pfade mitfahren.

    Rückgabe: (text, entfernt) — `entfernt` ist eine Liste wie
    ["2 Mailadressen", "1 API-Schlüssel", "Pfade"], die im Ausgangsbuch steht."""
    if not text:
        return text, []
    entfernt = {}

    def zaehlen(name, anzahl=1):
        entfernt[name] = entfernt.get(name, 0) + anzahl

    for name, muster in MUSTER:
        text, n = muster.subn(ENTFERNT, text)
        if n:
            zaehlen(name, n)

    def zuweisung(m):
        return m.group(1) + ENTFERNT
    text, n = GEHEIM_ZUWEISUNG.subn(zuweisung, text)
    if n:
        zaehlen("Zugangsdaten aus Einstellungen", n)

    # Der Arbeitsordner wird relativ — das ist nicht nur privater, sondern auch
    # lesbarer: „./tests/test_x.py" statt „/Users/name/DowOS/app/storage/…".
    if arbeitsordner:
        wurzel = os.path.abspath(os.path.expanduser(arbeitsordner)).rstrip("/\\")
        # Windows schreibt denselben Ordner als C:\\… und als C:/… (Python gibt gern
        # Letzteres aus), dazu so, wie er übergeben wurde (Windows-VM, 29.09.2026).
        formen = sorted({wurzel, wurzel.replace("\\", "/"), str(arbeitsordner).rstrip("/\\")},
                        key=len, reverse=True)
        gefunden = False
        for form in formen:
            if form and len(form) > 1 and form in text:
                text = text.replace(form, ".")
                gefunden = True
        if gefunden:
            zaehlen("Pfad des Arbeitsordners")
    text, n = HEIMPFAD.subn("<Pfad entfernt>", text)
    if n:
        zaehlen("Pfade", n)

    return text, ["%d %s" % (v, k) if v > 1 else k for k, v in entfernt.items()]


def _text(wert, arbeitsordner, entfernt):
    """Einen Textwert filtern und die Befunde einsammeln."""
    neu, weg = filtern(wert, arbeitsordner)
    for w in weg:
        if w not in entfernt:
            entfernt.append(w)
    return neu


def bericht(auftrag, stufe, arbeitsordner=""):
    """Was ein Harness über diesen Auftrag erfährt — je Stufe.

    `auftrag` ist der abgelegte Werkbank-Auftrag (mit „ergebnis"), wie ihn die
    Oberfläche auch liest. Rückgabe: (bericht, entfernt)."""
    stufe = stufe_lesen(stufe)
    e = (auftrag.get("ergebnis") or {}) if isinstance(auftrag, dict) else {}
    zustand = e.get("beendet") or auftrag.get("zustand") or "laeuft"
    aus = {"id": auftrag.get("id", ""), "zustand": zustand, "stufe": stufe}
    entfernt = []
    if stufe == "aus":
        return {"stufe": "aus",
                "hinweis": "Der Zugang für fremde Gerüste ist abgeschaltet."}, entfernt

    aend = e.get("aenderungen") or {}
    aus.update({
        "schritte": e.get("schritte"),
        "sekunden": e.get("sekunden"),
        "unlesbare_antworten": e.get("ungueltig"),
        "abgelehnte_aktionen": e.get("abgelehnt"),
        "werkzeuge": dict(e.get("werkzeuge") or {}),
        "dateien": {art: len(aend.get(art) or [])
                    for art in ("neu", "geaendert", "geloescht")},
        "rueckfrage_offen": bool(e.get("frage")),
    })
    if stufe == "urteil":
        return aus, entfernt

    aus["zusammenfassung"] = _text(e.get("zusammenfassung") or "",
                                   arbeitsordner, entfernt)
    if e.get("frage"):
        aus["frage"] = _text(e["frage"], arbeitsordner, entfernt)
    aus["dateinamen"] = {art: [_text(p, arbeitsordner, entfernt)
                               for p in (aend.get(art) or [])]
                         for art in ("neu", "geaendert", "geloescht")}
    aus["schritte_liste"] = [
        {"schritt": s.get("schritt"), "werkzeug": s.get("werkzeug"),
         "sekunden": s.get("sek")}
        for s in (e.get("verlauf") or [])]
    if stufe == "zusammenfassung":
        return aus, entfernt

    aus["verlauf"] = [
        {"schritt": s.get("schritt"), "werkzeug": s.get("werkzeug"),
         "sekunden": s.get("sek"),
         "gedanke": _text(s.get("gedanke") or "", arbeitsordner, entfernt),
         "ergebnis": _text(s.get("ergebnis") or "", arbeitsordner, entfernt)}
        for s in (e.get("verlauf") or [])]
    return aus, entfernt


# --------------------------------------------------------- Ausgangsbuch ---

def buch_pfad(speicher):
    return os.path.join(speicher, "ausgangsbuch.jsonl")


def eintragen(speicher, empfaenger, pfad, stufe, zeichen, entfernt=(), auftrag=""):
    """Eine Zeile ins Ausgangsbuch — anfügen, nie ändern.

    Das Buch ist der Beweis: Was hier nicht steht, ist nicht hinausgegangen.
    Es enthält absichtlich keine Inhalte, nur Umfang und Art — ein Protokoll,
    das selbst zur Datenspur wird, wäre das Gegenteil des Zwecks."""
    eintrag = {"zeit": time.time(), "empfaenger": (empfaenger or "?")[:60],
               "pfad": (pfad or "")[:120], "stufe": stufe,
               "zeichen": int(zeichen or 0), "entfernt": list(entfernt),
               "auftrag": (auftrag or "")[:40]}
    try:
        os.makedirs(speicher, exist_ok=True)
        with open(buch_pfad(speicher), "a", encoding="utf-8") as f:
            f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
        if os.path.getsize(buch_pfad(speicher)) > BUCH_GRENZE and time.time() - _archiv_zuletzt.get(speicher, 0) > 3600:
            # höchstens einmal je Stunde — sonst läse bei Dauerlast jeder Eintrag das ganze Buch
            _archiv_zuletzt[speicher] = time.time()
            archivieren(speicher)
    except OSError:
        pass                     # ein blindes Buch darf den Betrieb nicht stoppen
    return eintrag


BUCH_GRENZE = 5 * 1024 * 1024        # darüber wandert Altes ins Archiv
_archiv_zuletzt = {}
BUCH_BEHALTEN_TAGE = 30


def archivieren(speicher, jetzt=None):
    """Einträge älter als BUCH_BEHALTEN_TAGE ins Archiv daneben (ausgangsbuch-archiv.jsonl) — nichts wird gelöscht.

    Stresstest 08.10.2026: Seit auch Websuche und Seitenabrufe im Buch stehen, wächst es schnell; 200 000 Zeilen
    (29 MB) kosteten jede Tagessumme 0,3 s, und die las das Dashboard bei jedem Aufruf."""
    grenze = (jetzt or time.time()) - BUCH_BEHALTEN_TAGE * 86400
    with _archiv_sperre:
        pfad = buch_pfad(speicher)
        try:
            with open(pfad, encoding="utf-8") as f:
                zeilen = f.readlines()
        except OSError:
            return 0
        alt, neu = [], []
        for z in zeilen:
            try:
                (alt if json.loads(z).get("zeit", 0) < grenze else neu).append(z)
            except ValueError:
                neu.append(z)
        if not alt:
            return 0
        with open(os.path.join(speicher, "ausgangsbuch-archiv.jsonl"), "a", encoding="utf-8") as f:
            f.writelines(alt)
        tmp = pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(neu)
        os.replace(tmp, pfad)
        return len(alt)


def buch_lesen(speicher, anzahl=50):
    """Die letzten Einträge, neueste zuerst."""
    pfad = buch_pfad(speicher)
    if not os.path.isfile(pfad):
        return []
    zeilen = []
    try:
        with open(pfad, encoding="utf-8") as f:
            for z in f:
                z = z.strip()
                if not z:
                    continue
                try:
                    zeilen.append(json.loads(z))
                except ValueError:
                    continue
    except OSError:
        return []
    return list(reversed(zeilen))[:max(1, anzahl)]


def summe(speicher, seit=None):
    """Was seit `seit` (Standard: heute 0 Uhr) hinausgegangen ist."""
    if seit is None:
        t = time.localtime()
        seit = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    antworten, zeichen, empfaenger, entfernt = 0, 0, {}, {}
    for e in buch_lesen(speicher, 100000):
        if e.get("zeit", 0) < seit:
            continue
        antworten += 1
        zeichen += int(e.get("zeichen") or 0)
        name = e.get("empfaenger") or "?"
        empfaenger[name] = empfaenger.get(name, 0) + 1
        for w in e.get("entfernt") or []:
            entfernt[w] = entfernt.get(w, 0) + 1
    return {"antworten": antworten, "zeichen": zeichen, "seit": seit,
            "empfaenger": empfaenger, "entfernt": entfernt}


def summe_text(s):
    """Eine Zeile für Lagebild und Kommandozeile."""
    if not s["antworten"]:
        return "Heute ist nichts nach draußen gegangen."
    menge = ("%.1f kB" % (s["zeichen"] / 1024.0)) if s["zeichen"] >= 1024 \
        else "%d Zeichen" % s["zeichen"]
    wer = ", ".join("%s (%d)" % (k, v) for k, v in
                    sorted(s["empfaenger"].items(), key=lambda kv: -kv[1])[:3])
    text = "Heute %d %s, %s nach draußen — %s" % (
        s["antworten"], "Eintrag" if s["antworten"] == 1 else "Einträge", menge, wer)
    if s["entfernt"]:
        text += " · entfernt: " + ", ".join(
            "%s (%d)" % (k, v) for k, v in sorted(s["entfernt"].items()))
    return text


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    speicher = os.environ.get("DOWOS_STORAGE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "storage")
    befehl = argv[0] if argv else "summe"
    if befehl == "buch":
        anzahl = int(argv[1]) if len(argv) > 1 else 20
        eintraege = buch_lesen(speicher, anzahl)
        if not eintraege:
            print("Das Ausgangsbuch ist leer — es ist nichts hinausgegangen.")
            return 0
        for e in eintraege:
            print("%s  %-16s %-14s %-24s %6d Zeichen%s" % (
                time.strftime("%d.%m. %H:%M", time.localtime(e.get("zeit", 0))),
                (e.get("empfaenger") or "?")[:16], e.get("stufe", ""),
                (e.get("pfad") or "")[:24], e.get("zeichen", 0),
                ("  [%s]" % ", ".join(e.get("entfernt") or []))
                if e.get("entfernt") else ""))
        return 0
    if befehl == "summe":
        print(summe_text(summe(speicher)))
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------------------
# Jede HTTP-Anfrage an einen fremden Rechner kommt ins Buch (07.10.2026)
# ---------------------------------------------------------------------------
# README und FAQ versprachen: „Jede Anfrage nach draußen (Websuche, Cloud-Anbieter, fremde Agenten) steht im
# Ausgangsbuch.“ Eingetragen wurden aber nur Antworten an fremde Agenten. Jetzt sitzt ein Protokoll-Griff im
# globalen urllib-Öffner: Was über urllib hinausgeht — Websuche, Seitenabruf, Cloud-Modell, Messenger, Modell-
# Download über die Registry — steht im Buch. Nur Empfänger, Art und Umfang; keine Inhalte, keine Suchbegriffe.
# Gleiches (Empfänger, Art) innerhalb einer Minute wird zu einer Zeile zusammengefasst, damit ein Bot, der alle paar
# Sekunden nachfragt, das Buch nicht flutet.

import ipaddress
import threading
import urllib.parse
import urllib.request

LOKAL = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
_sammel = {}
_sammel_sperre = threading.Lock()
_archiv_sperre = threading.Lock()
BUENDEL_S = 60


def ist_lokal(host):
    host = (host or "").strip("[]").lower()
    if host in LOKAL or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def art_von(host, pfad):
    h, p = (host or "").lower(), (pfad or "").lower()
    if any(x in h for x in ("discord", "telegram", "slack")):
        return "messenger"
    if "/chat/completions" in p or "/v1/messages" in p or "/api/chat" in p or "/api/generate" in p:
        return "modell"
    if any(x in h for x in ("registry.ollama", "huggingface", "hf.co")):
        return "download"
    return "web"


def vermerken(speicher, url, zeichen=0, jetzt=None):
    """Eine Anfrage nach draußen vermerken — gebündelt je Empfänger, Art und Minute."""
    t = urllib.parse.urlsplit(url)
    host = t.hostname or ""
    if ist_lokal(host):
        return None
    art = art_von(host, t.path)
    jetzt = jetzt or time.time()
    schluessel = (host, art)
    with _sammel_sperre:
        alt = _sammel.get(schluessel)
        if alt and jetzt - alt["start"] < BUENDEL_S:
            alt["anzahl"] += 1
            alt["zeichen"] += int(zeichen or 0)
            return None
        if alt:
            _buch_schreiben(speicher, schluessel, alt)
        _sammel[schluessel] = {"start": jetzt, "anzahl": 1, "zeichen": int(zeichen or 0), "pfad": t.path}
    return True


def _buch_schreiben(speicher, schluessel, s):
    host, art = schluessel
    eintragen(speicher, host, s["pfad"] if s["anzahl"] == 1 else "%s (%d Anfragen)" % (s["pfad"], s["anzahl"]),
              art, s["zeichen"])


def ausschuetten(speicher, alles=False, jetzt=None):
    """Gebündelte Zeilen ins Buch schreiben — abgelaufene, oder mit alles=True sofort alle."""
    jetzt = jetzt or time.time()
    with _sammel_sperre:
        for k in list(_sammel):
            if alles or jetzt - _sammel[k]["start"] >= BUENDEL_S:
                _buch_schreiben(speicher, k, _sammel.pop(k))


class Protokoll(urllib.request.BaseHandler):
    """Sieht jede Anfrage, die durch einen urllib-Öffner geht, bevor sie hinausgeht."""
    handler_order = 1          # vor allen anderen Griffen

    def __init__(self, speicher):
        self.speicher = speicher

    def _vermerken(self, req):
        try:
            daten = req.data if isinstance(req.data, (bytes, bytearray)) else b""
            vermerken(self.speicher, req.full_url, len(daten))
        except Exception:
            pass                 # das Buch darf nie eine Anfrage verhindern
        return req

    http_request = https_request = _vermerken


def einschalten(speicher):
    """Den Griff in den globalen Öffner setzen — einmal beim Start."""
    urllib.request.install_opener(urllib.request.build_opener(Protokoll(speicher)))
    return Protokoll(speicher)
