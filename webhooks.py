# -*- coding: utf-8 -*-
"""Webhooks — Werkbank-Aufträge von außen starten (GitHub, Gitea, CI, eigene Skripte).

Ein Webhook ist eine Adresse `/api/hooks/<id>` mit einem eigenen Geheimnis.
Wer dorthin schickt, muss den Rumpf mit HMAC-SHA256 signieren — genau so, wie
GitHub es mit `X-Hub-Signature-256` tut:

    signatur = "sha256=" + hmac_sha256(geheimnis, rumpf).hexdigest()
    curl -X POST https://…/api/hooks/<id> -H "X-Dowos-Signature: $signatur" -d '{"auftrag": "…"}'

Aus dem JSON-Rumpf und einer Vorlage entsteht der Auftrag: `{auftrag}` oder für
GitHub-Issues `Behebe: {issue.title}\\n\\n{issue.body}`.

Was schützt:
- ohne gültige Signatur passiert nichts (Vergleich in konstanter Zeit),
- dieselbe Zustellung (`X-GitHub-Delivery`) startet nur einmal,
- der Lauf ist unbeaufsichtigt: Was eine Freigabe bräuchte, wird abgelehnt,
- Rechtestufe je Webhook, **Standard „nur lesen“** — der Auftragstext kommt von
  außen und kann Anweisungen enthalten, die nicht vom Besitzer stammen.
"""

import hashlib
import hmac
import os
import json
import re
import secrets
import time

STUFEN = ("lesen", "projekt")            # „voll“ gibt es für Aufträge von außen bewusst nicht
PLATZHALTER_RE = re.compile(r"\{([A-Za-z0-9_.\-]+)\}")
MAX_FELD = 8000


def neu(daten):
    name = " ".join(str(daten.get("name") or "").split())[:60]
    if not name:
        raise ValueError("Der Webhook braucht einen Namen.")
    projekt = str(daten.get("projekt") or "").strip()
    if not projekt:
        raise ValueError("Welches Projekt? Workspace-Name oder voller Pfad.")
    stufe = daten.get("stufe") or "lesen"
    if stufe not in STUFEN:
        raise ValueError("Rechte: „lesen“ oder „projekt“ — voller Zugriff ist für Aufträge von außen nicht möglich.")
    vorlage = str(daten.get("vorlage") or "{auftrag}").strip()[:4000]
    if not PLATZHALTER_RE.search(vorlage):
        raise ValueError("Die Vorlage braucht mindestens einen Platzhalter, z. B. {auftrag} oder {issue.title}.")
    ereignisse = [e.strip() for e in str(daten.get("ereignisse") or "").split(",") if e.strip()]
    return {"id": secrets.token_hex(8), "geheimnis": secrets.token_urlsafe(32), "name": name, "projekt": projekt,
            "stufe": stufe, "profil": str(daten.get("profil") or "").strip(), "vorlage": vorlage,
            "ereignisse": ereignisse, "zustellen": str(daten.get("zustellen") or ""), "aktiv": True,
            "angelegt": time.time(), "letzter_aufruf": 0, "letzter_lauf": ""}


def signatur_pruefen(geheimnis, rumpf, kopf):
    """kopf: „sha256=<hex>“ (X-Dowos-Signature oder X-Hub-Signature-256)."""
    if not geheimnis or not kopf or not str(kopf).startswith("sha256="):
        return False
    erwartet = "sha256=" + hmac.new(geheimnis.encode(), rumpf, hashlib.sha256).hexdigest()
    return hmac.compare_digest(erwartet, str(kopf).strip())


def signieren(geheimnis, rumpf):
    return "sha256=" + hmac.new(geheimnis.encode(), rumpf, hashlib.sha256).hexdigest()


def feld(daten, pfad):
    wert = daten
    for teil in pfad.split("."):
        if isinstance(wert, dict):
            wert = wert.get(teil)
        elif isinstance(wert, list) and teil.isdigit() and int(teil) < len(wert):
            wert = wert[int(teil)]
        else:
            return ""
    if wert is None:
        return ""
    if isinstance(wert, (dict, list)):
        return ""
    return str(wert)[:MAX_FELD]


def auftrag_bauen(vorlage, daten):
    text = PLATZHALTER_RE.sub(lambda m: feld(daten, m.group(1)), vorlage).strip()
    if not text or text == PLATZHALTER_RE.sub("", vorlage).strip():
        raise ValueError("Aus dem Rumpf ergibt sich kein Auftrag (Platzhalter leer).")
    return text


def oeffentlich(hook):
    """Für die Oberfläche: ohne Geheimnis."""
    return {k: v for k, v in hook.items() if k != "geheimnis"}


class Zustellungen:
    """Merkt sich, was schon zugestellt wurde, damit nichts doppelt startet.

    Zwei Schluessel, und der zweite ist der entscheidende:

    * die Zustellungs-Kennung (X-GitHub-Delivery / X-Dowos-Delivery) — erkennt
      eine ehrliche Wiederholung des Absenders;
    * die SIGNATUR — erkennt ein Wiedereinspielen durch Dritte.

    Bis zum 25.09.2026 gab es nur den ersten. Die Kennung steht aber nicht unter
    der Signatur, die nur den Rumpf abdeckt: Wer eine gueltige Anfrage
    mitschnitt, schickte Rumpf und Signatur unveraendert und liess die Kennung
    weg oder erfand eine neue — und derselbe Auftrag startete erneut, beliebig
    oft (nachgewiesen: Status 202, der Werkbank-Lauf lief). Die Signatur ist per
    HMAC an den Rumpf gebunden; aendern laesst sie sich nur, indem man sie
    ungueltig macht.

    Und die Liste liegt auf der Platte. Im Arbeitsspeicher war sie nach jedem
    Neustart leer — ein Wiedereinspielen musste nur einen Neustart abwarten.

    Folge fuer ehrliche Absender: Derselbe Rumpf zweimal innerhalb von 24 Stunden
    gilt als doppelt. Wer bewusst dasselbe noch einmal ausloesen will, nimmt ein
    wechselndes Feld in den Rumpf auf, etwa einen Zeitstempel — der steht dann
    auch unter der Signatur."""

    FRIST = 24 * 3600

    def __init__(self, grenze=5000, pfad=None):
        self.grenze, self.pfad = grenze, pfad
        self.gesehen = {}                # Schluessel -> Zeitpunkt
        if pfad and os.path.isfile(pfad):
            try:
                with open(pfad, encoding="utf-8") as f:
                    self.gesehen = {k: float(v) for k, v in json.load(f).items()}
            except (OSError, ValueError, AttributeError):
                self.gesehen = {}

    def _aufraeumen(self, jetzt):
        self.gesehen = {k: t for k, t in self.gesehen.items() if jetzt - t < self.FRIST}
        if len(self.gesehen) > self.grenze:
            behalten = sorted(self.gesehen.items(), key=lambda kv: kv[1])[-self.grenze:]
            self.gesehen = dict(behalten)

    def _sichern(self):
        if not self.pfad:
            return
        zwischen = self.pfad + ".neu"
        try:
            with open(zwischen, "w", encoding="utf-8") as f:
                json.dump(self.gesehen, f)
            os.replace(zwischen, self.pfad)
        except OSError:
            pass

    def neu(self, kennung, signatur=None):
        jetzt = time.time()
        self._aufraeumen(jetzt)
        schluessel = []
        if kennung:
            schluessel.append("id:" + str(kennung)[:200])
        if signatur:
            schluessel.append("sig:" + hashlib.sha256(str(signatur).encode()).hexdigest())
        if any(k in self.gesehen for k in schluessel):
            return False
        for k in schluessel:
            self.gesehen[k] = jetzt
        if schluessel:
            self._sichern()
        return True
        if kennung in self.gesehen:
            return False
        self.gesehen = (self.gesehen + [kennung])[-self.grenze:]
        return True
