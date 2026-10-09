"""Dive on Wide Mesh — die Doppelratsche: jede Nachricht ihr eigener Schlüssel.

WAS VORHER FEHLTE
-----------------
Zwei Geräte einigten sich einmal auf einen Sitzungsschlüssel und benutzten
ihn für alles. Wer diesen einen Schlüssel bekommt, liest damit den gesamten
Verlauf. Die SECURITY.md hat das ausdrücklich als Lücke benannt.

Die Doppelratsche dreht den Schlüssel weiter:

  * Nach JEDER Nachricht — die symmetrische Ratsche. Ein erbeuteter
    Nachrichtenschlüssel öffnet genau diese eine Nachricht.
  * Bei JEDER Antwort der Gegenseite mit einem frischen X25519-Schlüssel —
    die Diffie-Hellman-Ratsche. Damit heilt sich das Gespräch selbst: Wer
    einmal alles mitgelesen hat, verliert den Zugriff wieder, sobald ein
    frischer Schlüssel dazukommt.

DER PUNKT, AN DEM DIE MEISTEN UMSETZUNGEN BRECHEN
-------------------------------------------------
Der Transport hier ist UDP. Pakete kommen verloren, doppelt oder in falscher
Reihenfolge an. Eine Ratsche ohne Vorsorge dafür wäre nach dem ersten
verlorenen Paket dauerhaft kaputt — die Kette wäre nicht mehr aufholbar.

Deshalb werden übersprungene Nachrichtenschlüssel aufbewahrt (begrenzt, sonst
wäre das ein Angriffspunkt): Kommt Nachricht 5 vor Nachricht 3, werden die
Schlüssel für 3 und 4 abgelegt und später verwendet. Das ist kein Beiwerk,
sondern die Bedingung dafür, dass die Ratsche über UDP überhaupt taugt.
"""

import hashlib
import struct

from . import crypto

KOPF_LAENGE = 32 + 4 + 4          # DH-Schlüssel | vorige Kettenlänge | Nummer
MAX_UEBERSPRUNGEN = 256           # so viele Lücken werden überbrückt
MAX_SPRUNG = 512                  # weiter als das ist kein Verlust, sondern Unsinn


class RatschenFehler(Exception):
    """Die Nachricht passt nicht zu diesem Gespräch."""


def _kdf_wurzel(wurzel, dh_ausgabe):
    """Wurzelschlüssel weiterdrehen. Gibt (neue Wurzel, neue Kette)."""
    roh = hashlib.blake2b(bytes(dh_ausgabe), key=bytes(wurzel), digest_size=64,
                          person=b"dowos-ratsche-rt").digest()
    return roh[:32], roh[32:]


def _kdf_kette(kette):
    """Kettenschlüssel weiterdrehen. Gibt (neue Kette, Nachrichtenschlüssel).

    Zwei verschiedene Personalisierungen, damit aus dem Nachrichtenschlüssel
    niemals auf die weitere Kette geschlossen werden kann."""
    neu = hashlib.blake2b(b"\x02", key=bytes(kette), digest_size=32,
                          person=b"dowos-ratsche-ck").digest()
    nachricht = hashlib.blake2b(b"\x01", key=bytes(kette), digest_size=32,
                                person=b"dowos-ratsche-mk").digest()
    return neu, nachricht


def kopf_bauen(dh_pub, vorige, nummer):
    return bytes(dh_pub) + struct.pack("<II", vorige, nummer)


def kopf_lesen(roh):
    if len(roh) < KOPF_LAENGE:
        raise RatschenFehler("Kopf ist zu kurz")
    dh_pub = roh[:32]
    vorige, nummer = struct.unpack("<II", roh[32:KOPF_LAENGE])
    return dh_pub, vorige, nummer


class Ratsche:
    """Ein Gespräch zwischen genau zwei Seiten.

    Eine Seite beginnt (`starten`), die andere antwortet auf die erste
    Nachricht (`empfangend`). Beide brauchen dasselbe gemeinsame Geheimnis —
    das liefert der Kontaktanker oder ein X25519-Handschlag."""

    __slots__ = ("_wurzel", "_dh_geheim", "dh_oeffentlich", "_fremd_dh",
                 "_kette_senden", "_kette_empfangen", "_n_senden",
                 "_n_empfangen", "_vorige", "_uebersprungen", "_tot")

    def __init__(self):
        self._wurzel = b"\x00" * 32
        self._dh_geheim = None
        self.dh_oeffentlich = None
        self._fremd_dh = None
        self._kette_senden = None
        self._kette_empfangen = None
        self._n_senden = 0
        self._n_empfangen = 0
        self._vorige = 0
        self._uebersprungen = {}
        self._tot = False

    # -- Aufbau ------------------------------------------------------------
    @classmethod
    def starten(cls, geheimnis, fremd_dh_pub):
        """Die Seite, die zuerst schreibt. Kennt den Schlüssel der Gegenseite."""
        r = cls()
        r._wurzel = crypto.ableiten(geheimnis, "ratsche-wurzel")
        r._dh_geheim, r.dh_oeffentlich = crypto.x25519_schluesselpaar()
        r._fremd_dh = bytes(fremd_dh_pub)
        r._wurzel, r._kette_senden = _kdf_wurzel(
            r._wurzel, crypto.x25519(r._dh_geheim, r._fremd_dh))
        return r

    @classmethod
    def empfangend(cls, geheimnis, eigen_geheim, eigen_oeffentlich):
        """Die Seite, die wartet. Ihr Schlüsselpaar ist der Gegenseite bekannt."""
        r = cls()
        r._wurzel = crypto.ableiten(geheimnis, "ratsche-wurzel")
        r._dh_geheim = bytes(eigen_geheim)
        r.dh_oeffentlich = bytes(eigen_oeffentlich)
        return r

    @classmethod
    def paar(cls, geheimnis, ich_bin_a, fremd_dh_pub, eigen_geheim=None,
             eigen_oeffentlich=None):
        """Beide Seiten können sofort schreiben — nicht nur eine.

        Ein Messenger, in dem eine Seite warten muss, bis die andere anfängt,
        ist kaputt: Wer zuerst etwas zu sagen hat, wird abgewiesen.

        Deshalb bekommt jede RICHTUNG eine eigene Anfangskette, beide aus dem
        gemeinsamen Geheimnis, aber mit verschiedener Kennzeichnung. Damit
        benutzt niemals jemand denselben Schlüssel zweimal — das wäre der
        Fehler, den eine gemeinsame Kette machen würde.

        Sobald die erste Nachricht ankommt, übernimmt die normale
        Diffie-Hellman-Ratsche und beide Anfangsketten verschwinden."""
        r = cls()
        r._wurzel = crypto.ableiten(geheimnis, "ratsche-wurzel")
        r._fremd_dh = bytes(fremd_dh_pub)
        # Die Richtung B->A laeuft anfangs ueber eine aus dem Anker abgeleitete
        # Kette. Damit kann B sofort schreiben, ohne auf A zu warten.
        anfang_b_a = crypto.ableiten(geheimnis, "ratsche-b-a")
        if ich_bin_a:
            # A schreibt mit einem FRISCHEN Schluessel und dreht die
            # Diffie-Hellman-Ratsche sofort. Genau diese Kette rechnet B nach,
            # sobald der frische Schluessel im Kopf der ersten Nachricht
            # ankommt — deshalb passt beides zusammen.
            r._dh_geheim, r.dh_oeffentlich = crypto.x25519_schluesselpaar()
            r._wurzel, r._kette_senden = _kdf_wurzel(
                r._wurzel, crypto.x25519(r._dh_geheim, r._fremd_dh))
            r._kette_empfangen = anfang_b_a
        else:
            # B benutzt sein bekanntes Paar. Die Empfangskette entsteht erst,
            # wenn A das erste Mal schreibt — die Ratsche setzt sie dann so
            # auf, dass sie A's Sendekette trifft.
            r._dh_geheim = bytes(eigen_geheim)
            r.dh_oeffentlich = bytes(eigen_oeffentlich)
            r._kette_senden = anfang_b_a
            r._kette_empfangen = None
        return r

    # -- Senden ------------------------------------------------------------
    def senden(self, klartext, aad=b""):
        self._pruefen_lebt()
        if self._kette_senden is None:
            raise RatschenFehler(
                "Diese Seite hat noch nichts empfangen und kann deshalb noch "
                "nicht senden — die Gegenseite beginnt.")
        self._kette_senden, nachrichtenschluessel = _kdf_kette(self._kette_senden)
        kopf = kopf_bauen(self.dh_oeffentlich, self._vorige, self._n_senden)
        self._n_senden += 1
        paket = crypto.versiegeln(nachrichtenschluessel, klartext,
                                  aad=kopf + bytes(aad))
        return kopf + paket

    # -- Empfangen ---------------------------------------------------------
    def empfangen(self, paket, aad=b""):
        self._pruefen_lebt()
        kopf, rest = paket[:KOPF_LAENGE], paket[KOPF_LAENGE:]
        fremd_dh, vorige, nummer = kopf_lesen(kopf)

        # Zuerst nachsehen, ob dieser Schlüssel schon zurückgelegt wurde —
        # das ist der Fall einer nachgereichten, verspäteten Nachricht.
        merker = (bytes(fremd_dh), nummer)
        if merker in self._uebersprungen:
            schluessel = self._uebersprungen.pop(merker)
            return crypto.entsiegeln(schluessel, rest, aad=kopf + bytes(aad))

        if self._kette_empfangen is None and crypto.gleich(fremd_dh, self._fremd_dh):
            # Sonderfall: B hat noch keine Empfangskette, aber der Absender
            # benutzt den bereits bekannten Schluessel. Kann nicht passen.
            raise RatschenFehler("Für diese Richtung gibt es noch keine Kette.")
        if self._fremd_dh is None or not crypto.gleich(fremd_dh, self._fremd_dh):
            # Die Gegenseite hat einen frischen Schlüssel geschickt: erst die
            # Lücken der ALTEN Kette schließen, dann weiterdrehen.
            self._luecken_schliessen(vorige)
            self._dh_ratsche(fremd_dh)
        self._luecken_schliessen(nummer)

        self._kette_empfangen, nachrichtenschluessel = _kdf_kette(self._kette_empfangen)
        self._n_empfangen += 1
        try:
            return crypto.entsiegeln(nachrichtenschluessel, rest,
                                     aad=kopf + bytes(aad))
        except crypto.EntschluesselungFehlgeschlagen:
            raise RatschenFehler(
                "Die Nachricht gehört nicht zu diesem Gespräch oder wurde "
                "verändert.")

    def _luecken_schliessen(self, bis):
        """Schlüssel für ausgelassene Nummern zurücklegen.

        Ohne das wäre die Ratsche nach dem ersten verlorenen UDP-Paket
        dauerhaft unbrauchbar."""
        if self._kette_empfangen is None:
            return
        if bis - self._n_empfangen > MAX_SPRUNG:
            raise RatschenFehler(
                "Sprung von %d Nachrichten — das ist kein Paketverlust mehr. "
                "Abgelehnt, damit niemand darüber Rechenzeit bindet."
                % (bis - self._n_empfangen))
        while self._n_empfangen < bis:
            self._kette_empfangen, schluessel = _kdf_kette(self._kette_empfangen)
            self._uebersprungen[(bytes(self._fremd_dh), self._n_empfangen)] = schluessel
            self._n_empfangen += 1
            if len(self._uebersprungen) > MAX_UEBERSPRUNGEN:
                # Den ältesten fallen lassen. Unbegrenzt aufzubewahren wäre
                # ein Angriffspunkt: Ein Fremder könnte uns mit erfundenen
                # Nummern den Speicher vollschreiben.
                self._uebersprungen.pop(next(iter(self._uebersprungen)))

    def _dh_ratsche(self, fremd_dh):
        """Frischer Schlüssel der Gegenseite: beide Ketten neu aufsetzen."""
        self._vorige = self._n_senden
        self._n_senden = 0
        self._n_empfangen = 0
        self._fremd_dh = bytes(fremd_dh)
        self._wurzel, self._kette_empfangen = _kdf_wurzel(
            self._wurzel, crypto.x25519(self._dh_geheim, self._fremd_dh))
        # Eigenes Paar erneuern — erst dadurch heilt sich das Gespräch.
        self._dh_geheim, self.dh_oeffentlich = crypto.x25519_schluesselpaar()
        self._wurzel, self._kette_senden = _kdf_wurzel(
            self._wurzel, crypto.x25519(self._dh_geheim, self._fremd_dh))

    # -- Ende --------------------------------------------------------------
    def _pruefen_lebt(self):
        if self._tot:
            raise RatschenFehler("Dieses Gespräch wurde beendet.")

    def vernichten(self):
        """Alle Schlüssel weg. Danach ist auch Aufbewahrtes nicht mehr lesbar."""
        self._wurzel = b"\x00" * 32
        self._dh_geheim = None
        self._kette_senden = None
        self._kette_empfangen = None
        self._uebersprungen.clear()
        self._tot = True

    @property
    def offene_luecken(self):
        return len(self._uebersprungen)

    def __repr__(self):
        return "<Ratsche gesendet=%d empfangen=%d Lücken=%d%s>" % (
            self._n_senden, self._n_empfangen, len(self._uebersprungen),
            ", beendet" if self._tot else "")
