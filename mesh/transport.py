"""Dive on Wide Mesh — Transport: wie Pakete tatsächlich von Knoten zu Knoten kommen.

Zwei austauschbare Untergründe hinter EINER Schnittstelle:

    UdpNetz        echte UDP-Pakete, Nachbarschaftssuche per Multicast im LAN
    SchleifenNetz  alles im selben Prozess, kein Paket verlässt den Rechner

Der zweite ist nicht nur zum Testen da, auch wenn er das möglich macht: Ein
verteiltes System, das man nicht in Ruhe durchspielen kann, wird nie fertig.
Mit dem Schleifennetz lassen sich zwanzig Knoten aufsetzen, Paketverluste
erzwingen und Abstürze nachstellen — in Millisekunden und reproduzierbar.

DAS PAKETFORMAT
---------------
    DOWM | version | typ | rumpf

`ruf` (Anwesenheitsruf) ist signiert, aber nicht verschlüsselt: Im eigenen
Netz ist es der Sinn der Sache, gefunden zu werden. `brief` ist versiegelt und
nur für einen Empfänger lesbar.

WAS DIESE SCHICHT NICHT LEISTET
-------------------------------
Sie verspricht keine Zustellung. UDP verliert Pakete, und das ist in Ordnung:
Anwesenheitsrufe werden wiederholt, Briefe quittiert. Wer hier
Zuverlässigkeit einbaut, baut TCP nach — schlechter als das Original.
"""

import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time

from . import arbeit, crypto, inhalt

MAGIE = b"DOWM"
VERSION = 1
TYP_RUF = 1          # „ich bin da, das kann ich beisteuern"
TYP_BRIEF = 2        # versiegelte Nachricht an genau einen
TYP_SATZ = 3         # offener, signierter Datensatz (Forum) fuer alle
TYP_ZWIEBEL = 4      # in Schichten verpackt: jeder Knoten kennt nur den naechsten
TYP_FRAGE = 5        # Kademlia: „wer liegt nahe bei X?" / „hast du X?"
TYP_ANTWORT = 6      # Kademlia: die Antwort darauf, mit derselben Vorgangsnummer
TYP_SPIEGEL = 7      # „unter welcher Adresse siehst du mich?" / die Antwort
TYP_STUPS = 8        # NAT-Durchstich: „klopf bitte bei dieser Adresse an"

# Wie viele Knoten eine Antwort hoechstens nennen darf. Genau K: mehr braucht
# niemand, und mehr zuzulassen hiesse, fremde Tabellen fluten zu koennen.
MAX_ANTWORT_KNOTEN = 20
MAX_PAKET = 8192     # bleibt unter der üblichen MTU-Fragmentierung

# Eigene Multicast-Gruppe — bewusst NICHT die von mDNS (224.0.0.251),
# damit wir fremden Diensten im Netz nicht ins Handwerk pfuschen.
GRUPPE = "239.255.77.77"
# Abweichender Port nur fuer Pruefungen (werkzeuge/volldurchlauf.py): Zwei
# Test-Instanzen sollen sich finden, aber keine echten Geraete im selben Netz
# — am 27.09.2026 scheiterte eine Freigabe, weil der Windows-PC des Besitzers
# gerade im Mesh war. Wer diesen Port aendert, ist fuer alle anderen unsichtbar.
PORT = int(os.environ.get("DOWOS_MESH_PORT") or 47771)
WEGE_FRISCH = 30.0   # Sekunden; so oft prueft der Rundruf, ob ein fehlender Weg inzwischen geht


class Paketfehler(ValueError):
    """Unlesbar, zu groß oder nicht für uns — wird still verworfen."""


def _nimmt_drei(fn):
    """Nimmt dieser Empfaenger den dritten Parameter entgegen?

    Gefragt wird die Signatur, NICHT ausprobiert. Der naheliegende Weg waere
    gewesen, mit drei Argumenten aufzurufen und bei TypeError auf zwei
    zurueckzufallen — aber dann verschluckt man auch jeden TypeError, der IM
    Empfaenger entsteht, und ruft ihn ein zweites Mal auf. Ein echter Fehler
    saehe damit aus wie eine alte Signatur, und die Nebenwirkung liefe doppelt.
    Das Ergebnis wird gemerkt: Die Frage kostet sonst bei jedem Paket."""
    merker = getattr(fn, "_dowos_nimmt_drei", None)
    if merker is not None:
        return merker
    try:
        import inspect
        params = inspect.signature(fn).parameters
        antwort = (len(params) >= 3
                   or any(p.kind == p.VAR_POSITIONAL for p in params.values()))
    except (TypeError, ValueError):
        antwort = False           # nicht befragbar: der sichere Weg sind zwei
    try:
        fn._dowos_nimmt_drei = antwort
    except (AttributeError, TypeError):
        pass                      # z. B. eine gebundene Methode ohne __dict__
    return antwort


def _zustellen(empfaenger, von, daten, ueber_rundruf):
    """Ruft den Empfaenger auf — mit dem Hinweis, ob es ein Rundruf war.

    Aeltere Empfaenger (und Testattrappen) nehmen nur zwei Argumente."""
    if _nimmt_drei(empfaenger):
        return empfaenger(von, daten, ueber_rundruf)
    return empfaenger(von, daten)


def paket_bauen(typ, rumpf):
    if len(rumpf) > MAX_PAKET - 6:
        raise Paketfehler("Paket zu groß (%d Byte)" % len(rumpf))
    return MAGIE + bytes([VERSION, typ]) + rumpf


def paket_lesen(roh):
    if len(roh) < 6 or roh[:4] != MAGIE:
        raise Paketfehler("kein Dive-on-Wide-Mesh-Paket")
    if roh[4] != VERSION:
        raise Paketfehler("Version %d wird nicht unterstützt" % roh[4])
    return roh[5], roh[6:]


# ---------------------------------------------------------------------------
# Anwesenheitsruf
# ---------------------------------------------------------------------------

def treff_marke(treffpunkt, nonce=None):
    """Eine BLINDE Marke für einen Treffpunkt.

    Warum nicht einfach den Treffpunkt senden: Zwei Freunde teilen denselben
    Treffpunkt. Riefen beide ihn im Klartext aus, sähe jeder Beobachter im
    Netz sofort, welche zwei Geräte zusammengehören — genau die Verknüpfung,
    die das ganze System vermeiden soll.

    Deshalb bei JEDEM Ruf ein frischer Zufall: gesendet wird (nonce, marke)
    mit marke = H(treffpunkt | nonce). Wer den Treffpunkt kennt, rechnet nach
    und erkennt seinen Kontakt. Wer ihn nicht kennt, sieht bei jedem Ruf einen
    anderen Wert und kann nichts verketten — auch nicht über Tage."""
    nonce = nonce or crypto.zufall(16)
    marke = hashlib.blake2b(bytes(treffpunkt) + nonce, digest_size=16,
                            person=b"dowos-treffmarke").digest()
    return nonce.hex(), marke.hex()


def treff_passt(treffpunkt, nonce_hex, marke_hex):
    """Gehört diese fremde Marke zu meinem Treffpunkt?"""
    try:
        nonce = bytes.fromhex(nonce_hex)
        soll = hashlib.blake2b(bytes(treffpunkt) + nonce, digest_size=16,
                               person=b"dowos-treffmarke").digest()
        return crypto.gleich(soll, bytes.fromhex(marke_hex))
    except Exception:
        return False


MAX_MODELLE = 12       # der Ruf muss in ein UDP-Paket passen


def ruf_bauen(identitaet, betriebsart, compute_bytes, treffpunkte=(), jetzt=None,
              port=None, modelle=(), rpc_port=0):
    """Signierter Ruf: wer ich bin, was ich beisteuere, worauf ich horche.

    Der Arbeitsnachweis ist bewusst der billigste (Stufe „nachricht"): Ein Ruf
    geht alle paar Sekunden raus, er darf niemanden ausbremsen. Er reicht, um
    ein Fluten mit erfundenen Knoten unattraktiv zu machen.

    Die Treffpunkte gehen als blinde Marken raus — siehe `treff_marke`."""
    jetzt = time.time() if jetzt is None else jetzt
    kern = {
        "id": identitaet.knoten_id.hex(),
        "sig_pub": identitaet.sign_oeffentlich.hex(),
        "dh_pub": identitaet.dh_oeffentlich.hex(),
        "art": betriebsart,
        "compute": int(compute_bytes),
        "zeit": int(jetzt),
        "treff": [treff_marke(t) for t in treffpunkte],
        # Der eigene Empfangsport. Ohne ihn koennten zwei Knoten auf DEMSELBEN
        # Rechner keine Briefe austauschen: Sie teilen sich den Multicast-Port,
        # und der Kern liefert ein Unicast-Paket dorthin nur an EINEN von beiden.
        "port": int(port) if port else 0,
        # Welche Modelle dieser Knoten SELBST fahren kann. Ohne diese Liste
        # koennte niemand einen Auftrag sinnvoll verteilen — man wuesste nicht,
        # wer ihn ueberhaupt bearbeiten kann. Die Liste ist abschaltbar; ein
        # Knoten, der nichts nennt, bekommt einfach keine Auftraege.
        "modelle": [str(m)[:80] for m in list(modelle)[:MAX_MODELLE]],
        # Der Port, auf dem dieser Knoten Modellschichten HALTEN kann (0 = tut
        # er nicht). Damit ein Verteilplan ueberhaupt entstehen kann, muss
        # bekannt sein, wer mitrechnen WILL — Speicher zu haben genuegt nicht.
        # Standardmaessig 0: Niemand wird ungefragt zum Rechenknecht.
        "rpc": int(rpc_port) if rpc_port else 0,
    }
    rumpf = json.dumps(kern, sort_keys=True, separators=(",", ":")).encode()
    nonce = arbeit.finden(rumpf, arbeit.bits_fuer("nachricht"), frist=5.0)
    unterschrieben = rumpf + struct.pack("<Q", nonce)
    sig = identitaet.signieren(unterschrieben)
    return paket_bauen(TYP_RUF, struct.pack("<I", len(sig)) + sig + unterschrieben)


def ruf_pruefen(rumpf, hoechstalter=120, jetzt=None):
    """Prüft einen fremden Ruf vollständig, BEVOR irgendetwas davon benutzt wird.

    Reihenfolge ist Absicht: erst billig (Form, Alter), dann Arbeitsnachweis,
    dann die teure Signatur. Ein Angreifer soll uns nicht mit Müll dazu
    bringen, tausende Signaturen zu prüfen."""
    try:
        (n,) = struct.unpack("<I", rumpf[:4])
        if n != 64:
            raise Paketfehler("Signatur hat die falsche Länge")
        sig = rumpf[4:4 + n]
        unterschrieben = rumpf[4 + n:]
        kern_roh, nonce_roh = unterschrieben[:-8], unterschrieben[-8:]
        (nonce,) = struct.unpack("<Q", nonce_roh)
        kern = json.loads(kern_roh.decode("utf-8"))
    except Paketfehler:
        raise
    except Exception:
        raise Paketfehler("Ruf ist nicht lesbar")
    jetzt = time.time() if jetzt is None else jetzt
    if abs(jetzt - kern.get("zeit", 0)) > hoechstalter:
        raise Paketfehler("Ruf ist zu alt oder aus der Zukunft")
    if not arbeit.erfuellt(kern_roh, nonce, arbeit.bits_fuer("nachricht")):
        raise Paketfehler("Arbeitsnachweis fehlt")
    try:
        sig_pub = bytes.fromhex(kern["sig_pub"])
        dh_pub = bytes.fromhex(kern["dh_pub"])
    except Exception:
        raise Paketfehler("Schlüssel im Ruf sind unlesbar")
    if not crypto.ed25519_pruefen(sig_pub, unterschrieben, sig):
        raise Paketfehler("Signatur passt nicht")
    # Die Knoten-ID MUSS aus dem Schlüssel folgen — sonst könnte sich jeder
    # eine beliebige Adresse aussuchen und fremde Plätze besetzen.
    if crypto.ableiten(sig_pub, "knoten-id", 20).hex() != kern.get("id"):
        raise Paketfehler("Knoten-ID passt nicht zum Schlüssel")
    kern["sig_pub_bytes"] = sig_pub
    kern["dh_pub_bytes"] = dh_pub
    return kern


# ---------------------------------------------------------------------------
# Versiegelter Brief
# ---------------------------------------------------------------------------

def brief_bauen(identitaet, empfaenger_dh_pub, nutzlast, zweck="nachricht"):
    schluessel = identitaet.sitzungsschluessel(empfaenger_dh_pub, zweck)
    # Der eigene Einigungsschlüssel liegt offen bei — sonst wüsste die
    # Gegenseite nicht, mit wem sie den gemeinsamen Schlüssel bilden soll.
    kopf = identitaet.dh_oeffentlich
    versiegelt = crypto.versiegeln(schluessel, nutzlast, aad=kopf)
    return paket_bauen(TYP_BRIEF, kopf + versiegelt)


def brief_oeffnen(identitaet, rumpf, zweck="nachricht"):
    if len(rumpf) < 32 + crypto.NONCE_LAENGE + crypto.SIEGEL_LAENGE:
        raise Paketfehler("Brief ist zu kurz")
    absender_dh, versiegelt = rumpf[:32], rumpf[32:]
    schluessel = identitaet.sitzungsschluessel(absender_dh, zweck)
    try:
        klartext = crypto.entsiegeln(schluessel, versiegelt, aad=absender_dh)
    except crypto.EntschluesselungFehlgeschlagen:
        raise Paketfehler("Brief ist nicht für uns oder wurde verändert")
    return absender_dh, klartext


# ---------------------------------------------------------------------------
# Untergrund 1: alles im Prozess — für Tests und ganze simulierte Netze
# ---------------------------------------------------------------------------

class SchleifenNetz:
    """Ein Netz im Arbeitsspeicher. Kein Paket verlässt den Prozess.

    `verlustrate` und `verzoegerung` machen aus dem Testlauf ein ehrliches
    Netz: Ein verteiltes System, das nur ohne Paketverlust funktioniert,
    funktioniert draußen nicht."""

    def __init__(self, verlustrate=0.0, verzoegerung=0.0):
        self.teilnehmer = {}
        self.verlustrate = verlustrate
        self.verzoegerung = verzoegerung
        self._sperre = threading.RLock()
        self.gesendet = 0
        self.verloren = 0

    def anmelden(self, adresse, zustellen):
        with self._sperre:
            self.teilnehmer[adresse] = zustellen

    def abmelden(self, adresse):
        with self._sperre:
            self.teilnehmer.pop(adresse, None)

    def _wuerfeln(self):
        if self.verlustrate <= 0:
            return False
        return int.from_bytes(crypto.zufall(2), "big") / 65535.0 < self.verlustrate

    def senden(self, von, an, daten):
        with self._sperre:
            self.gesendet += 1
            if self._wuerfeln():
                self.verloren += 1
                return False
            ziel = self.teilnehmer.get(an)
        if ziel is None:
            return False
        if self.verzoegerung:
            time.sleep(self.verzoegerung)
        _zustellen(ziel, von, daten, False)
        return True

    def rundruf(self, von, daten):
        with self._sperre:
            ziele = [(a, f) for a, f in self.teilnehmer.items() if a != von]
        erreicht = 0
        for adresse, zustellen in ziele:
            with self._sperre:
                self.gesendet += 1
                verlust = self._wuerfeln()
                if verlust:
                    self.verloren += 1
            if verlust:
                continue
            _zustellen(zustellen, von, daten, True)
            erreicht += 1
        return erreicht


# ---------------------------------------------------------------------------
# Untergrund 2: echtes UDP mit Nachbarschaftssuche im LAN
# ---------------------------------------------------------------------------

class UdpNetz:
    """UDP mit Multicast-Rundruf — die Klause im eigenen Netz.

    Bewusst ohne jeden Verzeichnisdienst: Wer im selben Netz ist, hört den
    Rundruf. Das ist die einfachste Form von „kein zentraler Server", und
    sie funktioniert auch ohne Internetanschluss."""

    def __init__(self, port=PORT, gruppe=GRUPPE, empfangen=None):
        self.port = port
        self.gruppe = gruppe
        self.empfangen = empfangen or (lambda von, daten: None)
        self._sock = None          # Multicast: gemeinsamer Port, nur Rundrufe
        self._uni = None           # eigener Port: nur fuer diesen einen Knoten
        self.unicast_port = None
        self._sender = []            # (ip, socket) je brauchbarer Schnittstelle
        self.wege_fehler = {}        # ip -> Grund, warum es dort nicht geht
        self._wege_stand = (0.0, ())  # (wann zuletzt geprueft, eigene Adressen damals)
        self._beigetreten = set()
        self._laeuft = False
        self._faden = None
        self._faden_uni = None
        self.fehler = None

    def wege_bestimmen(self):
        """Ueber welche Schnittstellen kann der Rundruf hinaus?

        Nicht raten, sondern PROBIEREN. Auf dieser Maschine scheiterte
        Multicast ueber WLAN mit „No route to host", waehrend es ueber
        Loopback einwandfrei lief — je nach Router, Firewall und
        Netzwerkzustand ist das mal so, mal so.

        Loopback ist bewusst IMMER dabei: Zwei Dive on Wide auf demselben Rechner
        (und der erste Test eines Menschen ist genau das) muessen sich auch
        dann finden, wenn gar kein Netz da ist."""
        kandidaten = ["127.0.0.1"] + [a for a in eigene_adressen()]
        wege = []
        for ip in kandidaten:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                             socket.inet_aton(ip))
                s.sendto(b"", (self.gruppe, self.port))   # leerer Probe-Ruf
                wege.append((ip, s))
            except OSError as e:
                self.wege_fehler[ip] = str(e)
                try:
                    s.close()
                except Exception:
                    pass
        return wege

    def starten(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            s.bind(("", self.port))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)  # nur LAN
            s.settimeout(0.5)
            self._sock = s
            self._beigetreten = set()
            self._beitreten(["0.0.0.0", "127.0.0.1"] + eigene_adressen())
            # Zweiter Socket auf einem freien Port — die eigene Postadresse.
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            u.bind(("", 0))
            u.settimeout(0.5)
            self._uni = u
            self.unicast_port = u.getsockname()[1]
            self.wege_fehler = {}
            self._sender = self.wege_bestimmen()
            self._wege_stand = (time.time(), tuple(eigene_adressen()))
        except Exception as e:
            self.fehler = ("Konnte nicht auf Port %d horchen: %s. Läuft schon "
                           "ein Knoten auf diesem Gerät?" % (self.port, e))
            return False
        self._laeuft = True
        # Es ist wichtig zu WISSEN, ueber welchen Weg ein Paket kam: Der
        # Multicast-Port hoert jeder im LAN, der eigene Unicast-Port nur, wer
        # ihn kennt. Die Weite darf darauf verschieden reagieren.
        self._faden = threading.Thread(target=self._horchen,
                                       args=(lambda: self._sock, True), daemon=True)
        self._faden.start()
        self._faden_uni = threading.Thread(target=self._horchen,
                                           args=(lambda: self._uni, False), daemon=True)
        self._faden_uni.start()
        return True

    def _horchen(self, hol_sock, ueber_rundruf=False):
        while self._laeuft:
            sock = hol_sock()
            if sock is None:
                break
            try:
                daten, absender = sock.recvfrom(MAX_PAKET)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                _zustellen(self.empfangen, absender, daten, ueber_rundruf)
            except Exception:
                pass            # ein fehlerhaftes Paket darf nie den Knoten töten

    def senden(self, von, an, daten):
        """Brief an genau einen Knoten — ueber dessen eigenen Empfangsport."""
        if not self._uni:
            return False
        try:
            self._uni.sendto(daten, an)
            return True
        except OSError:
            return False

    def _beitreten(self, adressen):
        """Der Gruppe auf JEDER Schnittstelle beitreten, nicht nur auf der vom
        System gewaehlten — sonst hoert man auf der falschen."""
        for ip in adressen:
            if ip in self._beigetreten:
                continue
            try:
                self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                      socket.inet_aton(self.gruppe) + socket.inet_aton(ip))
                self._beigetreten.add(ip)
            except OSError:
                pass          # schon beigetreten oder Schnittstelle taugt (noch) nicht

    def _wege_auffrischen(self, jetzt=None):
        """Die Wege nicht nur beim Start bestimmen.

        Frueher geschah das genau einmal. War das WLAN beim Start noch nicht da
        oder wechselte danach das Netz, blieb der Knoten fuer immer bei
        „nur dieser Rechner" — beide Geraete an, keines sah das andere
        (06.10.2026, Mac + Windows-PC). Jetzt: hoechstens alle WEGE_FRISCH
        Sekunden neu probieren, aber nur wenn ein Weg fehlte oder sich die
        eigenen Adressen geaendert haben."""
        if not self._laeuft:
            return False
        jetzt = jetzt or time.time()
        stand_zeit, stand_adressen = self._wege_stand
        if jetzt - stand_zeit < WEGE_FRISCH:
            return False
        adressen = tuple(eigene_adressen())
        self._wege_stand = (jetzt, adressen)
        if not self.wege_fehler and adressen == stand_adressen:
            return False
        alt = self._sender
        self.wege_fehler = {}
        self._sender = self.wege_bestimmen()
        for _ip, s in alt:
            try:
                s.close()
            except OSError:
                pass
        if self._sock is not None:
            self._beitreten(list(adressen))
        return True

    def rundruf(self, von, daten):
        """Ueber JEDE brauchbare Schnittstelle rufen. Gibt die Anzahl zurueck.

        Frueher ging der Ruf ueber genau einen Socket, und ein OSError wurde
        verschluckt: Der Mensch sah „keine Nachbarn" und keinen Grund. Jetzt
        zaehlt der Rueckgabewert die erreichten Wege — 0 ist ein Zustand, den
        die Diagnose zeigen kann."""
        self._wege_auffrischen()
        if not self._sender:
            return 0
        erreicht = 0
        for ip, s in list(self._sender):
            try:
                s.sendto(daten, (self.gruppe, self.port))
                erreicht += 1
            except OSError as e:
                self.wege_fehler[ip] = str(e)
        return erreicht

    def stoppen(self):
        self._laeuft = False
        for _ip, s in self._sender:
            try:
                s.close()
            except OSError:
                pass
        self._sender = []
        for name in ("_sock", "_uni"):
            sock = getattr(self, name)
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
                setattr(self, name, None)
        self.unicast_port = None


_adressen_zwischenlager = {"zeit": 0.0, "wert": []}
ADRESSEN_FRISCH = 20.0        # Sekunden; ein Netzwechsel darf nicht lange nachhallen


def _adressen_ueber_namen(sammel):
    """Der zweite Weg: den eigenen Rechnernamen auflösen.

    Läuft in einem eigenen Faden, weil er auf macOS regelmäßig HÄNGT: Der
    Rechnername endet dort auf .local, das geht über mDNS, und wenn niemand
    antwortet, wartet getaddrinfo die vollen fünf Sekunden ab, bevor es
    aufgibt. Genau diese fünf Sekunden steckten in JEDER Abfrage von
    /api/mesh — bei einer Ansicht, die alle sechs Sekunden nachfragt."""
    try:
        for eintrag in socket.getaddrinfo(socket.gethostname(), None):
            ip = eintrag[4][0]
            if not ip.startswith("127.") and ":" not in ip:
                sammel.add(ip)
    except Exception:
        pass


def eigene_adressen(frist=0.4):
    """Die eigenen IP-Adressen im LAN — für die Anzeige, nicht für Logik.

    Zwei Wege, in dieser Reihenfolge, und das ist der Punkt: Der billige
    zuerst. Der UDP-Trick kostet eine Millisekunde und liefert die Adresse,
    über die dieser Rechner tatsächlich hinausredet — also die, die zählt.
    Die Namensauflösung findet zusätzlich weitere Schnittstellen, darf dafür
    aber niemanden aufhalten: Was sie nicht binnen `frist` liefert, fehlt eben."""
    jetzt = time.time()
    if jetzt - _adressen_zwischenlager["zeit"] < ADRESSEN_FRISCH:
        return list(_adressen_zwischenlager["wert"])
    adressen = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))     # geht nie raus, verrät aber das Interface
        adressen.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    faden = threading.Thread(target=_adressen_ueber_namen, args=(adressen,),
                             daemon=True)
    faden.start()
    faden.join(frist)
    wert = sorted(a for a in adressen if not a.startswith("127."))
    _adressen_zwischenlager["zeit"] = jetzt
    _adressen_zwischenlager["wert"] = wert
    return list(wert)


# ---------------------------------------------------------------------------
# Kademlia: Frage und Antwort
# ---------------------------------------------------------------------------
# UDP kennt nur Hinausschicken. Damit eine Antwort ihrer Frage zugeordnet
# werden kann, traegt jede Frage eine Vorgangsnummer, die die Antwort
# zurueckspiegelt. Sie ist zufaellig und 8 Byte lang — nicht fortlaufend, sonst
# koennte ein Dritter Antworten erraten und unterschieben.

FRAGE_KNOTEN = 1     # „nenne mir die Knoten nahe bei X"
FRAGE_WERT = 2       # „hast du X? sonst nenne mir die Naechsten"


def frage_bauen(vorgang, art, ziel, absender_id, port):
    """Feste Laenge, kein JSON: Diese Pakete gehen oft und muessen billig sein.

    Der Port steht drin, weil der Antwortende sonst an den Multicast-Port
    zurueckschicken wuerde — der gehoert allen, und bei zwei Knoten auf einem
    Rechner landete die Antwort beim Falschen. Genau dieser Fehler ist beim
    Briefverkehr schon einmal aufgetreten."""
    if len(ziel) != 20 or len(absender_id) != 20:
        raise Paketfehler("Kennung muss 20 Byte lang sein")
    return paket_bauen(TYP_FRAGE, vorgang + bytes([art]) + ziel + absender_id
                       + struct.pack("<H", int(port or 0)))


def frage_lesen(rumpf):
    if len(rumpf) != 8 + 1 + 20 + 20 + 2:
        raise Paketfehler("Frage hat die falsche Länge")
    vorgang, art = rumpf[:8], rumpf[8]
    ziel, absender = rumpf[9:29], rumpf[29:49]
    (port,) = struct.unpack("<H", rumpf[49:51])
    if art not in (FRAGE_KNOTEN, FRAGE_WERT):
        raise Paketfehler("unbekannte Frageart %d" % art)
    return vorgang, art, ziel, absender, port


def antwort_bauen(vorgang, knoten, wert=None):
    """`knoten` ist eine Liste (id, ip, port). `wert` ist optional der Inhalt.

    Die Zahl der genannten Knoten ist gedeckelt: Sonst koennte ein boesartiger
    Knoten mit einer einzigen Antwort tausende erfundene Adressen in fremde
    Tabellen spuelen."""
    teile = [vorgang, bytes([min(len(knoten), MAX_ANTWORT_KNOTEN)])]
    for kid, ip, port in list(knoten)[:MAX_ANTWORT_KNOTEN]:
        roh_ip = socket.inet_aton(ip)
        teile.append(kid + roh_ip + struct.pack("<H", int(port)))
    wert = wert or b""
    teile.append(struct.pack("<I", len(wert)))
    teile.append(wert)
    return paket_bauen(TYP_ANTWORT, b"".join(teile))


def antwort_lesen(rumpf):
    if len(rumpf) < 9:
        raise Paketfehler("Antwort ist zu kurz")
    vorgang, anzahl = rumpf[:8], rumpf[8]
    if anzahl > MAX_ANTWORT_KNOTEN:
        raise Paketfehler("Antwort nennt zu viele Knoten (%d)" % anzahl)
    stelle = 9
    knoten = []
    for _ in range(anzahl):
        if stelle + 26 > len(rumpf):
            raise Paketfehler("Antwort bricht mitten im Knoten ab")
        kid = rumpf[stelle:stelle + 20]
        ip = socket.inet_ntoa(rumpf[stelle + 20:stelle + 24])
        (port,) = struct.unpack("<H", rumpf[stelle + 24:stelle + 26])
        knoten.append((kid, ip, port))
        stelle += 26
    if stelle + 4 > len(rumpf):
        raise Paketfehler("Antwort ohne Längenangabe für den Wert")
    (laenge,) = struct.unpack("<I", rumpf[stelle:stelle + 4])
    stelle += 4
    if laenge > MAX_PAKET:
        raise Paketfehler("Wert im Paket ist zu groß (%d)" % laenge)
    if stelle + laenge > len(rumpf):
        raise Paketfehler("Wert ist kürzer als angekündigt")
    wert = rumpf[stelle:stelle + laenge] if laenge else None
    return vorgang, knoten, wert


# ---------------------------------------------------------------------------
# NAT-Durchstich
# ---------------------------------------------------------------------------
# DAS PROBLEM
# Zwei Leute hinter Heimroutern koennen einander nicht anrufen. Beide sitzen
# hinter einer Adressumsetzung (NAT): Ihre Pakete kommen heraus, aber ein
# Paket, das von aussen ankommt, ohne dass vorher etwas hinausging, wird
# verworfen. Ein Ticket nennt eine Adresse, die von aussen gar nicht existiert.
#
# DIE LOESUNG, OHNE SERVER
# Zwei Schritte, beide ohne fremde Infrastruktur:
#
#  1. **Spiegel.** Man weiss selbst nicht, wie man von aussen aussieht — der
#     Router vergibt Adresse und Port. Also fragt man jemanden, den man
#     ohnehin kennt: „Unter welcher Adresse siehst du mich?" Das ist dasselbe,
#     was STUN-Server tun, nur dass hier jeder bereits bekannte Knoten es kann.
#     Kein zentraler Dienst, kein Anbieter, der mitschreibt.
#
#  2. **Stups.** Um B zu erreichen, bittet A einen Knoten R, den BEIDE kennen:
#     „Sag B, er soll bei meiner Aussenadresse anklopfen." B schickt daraufhin
#     ein paar Pakete an A. Diese Pakete kommen bei A vielleicht nicht an —
#     aber sie oeffnen in Bs Router den Rueckweg. Gleichzeitig schickt A zu B.
#     Eine der beiden Richtungen trifft auf einen bereits geoeffneten Weg.
#
# WAS DAS NICHT LEISTET
# Bei einem *symmetrischen* NAT vergibt der Router je Ziel einen anderen Port.
# Dann ist die gespiegelte Adresse fuer den Anruf des Dritten wertlos, und der
# Durchstich scheitert — daran ist ohne einen weiterleitenden Server nichts zu
# machen. Das betrifft in freier Wildbahn eine Minderheit, aber keine kleine.
# Dive on Wide sagt es, statt es zu verschweigen.

def spiegel_bauen(vorgang, gesehen=None):
    """Frage: `gesehen` ist None. Antwort: die beobachtete Adresse.

    Es wird bewusst NICHT signiert. Der Inhalt ist eine Beobachtung ueber
    einen selbst, keine Behauptung ueber die Welt — und wer luegt, verrat sich
    sofort, weil der Durchstich dann nicht klappt. Eine Signatur wuerde hier
    nur Rechenzeit kosten und einen Angreifer nicht aufhalten."""
    if gesehen is None:
        return paket_bauen(TYP_SPIEGEL, vorgang + b"\x00")
    ip, port = gesehen
    return paket_bauen(TYP_SPIEGEL, vorgang + b"\x01" + socket.inet_aton(ip)
                       + struct.pack("<H", int(port)))


def spiegel_lesen(rumpf):
    """(vorgang, gesehen) — `gesehen` ist None, wenn es eine Frage ist."""
    if len(rumpf) < 9:
        raise Paketfehler("Spiegel ist zu kurz")
    vorgang, ist_antwort = rumpf[:8], rumpf[8]
    if not ist_antwort:
        return vorgang, None
    if len(rumpf) != 9 + 6:
        raise Paketfehler("Spiegel-Antwort hat die falsche Länge")
    ip = socket.inet_ntoa(rumpf[9:13])
    (port,) = struct.unpack("<H", rumpf[13:15])
    return vorgang, (ip, port)


def stups_bauen(ziel_id, ip, port):
    """„Klopf bitte bei ip:port an." — die Bitte, die ein Dritter weiterreicht.

    Der Empfaenger prueft nur eines: dass die Adresse nicht in sein eigenes
    Netz zeigt. Sonst waere das ein hervorragendes Werkzeug, um fremde Knoten
    Pakete an beliebige Ziele schicken zu lassen — ein Verstaerker fuer
    Angriffe, gebaut aus lauter hilfsbereiten Teilnehmern."""
    if len(ziel_id) != 20:
        raise Paketfehler("Kennung muss 20 Byte lang sein")
    return paket_bauen(TYP_STUPS, ziel_id + socket.inet_aton(ip)
                       + struct.pack("<H", int(port)))


def stups_lesen(rumpf):
    if len(rumpf) != 20 + 4 + 2:
        raise Paketfehler("Stups hat die falsche Länge")
    ziel_id = rumpf[:20]
    ip = socket.inet_ntoa(rumpf[20:24])
    (port,) = struct.unpack("<H", rumpf[24:26])
    return ziel_id, (ip, port)


def adresse_oeffentlich(ip):
    """Zeigt diese Adresse ins offene Netz — oder auf die eigene Haustür?

    Ein Stups auf eine private, lokale oder besondere Adresse wird abgelehnt.
    Sonst koennte jemand eine Handvoll Knoten dazu bringen, gemeinsam auf ein
    Geraet im lokalen Netz eines Opfers einzuprasseln, oder auf einen Dienst,
    der gar nichts mit dem Mesh zu tun hat."""
    try:
        teile = [int(t) for t in str(ip).split(".")]
        if len(teile) != 4 or any(t < 0 or t > 255 for t in teile):
            return False
    except Exception:
        return False
    a, b = teile[0], teile[1]
    if a in (0, 10, 127):
        return False                                  # dieses Netz, privat, lokal
    if a == 169 and b == 254:
        return False                                  # Selbstvergabe
    if a == 172 and 16 <= b <= 31:
        return False                                  # privat
    if a == 192 and b == 168:
        return False                                  # privat
    if a == 100 and 64 <= b <= 127:
        return False                                  # Anbieter-NAT
    if a == 192 and b == 0:
        return False                                  # Sonderzwecke, Dokumentation
    if a == 198 and b in (18, 19):
        return False                                  # Messnetze
    if a == 198 and b == 51:
        return False                                  # Dokumentation
    if a == 203 and b == 0:
        return False                                  # Dokumentation
    if a >= 224:
        return False                                  # Multicast und reserviert
    return True


# ---------------------------------------------------------------------------
# Offene Datensaetze (Forum)
# ---------------------------------------------------------------------------

def satz_bauen(datensatz):
    """Ein Forumssatz geht offen ins Netz — lesbar zu sein ist sein Zweck.

    Geschuetzt ist nicht der Inhalt, sondern die Identitaet: signiert wird mit
    einem Schluessel, der nur fuer diesen einen Faden existiert."""
    return paket_bauen(TYP_SATZ, datensatz.kodieren())


def satz_lesen(rumpf):
    """Fremden Satz einlesen. Geprueft wird erst beim Ablegen im Speicher."""
    return inhalt.Datensatz.dekodieren(rumpf)


# ---------------------------------------------------------------------------
# Umschlag: was in einem versiegelten Brief steckt
# ---------------------------------------------------------------------------
# Ein Brief kann eine Chatnachricht sein, ein Auftrag oder ein Ergebnis.
# Ohne Umschlag muesste der Empfaenger raten — und Raten ist bei fremden
# Daten die schlechteste aller Vorgehensweisen.

ART_CHAT = "chat"
ART_AUFTRAG = "auftrag"
ART_ERGEBNIS = "ergebnis"
ART_NACHBARN = "nachbarn"
ART_TEIL = "teil"
ARTEN = (ART_CHAT, ART_AUFTRAG, ART_ERGEBNIS, ART_NACHBARN, ART_TEIL)

# Grosse Briefe in Teilen. Ein Paket darf MAX_PAKET Byte haben — eine Modellantwort hat oft mehr. Bis 09.10.2026
# scheiterte das Versiegeln einer solchen Antwort an „Paket zu groß“, der Fehler galt als „Knoten wurde beendet“,
# und die Antwort verschwand still: Der Auftraggeber (Windows-PC) wartete 600 s auf eine Antwort, die der Mac längst
# fertig hatte. Jetzt wird zerlegt, jedes Teil einzeln versiegelt, und der Empfänger setzt zusammen.
TEIL_ROH = 4800             # Byte Nutzlast je Teil — Base64 und Umschlag passen dann sicher in ein Paket
MAX_TEILE = 256             # ~1,2 MB je Brief; mehr ist kein Brief mehr
TEIL_FRIST = 180.0          # so lange wartet ein unvollständiger Brief auf seine restlichen Teile
TEIL_SPEICHER = 8 * 1024 * 1024


def briefe_bauen(identitaet, empfaenger_dh_pub, nutzlast, zweck="nachricht"):
    """Ein Brief — oder, wenn er nicht in ein Paket passt, mehrere Teile."""
    try:
        return [brief_bauen(identitaet, empfaenger_dh_pub, nutzlast, zweck)]
    except Paketfehler:
        pass
    stuecke = [nutzlast[i:i + TEIL_ROH] for i in range(0, len(nutzlast), TEIL_ROH)]
    if len(stuecke) > MAX_TEILE:
        raise Paketfehler("Brief zu groß (%d Byte, höchstens %d)" % (len(nutzlast), MAX_TEILE * TEIL_ROH))
    gruppe = os.urandom(8).hex()
    return [brief_bauen(identitaet, empfaenger_dh_pub,
                        umschlag(ART_TEIL, g=gruppe, nr=i, von=len(stuecke),
                                 d=base64.b64encode(st).decode("ascii")), zweck)
            for i, st in enumerate(stuecke)]


class Zusammensetzer:
    """Teile eines Briefs sammeln, bis er vollständig ist — begrenzt in Zeit und Speicher."""

    def __init__(self):
        self._offen = {}
        self._sperre = threading.Lock()

    def hinzufuegen(self, absender, u, jetzt=None):
        """Gibt die ganze Nutzlast zurück, sobald das letzte Teil da ist, sonst None."""
        jetzt = time.time() if jetzt is None else jetzt
        g, nr, von, d = u.get("g"), u.get("nr"), u.get("von"), u.get("d")
        if not (isinstance(g, str) and isinstance(nr, int) and isinstance(von, int) and isinstance(d, str)
                and 0 <= nr < von <= MAX_TEILE):
            raise Paketfehler("Teil ist unvollständig")
        try:
            stueck = base64.b64decode(d.encode("ascii"), validate=True)
        except Exception:
            raise Paketfehler("Teil ist nicht lesbar")
        schluessel = (bytes(absender), g)
        with self._sperre:
            for k in [k for k, e in self._offen.items() if jetzt - e["zeit"] > TEIL_FRIST]:
                del self._offen[k]
            e = self._offen.setdefault(schluessel, {"von": von, "teile": {}, "zeit": jetzt})
            if e["von"] != von:
                raise Paketfehler("Teile passen nicht zusammen")
            e["teile"][nr] = stueck
            if sum(len(x) for o in self._offen.values() for x in o["teile"].values()) > TEIL_SPEICHER:
                del self._offen[schluessel]
                raise Paketfehler("Zu viele unvollständige Briefe")
            if len(e["teile"]) < von:
                return None
            del self._offen[schluessel]
        return b"".join(e["teile"][i] for i in range(von))


def umschlag(art, **felder):
    if art not in ARTEN:
        raise Paketfehler("unbekannte Briefart: %r" % art)
    felder["art"] = art
    return json.dumps(felder, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def umschlag_lesen(roh):
    """Fremden Umschlag oeffnen. Alles, was nicht passt, fliegt raus."""
    try:
        d = json.loads(bytes(roh).decode("utf-8"))
    except Exception:
        raise Paketfehler("Umschlag ist nicht lesbar")
    if not isinstance(d, dict) or d.get("art") not in ARTEN:
        raise Paketfehler("Umschlag ohne gueltige Art")
    return d


# ---------------------------------------------------------------------------
# Zwiebel-Routing: niemand kennt Absender UND Ziel
# ---------------------------------------------------------------------------
# Das Paket wird in Schichten verpackt, eine je Zwischenknoten. Jeder kann
# GENAU SEINE Schicht oeffnen und findet darin nur zwei Dinge: die naechste
# Adresse und ein weiterhin verschluesseltes Paket. Der erste Knoten kennt den
# Absender, aber nicht das Ziel; der letzte kennt das Ziel, aber nicht den
# Absender; die Mitte kennt keins von beidem.
#
# Bewusst BINAER und nicht als JSON mit Base64: Jede Schachtelung wuerde die
# Nutzlast um ein Drittel aufblaehen, und bei drei Schichten waere das Paket
# mehr als doppelt so gross. So kostet eine Schicht nur rund 100 Byte.
#
# WAS DAS NICHT LEISTET: Schutz vor einem Beobachter, der ALLE Leitungen sieht.
# Der kann Pakete nach Zeit und Groesse zuordnen. Dagegen helfen nur Verzoegern
# und Vereinheitlichen der Groessen — beides kostet Latenz und ist hier
# absichtlich nicht eingebaut. Der Schutz richtet sich gegen die einzelnen
# Zwischenknoten, und der ist echt.


def _adresse_kodieren(adresse):
    if adresse is None:
        return b"\x00"
    host, port = adresse
    roh = str(host).encode("utf-8")[:255]
    return b"\x01" + bytes([len(roh)]) + roh + struct.pack("<H", int(port))


def _adresse_lesen(daten):
    if not daten:
        raise Paketfehler("Schicht ist leer")
    if daten[0] == 0:
        return None, daten[1:]
    if len(daten) < 2:
        raise Paketfehler("Adresse in der Schicht ist abgeschnitten")
    n = daten[1]
    if len(daten) < 2 + n + 2:
        raise Paketfehler("Adresse in der Schicht ist abgeschnitten")
    host = daten[2:2 + n].decode("utf-8", "replace")
    (port,) = struct.unpack("<H", daten[2 + n:4 + n])
    return (host, port), daten[4 + n:]


def zwiebel_bauen(identitaet, pfad, innerstes_paket):
    """Verpackt ein fertiges Paket in Schichten.

    `pfad` ist die Reise von aussen nach innen:
        [(dh_pub, adresse_des_naechsten), ...]
    Der letzte Eintrag ist das Ziel; seine `adresse_des_naechsten` ist None.
    Gebaut wird von INNEN nach aussen — anders geht es nicht, weil jede
    Schicht die naechste Adresse kennen muss, aber keine die vorherige."""
    if not pfad:
        raise Paketfehler("Ein Pfad ohne Knoten ist kein Pfad")
    rest = bytes(innerstes_paket)
    for dh_pub, weiter in reversed(pfad):
        schluessel = identitaet.sitzungsschluessel(dh_pub, "zwiebel")
        klartext = _adresse_kodieren(weiter) + rest
        versiegelt = crypto.versiegeln(schluessel, klartext,
                                       aad=identitaet.dh_oeffentlich)
        rest = paket_bauen(TYP_ZWIEBEL,
                           identitaet.dh_oeffentlich + versiegelt)
    return rest


def zwiebel_schaelen(identitaet, rumpf):
    """Die eigene Schicht oeffnen. Gibt (naechste_adresse, restpaket) zurueck.

    Ist die Adresse None, sind wir das Ziel und `restpaket` ist fuer uns."""
    if len(rumpf) < 32 + crypto.NONCE_LAENGE + crypto.SIEGEL_LAENGE:
        raise Paketfehler("Zwiebelschicht ist zu kurz")
    absender_dh, versiegelt = rumpf[:32], rumpf[32:]
    schluessel = identitaet.sitzungsschluessel(absender_dh, "zwiebel")
    try:
        klartext = crypto.entsiegeln(schluessel, versiegelt, aad=absender_dh)
    except crypto.EntschluesselungFehlgeschlagen:
        raise Paketfehler("Diese Schicht ist nicht fuer uns")
    weiter, rest = _adresse_lesen(klartext)
    if not rest:
        raise Paketfehler("Zwiebel ohne Inhalt")
    return weiter, rest
