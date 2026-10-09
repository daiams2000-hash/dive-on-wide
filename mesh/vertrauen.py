"""Dive on Wide Mesh — traut man einem fremden Rechenknoten?

DIE HARTE WAHRHEIT ZUERST
-------------------------
Man kann eine Sprachmodell-Antwort NICHT nachprüfen. Zwei ehrliche Knoten mit
demselben Modell geben verschiedenen Text zurück; schon eine andere
Quantisierung reicht. „Mehrheitsentscheid" funktioniert bei freiem Text nicht,
und wer das behauptet, hat es nicht ausprobiert.

Was man prüfen kann, ist etwas Kleineres und trotzdem Wichtiges: **ob ein
Knoten überhaupt rechnet.** Der billige Betrug ist nicht das raffiniert falsche
Ergebnis, sondern gar keins — Müll zurückschicken, Guthaben kassieren, Strom
sparen.

EIN IRRWEG, DER FAST AUSGELIEFERT WORDEN WÄRE
---------------------------------------------
Der erste Entwurf verschickte Aufgaben mit einer Formatvorgabe: „Beginne mit
dem Kennwort K1A2B3 und nenne das Ergebnis von 1399 + 379." Gegen ein
12B-Modell: fünf von fünf bestanden. Gegen ein völlig ehrliches 0,5B-Modell:
**vier von fünf durchgefallen** — es ignorierte die Formatvorgabe schlicht und
antwortete inhaltlich richtig.

Diese Prüfung maß **Anweisungstreue**, nicht **ob gerechnet wurde**. Sie hätte
schwache, aber ehrliche Teilnehmer als Betrüger gebrandmarkt — genau die
Geräte, die ein Netz aus Freundesrechnern ausmachen. Deshalb ist sie hier
nicht mehr die Betrugsprüfung, sondern nur noch ein Können-Test, der
ausdrücklich NICHT ins Urteil eingeht.

WAS STATTDESSEN GEPRÜFT WIRD — beides an echten Modellen gemessen
------------------------------------------------------------------
1. **Relevanz.** Wie viel vom Inhalt der Frage kommt in der Antwort vor?
   Gemessen: ehrliches 0,5B **62 %**, ehrliches 12B **88 %**, eine
   Standardfloskel als Betrug **12 %**. Dazwischen liegt viel Platz.
2. **Wiederholung.** Antwortet ein Knoten auf VERSCHIEDENE Fragen mit
   demselben Text, rechnet er nicht. Gemessen: ehrliche Antworten auf drei
   verschiedene Fragen überschneiden sich zu **0,11–0,18**; eine feste Floskel
   zu **1,00**. Das ist der schärfste Nachweis, den es hier gibt.
3. **Leere.** Keine Antwort ist keine Arbeit.

WAS DAS NICHT LEISTET
---------------------
Ein Knoten, der ein *schlechteres* Modell fährt als angekündigt, besteht alles
— er rechnet ja. Das ist nicht erkennbar, und Dive on Wide behauptet es nicht.
Erkannt wird: gar nicht rechnen, feste Floskeln, Themaverfehlung, Leere.
"""

import re
import time

from . import crypto

# Alle Schwellen stammen aus Messungen an echten Modellen, nicht aus dem
# Gefuehl. Sie liegen bewusst WEIT auf der grosszuegigen Seite: Ein faelschlich
# ausgesperrter ehrlicher Knoten ist teurer als ein durchgerutschter Betrueger,
# denn der erste geht und kommt nicht wieder.

# Anteil der Fragewoerter, der in der Antwort vorkommen muss.
#
# Die Zaehlweise ist wichtiger als die Schwelle, und der erste Entwurf hatte
# sie falsch: Er verglich ganze Woerter. „Peer-to-Peer-NETZEN" in der Frage und
# „Peer-to-Peer-NETZE" in der Antwort galten als verschieden, und Befehlswoerter
# wie „nenne" oder „zwei" kommen in keiner Antwort vor. Eine tadellose Antwort
# fiel dadurch mit 17 % durch — genau der Fehler, den diese Pruefung vermeiden
# sollte.
#
# Gemessen ueber 12 echte Antworten (0,5B und 12B) gegen 12 Betrugsmuster:
#   ganze Woerter            ehrlich 0,40-1,00 · Betrug 0,00-0,25 · Abstand 0,15
#   Stamm 4 / ab 5 Zeichen   ehrlich 0,50-1,00 · Betrug 0,00      · Abstand 0,50
# Also Stamm 4, Mindestlaenge 5 — und die Schwelle mitten in die Luecke.
STAMM_LAENGE = 4              # so viele Anfangsbuchstaben zaehlen
INHALTSWORT_AB = 5            # kuerzere Woerter sind meist Befehle oder Fuellsel
RELEVANZ_MINDESTENS = 0.30

# So viele Woerter muss eine Antwort ENTHALTEN, die nicht in der Frage stehen —
# sonst hat jemand nur die Frage zurueckgespiegelt.
# Gemessen: ehrlich 88–222 · Floskel 4.
NEUE_WOERTER_MINDESTENS = 8

# Erst ab so vielen Inhaltswortstaemmen in der Frage ist die Relevanzquote
# aussagekraeftig. Bei „Hallo?" ist jede Zahl Zufall.
FRAGE_MINDESTWOERTER = 3

# Aehnlichkeit zweier Antworten desselben Knotens auf VERSCHIEDENE Fragen.
# Gemessen: ehrlich 0,11–0,18 · feste Floskel 1,00.
WIEDERHOLUNG_AB = 0.60
WIEDERHOLUNG_GEDAECHTNIS = 8     # so viele fruehere Antworten je Knoten

# Ab wann ein Knoten als unzuverlaessig gilt.
DURCHGEFALLEN_AB = 2
MINDESTANTWORTEN = 3             # vorher ist jedes Urteil Zufall

# Aehnlichkeit, unter der eine Antwort als Ausreisser gilt.
AUSREISSER_SCHWELLE = 0.18


class Probe:
    """Eine Stichprobe: ein Auftrag, dessen Antwort wir selbst prüfen können."""

    __slots__ = ("prompt", "kennwort", "summe", "erstellt")

    def __init__(self, prompt, kennwort, summe):
        self.prompt = prompt
        self.kennwort = kennwort
        self.summe = summe
        self.erstellt = time.time()

    def __repr__(self):
        return "<Probe %s ... %d>" % (self.kennwort, self.summe)


def probe_bauen(zufall=None):
    """Ein KÖNNEN-Test: Hält sich dieses Modell an eine Formatvorgabe?

    ACHTUNG — das ist ausdrücklich **keine Betrugsprüfung**, und es war einmal
    als solche gedacht. Gemessen fällt ein völlig ehrliches 0,5B-Modell hier
    vier von fünf Mal durch, weil es die Formatvorgabe ignoriert und trotzdem
    inhaltlich richtig antwortet. Wer daraus ein Betrugsurteil ableitet,
    sperrt genau die kleinen Geräte aus, die ein Freundesnetz ausmachen.

    Nützlich bleibt es trotzdem: Wer Aufträge mit strengem Format verteilen
    will (JSON, feste Felder), sollte wissen, welche Knoten das können.

    Zwei Dinge werden geprüft, und beide zusammen sind schwer zu erraten:

    * ein **Kennwort**, das in der Antwort vorkommen muss — es ist zufällig und
      steht in der Aufgabe, also kann es nur nennen, wer sie gelesen hat;
    * eine **Rechnung**, deren Ergebnis wir kennen — sie kostet ein echtes
      Modell nichts, aber ein Knoten, der ausgedachten Text zurückschickt,
      trifft die Zahl nicht.

    Das Kennwort allein genügte nicht: Ein Betrüger könnte die Aufgabe lesen,
    das Kennwort herauskopieren und sonst Müll liefern. Die Rechnung allein
    genügte auch nicht: Sie wäre als Prüfung erkennbar. Zusammen sieht es aus
    wie eine kleine Aufgabe mit Formatvorgabe — davon gibt es viele echte."""
    roh = zufall or crypto.zufall(8)
    kennwort = "K" + roh.hex()[:6].upper()
    a = 1000 + roh[0] * 7 + roh[1]
    b = 100 + roh[2] * 3 + roh[3]
    prompt = (
        "Beantworte kurz und sachlich: Was ist der Unterschied zwischen "
        "Arbeitsspeicher und Festplattenspeicher? Zwei Sätze genügen.\n"
        "Beginne deine Antwort mit dem Kennwort %s und schreibe danach in "
        "derselben Zeile das Ergebnis von %d + %d als Zahl."
        % (kennwort, a, b))
    return Probe(prompt, kennwort, a + b)


def probe_pruefen(probe, antwort):
    """Hält sich dieses Modell an die Formatvorgabe? (KEIN Betrugsurteil.)

    Gibt (bestanden, grund) zurück. „Nicht bestanden" heißt hier: kann kein
    striktes Format — nicht: betrügt."""
    if not antwort or not str(antwort).strip():
        return False, "keine Antwort"
    text = str(antwort)
    if probe.kennwort not in text:
        # Wer das Kennwort nicht nennt, hat die Aufgabe nicht gelesen —
        # oder gar nicht erst an ein Modell gegeben.
        return False, "Kennwort fehlt (Aufgabe nicht gelesen)"
    zahlen = {int(z) for z in re.findall(r"\d+", text)}
    if probe.summe not in zahlen:
        # Rechenfehler sind bei kleinen Modellen moeglich. Das ist deshalb
        # KEIN Betrugsbeweis — aber es zaehlt als nicht bestanden, denn wer
        # nicht rechnen kann, taugt auch nicht als Rechenknoten.
        return False, "Rechenergebnis stimmt nicht"
    return True, "bestanden"


def staemme(text, mindestlaenge=1):
    """Wortanfänge statt ganzer Wörter — gegen die deutsche Beugung.

    „Netzen" und „Netze" sind dasselbe Thema, aber verschiedene Zeichenketten.
    Vier Anfangsbuchstaben fangen das, ohne verschiedene Wörter
    zusammenzuwerfen. Kein Stemmer, keine Wortliste, keine Sprachabhängigkeit
    über das Alphabet hinaus — grob, aber gemessen ausreichend."""
    return {w[:STAMM_LAENGE] for w in wortmenge(text) if len(w) >= mindestlaenge}


def relevanz(frage, antwort):
    """Wie viel vom Inhalt der Frage kommt in der Antwort vor? 0 bis 1.

    Gezählt werden nur Wörter ab fünf Zeichen: „nenne", „zwei", „kurz" stehen
    in keiner vernünftigen Antwort und zögen eine gute Antwort nach unten.

    Der einfachste Nachweis, dass sich jemand mit der Frage befasst hat — und
    fair zu schwachen Modellen, denn er verlangt kein Format, keine Länge und
    keine Klugheit, nur Bezug."""
    wf = staemme(frage, INHALTSWORT_AB)
    if not wf:
        return 1.0
    return len(wf & staemme(antwort)) / float(len(wf))


def pruefen(frage, antwort, frueher=()):
    """Hat dieser Knoten gearbeitet? Gibt (in_ordnung, grund) zurück.

    `frueher` sind seine Antworten auf ANDERE Fragen. Sie sind das schärfste
    Mittel: Wer auf verschiedene Fragen dasselbe schickt, rechnet nicht — und
    das gilt unabhängig davon, wie klein sein Modell ist.

    Im Zweifel gilt „in Ordnung". Ein fälschlich ausgesperrter ehrlicher
    Knoten ist teurer als ein durchgerutschter Betrüger: Der Erste geht und
    kommt nicht wieder."""
    text = str(antwort or "").strip()
    if not text:
        return False, "keine Antwort"

    # 1. Wiederholt er sich ueber verschiedene Fragen? Schaerfster Nachweis.
    for alt_text in list(frueher)[-WIEDERHOLUNG_GEDAECHTNIS:]:
        if not str(alt_text or "").strip():
            continue
        if aehnlichkeit(text, alt_text) >= WIEDERHOLUNG_AB:
            return False, ("dieselbe Antwort wie auf eine andere Frage — "
                           "dieser Knoten rechnet nicht")

    # 2. Bezieht sich die Antwort ueberhaupt auf die Frage?
    wf = staemme(frage, INHALTSWORT_AB)
    if len(wf) >= FRAGE_MINDESTWOERTER:
        quote = relevanz(frage, text)
        if quote < RELEVANZ_MINDESTENS:
            return False, ("Antwort passt nicht zur Frage (%.0f %% Bezug, "
                           "erwartet mindestens %.0f %%)"
                           % (quote * 100, RELEVANZ_MINDESTENS * 100))
        # 3. Oder hat er nur die Frage zurueckgespiegelt?
        if len(wortmenge(text) - wortmenge(frage)) < NEUE_WOERTER_MINDESTENS:
            return False, "die Frage wurde nur zurückgespiegelt"
    return True, "in Ordnung"


def wortmenge(text):
    """Kleingeschriebene Wörter ab drei Buchstaben — für den Vergleich."""
    return {w for w in re.findall(r"[\wäöüßÄÖÜ]+", str(text or "").lower())
            if len(w) >= 3}


def aehnlichkeit(a, b):
    """Wie stark überschneiden sich zwei Antworten? 0 bis 1 (Jaccard).

    Bewusst grob. Ein feinerer Vergleich (Einbettungen) wäre genauer, würde
    aber ein Modell brauchen — und diese Prüfung soll auch dann arbeiten,
    wenn gerade keins läuft. Für „völlig aus der Reihe" reicht das."""
    ma, mb = wortmenge(a), wortmenge(b)
    if not ma and not mb:
        return 1.0
    if not ma or not mb:
        return 0.0
    return len(ma & mb) / float(len(ma | mb))


def ausreisser(antworten, schwelle=AUSREISSER_SCHWELLE):
    """Welche Antworten fallen völlig aus der Reihe?

    Gibt die Indizes zurück. Ein **Hinweis, kein Beweis** — bei zwei Antworten
    sagt Uneinigkeit gar nichts, denn welche der beiden abweicht, ist nicht zu
    entscheiden. Deshalb erst ab drei."""
    texte = [str(a or "") for a in antworten]
    if len(texte) < 3:
        return []
    verdaechtig = []
    for i, t in enumerate(texte):
        andere = [aehnlichkeit(t, u) for j, u in enumerate(texte) if j != i]
        if andere and max(andere) < schwelle:
            verdaechtig.append(i)
    # Faellt MEHR ALS DIE HAELFTE aus der Reihe, ist nicht die Mehrheit falsch,
    # sondern die Frage schlecht gestellt — dann urteilen wir lieber gar nicht.
    if len(verdaechtig) * 2 > len(texte):
        return []
    return verdaechtig


class Akte:
    """Was ein Knoten bisher geliefert hat."""

    __slots__ = ("knoten_id", "auftraege", "beanstandet", "ausreisser",
                 "gruende", "antworten", "format_geprueft", "format_ok",
                 "zuletzt")

    def __init__(self, knoten_id):
        self.knoten_id = knoten_id
        self.auftraege = 0
        self.beanstandet = 0
        self.ausreisser = 0
        self.gruende = []                 # die letzten Beanstandungen im Klartext
        self.antworten = []               # fuer die Wiederholungspruefung
        self.format_geprueft = 0          # Koennen-Test, geht NICHT ins Urteil
        self.format_ok = 0
        self.zuletzt = time.time()

    @property
    def durchgefallen(self):
        return self.beanstandet

    def urteil(self):
        """Ein Wort und ein Satz — mehr braucht die Anzeige nicht.

        Bewusst zurückhaltend: Ohne genug Antworten gibt es KEIN Urteil. Einen
        Knoten nach einer einzigen misslungenen Antwort auszusperren wäre
        schlimmer als gar nicht zu prüfen — Pakete gehen verloren, Modelle
        verhaspeln sich, und wer einmal Pech hatte, bliebe draußen.

        Der Können-Test (Formattreue) geht ABSICHTLICH nicht ein. Er misst,
        was ein Modell kann, nicht ob jemand ehrlich ist."""
        if self.beanstandet >= DURCHGEFALLEN_AB:
            letzter = self.gruende[-1] if self.gruende else ""
            return ("unzuverlässig",
                    "%d von %d Antworten beanstandet%s"
                    % (self.beanstandet, self.auftraege,
                       (" — zuletzt: " + letzter) if letzter else "."))
        if self.auftraege < MINDESTANTWORTEN:
            return ("unbekannt",
                    "Erst %d Antwort(en) — für ein Urteil zu wenig."
                    % self.auftraege)
        if self.ausreisser and self.ausreisser * 3 >= self.auftraege:
            return ("auffällig",
                    "%d von %d Antworten fielen aus der Reihe. Das ist ein "
                    "Hinweis, kein Beweis — ein Modell darf anders antworten."
                    % (self.ausreisser, self.auftraege))
        return ("verlässlich",
                "%d Antworten, keine beanstandet." % self.auftraege)

    def bericht(self):
        wort, satz = self.urteil()
        return {"knoten": self.knoten_id, "urteil": wort, "begruendung": satz,
                "auftraege": self.auftraege, "beanstandet": self.beanstandet,
                "ausreisser": self.ausreisser,
                "gruende": list(self.gruende[-3:]),
                # Getrennt ausgewiesen, damit niemand es fuer ein Urteil haelt.
                "formattreue": ("%d/%d" % (self.format_ok, self.format_geprueft)
                                if self.format_geprueft else "nicht geprüft")}


class Buch:
    """Alle Akten zusammen. Wie alles im Mesh: nur im Arbeitsspeicher.

    Ein Vertrauensurteil, das einen Neustart überlebt, wäre eine dauerhafte
    Bewertung von Menschen — und genau die soll es hier nicht geben. Wer sich
    neu verbindet, fängt neu an. Das kostet Genauigkeit und ist es wert."""

    def __init__(self):
        self.akten = {}

    def akte(self, knoten_id):
        return self.akten.setdefault(str(knoten_id), Akte(str(knoten_id)))

    def format_vermerken(self, knoten_id, bestanden):
        """Können-Test. Geht NICHT ins Vertrauensurteil ein."""
        a = self.akte(knoten_id)
        a.format_geprueft += 1
        a.format_ok += 1 if bestanden else 0
        a.zuletzt = time.time()
        return a

    def antwort_pruefen(self, knoten_id, frage, antwort, ist_ausreisser=False):
        """Eine eingegangene Antwort bewerten und vermerken.

        Gibt (in_ordnung, grund) zurück. Die Antwort wird gemerkt, damit die
        nächste gegen sie verglichen werden kann — das ist die
        Wiederholungsprüfung, und sie braucht ein Gedächtnis."""
        a = self.akte(knoten_id)
        gut, grund = pruefen(frage, antwort, a.antworten)
        a.auftraege += 1
        if not gut:
            a.beanstandet += 1
            a.gruende.append(grund)
            del a.gruende[:-5]
        if ist_ausreisser:
            a.ausreisser += 1
        if str(antwort or "").strip():
            a.antworten.append(str(antwort))
            del a.antworten[:-WIEDERHOLUNG_GEDAECHTNIS]
        a.zuletzt = time.time()
        return gut, grund

    def taugt(self, knoten_id):
        """Darf diesem Knoten noch ein Auftrag gegeben werden?"""
        a = self.akten.get(str(knoten_id))
        return True if a is None else a.urteil()[0] != "unzuverlässig"

    def bericht(self):
        return [a.bericht() for a in
                sorted(self.akten.values(), key=lambda x: -x.zuletzt)]

    def leeren(self):
        self.akten.clear()
