"""Dive on Wide Mesh — der Knoten.

Hier laufen alle Schichten zusammen: Identität, Ressourcen, Transport,
Nachbarschaft. Das ist das Stück, das ein Mensch startet.

DIE ZWEI BETRIEBSARTEN
----------------------
    KLAUSE   Das eigene Netz. Nur wer im selben LAN ist oder einen persönlich
             getauschten Anker hat. Kein Weg nach draußen.
    WEITE    Das offene Netz, das nach und nach wächst. Fremde Knoten,
             Onion-Routing nötig.

Ein Knoten läuft in GENAU EINER Betriebsart. Kein Schalter im Hintergrund,
kein automatischer Wechsel: Wer eine Klause betreibt, soll nicht versehentlich
in einem offenen Netz landen, weil eine Voreinstellung sich geändert hat. Der
Wechsel bedeutet Neustart mit anderer Wahl — unbequem, und genau deshalb sicher.

WAS DER KNOTEN ÜBER SICH ERZÄHLT
--------------------------------
Nur das Nötigste: seine Adresse (ein Hash, kein Schlüssel), was er gerade an
Speicher beisteuert, und auf welchen Treffpunkten er horcht. Kein Gerätename,
kein Benutzername, keine Seriennummer. Was nicht gesendet wird, kann auch
nicht ausgewertet werden.
"""

import base64
import threading
import time

from . import anker as anker_mod, kademlia, verteilt
from . import vertrauen as vertrauen_mod
from . import ratsche as ratsche_mod
from . import crypto, fluechtig, forum as forum_mod, inhalt
from . import ressourcen, ticket as ticket_mod, transport

KLAUSE = "klause"
WEITE = "weite"
BETRIEBSARTEN = (KLAUSE, WEITE)

# Wie viele fremde Auftraege ein Knoten GLEICHZEITIG rechnet. Ohne Grenze
# startete er je Auftrag einen Thread und rief je Thread das Modell auf:
# 120 Auftraege von EINEM Nachbarn ergaben am 23.09.2026 120 gleichzeitige
# Modellaufrufe. Das ist kein theoretischer Fall, sondern der Normalbetrieb
# der Weite - dort schicken Fremde Auftraege. Ein Modellaufruf belegt
# Gigabytes; zwei gleichzeitig sind auf einem Geraet schon viel.
MAX_FREMDAUFTRAEGE = 2
# Und einer allein darf nicht alle Plaetze belegen. Sonst genuegt ein
# einziger unfreundlicher Nachbar, um alle anderen auszusperren.
MAX_JE_NACHBAR = 1

RUF_ABSTAND = 5.0          # Sekunden zwischen zwei Anwesenheitsrufen
NACHBAR_VERFALL = 30.0     # ohne Lebenszeichen gilt ein Nachbar als weg
# Wie oft ein Knoten seine Saetze in der Weite nachliefert. Ohne das ist ein
# Inhalt weg, sobald die zwanzig Knoten gegangen sind, die ihn hielten — auch
# wenn seine Verfallszeit noch tagelang laeuft. Das waere ein gebrochenes
# Versprechen: Die Verfallszeit ist die EINZIGE Zusage, die das Mesh gibt.
NACHLIEFERN_ABSTAND = 600.0     # 10 Minuten
# Ein Satz, der ohnehin gleich verfaellt, wird nicht mehr nachgeliefert.
NACHLIEFERN_MINDESTREST = 120.0
# Wie viele Adressen eine Runde hoechstens nachliefert. Reihum, damit ueber
# mehrere Runden trotzdem alles drankommt.
NACHLIEFERN_JE_RUNDE = 50

# ANTWORT-BREMSE — gegen Verstaerkungsangriffe.
#
# UDP prueft den Absender nicht. Eine Kademlia-Antwort ist 539 Byte, die Frage
# 57 — das Neunfache. Wer eine fremde Adresse als Absender einträgt, laesst
# also viele Knoten gleichzeitig mit dem Neunfachen auf sein Opfer eindreschen.
# Ohne Bremse waere jeder Dive-on-Wide-Knoten ein hilfsbereiter Verstaerker.
#
# Die Bremse ist bewusst grob: Sie zaehlt Antworten je Absenderadresse in einem
# Zeitfenster. Ein echter Sucher braucht wenige Fragen (gemessen 28 fuer ein
# Netz mit 20.000 Knoten, ueber mehrere Runden verteilt); wer mehr stellt, ist
# entweder kaputt oder boesartig, und beides verdient dieselbe Antwort: keine.
ANTWORT_FENSTER = 10.0            # Sekunden
ANTWORT_JE_ABSENDER = 20          # Antworten je Adresse in diesem Fenster
ANTWORT_GESAMT = 300              # Obergrenze ueber alle Adressen zusammen


class Nachbar:
    """Was wir über einen anderen Knoten wissen — und wie frisch es ist."""

    __slots__ = ("id", "sig_pub", "dh_pub", "art", "compute", "adresse",
                 "zuletzt", "zuerst", "marken", "modelle", "rpc_port")

    def __init__(self, kern, adresse, jetzt):
        self.id = kern["id"]
        self.sig_pub = kern["sig_pub_bytes"]
        self.dh_pub = kern["dh_pub_bytes"]
        self.art = kern.get("art")
        self.compute = int(kern.get("compute", 0))
        self.adresse = adresse
        self.zuletzt = jetzt
        self.zuerst = jetzt
        self.marken = list(kern.get("treff") or [])
        self.modelle = list(kern.get("modelle") or [])
        self.rpc_port = int(kern.get("rpc") or 0)

    def frisch(self, jetzt=None, verfall=NACHBAR_VERFALL):
        return ((jetzt or time.time()) - self.zuletzt) < verfall

    def __repr__(self):
        return "<Nachbar %s %.1f GB>" % (self.id[:12], self.compute / (1024 ** 3))


class Kontakt:
    """Ein Mensch, mit dem ein Anker getauscht wurde — und der Chat mit ihm.

    ALLES HIER LEBT NUR IM ARBEITSSPEICHER. Es gibt keinen Pfad, der einen
    Anker oder eine Nachricht auf die Platte schreibt. Das heißt auch: Nach
    einem Neustart ist der Kontakt weg und der Anker muss neu eingegeben
    werden. Das ist keine fehlende Bequemlichkeit, sondern die Zusage: Wer
    das Gerät später in die Hand bekommt, findet nichts vor."""

    __slots__ = ("name", "_anker", "nachrichten", "nachbar_id", "zuletzt_gesehen",
                 "sicherheitszahl", "bestaetigt", "ratsche")

    def __init__(self, name, anker_code):
        self.name = name
        # Der Anker liegt in einem loeschbaren Puffer, nicht in einem str:
        # Zeichenketten sind in Python unveraenderlich und lassen sich nicht
        # ueberschreiben.
        self._anker = fluechtig.GeheimBytes(
            anker_mod.anker_normalisieren(anker_code).encode("ascii"))
        self.nachrichten = []
        self.nachbar_id = None
        self.zuletzt_gesehen = None
        self.sicherheitszahl = None
        self.bestaetigt = False
        self.ratsche = None

    def anker(self):
        return self._anker.lesen().decode("ascii")

    def treffpunkte(self):
        return anker_mod.treffpunkte_umfeld(self.anker())

    def online(self, jetzt=None):
        if self.zuletzt_gesehen is None:
            return False
        return ((jetzt or time.time()) - self.zuletzt_gesehen) < NACHBAR_VERFALL

    def vernichten(self):
        """Chat und Anker unwiderruflich weg — der Knopf „auslöschen“.

        Erst die Nachrichten überschreiben, dann den Anker vernichten. Ohne
        Anker gibt es keinen Treffpunkt mehr; die Verbindung existiert danach
        nicht einmal mehr als Adresse."""
        for n in self.nachrichten:
            if isinstance(n.get("text"), bytearray):
                for i in range(len(n["text"])):
                    n["text"][i] = 0
        self.nachrichten.clear()
        if self.ratsche is not None:
            self.ratsche.vernichten()
            self.ratsche = None
        self._anker.vernichten()
        self.nachbar_id = None
        self.bestaetigt = False

    def __repr__(self):
        return "<Kontakt %r %s>" % (self.name, "online" if self.online() else "offline")


class Knoten:
    """Ein Teilnehmer im Mesh."""

    def __init__(self, betriebsart=KLAUSE, netz=None, adresse=None,
                 statthalter=None, anker=None, modelle=None, llm=None):
        if betriebsart not in BETRIEBSARTEN:
            raise ValueError("Unbekannte Betriebsart: %r" % betriebsart)
        self.betriebsart = betriebsart
        self.sitzung = fluechtig.Sitzung()
        self.ich = self.sitzung.fuer("knoten:" + betriebsart)
        self.statthalter = statthalter or ressourcen.Statthalter()
        self.speicher = inhalt.Speicher()
        self.netz = netz or transport.SchleifenNetz()
        self.adresse = adresse or ("knoten", self.ich.knoten_id.hex()[:8])
        self.nachbarn = {}
        self.anker = list(anker or [])
        self._sperre = threading.RLock()
        self._laeuft = False
        self._faden = None
        self.briefe = []                   # empfangene Nachrichten, nur im RAM
        self.kontakte = {}                 # Name -> Kontakt, nur im RAM
        # Was dieser Knoten selbst rechnen kann, und womit.
        # `modelle` ist ein Aufruf, kein fester Wert: Modelle kommen und gehen,
        # waehrend der Knoten laeuft.
        self.modelle_liefern = modelle or (lambda: [])
        self.llm = llm                     # (modell, prompt) -> Text
        self.auftraege = {}                # eigene offene Auftraege, nur im RAM
        self.fremdauftraege = 0            # wie viele wir fuer andere erledigt haben
        self._teile = transport.Zusammensetzer()   # große Briefe kommen in Teilen
        self._rechnet = {}                 # Nachbar-Kennung -> laufende Auftraege
        self.abgewiesen_ueberlast = 0      # wie oft wir wegen Ueberlast nein sagten
        self.auftraege_erlaubt = True
        # Der Port, auf dem dieser Knoten Modellschichten fuer andere haelt.
        # 0 heisst: tut er nicht. BEWUSST AUS. Ein Geraet, das ungefragt
        # Schichten eines fremden Modells traegt, ist fuer seinen Besitzer
        # waehrend der ganzen Sitzung blockiert — das muss man wollen.
        self.rpc_port = 0
        # Kademlia-Wegetabelle. NUR in der Weite: In einer Klause mit zwanzig
        # Geraeten waere sie Aufwand ohne Ertrag — dort findet der Rundruf
        # jeden sofort, und zwar zuverlaessiger. In der Weite ist Rundruf
        # dagegen das Ende: Bei tausend Knoten schickt jede Veroeffentlichung
        # tausend Pakete.
        self.wege = (kademlia.Tabelle(self.knoten_id)
                     if betriebsart == WEITE else None)
        self._offene_fragen = {}          # Vorgangsnummer -> [Ereignis, Antwort]
        self.aussenadresse = None         # wie uns die Welt sieht, wenn bekannt
        self.durchstiche = 0              # wie oft wir fuer andere angeklopft haben
        self.stupse_abgelehnt = 0         # wie oft ein Stups abgewiesen wurde
        self._bremse = {}                 # Adresse -> [Fensterbeginn, Anzahl]
        self._bremse_gesamt = [0.0, 0]    # Fensterbeginn, Anzahl
        self.gebremst = 0                 # wie oft wir eine Antwort verweigert haben
        self._nachliefer_zeiger = 0       # wo die naechste Nachliefer-Runde beginnt
        # Was fremde Rechenknoten bisher geliefert haben. Nur im RAM: Ein
        # Vertrauensurteil, das einen Neustart ueberlebt, waere eine dauerhafte
        # Bewertung von Menschen — und die soll es hier nicht geben.
        self.vertrauen = vertrauen_mod.Buch()
        self.vermittelt = 0               # wie oft wir fuer zwei andere vermittelt haben
        self._fragen_sperre = threading.RLock()
        self.weiterleiten_erlaubt = True   # als Zwischenknoten dienen
        self.weitergeleitet = 0
        self.verworfen = {}                 # Grund -> Anzahl, für die Diagnose
        if hasattr(self.netz, "anmelden"):
            self.netz.anmelden(self.adresse, self._paket_empfangen)
        elif hasattr(self.netz, "empfangen"):
            self.netz.empfangen = self._paket_empfangen

    # -- Selbstauskunft ----------------------------------------------------
    @property
    def knoten_id(self):
        return self.ich.knoten_id

    def treffpunkte(self):
        """Alle Adressen, auf denen dieser Knoten gerade horcht."""
        punkte = []
        for a in self.anker:
            punkte.extend(anker_mod.treffpunkte_umfeld(a))
        with self._sperre:
            for k in self.kontakte.values():
                if not k._anker.vernichtet:
                    punkte.extend(k.treffpunkte())
        return punkte

    def compute_angebot(self):
        """Was dieses Gerät JETZT beisteuert. Fragt jedes Mal neu nach."""
        return self.statthalter.abgebbar()

    # -- Rufen und Horchen -------------------------------------------------
    def rufen(self):
        """Einen Anwesenheitsruf ins Netz geben."""
        paket = transport.ruf_bauen(self.ich, self.betriebsart,
                                    self.compute_angebot(), self.treffpunkte(),
                                    port=getattr(self.netz, "unicast_port", None),
                                    modelle=self.eigene_modelle(),
                                    rpc_port=self.rpc_port)
        return self.netz.rundruf(self.adresse, paket)

    def _verwerfen(self, grund):
        self.verworfen[grund] = self.verworfen.get(grund, 0) + 1

    def _paket_empfangen(self, von, daten, ueber_rundruf=False):
        try:
            typ, rumpf = transport.paket_lesen(daten)
        except transport.Paketfehler as e:
            return self._verwerfen(str(e))
        if typ == transport.TYP_RUF:
            self._ruf_empfangen(von, rumpf, ueber_rundruf)
        elif typ == transport.TYP_BRIEF:
            self._brief_empfangen(von, rumpf)
        elif typ == transport.TYP_SATZ:
            self._satz_empfangen(von, rumpf)
        elif typ == transport.TYP_ZWIEBEL:
            self._zwiebel_empfangen(von, rumpf)
        elif typ == transport.TYP_FRAGE:
            self._frage_empfangen(von, rumpf)
        elif typ == transport.TYP_ANTWORT:
            self._antwort_empfangen(von, rumpf)
        elif typ == transport.TYP_SPIEGEL:
            self._spiegel_empfangen(von, rumpf)
        elif typ == transport.TYP_STUPS:
            self._stups_empfangen(von, rumpf)
        else:
            self._verwerfen("unbekannter Pakettyp")

    def _ruf_empfangen(self, von, rumpf, ueber_rundruf=False):
        ist_neu = False
        try:
            kern = transport.ruf_pruefen(rumpf)
        except transport.Paketfehler as e:
            return self._verwerfen(str(e))
        if kern.get("id") == self.knoten_id.hex():
            return                                  # das eigene Echo
        # Betriebsarten mischen sich NIE. Eine Klause nimmt keinen Knoten aus
        # der Weite auf, auch wenn er technisch erreichbar wäre — sonst wäre
        # die Trennung nur eine Beschriftung.
        if kern.get("art") != self.betriebsart:
            return self._verwerfen("andere Betriebsart")
        # DIE WEITE BETRITT MAN MIT EINEM TICKET — auch von nebenan.
        #
        # Vorher fanden sich zwei fremde Weite-Knoten im selben LAN allein
        # ueber den Rundruf, ohne dass je ein Ticket getauscht wurde. Die
        # Dokumentation versprach das Gegenteil („jeder MIT einem Ticket"),
        # und das Versprechen ist das richtige: Im Cafe oder im Uni-Netz sitzt
        # man mit Fremden im selben Netz, und deren Anwesenheit ist kein
        # Einverstaendnis. Ein Rundruf verriet ausserdem jedem im LAN, dass
        # hier ein Weite-Knoten laeuft.
        #
        # Deshalb: In der Weite zaehlt nur, was ueber den EIGENEN Port kommt.
        # Den kennt, wer ein Ticket eingeloest hat oder von einem bereits
        # bekannten Knoten weitergereicht wurde. In der Klause bleibt der
        # Rundruf genau das, was er sein soll — die Bequemlichkeit im eigenen
        # Netz, das man ohnehin kontrolliert.
        if ueber_rundruf and self.betriebsart == WEITE:
            return self._verwerfen("Rundruf in der Weite — dort öffnet das Ticket")
        with self._sperre:
            vorhanden = self.nachbarn.get(kern["id"])
            jetzt = time.time()
            # Antwortadresse: die IP, aus der das Paket kam, aber der Port,
            # den der Knoten selbst nennt — der Multicast-Port gehoert allen.
            antwort = von
            if kern.get("port") and isinstance(von, tuple) and len(von) == 2:
                antwort = (von[0], int(kern["port"]))
            if vorhanden:
                vorhanden.compute = int(kern.get("compute", 0))
                vorhanden.adresse = antwort
                vorhanden.zuletzt = jetzt
                vorhanden.marken = list(kern.get("treff") or [])
                vorhanden.modelle = list(kern.get("modelle") or [])
                # Auch das Rechenangebot auffrischen. Es fehlte hier zuerst,
                # und das war der Normalfall und nicht der Sonderfall: Das
                # Angebot startet AUS, wird also fast immer erst eingeschaltet,
                # wenn die Nachbarn einen laengst kennen. Ohne diese Zeile
                # bemerkt es niemand jemals.
                vorhanden.rpc_port = int(kern.get("rpc") or 0)
            else:
                self.nachbarn[kern["id"]] = Nachbar(kern, antwort, jetzt)
                ist_neu = True
        # Wer sich meldet, gehoert auch in die Wegetabelle. Das ist der eine
        # Ort, an dem Rundruf und Kademlia zusammenkommen: Der Rundruf bringt
        # die ersten Bekanntschaften, ab da traegt die Tabelle.
        if self.wege is not None and kern.get("port"):
            wirt = antwort[0] if isinstance(antwort, tuple) else str(antwort)
            self.wege.sehen(bytes.fromhex(kern["id"]), (wirt, int(kern["port"])))
        self._kontakte_erkennen(self.nachbarn.get(kern["id"]), jetzt)
        # In der Weite gibt es keinen Rundruf. Wer sich per Ticket meldet,
        # wuerde uns kennen, ohne dass wir ihm antworten — Bekanntschaft muss
        # aber in BEIDE Richtungen gehen, sonst kann er uns nichts schicken.
        # Deshalb: bei einem NEUEN Nachbarn einmal direkt zurueckrufen. Das
        # laeuft nicht endlos, weil wir ihn danach kennen und nicht mehr
        # antworten.
        if ist_neu and self.betriebsart == WEITE:
            try:
                self.rufen_an(antwort)
            except Exception:
                pass
            try:
                self.nachbarn_teilen(kern["id"])
            except Exception:
                pass

    def _kontakte_erkennen(self, nachbar, jetzt):
        """Ist dieser Nachbar einer meiner Kontakte?

        Er ruft blinde Marken aus (siehe transport.treff_marke). Nur wer den
        zugehoerigen Anker kennt, kann nachrechnen — deshalb probieren wir
        unsere eigenen Treffpunkte gegen jede Marke. Fuer einen Beobachter
        ohne Anker bleibt das ununterscheidbarer Zufall."""
        if nachbar is None:
            return
        with self._sperre:
            kontakte = list(self.kontakte.values())
        for k in kontakte:
            if k._anker.vernichtet:
                continue
            treff = k.treffpunkte()
            for nonce_hex, marke_hex in nachbar.marken:
                if any(transport.treff_passt(t, nonce_hex, marke_hex)
                       for t in treff):
                    with self._sperre:
                        k.nachbar_id = nachbar.id
                        k.zuletzt_gesehen = jetzt
                        # Die Zahl zum lauten Vergleichen — schuetzt gegen
                        # einen Dritten, der sich dazwischenschiebt.
                        k.sicherheitszahl = anker_mod.sicherheitszahl(
                            self.ich.sign_oeffentlich, nachbar.sig_pub)
                        if k.ratsche is None:
                            k.ratsche = self._ratsche_aufsetzen(k, nachbar)
                    return

    def _ratsche_aufsetzen(self, kontakt, nachbar):
        """Die Doppelratsche fuer dieses Gespraech.

        Beide Seiten brauchen dieselbe Rollenverteilung, ohne sich abzusprechen:
        Wer den kleineren Signaturschluessel hat, beginnt. Willkuerlich, aber
        auf beiden Geraeten dasselbe Ergebnis — und kostet keine Extrarunde.

        Das gemeinsame Geheimnis kommt aus dem Anker. Der Anker selbst
        verschluesselt danach nie wieder etwas: Er aendert sich nicht und
        haette deshalb keine nachtraegliche Geheimhaltung.""" 
        geheim = anker_mod.paarschluessel(kontakt.anker(), "ratsche")
        return ratsche_mod.Ratsche.paar(
            geheim,
            ich_bin_a=self.ich.sign_oeffentlich < nachbar.sig_pub,
            fremd_dh_pub=nachbar.dh_pub,
            eigen_geheim=self.ich._dh_geheim.lesen(),
            eigen_oeffentlich=self.ich.dh_oeffentlich)

    def _brief_empfangen(self, von, rumpf):
        try:
            absender_dh, klartext = transport.brief_oeffnen(self.ich, rumpf)
        except transport.Paketfehler as e:
            return self._verwerfen(str(e))
        try:
            u = transport.umschlag_lesen(klartext)
        except transport.Paketfehler as e:
            return self._verwerfen(str(e))
        art = u.get("art")
        if art == transport.ART_TEIL:
            try:
                ganz = self._teile.hinzufuegen(absender_dh, u)
                if ganz is None:
                    return None
                u = transport.umschlag_lesen(ganz)
            except transport.Paketfehler as e:
                return self._verwerfen(str(e))
            art = u.get("art")
            if art == transport.ART_TEIL:
                return self._verwerfen("Teil in Teil")
        if art == transport.ART_CHAT:
            self._chat_empfangen(absender_dh, u)
        elif art == transport.ART_AUFTRAG:
            self._auftrag_empfangen(absender_dh, u)
        elif art == transport.ART_ERGEBNIS:
            self._ergebnis_empfangen(absender_dh, u)
        elif art == transport.ART_NACHBARN:
            self._nachbarn_empfangen(u)

    def _chat_empfangen(self, absender_dh, u):
        verpackt = u.get("rr")
        text = u.get("text")
        if not verpackt and (not isinstance(text, str) or not text):
            return self._verwerfen("Chatnachricht ohne Text")
        jetzt = time.time()
        with self._sperre:
            for k in self.kontakte.values():
                n = self.nachbarn.get(k.nachbar_id) if k.nachbar_id else None
                if not (n and crypto.gleich(n.dh_pub, absender_dh)):
                    continue
                if verpackt:
                    if k.ratsche is None:
                        return self._verwerfen("Ratsche fehlt für diesen Kontakt")
                    try:
                        roh = k.ratsche.empfangen(base64.b64decode(verpackt))
                    except Exception:
                        # Nicht entschluesselbar: verwerfen statt Rohbytes
                        # anzuzeigen. Eine unlesbare Nachricht ist keine.
                        return self._verwerfen("Ratsche passt nicht")
                else:
                    roh = text.encode("utf-8")
                self.briefe.append({"von": absender_dh.hex()[:16],
                                    "text": roh, "zeit": jetzt})
                k.nachrichten.append({"richtung": "ein",
                                      "text": bytearray(roh), "zeit": jetzt})
                k.zuletzt_gesehen = jetzt
                return
            # Kein Kontakt dazu — nur wenn es Klartext ist, ueberhaupt ablegen.
            if not verpackt:
                self.briefe.append({"von": absender_dh.hex()[:16],
                                    "text": text.encode("utf-8"), "zeit": jetzt})

    def _satz_empfangen(self, von, rumpf):
        """Fremden Forumssatz aufnehmen und EINMAL weitertragen.

        Das Weitertragen nur bei NEUEN Saetzen ist der ganze Trick: Sonst
        wuerde jeder Satz endlos im Netz kreisen. `Speicher.legen` gibt False
        zurueck, wenn er den Satz schon kennt — damit endet die Welle von
        selbst, ohne dass jemand mitzaehlen muss."""
        try:
            d = transport.satz_lesen(rumpf)
        except inhalt.UngueltigerDatensatz as e:
            return self._verwerfen(str(e))
        try:
            neu = self.speicher.legen(d)
        except inhalt.UngueltigerDatensatz as e:
            return self._verwerfen(str(e))
        if neu and self.wege is None:
            # NUR in der Klause weiterreichen. Dort ist das Weitersagen der
            # Verbreitungsweg: zwanzig Geraete, ein Paket, jeder hat es.
            #
            # In der Weite waere genau das die Katastrophe, die Kademlia
            # verhindern soll: Jeder Knoten, der einen Satz bekommt, wuerde ihn
            # an alle weiterschicken, die ihn wieder an alle schicken. Dort hat
            # der Veroeffentlichende den Satz bereits gezielt zu den K
            # naechsten Knoten getragen — mehr soll und darf nicht geschehen.
            self.netz.rundruf(self.adresse, transport.satz_bauen(d))

    # -- Forum -------------------------------------------------------------
    def _forum_ich(self, faden_id):
        """Eigene Identitaet NUR fuer diesen Faden.

        Zwei Beitraege desselben Menschen in zwei Faeden sind damit nicht als
        derselbe Mensch erkennbar — auch nicht fuer uns selbst im Nachhinein."""
        return self.sitzung.fuer("forum:" + faden_id)

    def forum_raum(self, raum="allgemein"):
        return forum_mod.Raum(self.speicher, raum)

    def faden_eroeffnen(self, titel, text, raum="allgemein",
                        ttl=forum_mod.STANDARD_TTL):
        # Der Eroeffnungssatz wird mit einer Identitaet fuer den Raum
        # signiert; die Faden-Kennung entsteht erst aus seinem Inhalt.
        ich = self.sitzung.fuer("forum-raum:" + raum)
        d = forum_mod.faden_eroeffnen(ich, titel, text, raum, ttl)
        self.speicher.legen(d)
        self.veroeffentlichen(d)
        return forum_mod.faden_id_von(d)

    def veroeffentlichen(self, datensatz, frist_je_frage=2.0):
        """Einen Datensatz ins Netz bringen — auf dem Weg, der zur Betriebsart passt.

        **Klause: Rundruf.** Zwanzig Geräte im selben LAN, ein Paket, jeder hat
        es. Einfacher und zuverlässiger geht es nicht.

        **Weite: Kademlia.** Hier wäre Rundruf das Ende — bei tausend Knoten
        tausend Pakete je Veröffentlichung, und jeder müsste alles speichern.
        Stattdessen wird der Satz zu den K Knoten getragen, deren Kennung
        seiner Adresse am nächsten liegt. Wer ihn sucht, läuft denselben Weg
        und landet bei denselben Knoten.

        Gibt zurück, an wie viele Knoten er wirklich ging. Null heißt: Es hat
        niemanden erreicht — das ist eine Auskunft, keine Ausnahme, denn ein
        frisch beigetretener Knoten kennt naturgemäß noch niemanden."""
        paket = transport.satz_bauen(datensatz)
        if self.wege is None:
            self.netz.rundruf(self.adresse, paket)
            return -1                       # Rundruf: Zahl unbekannt, das ist ok
        ziele = self.naechste_knoten(datensatz.schluessel, frist_je_frage)
        erreicht = 0
        for k in ziele[:kademlia.K]:
            try:
                if self.netz.senden(self.adresse, k.adresse, paket):
                    erreicht += 1
            except Exception:
                continue
        return erreicht

    def beitrag_schreiben(self, faden_id, text, ttl=forum_mod.STANDARD_TTL):
        d = forum_mod.beitrag_schreiben(self._forum_ich(faden_id), faden_id, text, ttl)
        self.speicher.legen(d)
        self.veroeffentlichen(d)
        return True

    def faden_schliessen(self, faden_id, raum="allgemein",
                         ttl=forum_mod.STANDARD_TTL):
        """Nur der Eroeffner kann schliessen — er allein hat den Schluessel."""
        ich = self.sitzung.fuer("forum-raum:" + raum)
        d = forum_mod.schluss_setzen(ich, faden_id, ttl)
        self.speicher.legen(d)
        self.veroeffentlichen(d)
        return True

    # -- Briefe ------------------------------------------------------------
    def brief_senden(self, nachbar_id, nutzlast):
        """Versiegelte Nutzlast (bereits im Umschlag) an einen Nachbarn."""
        with self._sperre:
            n = self.nachbarn.get(nachbar_id)
        if n is None:
            return False
        return self._pakete_senden(n.adresse, transport.briefe_bauen(self.ich, n.dh_pub, nutzlast))

    def _pakete_senden(self, adresse, pakete):
        """Alle Teile eines Briefs. Kleine Pause dazwischen: Windows nimmt per UDP standardmäßig nur 64 KB auf
        einmal an — ein Schwall aus Teilen liefe dort über."""
        ok = True
        for i, paket in enumerate(pakete):
            if i:
                time.sleep(0.004)
            ok = bool(self.netz.senden(self.adresse, adresse, paket)) and ok
        return ok

    # -- Modelle und Auftraege ---------------------------------------------
    def eigene_modelle(self):
        """Welche Modelle kann dieses Gerät selbst fahren?"""
        if not self.auftraege_erlaubt:
            return []
        try:
            return [str(m) for m in (self.modelle_liefern() or [])]
        except Exception:
            return []

    def modell_karte(self):
        """Wo im Netz liegt welches Modell? Grundlage jeder Verteilung."""
        jetzt = time.time()
        karte = {}
        for m in self.eigene_modelle():
            karte.setdefault(m, {"hier": True, "knoten": []})
            karte[m]["hier"] = True
        with self._sperre:
            for n in self.nachbarn.values():
                if not n.frisch(jetzt):
                    continue
                for m in n.modelle:
                    e = karte.setdefault(m, {"hier": False, "knoten": []})
                    e["knoten"].append(n.id)
        return karte

    def _auftrag_empfangen(self, absender_dh, u):
        """Fremden Auftrag ausfuehren — wenn wir das ueberhaupt wollen.

        Das ist die Stelle, an der ein Knoten fremde Rechenlast annimmt.
        Deshalb steht die Erlaubnis am Anfang und nicht am Ende: Wer nicht
        beitragen will, rechnet nicht, auch wenn der Auftrag gueltig ist."""
        auf_id = u.get("id")
        modell = u.get("modell")
        prompt = u.get("prompt")
        if not (isinstance(auf_id, str) and isinstance(modell, str)
                and isinstance(prompt, str) and prompt.strip()):
            return self._verwerfen("Auftrag unvollständig")
        if not self.auftraege_erlaubt or self.llm is None:
            return self._antwort_senden(absender_dh, auf_id, None,
                                        "Dieser Knoten nimmt keine Aufträge an.")
        if modell not in self.eigene_modelle():
            return self._antwort_senden(absender_dh, auf_id, None,
                                        "Modell %r ist hier nicht vorhanden." % modell)
        wer = absender_dh.hex()[:12]
        with self._sperre:
            gesamt = sum(self._rechnet.values())
            seine = self._rechnet.get(wer, 0)
            if gesamt >= MAX_FREMDAUFTRAEGE or seine >= MAX_JE_NACHBAR:
                self.abgewiesen_ueberlast += 1
                voll = True
            else:
                self._rechnet[wer] = seine + 1
                voll = False
        if voll:
            # Absagen statt anstauen: Der Auftraggeber erfaehrt es sofort und
            # kann einen anderen Knoten fragen. Eine Warteschlange waere
            # dasselbe Leck, nur langsamer.
            return self._antwort_senden(
                absender_dh, auf_id, None,
                "Dieser Knoten rechnet gerade schon %d Auftrag/Auftraege "
                "(hoechstens %d, je Nachbar %d). Frag es spaeter noch einmal "
                "oder nimm einen anderen Knoten."
                % (gesamt, MAX_FREMDAUFTRAEGE, MAX_JE_NACHBAR))
        threading.Thread(target=self._auftrag_rechnen,
                         args=(absender_dh, auf_id, modell, prompt, wer),
                         daemon=True).start()

    def _auftrag_rechnen(self, absender_dh, auf_id, modell, prompt, wer=None):
        try:
            text = self.llm(modell, prompt)
            self._antwort_senden(absender_dh, auf_id, text, None)
            with self._sperre:
                self.fremdauftraege += 1
        except Exception as e:
            # Auch der Fehlerweg kann scheitern — und tat es: Wurde der Knoten
            # waehrend der Rechnung beendet, warf schon das Absenden. Die
            # zweite Ausnahme fing niemand mehr, und der Thread starb mit
            # einem Rueckverfolgungsprotokoll auf der Fehlerausgabe. „Der
            # Laptop klappt zu, waehrend ein Auftrag laeuft" ist kein
            # Sonderfall, sondern der Normalfall eines Netzes aus Geraeten.
            try:
                self._antwort_senden(absender_dh, auf_id, None, str(e)[:300])
            except Exception:
                pass
        finally:
            # Der Platz muss auf JEDEM Weg wieder frei werden, auch wenn das
            # Modell wirft oder der Knoten mitten im Rechnen beendet wird.
            if wer is not None:
                with self._sperre:
                    rest = self._rechnet.get(wer, 1) - 1
                    if rest > 0:
                        self._rechnet[wer] = rest
                    else:
                        self._rechnet.pop(wer, None)

    def _antwort_senden(self, absender_dh, auf_id, text, fehler):
        """Ergebnis an den Auftraggeber — nur er kann es lesen.

        Adressiert wird ueber den Einigungsschluessel aus dem Auftrag, nicht
        ueber eine Knoten-ID: So braucht der Rechenknoten den Auftraggeber
        nicht zu kennen, um ihm zu antworten."""
        with self._sperre:
            ziel = None
            for n in self.nachbarn.values():
                if crypto.gleich(n.dh_pub, absender_dh):
                    ziel = n
                    break
        if ziel is None:
            return False
        nutz = transport.umschlag(transport.ART_ERGEBNIS, id=auf_id,
                                  text=text, fehler=fehler,
                                  zeit=int(time.time()))
        try:
            pakete = transport.briefe_bauen(self.ich, absender_dh, nutz)
        except transport.Paketfehler as e:
            # Zu groß selbst in Teilen: Das sagen, statt still zu schweigen.
            if text is None:
                return False
            return self._antwort_senden(absender_dh, auf_id, None, "Antwort nicht zustellbar: %s" % e)
        except ValueError:
            # Die Sitzungsidentitaet ist vernichtet: Der Knoten wurde beendet,
            # waehrend diese Antwort entstand. Das ist ein geordnetes Ende und
            # kein Fehler — die Gegenseite merkt das Ausbleiben selbst
            # (`auftrag_lage` meldet dann „vergeblich"). Still zurueckgeben
            # statt aus einem Hintergrund-Thread zu werfen.
            return False
        return self._pakete_senden(ziel.adresse, pakete)

    def _ergebnis_empfangen(self, absender_dh, u):
        auf_id = u.get("id")
        with self._sperre:
            a = self.auftraege.get(auf_id)
            if a is None:
                return
            frage = a.get("prompt", "")
        von = absender_dh.hex()[:12]
        # PRUEFEN, nicht nur sammeln. Vorher wurde jedes Ergebnis unbesehen
        # angenommen — der Kommentar bei `auftrag_verteilen` nannte den
        # Vergleich zwar „die einzige Handhabe gegen absichtlich falsche
        # Ergebnisse", aber verglichen hat nie jemand.
        gut, grund = True, ""
        if not u.get("fehler"):
            gut, grund = self.vertrauen.antwort_pruefen(von, frage, u.get("text"))
        with self._sperre:
            a = self.auftraege.get(auf_id)
            if a is None:
                return
            a["ergebnisse"].append({
                "von": von,
                "text": u.get("text"),
                "fehler": u.get("fehler"),
                "beanstandet": (not gut),
                "grund": ("" if gut else grund),
                "zeit": time.time(),
            })
            a["offen"] = max(0, a["offen"] - 1)
            # Sind alle da, koennen Ausreisser bestimmt werden — das geht erst
            # im Vergleich und deshalb erst jetzt.
            if a["offen"] == 0:
                self._ausreisser_vermerken(a)

    def _ausreisser_vermerken(self, auftrag):
        """Welche Antwort fällt aus der Reihe? Hinweis, kein Beweis.

        Muss unter der Sperre laufen und tut das auch — der Aufrufer hält sie
        bereits."""
        brauchbar = [e for e in auftrag["ergebnisse"] if not e.get("fehler")]
        if len(brauchbar) < 3:
            return                      # bei zweien sagt Uneinigkeit nichts
        indizes = vertrauen_mod.ausreisser([e["text"] for e in brauchbar])
        for i in indizes:
            brauchbar[i]["ausreisser"] = True
            akte = self.vertrauen.akte(brauchbar[i]["von"])
            akte.ausreisser += 1

    def auftrag_verteilen(self, prompt, modell, hoechstens=3):
        """Denselben Auftrag an mehrere Knoten geben, die das Modell haben.

        Mehrfach zu fragen ist kein Luxus: Ein Knoten kann jederzeit
        verschwinden, und bei fremden Knoten ist ein Vergleich zweier
        Antworten die einzige Handhabe gegen absichtlich falsche Ergebnisse
        (byzantinische Fehler, siehe ARCHITEKTUR.md 1.6)."""
        jetzt = time.time()
        with self._sperre:
            faehig = [n for n in self.nachbarn.values()
                      if n.frisch(jetzt) and modell in n.modelle]
        # Wer wiederholt beanstandet wurde, bekommt nichts mehr. Ohne diesen
        # Schritt waere die ganze Buchfuehrung Zierrat: Man wuesste, wer nicht
        # rechnet, und fragte ihn trotzdem weiter.
        geeignet = [n for n in faehig if self.vertrauen.taugt(n.id[:12])]
        gesperrt = len(faehig) - len(geeignet)
        faehig = geeignet[:max(1, int(hoechstens))]
        if not faehig:
            if gesperrt:
                raise ValueError(
                    "Alle %d Knoten mit %r sind als unzuverlässig vermerkt. "
                    "Unter Netzwerk → Vertrauen steht, warum."
                    % (gesperrt, modell))
            raise ValueError("Kein Knoten im Netz hat %r." % modell)
        auf_id = crypto.zufall(12).hex()
        with self._sperre:
            self.auftraege[auf_id] = {"id": auf_id, "prompt": prompt,
                                      "modell": modell, "gestartet": jetzt,
                                      "offen": len(faehig), "gefragt": len(faehig),
                                      # WEN wir gefragt haben, nicht nur wie viele:
                                      # Sonst laesst sich spaeter nicht sagen, ob ein
                                      # offener Auftrag noch auf jemanden wartet oder
                                      # auf einen Knoten, den es nicht mehr gibt.
                                      "gefragt_ids": [n.id for n in faehig],
                                      "ergebnisse": []}
        nutz = transport.umschlag(transport.ART_AUFTRAG, id=auf_id,
                                  modell=modell, prompt=prompt,
                                  zeit=int(jetzt))
        verschickt = 0
        erreicht = []
        for n in faehig:
            if self.brief_senden(n.id, nutz):
                verschickt += 1
                erreicht.append(n.id)
        if not verschickt:
            with self._sperre:
                self.auftraege.pop(auf_id, None)
            raise ValueError("Der Auftrag konnte nicht zugestellt werden.")
        with self._sperre:
            self.auftraege[auf_id]["gefragt"] = verschickt
            self.auftraege[auf_id]["offen"] = verschickt
            self.auftraege[auf_id]["gefragt_ids"] = erreicht
        return auf_id

    def auftrag_lage(self, auf_id):
        """Stand eines Auftrags — samt der Frage, ob ueberhaupt noch jemand da ist.

        Ein Knoten kann jederzeit verschwinden: Ein Laptop klappt zu, ein WLAN
        bricht weg. Vorher stand ein solcher Auftrag unbegrenzt auf „offen“,
        und nichts an der Lage verriet, dass keine Antwort mehr kommen wird —
        wer darauf wartete, wartete fuer immer (gemessen am 23.09.2026 mit
        `werkzeuge/mesh_ausfall.py`). Deshalb wird hier nachgesehen, ob die
        Gefragten noch Lebenszeichen geben.

        `verstummt` zaehlt die, die nicht geantwortet haben und weg sind.
        `vergeblich` sagt geradeheraus: Von den noch Offenen kommt nichts mehr."""
        jetzt = time.time()
        with self._sperre:
            a = self.auftraege.get(auf_id)
            if a is None:
                return None
            geantwortet = {e.get("von") for e in a["ergebnisse"]}
            offene_ids = [nid for nid in a.get("gefragt_ids", ())
                          if nid[:12] not in geantwortet]
            verstummt = [nid for nid in offene_ids
                         if nid not in self.nachbarn
                         or not self.nachbarn[nid].frisch(jetzt)]
            return {"id": a["id"], "modell": a["modell"], "prompt": a["prompt"],
                    "gefragt": a["gefragt"], "offen": a["offen"],
                    "wartet_s": int(jetzt - a["gestartet"]),
                    "verstummt": len(verstummt),
                    "vergeblich": bool(offene_ids) and len(verstummt) == len(offene_ids),
                    "ergebnisse": list(a["ergebnisse"]),
                    "beanstandet": sum(1 for e in a["ergebnisse"]
                                       if e.get("beanstandet"))}

    def auftrag_vergessen(self, auf_id):
        with self._sperre:
            return self.auftraege.pop(auf_id, None) is not None

    # -- Zwiebel-Routing ---------------------------------------------------
    def _zwiebel_empfangen(self, von, rumpf):
        """Eigene Schicht oeffnen: weiterleiten oder selbst auspacken.

        Wichtig: Wir erfahren dabei NICHT, wer der Absender ist und, wenn wir
        weiterleiten, auch nicht, was drin steht. Genau das ist der Zweck."""
        try:
            weiter, rest = transport.zwiebel_schaelen(self.ich, rumpf)
        except transport.Paketfehler as e:
            return self._verwerfen(str(e))
        if weiter is None:
            # Wir sind das Ziel. Das Innere ist ein vollstaendiges Paket.
            return self._paket_empfangen(von, rest)
        if not self.weiterleiten_erlaubt:
            return self._verwerfen("Weiterleitung ist abgeschaltet")
        if self.netz.senden(self.adresse, weiter, rest):
            with self._sperre:
                self.weitergeleitet += 1
            return True
        return self._verwerfen("Weiterleitung fehlgeschlagen")

    def pfad_waehlen(self, ziel_id, laenge=2):
        """Zwischenknoten fuer einen Weg zum Ziel aussuchen.

        Zufaellig aus den frischen Nachbarn, ohne das Ziel selbst. Wenige
        Knoten heissen wenig Schutz — deshalb sagt `zwiebel_moeglich` ehrlich,
        wie lang der Weg wirklich werden kann, statt Sicherheit vorzutaeuschen."""
        jetzt = time.time()
        with self._sperre:
            ziel = self.nachbarn.get(ziel_id)
            frei = [n for n in self.nachbarn.values()
                    if n.frisch(jetzt) and n.id != ziel_id
                    and isinstance(n.adresse, tuple) and len(n.adresse) == 2]
        if ziel is None:
            raise ValueError("Das Ziel ist gerade nicht erreichbar.")
        laenge = max(0, min(int(laenge), len(frei), 4))
        gewaehlt = []
        rest = list(frei)
        for _ in range(laenge):
            i = int.from_bytes(crypto.zufall(2), "big") % len(rest)
            gewaehlt.append(rest.pop(i))
            if not rest:
                break
        # Pfad von aussen nach innen: jeder Eintrag zeigt auf den NAECHSTEN.
        kette = gewaehlt + [ziel]
        pfad = []
        for i, n in enumerate(kette):
            weiter = kette[i + 1].adresse if i + 1 < len(kette) else None
            pfad.append((n.dh_pub, weiter))
        return kette[0].adresse, pfad, len(gewaehlt)

    def zwiebel_moeglich(self):
        """Wie viele Zwischenknoten stehen ueberhaupt zur Verfuegung?"""
        jetzt = time.time()
        with self._sperre:
            frisch = sum(1 for n in self.nachbarn.values() if n.frisch(jetzt))
        # Ein Zwischenknoten braucht neben sich noch das Ziel.
        return max(0, frisch - 1)

    def brief_verschleiert(self, nachbar_id, nutzlast, umwege=2):
        """Wie brief_senden, aber ueber Zwischenknoten.

        Das aeussere Paket geht an den ersten Zwischenknoten; erst der letzte
        kennt das Ziel. Bei zu wenigen Nachbarn wird der Weg kuerzer — und
        die Rueckgabe sagt, wie kurz, damit die Oberflaeche nicht mehr
        verspricht als gilt."""
        with self._sperre:
            n = self.nachbarn.get(nachbar_id)
        if n is None:
            raise ValueError("Das Ziel ist gerade nicht erreichbar.")
        innen = transport.brief_bauen(self.ich, n.dh_pub, nutzlast)
        erster, pfad, umwege_echt = self.pfad_waehlen(nachbar_id, umwege)
        paket = transport.zwiebel_bauen(self.ich, pfad, innen)
        if len(paket) > transport.MAX_PAKET:
            raise ValueError("Die Nachricht ist fuer %d Umwege zu lang."
                             % umwege_echt)
        geschickt = bool(self.netz.senden(self.adresse, erster, paket))
        return {"gesendet": geschickt, "umwege": umwege_echt,
                "gewuenscht": int(umwege), "groesse": len(paket)}

    # -- Tickets: der Weg in die Weite -------------------------------------
    def ticket_erzeugen(self, host=None, notiz=""):
        """Ein Einladungsticket auf diesen Knoten.

        Braucht eine von aussen erreichbare Adresse. Die koennen wir nicht
        erraten — hinter einem NAT ist die eigene IP nicht die, die andere
        sehen. Deshalb wird sie uebergeben oder aus dem LAN geschaetzt, und
        der Nutzer bekommt gesagt, dass er sie pruefen muss."""
        port = getattr(self.netz, "unicast_port", None)
        if not port:
            raise ValueError("Dieser Knoten hat keinen Empfangsport — "
                             "laeuft das Netzwerk?")
        if not host:
            # In der WEITE zaehlt die Adresse, unter der uns die WELT sieht —
            # die LAN-Adresse steht auf keinem Router der Welt. Wenn wir sie
            # von Nachbarn erfragen konnten, ist sie die richtige Wahl; sonst
            # bleibt die LAN-Adresse als ehrlicher Notnagel, und der Text unten
            # sagt, dass sie zu pruefen ist.
            if self.betriebsart == WEITE:
                aussen = self.aussenadresse
                if aussen is None:
                    lage = self.aussen_erfragen()
                    aussen = lage and lage.get("adresse")
                if aussen and transport.adresse_oeffentlich(aussen[0]):
                    host, port = aussen[0], aussen[1]
            if not host:
                adressen = transport.eigene_adressen()
                if not adressen:
                    raise ValueError("Keine Netzadresse gefunden. Bitte die "
                                     "erreichbare Adresse selbst angeben.")
                host = adressen[0]
        return ticket_mod.erzeugen(self.ich, self.betriebsart, host, port, notiz)

    def ticket_einloesen(self, text):
        """Ein fremdes Ticket annehmen und den Knoten direkt ansprechen.

        Multicast hilft hier nicht — der andere ist nicht im eigenen Netz.
        Also wird ihm der eigene Ruf direkt geschickt; er antwortet mit
        seinem, und ab dann laeuft alles wie bei einem Nachbarn."""
        t = ticket_mod.lesen(text)
        if t["art"] != self.betriebsart:
            raise ticket_mod.UngueltigesTicket(
                "Dieses Ticket gehoert zur Betriebsart „%s“, dieser Knoten "
                "laeuft als „%s“. Betriebsarten mischen sich nicht — dafuer "
                "braucht es einen Neustart mit der anderen Wahl."
                % (t["art"], self.betriebsart))
        ziel = (t["host"], t["port"])
        self.rufen_an(ziel)
        return {"knoten_id": t["knoten_id"], "ziel": "%s:%d" % ziel,
                "notiz": t.get("notiz", "")}

    def rufen_an(self, ziel):
        """Den eigenen Ruf gezielt an EINE Adresse schicken."""
        paket = transport.ruf_bauen(
            self.ich, self.betriebsart, self.compute_angebot(),
            self.treffpunkte(),
            port=getattr(self.netz, "unicast_port", None),
            modelle=self.eigene_modelle())
        return bool(self.netz.senden(self.adresse, ziel, paket))

    # -- Nachbarn weitergeben: ein Ticket fuehrt ins ganze Netz ------------
    def nachbarn_teilen(self, nachbar_id, hoechstens=8):
        """Dem Neuen erzaehlen, wen wir kennen.

        Das ist der Grund, warum EIN Ticket genuegt: Der Neue lernt vom
        ersten Kontakt aus das ganze Netz kennen, ohne dass irgendwo eine
        Liste liegt. Weitergegeben werden nur Adresse und Port — keine
        Schluessel, keine Kontakte, keine Treffpunkte. Wer schon da ist,
        wird durch diese Bekanntschaft nicht mehr preisgegeben, als er im
        eigenen Netz ohnehin ausruft."""
        jetzt = time.time()
        with self._sperre:
            liste = []
            for n in self.nachbarn.values():
                if n.id == nachbar_id or not n.frisch(jetzt):
                    continue
                if isinstance(n.adresse, tuple) and len(n.adresse) == 2 \
                        and isinstance(n.adresse[1], int):
                    liste.append([str(n.adresse[0]), int(n.adresse[1])])
                if len(liste) >= hoechstens:
                    break
        if not liste:
            return False
        nutz = transport.umschlag(transport.ART_NACHBARN, wer=liste,
                                  zeit=int(jetzt))
        return self.brief_senden(nachbar_id, nutz)

    def _nachbarn_empfangen(self, u):
        """Fremde Nachbarliste: jeden einmal anrufen, dann selbst entscheiden.

        Bewusst ohne Vertrauen: Wir uebernehmen die Adressen NICHT in unsere
        Nachbarschaft. Wir schicken nur einen Ruf hin. Wer wirklich existiert,
        antwortet mit einem signierten Ruf und wird dadurch Nachbar — wer
        erfunden war, bleibt es. So kann uns niemand eine Nachbarschaft
        andichten."""
        wer = u.get("wer")
        if not isinstance(wer, list):
            return self._verwerfen("Nachbarliste ist keine Liste")
        genommen = 0
        for eintrag in wer[:16]:
            if not (isinstance(eintrag, list) and len(eintrag) == 2):
                continue
            host, port = eintrag
            if not (isinstance(host, str) and isinstance(port, int)):
                continue
            if not (1 <= port <= 65535) or len(host) > 100:
                continue
            try:
                self.rufen_an((host, port))
                genommen += 1
            except Exception:
                continue
        return genommen

    # -- Betrieb -----------------------------------------------------------
    def starten(self):
        if hasattr(self.netz, "starten") and not self.netz.starten():
            return False
        self._laeuft = True
        self._faden = threading.Thread(target=self._schleife, daemon=True)
        self._faden.start()
        return True

    def _schleife(self):
        naechstes_nachliefern = time.time() + NACHLIEFERN_ABSTAND
        while self._laeuft:
            try:
                self.rufen()
                self.aufraeumen()
                self.statthalter.nachpruefen()
                # Nachliefern ist teuer (je Satz eine Suche), deshalb selten
                # und nur in der Weite. Der Zeitpunkt wird gemerkt, statt einen
                # Zaehler mitzufuehren: Ein Zaehler haette bei geaendertem
                # Rufabstand still eine andere Bedeutung bekommen.
                if self.wege is not None and time.time() >= naechstes_nachliefern:
                    naechstes_nachliefern = time.time() + NACHLIEFERN_ABSTAND
                    self.nachliefern()
            except Exception:
                pass          # ein Knoten stirbt nie an einem einzelnen Fehler
            for _ in range(int(RUF_ABSTAND * 10)):
                if not self._laeuft:
                    return
                time.sleep(0.1)

    def nachliefern(self, jetzt=None, frist_je_frage=1.5):
        """Eigene und fremde Sätze erneut zu den nächsten Knoten tragen.

        Kademlias unauffälligster, aber unverzichtbarer Teil. Ein Satz liegt
        bei den zwanzig Knoten, die seiner Adresse am nächsten sind — und die
        gehen. Rechner werden zugeklappt, Netze gewechselt. Ohne Nachliefern
        ist der Satz nach ein paar Stunden verschwunden, obwohl seine
        Verfallszeit noch tagelang läuft. Und die Verfallszeit ist die einzige
        Zusage, die dieses Mesh überhaupt gibt.

        Nachgeliefert wird auch, was man selbst nur *aufbewahrt*: Wer einen
        fremden Satz hält, ist für ihn mitverantwortlich, solange er lebt.
        Sonst hinge alles am Ursprungsknoten, und der ist irgendwann weg.

        Gibt zurück, wie viele Sätze in dieser Runde weitergetragen wurden."""
        if self.wege is None:
            return 0                    # in der Klause erledigt das der Rundruf
        jetzt = jetzt or time.time()
        getragen = 0
        # DECKEL. Jedes Nachliefern ist eine vollstaendige Kademlia-Suche plus
        # bis zu K Sendungen. Der Speicher fasst 10.000 Saetze — ohne Grenze
        # wuerde ein voller Knoten alle zehn Minuten ein Paketgewitter
        # ausloesen und waere selbst die Ursache der Last, gegen die Kademlia
        # antritt. Was diesmal nicht drankommt, kommt in der naechsten Runde;
        # deshalb wird REIHUM begonnen und nicht immer vorn.
        alle = self.speicher.schluessel()
        if not alle:
            return 0
        beginn = self._nachliefer_zeiger % len(alle)
        reihe = alle[beginn:] + alle[:beginn]
        for adresse in reihe[:NACHLIEFERN_JE_RUNDE]:
            for d in self.speicher.holen(adresse):
                if d.ablauf - jetzt < NACHLIEFERN_MINDESTREST:
                    continue            # verfaellt ohnehin gleich
                try:
                    if self.veroeffentlichen(d, frist_je_frage) > 0:
                        getragen += 1
                except Exception:
                    continue            # ein Satz darf die Runde nicht kippen
        self._nachliefer_zeiger = beginn + NACHLIEFERN_JE_RUNDE
        return getragen

    def aufraeumen(self, jetzt=None):
        """Nachbarn ohne Lebenszeichen vergessen."""
        jetzt = jetzt or time.time()
        with self._sperre:
            weg = [k for k, n in self.nachbarn.items() if not n.frisch(jetzt)]
            for k in weg:
                del self.nachbarn[k]
        return len(weg)

    def stoppen(self):
        """Sitzungsende: Netz zu, Schlüssel vernichtet, Speicher geleert."""
        self._laeuft = False
        if hasattr(self.netz, "stoppen"):
            self.netz.stoppen()
        if hasattr(self.netz, "abmelden"):
            self.netz.abmelden(self.adresse)
        self.statthalter.anhalten("Knoten wird beendet")
        self.speicher.schliessen()
        with self._sperre:
            self.briefe.clear()
            self.auftraege.clear()
            for k in list(self.kontakte.values()):
                k.vernichten()
            self.kontakte.clear()
        self.sitzung.schliessen()

    def __enter__(self):
        self.starten()
        return self

    def __exit__(self, *_):
        self.stoppen()
        return False

    # -- Kontakte und Chat -------------------------------------------------
    def kontakt_anlegen(self, name, anker_code):
        """Einen Anker eintragen. Wirft mit klarer Meldung, wenn er nicht passt."""
        name = (name or "").strip()
        if not name:
            raise ValueError("Der Kontakt braucht einen Namen.")
        with self._sperre:
            alt = self.kontakte.get(name)
            if alt is not None:
                # DER ALLTAGSFALL: Man hat sich beim Abtippen vertan, der
                # Kontakt bleibt für immer „offline", und der zweite, richtige
                # Versuch lief gegen „Es gibt schon einen Kontakt namens X" —
                # ohne einen Hinweis, wie es weitergeht. Eine Sackgasse aus
                # einem Tippfehler.
                #
                # Wurde der Kontakt NIE erkannt und hat keine Nachrichten, ist
                # nichts zu verlieren: Dann ist der neue Anker offensichtlich
                # das, was gemeint war. Steht die Verbindung dagegen bereits,
                # wäre stilles Überschreiben gefährlich — ein anderer Anker
                # heißt ein anderer Mensch, und genau davor schützt der Anker.
                if alt.online() or alt.nachrichten:
                    raise ValueError(
                        "%r ist bereits verbunden. Einen bestehenden Kontakt "
                        "überschreibt Dive on Wide nicht: Ein anderer Anker bedeutet "
                        "einen anderen Menschen. Wenn du das wirklich willst, "
                        "erst löschen, dann neu anlegen." % name)
                neuer = Kontakt(name, anker_code)   # prüft den Anker zuerst
                alt.vernichten()
                self.kontakte[name] = neuer
                return neuer
            k = Kontakt(name, anker_code)      # prüft den Anker beim Anlegen
            self.kontakte[name] = k
        return k

    def kontakt_loeschen(self, name):
        """Auslöschen: Chat und Anker weg, die Verbindung existiert nicht mehr."""
        with self._sperre:
            k = self.kontakte.pop(name, None)
        if k:
            k.vernichten()
        return k is not None

    def kontakt_bestaetigen(self, name):
        """Der Mensch hat die Sicherheitszahl verglichen — sie stimmt."""
        with self._sperre:
            k = self.kontakte.get(name)
            if not k or not k.sicherheitszahl:
                return False
            k.bestaetigt = True
            return True

    def nachricht_senden(self, name, text, umwege=0):
        """Versiegelte Nachricht an einen Kontakt.

        Mit `umwege` laeuft sie ueber Zwischenknoten: Der Inhalt ist so oder
        so nur fuer den Empfaenger lesbar, aber bei Umwegen sieht auch kein
        Nachbar mehr, DASS wir mit diesem Kontakt sprechen."""
        with self._sperre:
            k = self.kontakte.get(name)
        if k is None:
            raise ValueError("Kein Kontakt namens %r." % name)
        if k._anker.vernichtet:
            raise ValueError("Dieser Kontakt wurde ausgelöscht.")
        if not k.nachbar_id:
            raise ValueError("%s ist gerade nicht erreichbar." % name)
        roh = text if isinstance(text, str) else bytes(text).decode("utf-8", "replace")
        if k.ratsche is not None:
            # Durch die Doppelratsche: jede Nachricht ihr eigener Schluessel.
            # Der aeussere Umschlag adressiert nur — was drinsteht, ist auch
            # dann nicht lesbar, wenn der statische Schluessel spaeter auffliegt.
            try:
                paket = k.ratsche.senden(roh.encode("utf-8"))
            except ratsche_mod.RatschenFehler:
                raise ValueError(
                    "%s muss zuerst schreiben — dieses Gespräch beginnt von "
                    "der anderen Seite." % name)
            nutz = transport.umschlag(transport.ART_CHAT, text="",
                                      rr=base64.b64encode(paket).decode(),
                                      zeit=int(time.time()))
        else:
            nutz = transport.umschlag(transport.ART_CHAT, text=roh,
                                      zeit=int(time.time()))
        gemeldet = None
        if int(umwege or 0) > 0:
            lage = self.brief_verschleiert(k.nachbar_id, nutz, int(umwege))
            if not lage["gesendet"]:
                raise ValueError("Die Nachricht konnte nicht zugestellt werden.")
            gemeldet = lage["umwege"]
        elif not self.brief_senden(k.nachbar_id, nutz):
            raise ValueError("Die Nachricht konnte nicht zugestellt werden.")
        roh = roh.encode("utf-8")
        with self._sperre:
            k.nachrichten.append({"richtung": "aus", "text": bytearray(roh),
                                  "zeit": time.time(), "umwege": gemeldet})
        return True if gemeldet is None else {"umwege": gemeldet}

    def chat(self, name):
        """Der Verlauf mit einem Kontakt — als Text für die Anzeige."""
        with self._sperre:
            k = self.kontakte.get(name)
            if k is None:
                return None
            return [{"richtung": n["richtung"], "zeit": n["zeit"],
                     "umwege": n.get("umwege"),
                     "text": bytes(n["text"]).decode("utf-8", "replace")}
                    for n in k.nachrichten]

    def kontaktliste(self):
        jetzt = time.time()
        with self._sperre:
            return [{"name": k.name, "online": k.online(jetzt),
                     "nachrichten": len(k.nachrichten),
                     "sicherheitszahl": k.sicherheitszahl,
                     "bestaetigt": k.bestaetigt,
                     "ausgeloescht": k._anker.vernichtet,
                     "ratsche": k.ratsche is not None}
                    for k in self.kontakte.values()]

    # -- Antwort-Bremse ----------------------------------------------------
    def _darf_antworten(self, von, jetzt=None):
        """Darf an diese Adresse noch geantwortet werden?

        Zwei Schranken: je Absender und insgesamt. Die zweite ist die
        wichtigere — ein Angreifer wechselt sonst einfach die gefälschte
        Absenderadresse und umgeht die erste vollständig.

        Wer gebremst wird, bekommt KEINE Fehlermeldung zurück. Eine Antwort
        „du wirst gebremst" wäre selbst wieder eine Antwort und damit genau
        das Paket, das nicht hinausgehen soll."""
        jetzt = jetzt or time.time()
        adresse = von[0] if isinstance(von, tuple) else str(von)
        with self._sperre:
            if jetzt - self._bremse_gesamt[0] > ANTWORT_FENSTER:
                self._bremse_gesamt = [jetzt, 0]
            if self._bremse_gesamt[1] >= ANTWORT_GESAMT:
                self.gebremst += 1
                return False
            eintrag = self._bremse.get(adresse)
            if eintrag is None or jetzt - eintrag[0] > ANTWORT_FENSTER:
                eintrag = [jetzt, 0]
                self._bremse[adresse] = eintrag
            if eintrag[1] >= ANTWORT_JE_ABSENDER:
                self.gebremst += 1
                return False
            eintrag[1] += 1
            self._bremse_gesamt[1] += 1
            # Alte Eintraege wegraeumen, sonst waechst der Zaehler mit jeder je
            # gesehenen Adresse — und gefaelschte Adressen gibt es unbegrenzt.
            if len(self._bremse) > 4096:
                alt = [a for a, e in self._bremse.items()
                       if jetzt - e[0] > ANTWORT_FENSTER]
                for a in alt:
                    del self._bremse[a]
            return True

    # -- NAT-Durchstich: erreichbar werden, ohne Server ---------------------
    def _spiegel_empfangen(self, von, rumpf):
        """Entweder fragt jemand nach seiner Außenadresse — oder sagt uns unsere."""
        try:
            vorgang, gesehen = transport.spiegel_lesen(rumpf)
        except transport.Paketfehler as e:
            return self._verwerfen(str(e))
        if gesehen is None:
            # Eine Frage: Wir sagen, woher das Paket kam. Mehr wissen wir nicht,
            # und mehr ist auch nicht gefragt.
            if not (isinstance(von, tuple) and len(von) == 2):
                return
            if not self._darf_antworten(von):
                return self._verwerfen("zu viele Spiegelfragen von dieser Adresse")
            try:
                self.netz.senden(self.adresse, von,
                                 transport.spiegel_bauen(vorgang, von))
            except Exception:
                pass
            return
        # Eine Antwort auf unsere eigene Frage.
        with self._fragen_sperre:
            eintrag = self._offene_fragen.get(vorgang)
            if eintrag is None:
                # Unaufgefordert: Sonst koennte jeder uns eine beliebige
                # Aussenadresse einreden, und wir wuerden sie in Tickets
                # weitergeben — dann fuehren unsere Einladungen ins Leere.
                return self._verwerfen("Spiegel ohne offene Frage")
            eintrag[1] = gesehen
        eintrag[0].set()

    def aussen_erfragen(self, frist=2.0):
        """Wie sieht uns die Welt? Fragt die bekannten Nachbarn.

        Man weiß es selbst nicht — die Adresse und der Port kommen vom Router.
        Dasselbe tun STUN-Server; hier kann es jeder Knoten, den man ohnehin
        kennt. Kein zentraler Dienst, kein Anbieter, der mitschreibt.

        Gefragt werden MEHRERE. Antworten zwei verschieden, ist das keine
        Panne, sondern die Diagnose: Dann vergibt der Router je Ziel einen
        anderen Port (symmetrisches NAT), und der Durchstich wird nicht
        gelingen. Das zu wissen ist mehr wert, als es zu versuchen."""
        with self._sperre:
            ziele = [n for n in self.nachbarn.values() if n.frisch()][:4]
        gesehen = []
        for nachbar in ziele:
            vorgang = crypto.zufall(8)
            ereignis = threading.Event()
            eintrag = [ereignis, None]
            with self._fragen_sperre:
                self._offene_fragen[vorgang] = eintrag
            try:
                self.netz.senden(self.adresse, nachbar.adresse,
                                 transport.spiegel_bauen(vorgang))
                if ereignis.wait(frist) and eintrag[1]:
                    gesehen.append(eintrag[1])
            except Exception:
                pass
            finally:
                with self._fragen_sperre:
                    self._offene_fragen.pop(vorgang, None)
        if not gesehen:
            return None
        einig = len(set(gesehen)) == 1
        self.aussenadresse = gesehen[0]
        self.nat_symmetrisch = (not einig) if len(gesehen) > 1 else None
        return {"adresse": gesehen[0], "antworten": gesehen,
                "einig": einig,
                # Ein Wort dazu, was das bedeutet — die Zahl allein hilft niemandem.
                "hinweis": ("" if einig or len(gesehen) < 2 else
                            "Zwei Nachbarn sehen dich unter verschiedenen Ports. "
                            "Dein Router vergibt je Ziel einen anderen — ein "
                            "Durchstich wird so nicht gelingen. Abhilfe: im "
                            "Router eine Portweiterleitung einrichten, oder "
                            "einen Knoten mit fester Adresse als Brücke nutzen.")}

    def _stups_empfangen(self, von, rumpf):
        """Ein Dritter bittet uns, bei einer Adresse anzuklopfen."""
        try:
            ziel_id, adresse = transport.stups_lesen(rumpf)
        except transport.Paketfehler as e:
            return self._verwerfen(str(e))
        # Nur wer uns bereits kennt, darf uns stupsen — weder als Ziel noch
        # als Vermittler. Sonst waere das ein Verstaerker: Ein Fremder schickt
        # einen Stups, und wir schicken Pakete an ein Ziel seiner Wahl.
        if isinstance(von, tuple) and len(von) == 2:
            with self._sperre:
                bekannt = any(n.adresse == von and n.frisch()
                              for n in self.nachbarn.values())
            if not bekannt:
                self.stupse_abgelehnt += 1
                return self._verwerfen("Stups von einem Unbekannten")
        if ziel_id != self.knoten_id:
            # WIR SIND DER VERMITTLER. Genau dafuer gibt es den Stups: Zwei
            # Knoten hinter Routern koennen einander nicht erreichen, aber
            # beide erreichen uns. Also reichen wir die Bitte weiter — der
            # erste Entwurf verwarf sie hier, womit nie ein Durchstich zustande
            # gekommen waere.
            #
            # Wir erfahren dabei nur, DASS zwei miteinander sprechen wollen,
            # nicht worueber. Der Inhalt geht spaeter direkt und versiegelt.
            with self._sperre:
                ziel = self.nachbarn.get(ziel_id.hex())
            if ziel is None or not ziel.frisch():
                return self._verwerfen("Stups für einen Unbekannten")
            if not transport.adresse_oeffentlich(adresse[0]):
                self.stupse_abgelehnt += 1
                return self._verwerfen("Stups auf eine nicht-öffentliche Adresse")
            try:
                self.netz.senden(self.adresse, ziel.adresse,
                                 transport.stups_bauen(ziel_id, adresse[0],
                                                       adresse[1]))
                self.vermittelt += 1
            except Exception:
                pass
            return
        if not transport.adresse_oeffentlich(adresse[0]):
            # Ein Stups auf eine private Adresse laesst uns auf ein Geraet im
            # lokalen Netz einprasseln, das mit dem Mesh nichts zu tun hat.
            self.stupse_abgelehnt += 1
            return self._verwerfen("Stups auf eine nicht-öffentliche Adresse")
        self.anklopfen(adresse)

    def anklopfen(self, adresse, versuche=3):
        """Ein paar Pakete hinausschicken, um den Rückweg zu öffnen.

        Diese Pakete kommen drüben vielleicht nicht an — das ist in Ordnung.
        Ihr Zweck ist, im EIGENEN Router einen Eintrag anzulegen, damit die
        Antwort der Gegenseite hereingelassen wird. Drei Stück, weil UDP eines
        davon verlieren darf."""
        paket = transport.spiegel_bauen(crypto.zufall(8))
        erfolge = 0
        for _ in range(versuche):
            try:
                if self.netz.senden(self.adresse, adresse, paket):
                    erfolge += 1
            except Exception:
                pass
        if erfolge:
            self.durchstiche += 1
        return erfolge

    def durchstich(self, ziel_id, vermittler_id, eigene_aussenadresse=None):
        """Über einen gemeinsamen Bekannten einen Weg zu `ziel_id` öffnen.

        Beide Seiten klopfen gleichzeitig; eine der beiden Richtungen trifft
        auf einen schon geöffneten Weg. Ohne den Vermittler ginge es nicht —
        er ist der Einzige, der beide erreichen kann. Er erfährt dabei nur,
        dass zwei Knoten miteinander sprechen wollen, nicht worüber."""
        aussen = eigene_aussenadresse or self.aussenadresse
        if not aussen:
            lage = self.aussen_erfragen()
            aussen = lage and lage["adresse"]
        if not aussen:
            return False
        with self._sperre:
            vermittler = self.nachbarn.get(
                vermittler_id.hex() if isinstance(vermittler_id, bytes)
                else vermittler_id)
            ziel = self.nachbarn.get(
                ziel_id.hex() if isinstance(ziel_id, bytes) else ziel_id)
        if vermittler is None:
            return False
        try:
            ziel_roh = (ziel_id if isinstance(ziel_id, bytes)
                        else bytes.fromhex(ziel_id))
            self.netz.senden(self.adresse, vermittler.adresse,
                             transport.stups_bauen(ziel_roh, aussen[0], aussen[1]))
        except Exception:
            return False
        # Selbst ebenfalls klopfen — von beiden Seiten gleichzeitig, sonst
        # oeffnet sich nur eine Richtung und die andere bleibt zu.
        if ziel is not None:
            self.anklopfen(ziel.adresse)
        return True

    # -- Kademlia: finden, ohne alle zu fragen ------------------------------
    def _frage_empfangen(self, von, rumpf):
        """Jemand fragt nach Knoten oder nach einem Inhalt."""
        if self.wege is None:
            return self._verwerfen("Frage im Diving Net — dort gilt der Rundruf")
        try:
            vorgang, art, ziel, absender, port = transport.frage_lesen(rumpf)
        except transport.Paketfehler as e:
            return self._verwerfen(str(e))
        if absender == self.knoten_id:
            return                                    # das eigene Echo
        if not self._darf_antworten(von):
            return self._verwerfen("zu viele Fragen von dieser Adresse")
        wirt = von[0] if isinstance(von, tuple) else str(von)
        rueck = (wirt, port or (von[1] if isinstance(von, tuple) else 0))
        # Wer fragt, wird gelernt — so fuellen sich die Tabellen ueberhaupt.
        self.wege.sehen(absender, rueck)
        wert = None
        if art == transport.FRAGE_WERT:
            # `holen` liefert eine LISTE — unter einer Adresse koennen mehrere
            # gueltige Saetze liegen (etwa Antworten in einem Faden). Fuer die
            # Frage genuegt der erste; wer alle will, holt sie beim Verbreiten.
            treffer = self.speicher.holen(ziel)
            if treffer:
                wert = treffer[0].kodieren()
        naechste = [(k.id, k.adresse[0], k.adresse[1])
                    for k in self.wege.naechste(ziel)
                    if isinstance(k.adresse, tuple) and len(k.adresse) == 2]
        try:
            self.netz.senden(self.adresse, rueck,
                             transport.antwort_bauen(vorgang, naechste, wert))
        except Exception:
            pass

    def _antwort_empfangen(self, von, rumpf):
        """Eine Antwort auf eine eigene Frage — der wartende Faden wird geweckt."""
        try:
            vorgang, knoten, wert = transport.antwort_lesen(rumpf)
        except transport.Paketfehler as e:
            return self._verwerfen(str(e))
        with self._fragen_sperre:
            eintrag = self._offene_fragen.get(vorgang)
            if eintrag is None:
                # Zu spaet, oder gar nicht gefragt. Beides ist normal und
                # keinen Fehler wert — nur eine unaufgeforderte Antwort darf
                # nichts bewirken, und genau das tut sie hier auch nicht.
                return self._verwerfen("Antwort ohne offene Frage")
            eintrag[1] = (knoten, wert)
        eintrag[0].set()

    def _fragen(self, bekannter, ziel, art=None, frist=2.0):
        """Eine Frage stellen und auf die Antwort warten.

        Wirft bei Zeitablauf — genau das erwartet `kademlia.Suche`, um den
        Knoten als tot zu verbuchen und weiterzugehen. Eine Suche, die auf
        Antworten wartet, die nie kommen, ist keine Suche."""
        art = art or transport.FRAGE_WERT
        vorgang = crypto.zufall(8)
        ereignis = threading.Event()
        eintrag = [ereignis, None]
        with self._fragen_sperre:
            self._offene_fragen[vorgang] = eintrag
        try:
            paket = transport.frage_bauen(
                vorgang, art, ziel, self.knoten_id,
                getattr(self.netz, "unicast_port", 0) or 0)
            if not self.netz.senden(self.adresse, bekannter.adresse, paket):
                raise ConnectionError("Frage ging nicht hinaus")
            if not ereignis.wait(frist):
                raise TimeoutError("keine Antwort binnen %.1f s" % frist)
            knoten, wert = eintrag[1]
        finally:
            with self._fragen_sperre:
                self._offene_fragen.pop(vorgang, None)
        gefunden = [kademlia.Bekannter(kid, (ip, port))
                    for kid, ip, port in knoten if kid != self.knoten_id]
        return gefunden, wert

    def suchen(self, adresse, frist_je_frage=2.0):
        """Einen Inhalt in der Weite suchen. Gibt den Datensatz oder None.

        Erst im eigenen Speicher nachsehen — wer das vergisst, schickt Pakete
        los, um etwas zu finden, das er schon hat."""
        eigene = self.speicher.holen(adresse)
        if eigene:
            return eigene[0]
        if self.wege is None:
            return None
        suche = kademlia.Suche(
            self.wege, adresse,
            lambda b, z: self._fragen(b, z, transport.FRAGE_WERT, frist_je_frage))
        suche.laufen()
        if suche.wert is None:
            return None
        try:
            datensatz = inhalt.Datensatz.dekodieren(suche.wert)
        except Exception:
            return None
        # Ungeprueft uebernehmen waere das Einfallstor: Ein fremder Knoten
        # koennte unter der gesuchten Adresse irgendetwas ausliefern. `legen`
        # prueft Adresse, Signatur, Arbeitsnachweis und Verfall — und wirft,
        # wenn etwas nicht stimmt. Dann gilt der Inhalt als nicht gefunden.
        try:
            self.speicher.legen(datensatz)
        except Exception:
            return None
        return datensatz

    def naechste_knoten(self, ziel, frist_je_frage=2.0):
        """Wer liegt im Netz am nächsten bei `ziel`? Für das Veröffentlichen."""
        if self.wege is None:
            return []
        suche = kademlia.Suche(
            self.wege, ziel,
            lambda b, z: self._fragen(b, z, transport.FRAGE_KNOTEN, frist_je_frage))
        return suche.laufen()

    # -- Ein Modell über mehrere Geräte ------------------------------------
    def rechenknoten(self):
        """Wer im Netz gerade bereit ist, Schichten zu halten.

        Der eigene Knoten steht vorn: Er führt die Kette an und trägt deshalb
        zusätzlich den Kontextspeicher. Fremde kommen nur vor, wenn sie einen
        rpc-Port ANGEKÜNDIGT haben — Speicher zu besitzen genügt nicht."""
        jetzt = time.time()
        with self._sperre:
            frisch = [n for n in self.nachbarn.values()
                      if n.frisch(jetzt) and n.rpc_port]
        # Der Führende braucht selbst KEINEN rpc-Port: Er ruft auf, er wird
        # nicht aufgerufen. Ihm eine Adresse anzudichten wäre eine Erfindung.
        liste = [{"id": self.knoten_id.hex()[:12], "adresse": "(führt an)",
                  "gb": self.compute_angebot() / (1024 ** 3), "selbst": True}]
        for n in frisch:
            wirt = n.adresse[0] if isinstance(n.adresse, tuple) else str(n.adresse)
            liste.append({"id": n.id[:12], "adresse": "%s:%d" % (wirt, n.rpc_port),
                          "gb": n.compute / (1024 ** 3), "selbst": False})
        return liste

    def rechenplan(self, modell_gb, schichten):
        """Wie ein Modell dieser Größe über die vorhandenen Geräte fiele.

        Gibt IMMER auch die Lage der Laufzeit zurück. Ein Plan allein bedeutet
        nichts, solange die RPC-Rückseite fehlt — und das darf die Anzeige
        nicht verschweigen."""
        antwort = {"laufzeit": verteilt.rpc_lage(), "geraete": self.rechenknoten()}
        # Ein „verteilter" Plan über ein einziges Gerät ist keiner. Ihn
        # trotzdem anzuzeigen würde vortäuschen, das Netz trage das Modell.
        if len(antwort["geraete"]) < 2:
            antwort["plan"] = None
            antwort["grund"] = ("Nur dieses Gerät steht bereit. Für ein verteiltes "
                                "Modell muss mindestens ein weiteres Gerät sein "
                                "Rechenangebot einschalten.")
            return antwort
        try:
            plan = verteilt.plan_erstellen(float(modell_gb), int(schichten),
                                           antwort["geraete"])
            antwort["plan"] = plan
            antwort["geschwindigkeit"] = verteilt.erwartete_geschwindigkeit(plan)
        except verteilt.VerteilungFehler as e:
            antwort["plan"] = None
            antwort["grund"] = str(e)
        return antwort

    # -- Für die Oberfläche ------------------------------------------------
    def lage(self):
        """Alles, was die Anzeige braucht — in einem Aufruf."""
        jetzt = time.time()
        with self._sperre:
            frisch = [n for n in self.nachbarn.values() if n.frisch(jetzt)]
        eigen = self.compute_angebot()
        fremd = sum(n.compute for n in frisch)
        return {
            "betriebsart": self.betriebsart,
            "knoten_id": self.knoten_id.hex(),
            "laeuft": self._laeuft,
            "nachbarn": len(frisch),
            "geraete_gesamt": len(frisch) + 1,
            "compute_eigen_gb": round(eigen / (1024 ** 3), 2),
            "compute_fremd_gb": round(fremd / (1024 ** 3), 2),
            "compute_gesamt_gb": round((eigen + fremd) / (1024 ** 3), 2),
            "beitrag": self.statthalter.bericht(),
            "briefe": len(self.briefe),
            "kontakte": self.kontaktliste(),
            "eigene_modelle": self.eigene_modelle(),
            "modell_karte": {m: {"hier": e["hier"], "knoten": len(e["knoten"])}
                             for m, e in self.modell_karte().items()},
            "fremdauftraege": self.fremdauftraege,
            "weitergeleitet": self.weitergeleitet,
            "weiterleiten_erlaubt": self.weiterleiten_erlaubt,
            "umwege_moeglich": self.zwiebel_moeglich(),
            "auftraege_erlaubt": self.auftraege_erlaubt,
            "anker": len(self.anker),
            "rpc_port": self.rpc_port,
            "rechenknoten": sum(1 for n in frisch if n.rpc_port),
            "verworfen": dict(self.verworfen),
            "vertrauen": self.vertrauen.bericht(),
            "gebremst": self.gebremst,
            "liste": [{"id": n.id[:12], "compute_gb": round(n.compute / (1024 ** 3), 2),
                       "seit_s": int(jetzt - n.zuerst)} for n in frisch],
        }


def was_laeuft_darauf(compute_bytes):
    """Welche Modelle trägt dieses Netz gerade? Für die Anzeige.

    Bewusst mit dem Hinweis auf dreifache Redundanz gerechnet: Ohne sie steht
    eine verteilte Pipeline praktisch immer still (siehe ARCHITEKTUR.md 3.1).
    Eine Anzeige, die das verschweigt, verspricht Modelle, die nie laufen."""
    gb = compute_bytes / (1024 ** 3)
    nutzbar = gb / 3.0                      # Redundanz kostet zwei Drittel
    modelle = [
        ("8B (lokal auf jedem Gerät)", 5),
        ("32B", 18),
        ("70B", 35),
        ("Qwen3-235B MoE", 118),
        ("DeepSeek-V3 671B MoE", 336),
    ]
    return {
        "gesamt_gb": round(gb, 1),
        "nutzbar_gb": round(nutzbar, 1),
        "passt": [name for name, bedarf in modelle if bedarf <= nutzbar],
        "naechstes": next((("%s (braucht %d GB nutzbar)" % (name, bedarf))
                           for name, bedarf in modelle if bedarf > nutzbar), None),
    }
