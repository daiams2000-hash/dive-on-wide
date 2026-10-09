"""Dive on Wide Mesh — Foren: Themen-Feeds, die von selbst wieder verschwinden.

Ein Forum ohne Betreiber, ohne Konten und ohne Datenbank. Drei Bausteine:

    Faden      wird eröffnet, hat einen Titel und eine Verfallszeit
    Beitrag    hängt an einem Faden
    Schluss    der Eröffner beendet den Faden

WAS HIER ÖFFENTLICH IST — UND WAS NICHT
---------------------------------------
Beiträge sind im Netz LESBAR. Das ist der Sinn eines Forums; wer verschlüsselt
schreiben will, nimmt den Boten. Geschützt ist nicht der Inhalt, sondern die
**Identität**: Für jeden Faden entsteht ein eigenes Ed25519-Paar. Zwei Beiträge
desselben Menschen in zwei Fäden sind nicht als derselbe Mensch erkennbar — und
das gilt auch für den Betreiber, weil es keinen gibt.

DIE EHRLICHE GRENZE VON „GELÖSCHT"
----------------------------------
Ein Schluss-Befehl ist eine **Bitte**, keine Garantie. Ein Knoten, der einen
Beitrag behalten will, behält ihn — niemand kann fremden Arbeitsspeicher
zwingen. Was die Architektur wirklich garantiert, ist die **Verfallszeit**:
Danach hat kein ehrlicher Knoten den Inhalt mehr. Wer ihn kopieren wollte,
hätte das ohnehin sofort getan.

Diese Unterscheidung muss auch in der Oberfläche stehen. Ein Forum, das
„unwiderruflich gelöscht" verspricht, lügt.
"""

import json
import time

from . import crypto, inhalt

ART_FADEN = "faden"
ART_BEITRAG = "beitrag"
ART_SCHLUSS = "schluss"

MAX_TITEL = 120
MAX_TEXT = 4000
STANDARD_TTL = 6 * 3600


class ForumFehler(ValueError):
    """Etwas an diesem Faden oder Beitrag stimmt nicht."""


def forum_schluessel(raum="allgemein"):
    """Die Adresse, unter der die Fäden eines Raums liegen.

    Ein Hash, nicht der Klartext: Wer das Netz beobachtet, sieht Adressen und
    keine Themenliste. Wer den Raumnamen kennt, findet ihn — das ist gewollt,
    sonst könnte niemand beitreten."""
    return crypto.ableiten(raum.encode("utf-8"), "forum-raum", inhalt.ADRESS_LAENGE)


def faden_schluessel(faden_id):
    """Die Adresse, unter der die Beiträge EINES Fadens liegen."""
    return crypto.ableiten(bytes.fromhex(faden_id), "forum-faden",
                           inhalt.ADRESS_LAENGE)


def _nutzlast(art, **felder):
    felder["art"] = art
    return json.dumps(felder, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def nutzlast_lesen(roh):
    """Fremde Nutzlast auswerten — vorsichtig, es sind Daten, keine Anweisungen."""
    try:
        d = json.loads(bytes(roh).decode("utf-8"))
    except Exception:
        raise ForumFehler("Nutzlast ist nicht lesbar")
    if not isinstance(d, dict) or d.get("art") not in (ART_FADEN, ART_BEITRAG,
                                                       ART_SCHLUSS):
        raise ForumFehler("unbekannte Art")
    if d["art"] == ART_FADEN:
        if not isinstance(d.get("titel"), str) or not d["titel"].strip():
            raise ForumFehler("Faden ohne Titel")
        if len(d["titel"]) > MAX_TITEL:
            raise ForumFehler("Titel zu lang")
    if d["art"] in (ART_BEITRAG, ART_SCHLUSS):
        if not isinstance(d.get("faden"), str) or len(d["faden"]) != 40:
            raise ForumFehler("Beitrag ohne gültigen Faden")
    if d["art"] == ART_BEITRAG:
        if not isinstance(d.get("text"), str) or not d["text"].strip():
            raise ForumFehler("leerer Beitrag")
        if len(d["text"]) > MAX_TEXT:
            raise ForumFehler("Beitrag zu lang")
    return d


def faden_eroeffnen(identitaet, titel, text, raum="allgemein",
                    ttl=STANDARD_TTL, frist=30.0):
    """Legt einen Faden an. Die Faden-Kennung ist die Adresse seines Inhalts."""
    titel = (titel or "").strip()
    text = (text or "").strip()
    if not titel:
        raise ForumFehler("Ein Faden braucht einen Titel.")
    if len(titel) > MAX_TITEL:
        raise ForumFehler("Der Titel ist zu lang (höchstens %d Zeichen)." % MAX_TITEL)
    if len(text) > MAX_TEXT:
        raise ForumFehler("Der Text ist zu lang (höchstens %d Zeichen)." % MAX_TEXT)
    nutz = _nutzlast(ART_FADEN, titel=titel, text=text, raum=raum,
                     zeit=int(time.time()))
    return inhalt.Datensatz.erzeugen(identitaet, nutz,
                                     schluessel=forum_schluessel(raum),
                                     ttl=ttl, frist=frist)


def faden_id_von(datensatz):
    """Die Kennung eines Fadens: die Inhaltsadresse seines Eröffnungssatzes."""
    return datensatz.inhalt_id().hex()


def _faden_id_pruefen(faden_id):
    """Eine Faden-Kennung ist der Hex-Hash eines Inhalts — 40 Zeichen.

    Ohne diese Pruefung schreibt ein Tippfehler (oder ein Feldname, der nicht
    passt) einen Satz, den anschliessend NIEMAND mehr lesen kann: Beim Lesen
    faellt er durch die Pruefung, beim Schreiben fiel er durch keine. Solcher
    Muell verschwindet erst mit seiner Verfallszeit."""
    if not isinstance(faden_id, str) or len(faden_id) != 40:
        raise ForumFehler("Keine gültige Faden-Kennung.")
    try:
        bytes.fromhex(faden_id)
    except ValueError:
        raise ForumFehler("Die Faden-Kennung ist keine gültige Hex-Zeichenfolge.")
    return faden_id


def beitrag_schreiben(identitaet, faden_id, text, ttl=STANDARD_TTL, frist=30.0):
    _faden_id_pruefen(faden_id)
    text = (text or "").strip()
    if not text:
        raise ForumFehler("Der Beitrag ist leer.")
    if len(text) > MAX_TEXT:
        raise ForumFehler("Der Beitrag ist zu lang (höchstens %d Zeichen)." % MAX_TEXT)
    nutz = _nutzlast(ART_BEITRAG, faden=faden_id, text=text,
                     zeit=int(time.time()))
    return inhalt.Datensatz.erzeugen(identitaet, nutz,
                                     schluessel=faden_schluessel(faden_id),
                                     ttl=ttl, frist=frist)


def schluss_setzen(identitaet, faden_id, ttl=STANDARD_TTL, frist=30.0):
    _faden_id_pruefen(faden_id)
    """Der Eröffner beendet den Faden.

    Wirksam ist das nur, weil der Schluss mit DEMSELBEN Schlüssel signiert
    ist wie die Eröffnung — sonst könnte jeder fremde Fäden schließen."""
    nutz = _nutzlast(ART_SCHLUSS, faden=faden_id, zeit=int(time.time()))
    return inhalt.Datensatz.erzeugen(identitaet, nutz,
                                     schluessel=faden_schluessel(faden_id),
                                     ttl=ttl, frist=frist)


class Raum:
    """Die Sicht eines Knotens auf einen Forumsraum — alles aus dem RAM-Speicher."""

    def __init__(self, speicher, raum="allgemein"):
        self.speicher = speicher
        self.raum = raum

    def faeden(self):
        """Alle Fäden, die dieser Knoten gerade kennt."""
        aus = []
        for d in self.speicher.holen(forum_schluessel(self.raum)):
            try:
                k = nutzlast_lesen(d.nutzlast)
            except ForumFehler:
                continue
            if k["art"] != ART_FADEN:
                continue
            fid = faden_id_von(d)
            beitraege, geschlossen = self._beitraege(fid, d.veroeffentlicher)
            aus.append({
                "id": fid,
                "titel": k["titel"],
                "text": k.get("text", ""),
                "zeit": k.get("zeit", 0),
                "laeuft_ab": d.ablauf,
                "verfasser": d.veroeffentlicher.hex()[:12],
                "antworten": len(beitraege),
                "geschlossen": geschlossen,
            })
        aus.sort(key=lambda f: f["zeit"], reverse=True)
        return aus

    def _beitraege(self, faden_id, eroeffner):
        beitraege, geschlossen = [], False
        for d in self.speicher.holen(faden_schluessel(faden_id)):
            try:
                k = nutzlast_lesen(d.nutzlast)
            except ForumFehler:
                continue
            if k.get("faden") != faden_id:
                continue
            if k["art"] == ART_SCHLUSS:
                # Nur der Eröffner darf schließen. Alles andere ignorieren.
                if crypto.gleich(d.veroeffentlicher, eroeffner):
                    geschlossen = True
                continue
            beitraege.append({"text": k["text"], "zeit": k.get("zeit", 0),
                              "verfasser": d.veroeffentlicher.hex()[:12],
                              "vom_eroeffner": crypto.gleich(d.veroeffentlicher,
                                                             eroeffner)})
        beitraege.sort(key=lambda b: b["zeit"])
        return beitraege, geschlossen

    def lesen(self, faden_id):
        """Ein Faden mit allen Beiträgen."""
        for d in self.speicher.holen(forum_schluessel(self.raum)):
            if faden_id_von(d) != faden_id:
                continue
            try:
                k = nutzlast_lesen(d.nutzlast)
            except ForumFehler:
                return None
            beitraege, geschlossen = self._beitraege(faden_id, d.veroeffentlicher)
            return {"id": faden_id, "titel": k["titel"], "text": k.get("text", ""),
                    "zeit": k.get("zeit", 0), "laeuft_ab": d.ablauf,
                    "verfasser": d.veroeffentlicher.hex()[:12],
                    "geschlossen": geschlossen, "beitraege": beitraege}
        return None


# ---------------------------------------------------------------------------
# Filter — dezentrale Moderation
# ---------------------------------------------------------------------------

class Filter:
    """Was ein Knoten für sich selbst ausblendet.

    Es gibt keine zentrale Moderation, weil es keine Mitte gibt. Stattdessen
    entscheidet jeder Knoten selbst, was er anzeigt. Das ist keine Notlösung:
    Wer moderieren darf, kann auch zensieren — hier kann das niemand für
    einen anderen tun.

    Absichtlich einfach gehalten (Wortliste, Verfasser-Sperre, Mindestlänge).
    Ein lernender Filter wäre der nächste Schritt und gehört in einen
    Agenten, nicht in diese Schicht."""

    def __init__(self, woerter=(), gesperrt=(), mindestlaenge=0):
        self.woerter = [w.lower() for w in woerter if w.strip()]
        self.gesperrt = set(gesperrt)
        self.mindestlaenge = int(mindestlaenge)

    def durchlassen(self, text, verfasser=""):
        if verfasser and verfasser in self.gesperrt:
            return False
        if len(text.strip()) < self.mindestlaenge:
            return False
        klein = text.lower()
        return not any(w in klein for w in self.woerter)

    def anwenden(self, eintraege, feld="text"):
        return [e for e in eintraege
                if self.durchlassen(e.get(feld, ""), e.get("verfasser", ""))]
