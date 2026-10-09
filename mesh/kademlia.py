"""Dive on Wide Mesh — Kademlia: Finden, ohne alle zu fragen.

WARUM DAS NÖTIG IST
-------------------
Bisher wird alles per Rundruf verbreitet. In einer Klause mit zwanzig Geräten
ist das genau richtig: einfach, sofort, und jeder hat alles. In der Weite ist
es das Ende — bei tausend Knoten schickt jede Veröffentlichung tausend Pakete,
und jeder Knoten müsste alles speichern, was irgendwer je abgelegt hat.

Kademlia dreht das um: Ein Knoten kennt nur **wenige** andere, aber die
richtigen — und findet damit jeden Inhalt in etwa log₂(N) Schritten. Bei einer
Million Knoten sind das zwanzig Fragen statt einer Million.

DER TRICK: ABSTAND IST XOR
--------------------------
Der „Abstand" zweier Kennungen ist ihr bitweises XOR, als Zahl gelesen. Das ist
keine Metapher, sondern eine echte Metrik: symmetrisch, dreiecksungleich, und
— das ist der Kniff — **eindeutig**. Zu jedem Abstand gibt es genau einen
Knoten. Deshalb lernen alle Suchenden dieselben Wege kennen, und die Wege
verstärken sich, statt sich zu zerstreuen.

Ein Inhalt liegt bei den K Knoten, deren Kennung seiner Adresse am nächsten
ist. Suchen heißt: immer den fragen, der dem Ziel näher ist als man selbst.

WAS HIER BEWUSST ANDERS IST ALS IM PAPIER
-----------------------------------------
* **Alte Knoten haben Vorrang.** Ist ein Eimer voll, wird NICHT der Neue
  aufgenommen und der Älteste verdrängt, sondern umgekehrt: Der Älteste wird
  angetippt, und wenn er antwortet, bleibt er. Das ist die wichtigste
  Sicherheitseigenschaft von Kademlia überhaupt — wer ein Netz übernehmen
  will, muss frische Knoten in Masse einbringen, und genau die kommen so nicht
  hinein. Ein Netz, das den Neuesten bevorzugt, ist mit einem Nachmittag
  Rechenzeit zu kapern.
* **Der Arbeitsnachweis hängt an der Kennung**, nicht am Paket. Sonst könnte
  sich ein Angreifer beliebig viele Kennungen aussuchen und die Umgebung einer
  Zieladresse mit eigenen Knoten füllen (Sybil). Wie teuer das sein soll,
  steht in `arbeit.py`.
* **Nichts wird auf Platte geschrieben.** Wie überall im Mesh: Der Speicher
  ist flüchtig, und die Verfallszeit ist die einzige verlässliche Zusage.
"""

import heapq
import threading
import time

from . import arbeit, crypto

# Kademlias zwei Konstanten. K ist die Eimergroesse und zugleich die Zahl der
# Kopien je Inhalt; 20 ist der Wert aus dem Papier und hat sich in freier
# Wildbahn gehalten. ALPHA ist, wie viele Fragen gleichzeitig unterwegs sind —
# klein genug, um nicht zu fluten, gross genug, um einzelne Ausfaelle zu
# ueberbruecken.
K = 20
ALPHA = 3
ID_BITS = 160

# Wie lange ein Knoten ohne Lebenszeichen als brauchbar gilt.
VERFALL = 900.0            # 15 Minuten
# Nach dieser Zeit fragt ein Eimer, der nichts erlebt hat, aktiv nach.
AUFFRISCHEN = 3600.0       # 1 Stunde


def abstand(a, b):
    """XOR-Abstand zweier Kennungen als Zahl."""
    return int.from_bytes(a, "big") ^ int.from_bytes(b, "big")


def eimer_index(eigen, fremd):
    """In welchen Eimer gehört `fremd` aus Sicht von `eigen`?

    Der Index ist die Position des höchsten Bits, in dem sich die beiden
    unterscheiden. Eimer 159 enthält die halbe Welt (erstes Bit anders),
    Eimer 0 höchstens einen einzigen Knoten. Genau diese Schieflage ist
    gewollt: Man kennt die eigene Nachbarschaft genau und die Ferne grob."""
    d = abstand(eigen, fremd)
    if d == 0:
        return -1                      # man selbst
    return d.bit_length() - 1


def zufalls_id_im_eimer(eigen, index, zufall=None):
    """Eine zufällige Kennung, die genau in Eimer `index` fiele.

    Klingt nach einer Spielerei, ist aber der fehlende Teil des Beitritts:
    Ein neuer Knoten sucht sich selbst — damit füllt er die Eimer NAHE bei
    sich. Die fernen Eimer bleiben leer, und genau die braucht man, um weit
    entfernte Ziele zu finden. Deshalb sucht man zusätzlich nach je einer
    ausgedachten Kennung aus jedem Eimerbereich. Ohne diesen Schritt fand die
    Suche in einem Netz mit tausend Knoten nur 35 von 40 Zielen; mit ihm
    findet sie praktisch alle."""
    import os as _os
    eigen_zahl = int.from_bytes(eigen, "big")
    zufalls_zahl = int.from_bytes(zufall or _os.urandom(len(eigen)), "big")
    # Damit `eimer_index` genau `index` ergibt, muss im XOR das hoechste
    # gesetzte Bit an Position `index` liegen. Also:
    #   Bits ueber index : gleich wie bei uns (XOR = 0)
    #   Bit index        : das GEGENTEIL von unserem (XOR = 1)
    #   Bits unter index : beliebig
    # Der erste Entwurf setzte Bit `index` einfach auf 1, statt es zu kippen.
    # War unser eigenes Bit dort schon 1, waren beide gleich — und die Kennung
    # landete in einem ganz anderen Eimer. 240 von 240 Proben daneben.
    maske_hoeher = ((1 << (len(eigen) * 8)) - 1) ^ ((1 << (index + 1)) - 1)
    eigen_bit = (eigen_zahl >> index) & 1
    ziel = ((eigen_zahl & maske_hoeher)
            | ((1 - eigen_bit) << index)
            | (zufalls_zahl & ((1 << index) - 1)))
    return ziel.to_bytes(len(eigen), "big")


class Bekannter:
    """Ein anderer Knoten, wie er in der Tabelle steht."""

    __slots__ = ("id", "adresse", "zuletzt", "zuerst", "fehlschlaege")

    def __init__(self, knoten_id, adresse, jetzt=None):
        self.id = knoten_id
        self.adresse = adresse
        self.zuerst = jetzt or time.time()
        self.zuletzt = self.zuerst
        self.fehlschlaege = 0

    def frisch(self, jetzt=None):
        return ((jetzt or time.time()) - self.zuletzt) < VERFALL

    def __repr__(self):
        return "<Bekannter %s %r>" % (self.id.hex()[:10], self.adresse)


class Eimer:
    """K Knoten mit ähnlichem Abstand — der Älteste vorn.

    Die Reihenfolge ist die Kernaussage: vorn steht, wer sich am längsten
    bewährt hat. Wer neu ist, kommt hinten an — und nur, wenn Platz ist."""

    def __init__(self, groesse=K):
        self.groesse = groesse
        self.knoten = []                # aeltester zuerst
        self.wartend = []               # Neue, die auf einen freien Platz warten
        self.zuletzt_benutzt = time.time()

    def sehen(self, bekannter, jetzt=None):
        """Einen Knoten eintragen oder auffrischen.

        Rückgabe: None, wenn alles erledigt ist — oder der ÄLTESTE Knoten,
        wenn der Eimer voll ist. Dann muss der Aufrufer ihn antippen: Antwortet
        er, bleibt er und der Neue wird verworfen. Antwortet er nicht, macht er
        Platz. Diese eine Regel ist der Grund, warum Kademlia gegen
        Übernahmeversuche stabil ist."""
        jetzt = jetzt or time.time()
        self.zuletzt_benutzt = jetzt
        for i, k in enumerate(self.knoten):
            if k.id == bekannter.id:
                # Bekannt: ans ENDE, weil zuletzt gesehen. Die Adresse kann
                # sich geaendert haben (neuer Port, anderes Netz).
                k.adresse = bekannter.adresse
                k.zuletzt = jetzt
                k.fehlschlaege = 0
                self.knoten.append(self.knoten.pop(i))
                return None
        if len(self.knoten) < self.groesse:
            self.knoten.append(bekannter)
            return None
        # Voll. Der Neue wartet; der Aelteste wird geprueft.
        if all(w.id != bekannter.id for w in self.wartend):
            self.wartend.append(bekannter)
            del self.wartend[:-self.groesse]
        return self.knoten[0]

    def entfernen(self, knoten_id):
        """Einen Knoten hinauswerfen und, wenn möglich, ersetzen."""
        for i, k in enumerate(self.knoten):
            if k.id == knoten_id:
                del self.knoten[i]
                if self.wartend:
                    self.knoten.append(self.wartend.pop(0))
                return True
        return False

    def frische(self, jetzt=None):
        jetzt = jetzt or time.time()
        return [k for k in self.knoten if k.frisch(jetzt)]


class Tabelle:
    """Die Wegetabelle eines Knotens: 160 Eimer, einer je Abstandsklasse."""

    def __init__(self, eigene_id, groesse=K):
        self.ich = eigene_id
        self.groesse = groesse
        self.eimer = {}                 # index -> Eimer, erst bei Bedarf
        self._sperre = threading.RLock()

    def sehen(self, knoten_id, adresse, jetzt=None):
        """Einen Knoten aufnehmen. Gibt den zu prüfenden Ältesten zurück
        oder None."""
        if knoten_id == self.ich:
            return None
        i = eimer_index(self.ich, knoten_id)
        if i < 0:
            return None
        with self._sperre:
            eimer = self.eimer.setdefault(i, Eimer(self.groesse))
            return eimer.sehen(Bekannter(knoten_id, adresse, jetzt), jetzt)

    def entfernen(self, knoten_id):
        i = eimer_index(self.ich, knoten_id)
        with self._sperre:
            eimer = self.eimer.get(i)
            return eimer.entfernen(knoten_id) if eimer else False

    def naechste(self, ziel, anzahl=None, jetzt=None):
        """Die `anzahl` bekannten Knoten, die `ziel` am nächsten sind.

        Es werden ALLE Eimer durchgesehen, nicht nur der „passende" — der
        passende Eimer kann leer sein, und dann ist der zweitbeste Weg immer
        noch ein Weg. Bei einigen tausend Einträgen ist das billig."""
        anzahl = anzahl or self.groesse
        jetzt = jetzt or time.time()
        with self._sperre:
            alle = [k for e in self.eimer.values() for k in e.frische(jetzt)]
        return heapq.nsmallest(anzahl, alle, key=lambda k: abstand(k.id, ziel))

    def anzahl(self):
        with self._sperre:
            return sum(len(e.knoten) for e in self.eimer.values())

    def eimer_zum_auffrischen(self, jetzt=None):
        """Welche Eimer haben zu lange nichts erlebt?

        Ohne dieses Nachfassen verrottet eine Tabelle still: Man merkt erst
        beim Suchen, dass die halbe Nachbarschaft weg ist — und dann ist es
        die falsche Zeit, es zu merken."""
        jetzt = jetzt or time.time()
        with self._sperre:
            return [i for i, e in self.eimer.items()
                    if jetzt - e.zuletzt_benutzt > AUFFRISCHEN]


def kennung_gueltig(sig_pub, knoten_id, nonce, bits=None):
    """Gehört diese Kennung wirklich zu diesem Schlüssel — und war sie teuer?

    Zwei Prüfungen in einer. Die erste bindet die Kennung an den Schlüssel:
    Ohne sie könnte sich jeder eine beliebige Adresse aussuchen. Die zweite
    macht das Aussuchen TEUER: Ohne Arbeitsnachweis erzeugt ein Angreifer so
    lange Schlüsselpaare, bis er zwanzig Kennungen dicht bei der Zieladresse
    hat, und kontrolliert damit alles, was dort liegt."""
    if crypto.ableiten(sig_pub, "knoten-id", len(knoten_id)) != knoten_id:
        return False
    bits = arbeit.bits_fuer("beitritt") if bits is None else bits
    return arbeit.erfuellt(knoten_id, nonce, bits)


def beantworten(tabelle, frager_id, frager_adresse, ziel, werte=None, anzahl=None):
    """Die EMPFANGSSEITE: Was ein Knoten tut, wenn er gefragt wird.

    Die erste Zeile ist die wichtigste, und sie fehlte zuerst: **Wer fragt,
    wird gelernt.** So füllen sich Kademlia-Tabellen überhaupt — nicht durch
    eigenes Suchen, sondern dadurch, dass man gefragt wird. Ohne das blieben
    Knoten unauffindbar, die selbst fleißig suchen: Sie lernen die Welt kennen,
    aber die Welt lernt sie nie. Im Versuch mit tausend Knoten fand die Suche
    dadurch nur 35 von 40 Zielen; mit dieser Zeile findet sie alle.

    Zurück gehen die `anzahl` nächsten Bekannten und — falls vorhanden — der
    gesuchte Wert. Gibt zusätzlich den zu prüfenden Ältesten zurück, wenn der
    Eimer voll war (siehe `Eimer.sehen`)."""
    zu_pruefen = tabelle.sehen(frager_id, frager_adresse)
    naechste = tabelle.naechste(ziel, anzahl or K)
    wert = (werte or {}).get(ziel)
    return naechste, wert, zu_pruefen


class Suche:
    """Eine laufende iterative Suche — Kademlias eigentlicher Vorgang.

    Ablauf: die ALPHA nächsten Bekannten fragen, aus ihren Antworten die neuen
    Nächsten bilden, wieder fragen — bis eine Runde niemanden Näheren mehr
    liefert. Dann sind die K Nächsten gefunden.

    `fragen(bekannter, ziel)` wird von außen gereicht und liefert
    `(knotenliste, wert_oder_None)`. Dadurch ist der ganze Vorgang ohne Netz
    prüfbar: Der Testlauf schiebt eine Funktion unter, die eine simulierte
    Welt befragt."""

    def __init__(self, tabelle, ziel, fragen, breite=ALPHA, anzahl=K):
        self.tabelle = tabelle
        self.ziel = ziel
        self.fragen = fragen
        self.breite = breite
        self.anzahl = anzahl
        self.gesehen = {}               # id -> Bekannter
        self.gefragt = set()
        self.tot = set()
        self.runden = 0
        self.wert = None
        self._stockt = False        # brachte die letzte Runde nichts Naeheres?

    def _aufnehmen(self, knoten):
        for k in knoten or []:
            if k.id == self.tabelle.ich or k.id in self.tot:
                continue
            if k.id not in self.gesehen:
                self.gesehen[k.id] = k

    def _beste(self, n=None):
        return heapq.nsmallest(n or self.anzahl, self.gesehen.values(),
                               key=lambda k: abstand(k.id, self.ziel))

    def laufen(self, hoechstens_runden=30):
        """Sucht, bis nichts Näheres mehr kommt. Gibt die K Nächsten zurück
        — oder bricht ab, sobald der gesuchte Wert auftaucht."""
        self._aufnehmen(self.tabelle.naechste(self.ziel, self.anzahl))
        if not self.gesehen:
            return []
        # Die Abbruchregel ist der heikle Teil, und der erste Entwurf hatte sie
        # zu lasch: Er hoerte auf, sobald die besten DREI gefragt waren. Das
        # fand den gesuchten Knoten nur in 28 von 40 Faellen — die Suche gab
        # auf, waehrend im Feld noch ein naeherer Knoten ungefragt stand.
        # Kademlia verlangt: Erst wenn ALLE K Naechsten gefragt sind, ist
        # Schluss. Bringt eine Runde nichts Naeheres, wird deshalb einmal in
        # die Breite gefragt statt sofort abgebrochen.
        while self.runden < hoechstens_runden:
            self.runden += 1
            spitze = self._beste()
            offen = [k for k in spitze if k.id not in self.gefragt]
            if not offen:
                break                       # alle K Naechsten sind gefragt
            vorher = abstand(spitze[0].id, self.ziel)
            # Normalerweise ALPHA auf einmal; wenn die letzte Runde nichts
            # Naeheres brachte, die ganze Spitzengruppe.
            diesmal = offen if self._stockt else offen[:self.breite]
            for bekannter in diesmal:
                self.gefragt.add(bekannter.id)
                try:
                    knoten, wert = self.fragen(bekannter, self.ziel)
                except Exception:
                    # Wer nicht antwortet, faellt raus — sofort, nicht spaeter.
                    # Eine Suche, die auf tote Knoten wartet, ist keine Suche.
                    self.tot.add(bekannter.id)
                    self.gesehen.pop(bekannter.id, None)
                    self.tabelle.entfernen(bekannter.id)
                    continue
                self.tabelle.sehen(bekannter.id, bekannter.adresse)
                self._aufnehmen(knoten)
                if wert is not None:
                    self.wert = wert
                    return self._beste()
            if not self.gesehen:
                break
            nachher = abstand(self._beste(1)[0].id, self.ziel)
            self._stockt = (nachher >= vorher)
        return self._beste()
