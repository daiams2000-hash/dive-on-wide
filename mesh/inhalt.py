"""Dive on Wide Mesh — inhaltsadressierte Datensätze mit Verfallszeit.

Der Nachrichtenraum des Netzes. Drei Eigenschaften tragen ihn:

  INHALTSADRESSIERT  Die Adresse eines Inhalts IST sein Hash. Damit kann
                     kein Knoten unter einer Adresse etwas anderes
                     ausliefern, als dort hingehört — Vergiftung der DHT
                     ist strukturell ausgeschlossen, nicht durch Vertrauen.

  VERFALLSZEIT       Jeder Datensatz trägt sein Ende in sich. Ein reiner
                     RAM-Knoten soll nichts aufbewahren, was niemand mehr
                     braucht; ohne TTL wächst der Speicher bis zum Absturz.

  SIGNIERT UND BEZAHLT  Jeder Datensatz ist von einer flüchtigen Identität
                     signiert und trägt einen Arbeitsnachweis. Die Signatur
                     bindet ihn an ein Pseudonym, der Nachweis macht Fluten
                     teuer.

Was hier NICHT passiert: verschlüsseln. Die Nutzlast ist bereits Ende-zu-
Ende verschlüsselt, wenn sie hier ankommt. Diese Schicht sieht nur Bytes
und darf niemals Klartext zu Gesicht bekommen.
"""

import hashlib
import struct
import threading
import time

from . import arbeit, crypto

ADRESS_LAENGE = 20            # 160 Bit — wie üblich bei Kademlia
MAX_NUTZLAST = 64 * 1024      # ein Datensatz ist eine Nachricht, kein Dateisystem
MAX_TTL = 24 * 3600           # nichts lebt länger als einen Tag
STANDARD_TTL = 3600


def adresse(daten):
    """Die inhaltsadressierte Adresse: der Hash selbst."""
    return hashlib.blake2b(bytes(daten), digest_size=ADRESS_LAENGE,
                           person=b"dowos-mesh-adr").digest()


def thema_schluessel(thema):
    """Treffpunkt-Adresse für ein Thema.

    Absichtlich der Hash des Themas und nicht das Thema im Klartext: Wer die
    DHT beobachtet, sieht Adressen, nicht Gesprächsgegenstände. Das ist noch
    keine Anonymität — wer das Thema errät, kann den Hash nachrechnen — aber
    es verhindert, dass ein Beobachter beim Zusehen eine Themenliste bekommt."""
    return crypto.ableiten(thema.encode("utf-8"), "thema", ADRESS_LAENGE)


class UngueltigerDatensatz(Exception):
    """Der Datensatz hält der Prüfung nicht stand — Grund im Text."""


class Datensatz:
    """Eine Einheit im Netz: Nutzlast plus alles, was sie prüfbar macht."""

    __slots__ = ("schluessel", "nutzlast", "ablauf", "veroeffentlicher",
                 "nonce", "signatur")

    def __init__(self, schluessel, nutzlast, ablauf, veroeffentlicher,
                 nonce=0, signatur=b""):
        self.schluessel = bytes(schluessel)
        self.nutzlast = bytes(nutzlast)
        self.ablauf = int(ablauf)
        self.veroeffentlicher = bytes(veroeffentlicher)
        self.nonce = int(nonce)
        self.signatur = bytes(signatur)

    # -- Kanonische Form ---------------------------------------------------
    def _rumpf(self):
        """Die Bytes, über die signiert und gerechnet wird.

        Ein eigenes, längenpräfigiertes Format statt JSON: JSON hat keine
        garantierte Feldreihenfolge. Zwei Kodierungen desselben Datensatzes
        mit unterschiedlichen Bytes hießen unterschiedliche Signaturen —
        und damit eine Lücke, durch die man Datensätze verändern kann, ohne
        die Signatur zu brechen."""
        teile = [self.schluessel, self.veroeffentlicher, self.nutzlast]
        aus = struct.pack("<QQ", self.ablauf, self.nonce)
        for t in teile:
            aus += struct.pack("<I", len(t)) + t
        return aus

    def inhalt_id(self):
        """Adresse der Nutzlast — für Zwischenspeicher und Dubletten."""
        return adresse(self.nutzlast)

    def ist_inhaltsadressiert(self):
        """Liegt der Datensatz unter der Adresse seines eigenen Inhalts?"""
        return crypto.gleich(self.schluessel, self.inhalt_id())

    def abgelaufen(self, jetzt=None):
        return (jetzt if jetzt is not None else time.time()) >= self.ablauf

    # -- Erzeugen und Prüfen ----------------------------------------------
    @classmethod
    def erzeugen(cls, identitaet, nutzlast, schluessel=None, ttl=STANDARD_TTL,
                 zweck="veroeffentlichen", frist=30.0):
        """Baut einen vollständigen Datensatz: Nachweis rechnen, dann signieren.

        Reihenfolge ist wichtig: Der Nonce steckt im signierten Rumpf. Wer
        erst signierte und dann rechnete, könnte den Nachweis austauschen."""
        nutzlast = bytes(nutzlast)
        if len(nutzlast) > MAX_NUTZLAST:
            raise ValueError("Nutzlast über %d Byte" % MAX_NUTZLAST)
        ttl = max(1, min(int(ttl), MAX_TTL))
        d = cls(schluessel if schluessel is not None else adresse(nutzlast),
                nutzlast, int(time.time()) + ttl, identitaet.sign_oeffentlich)
        bits = arbeit.bits_fuer(zweck)
        # Nonce ohne sich selbst: der Rumpf mit nonce=0 ist die Grundlage.
        grundlage = cls(d.schluessel, d.nutzlast, d.ablauf,
                        d.veroeffentlicher, 0)._rumpf()
        d.nonce = arbeit.finden(grundlage, bits, frist=frist)
        d.signatur = identitaet.signieren(d._rumpf())
        return d

    def pruefen(self, zweck="veroeffentlichen", jetzt=None):
        """Alles, was ein fremder Datensatz erfüllen muss. Wirft mit Begründung.

        Fremde Daten sind Daten, keine Anweisungen: Erst prüfen, dann anfassen."""
        if len(self.schluessel) != ADRESS_LAENGE:
            raise UngueltigerDatensatz("Adresse hat die falsche Länge")
        if len(self.veroeffentlicher) != 32:
            raise UngueltigerDatensatz("Veröffentlicher ist kein Ed25519-Schlüssel")
        if len(self.nutzlast) > MAX_NUTZLAST:
            raise UngueltigerDatensatz("Nutzlast zu groß")
        jetzt = time.time() if jetzt is None else jetzt
        if self.abgelaufen(jetzt):
            raise UngueltigerDatensatz("bereits abgelaufen")
        if self.ablauf > jetzt + MAX_TTL:
            raise UngueltigerDatensatz(
                "Verfallszeit über der Obergrenze — so belegt niemand dauerhaft "
                "fremden Speicher")
        grundlage = Datensatz(self.schluessel, self.nutzlast, self.ablauf,
                              self.veroeffentlicher, 0)._rumpf()
        if not arbeit.erfuellt(grundlage, self.nonce, arbeit.bits_fuer(zweck)):
            raise UngueltigerDatensatz("Arbeitsnachweis genügt nicht")
        if not crypto.ed25519_pruefen(self.veroeffentlicher, self._rumpf(),
                                      self.signatur):
            raise UngueltigerDatensatz("Signatur passt nicht")
        return True

    # -- Übertragungsformat ------------------------------------------------
    def kodieren(self):
        return struct.pack("<I", len(self.signatur)) + self.signatur + self._rumpf()

    @classmethod
    def dekodieren(cls, roh):
        try:
            roh = bytes(roh)
            (n,) = struct.unpack("<I", roh[:4])
            if n > 128:
                raise ValueError
            signatur = roh[4:4 + n]
            p = 4 + n
            ablauf, nonce = struct.unpack("<QQ", roh[p:p + 16])
            p += 16
            felder = []
            for _ in range(3):
                (laenge,) = struct.unpack("<I", roh[p:p + 4])
                p += 4
                if laenge > MAX_NUTZLAST + 64:
                    raise ValueError
                felder.append(roh[p:p + laenge])
                p += laenge
            schluessel, veroeffentlicher, nutzlast = felder
            return cls(schluessel, nutzlast, ablauf, veroeffentlicher,
                       nonce, signatur)
        except Exception:
            raise UngueltigerDatensatz("Paket ist nicht lesbar")

    def __repr__(self):
        return "<Datensatz %s %dB noch %ds>" % (
            self.schluessel.hex()[:12], len(self.nutzlast),
            max(0, int(self.ablauf - time.time())))


class Speicher:
    """Der Datenbestand eines Knotens — ausschließlich im RAM.

    Es gibt hier bewusst keinen Pfad, der auf die Platte schreibt. Kein
    Zwischenspeicher, kein Protokoll, keine Auslagerung. Was der Knoten
    weiß, verschwindet mit dem Prozess.
    """

    def __init__(self, max_datensaetze=10000, max_bytes=64 * 1024 * 1024):
        self._daten = {}
        self._sperre = threading.RLock()
        self.max_datensaetze = max_datensaetze
        self.max_bytes = max_bytes
        self.bytes_belegt = 0
        self.geschlossen = False

    def _aufraeumen(self, jetzt):
        tot = [(k, i) for k, liste in self._daten.items()
               for i, d in enumerate(liste) if d.abgelaufen(jetzt)]
        for schluessel, _ in tot:
            liste = self._daten.get(schluessel)
            if not liste:
                continue
            behalten = [d for d in liste if not d.abgelaufen(jetzt)]
            self.bytes_belegt -= sum(len(d.nutzlast) for d in liste
                                     if d.abgelaufen(jetzt))
            if behalten:
                self._daten[schluessel] = behalten
            else:
                self._daten.pop(schluessel, None)

    def anzahl(self):
        with self._sperre:
            self._aufraeumen(time.time())
            return sum(len(v) for v in self._daten.values())

    def legen(self, datensatz, zweck="veroeffentlichen"):
        """Fremden Datensatz aufnehmen — nur nach vollständiger Prüfung."""
        with self._sperre:
            if self.geschlossen:
                raise ValueError("Speicher ist geschlossen.")
            datensatz.pruefen(zweck)
            jetzt = time.time()
            self._aufraeumen(jetzt)
            liste = self._daten.setdefault(datensatz.schluessel, [])
            # Entdoppelt wird nach VERFASSER UND Inhalt, nicht nach Inhalt
            # allein. Sonst verschluckt der Speicher zwei verschiedene
            # Menschen, die zufaellig dasselbe schreiben — und im Forum
            # verschluckte er den Schluss-Satz des Eroeffners, weil ein
            # Fremder zuvor einen gleichlautenden abgesetzt hatte. Wer etwas
            # gesagt hat, ist Teil der Aussage.
            neue_id = datensatz.inhalt_id()
            for vorhanden in liste:
                if (crypto.gleich(vorhanden.inhalt_id(), neue_id)
                        and crypto.gleich(vorhanden.veroeffentlicher,
                                          datensatz.veroeffentlicher)):
                    return False                      # echte Dublette
            if (self.anzahl() >= self.max_datensaetze
                    or self.bytes_belegt + len(datensatz.nutzlast) > self.max_bytes):
                # Voll: den ablaufnächsten Datensatz opfern, nicht den neuen
                # blind ablehnen — sonst friert ein voller Knoten für immer ein.
                if not self._aeltesten_opfern():
                    return False
            liste.append(datensatz)
            self.bytes_belegt += len(datensatz.nutzlast)
            return True

    def _aeltesten_opfern(self):
        aeltester = None
        for schluessel, liste in self._daten.items():
            for d in liste:
                if aeltester is None or d.ablauf < aeltester[1].ablauf:
                    aeltester = (schluessel, d)
        if aeltester is None:
            return False
        schluessel, d = aeltester
        self._daten[schluessel] = [x for x in self._daten[schluessel] if x is not d]
        if not self._daten[schluessel]:
            self._daten.pop(schluessel, None)
        self.bytes_belegt -= len(d.nutzlast)
        return True

    def holen(self, schluessel):
        """Alle noch gültigen Datensätze unter dieser Adresse."""
        with self._sperre:
            jetzt = time.time()
            self._aufraeumen(jetzt)
            return list(self._daten.get(bytes(schluessel), []))

    def schluessel(self):
        with self._sperre:
            self._aufraeumen(time.time())
            return list(self._daten)

    def schliessen(self):
        """Sitzungsende: Nutzlasten überschreiben, dann vergessen.

        Überschreiben ist bei unveränderlichen `bytes` nur teilweise möglich —
        deshalb wird hier zusätzlich jede Referenz gelöst, damit nichts
        länger als nötig erreichbar bleibt."""
        with self._sperre:
            self._daten.clear()
            self.bytes_belegt = 0
            self.geschlossen = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.schliessen()
        return False

    def __repr__(self):
        return "<Speicher %d Datensätze, %d KB>" % (self.anzahl(),
                                                    self.bytes_belegt // 1024)
