"""Dive on Wide Mesh — flüchtiges Gedächtnis und nicht verknüpfbare Identitäten.

Zwei Forderungen aus der Spezifikation treffen sich hier:

  * Alles — Nachrichten wie Schlüssel — lebt nur im RAM und wird beim Ende
    der Sitzung kryptografisch vernichtet.
  * Für jede Sitzung und jedes Thema entsteht ein eigenes Ed25519-Paar,
    und diese Paare dürfen niemals miteinander verknüpfbar sein.

WAS PYTHON HIER WIRKLICH KANN — UND WAS NICHT
---------------------------------------------
„Kryptografisch vernichten" ist in Python nicht vollständig erreichbar, und
so zu tun als ob wäre die gefährlichere Variante. Die Wahrheit:

  * `bytes` sind unveränderlich. Jede Kopie bleibt liegen, bis der
    Garbage Collector sie irgendwann überschreibt — oder nie.
    → Deshalb liegt jedes Geheimnis hier in einem `bytearray`, das
      tatsächlich überschrieben werden kann.
  * Der Speicher kann vom Betriebssystem auf die Platte ausgelagert werden.
    Dann steht der Schlüssel im Swap, und Überschreiben im RAM hilft nicht.
    → `sperren()` versucht `mlock(2)` über ctypes. Das braucht Rechte und
      gelingt nicht überall; `gesperrt` sagt ehrlich, ob es geklappt hat.
  * Ein Kernspeicherabbild (Ruhezustand, Absturz-Dump) entzieht sich uns
    vollständig.

Das ist der ehrliche Rahmen: Wir verhindern, dass Geheimnisse länger als
nötig herumliegen, und wir schreiben sie nie selbst auf die Platte. Wir
können nicht garantieren, dass das Betriebssystem es auch nicht tut.
"""

import ctypes
import ctypes.util
import threading
import time

from . import crypto

# ---------------------------------------------------------------------------
# Speichersperre (best effort)
# ---------------------------------------------------------------------------

def _libc():
    try:
        return ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    except Exception:
        return None


_LIBC = _libc()


class GeheimBytes:
    """Ein Geheimnis, das man wirklich löschen kann.

    Benutzung möglichst als Kontextmanager — dann verschwindet es garantiert
    am Ende des Blocks, auch wenn eine Ausnahme fliegt:

        with GeheimBytes(crypto.zufall(32)) as k:
            paket = crypto.versiegeln(k.lesen(), b"hallo")
        # k ist hier bereits genullt
    """

    __slots__ = ("_puffer", "_vernichtet", "gesperrt", "_sperre")

    def __init__(self, daten, sperren=True):
        self._puffer = bytearray(daten)
        self._vernichtet = False
        self.gesperrt = False
        self._sperre = threading.Lock()
        if sperren:
            self.sperren()

    # -- Zugriff ----------------------------------------------------------
    def lesen(self):
        """Rohbytes für einen Krypto-Aufruf. Kurz halten, nie aufheben."""
        if self._vernichtet:
            raise ValueError("Dieses Geheimnis wurde bereits vernichtet.")
        return bytes(self._puffer)

    def __len__(self):
        return 0 if self._vernichtet else len(self._puffer)

    def __bool__(self):
        return not self._vernichtet and bool(self._puffer)

    # -- Schutz -----------------------------------------------------------
    def sperren(self):
        """Versucht, die Seite vom Auslagern auszunehmen (mlock)."""
        if _LIBC is None or self._vernichtet or not self._puffer:
            return False
        try:
            adr = (ctypes.c_char * len(self._puffer)).from_buffer(self._puffer)
            self.gesperrt = _LIBC.mlock(ctypes.byref(adr), len(self._puffer)) == 0
            del adr
        except Exception:
            self.gesperrt = False
        return self.gesperrt

    # -- Vernichtung ------------------------------------------------------
    def vernichten(self):
        """Überschreibt den Puffer und gibt ihn frei. Mehrfach aufrufbar.

        Drei Durchgänge (Einsen, Nullen, Zufall) sind gegen einen RAM-Leser
        kein Mehrwert gegenüber einem — sie schützen aber gegen Compiler-
        oder Interpreter-Optimierungen, die ein einzelnes Nullen als
        wirkungslos wegwerfen könnten."""
        with self._sperre:
            if self._vernichtet:
                return
            n = len(self._puffer)
            if n:
                for muster in (0xff, 0x00):
                    for i in range(n):
                        self._puffer[i] = muster
                zufall = crypto.zufall(n)
                for i in range(n):
                    self._puffer[i] = zufall[i]
                if self.gesperrt and _LIBC is not None:
                    try:
                        adr = (ctypes.c_char * n).from_buffer(self._puffer)
                        _LIBC.munlock(ctypes.byref(adr), n)
                        del adr
                    except Exception:
                        pass
                del self._puffer[:]
            self._vernichtet = True
            self.gesperrt = False

    @property
    def vernichtet(self):
        return self._vernichtet

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.vernichten()
        return False

    def __del__(self):
        try:
            self.vernichten()
        except Exception:
            pass

    def __repr__(self):
        # Ein Geheimnis darf niemals in einem Protokoll landen.
        zustand = "vernichtet" if self._vernichtet else "%d Byte" % len(self._puffer)
        return "<GeheimBytes %s%s>" % (zustand, ", gesperrt" if self.gesperrt else "")


# ---------------------------------------------------------------------------
# Identitäten
# ---------------------------------------------------------------------------

class Identitaet:
    """Ein Ed25519-Paar für GENAU eine Sitzung oder ein Thema.

    UNVERKNÜPFBARKEIT — der Kern der Sache:
    Jede Identität bekommt eigenen, frischen Zufall. Sie wird NICHT aus
    einem gemeinsamen Hauptschlüssel abgeleitet. Das ist der ganze Punkt:
    Bei Ableitung aus einem Hauptschlüssel wären alle Identitäten in dem
    Moment verknüpfbar, in dem dieser eine Schlüssel bekannt wird — und der
    Betreiber selbst könnte jederzeit beweisen, dass zwei Pseudonyme
    zusammengehören. Mit unabhängigem Zufall existiert diese Verbindung
    schlicht nicht; sie kann auch unter Zwang nicht hergestellt werden.

    Preis dieser Entscheidung: Identitäten sind nicht wiederherstellbar.
    Wer den Prozess verliert, verliert das Pseudonym. Das ist gewollt.
    """

    __slots__ = ("zweck", "erzeugt_am", "_sign_saat", "sign_oeffentlich",
                 "_dh_geheim", "dh_oeffentlich", "_tot")

    def __init__(self, zweck):
        self.zweck = zweck
        self.erzeugt_am = time.time()
        self._tot = False
        # Signatur-Paar: unabhängiger Zufall, kein gemeinsamer Ursprung.
        saat = crypto.zufall(32)
        _s, oeff = crypto.ed25519_schluesselpaar(saat)
        self._sign_saat = GeheimBytes(saat)
        self.sign_oeffentlich = oeff
        # Einigungs-Paar: ebenfalls eigener Zufall, NICHT aus der Saat oben
        # abgeleitet — sonst wären Signatur- und Verkehrsschlüssel verknüpft.
        geheim, oeffentlich = crypto.x25519_schluesselpaar()
        self._dh_geheim = GeheimBytes(geheim)
        self.dh_oeffentlich = oeffentlich

    @property
    def knoten_id(self):
        """Die Adresse dieser Identität im Netz — Hash, nie der Schlüssel selbst."""
        return crypto.ableiten(self.sign_oeffentlich, "knoten-id", 20)

    def signieren(self, nachricht):
        self._pruefen_lebt()
        return crypto.ed25519_signieren(self._sign_saat.lesen(),
                                        self.sign_oeffentlich, nachricht)

    def sitzungsschluessel(self, fremd_dh_oeffentlich, zweck="nachricht"):
        self._pruefen_lebt()
        return crypto.sitzungsschluessel(self._dh_geheim.lesen(),
                                         fremd_dh_oeffentlich, zweck)

    def _pruefen_lebt(self):
        if self._tot:
            raise ValueError("Diese Identität wurde bereits vernichtet.")

    def vernichten(self):
        self._sign_saat.vernichten()
        self._dh_geheim.vernichten()
        self._tot = True

    @property
    def vernichtet(self):
        return self._tot

    def __repr__(self):
        return "<Identitaet %r %s>" % (
            self.zweck, "vernichtet" if self._tot else
            self.knoten_id.hex()[:12])


class Sitzung:
    """Alle Identitäten eines Laufs — und ihr gemeinsames Ende.

    Der Knoten hält genau eine Sitzung. Wird sie geschlossen, ist jedes
    Schlüsselmaterial überschrieben. Danach gibt es keinen Weg zurück:
    weder für den Angreifer noch für den Besitzer."""

    def __init__(self):
        self._identitaeten = {}
        self._sperre = threading.RLock()
        self.geschlossen = False
        self.eroeffnet_am = time.time()

    def fuer(self, zweck):
        """Identität für dieses Thema — bestehende oder frisch erzeugte."""
        with self._sperre:
            if self.geschlossen:
                raise ValueError("Die Sitzung ist geschlossen.")
            if zweck not in self._identitaeten:
                self._identitaeten[zweck] = Identitaet(zweck)
            return self._identitaeten[zweck]

    def vergessen(self, zweck):
        """Ein einzelnes Pseudonym sofort fallen lassen."""
        with self._sperre:
            ident = self._identitaeten.pop(zweck, None)
            if ident:
                ident.vernichten()
            return ident is not None

    def zwecke(self):
        with self._sperre:
            return sorted(self._identitaeten)

    def schliessen(self):
        """Sitzungsende: alles vernichten. Mehrfach aufrufbar."""
        with self._sperre:
            for ident in self._identitaeten.values():
                ident.vernichten()
            self._identitaeten.clear()
            self.geschlossen = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.schliessen()
        return False

    def __repr__(self):
        return "<Sitzung %d Identität(en)%s>" % (
            len(self._identitaeten), ", geschlossen" if self.geschlossen else "")
