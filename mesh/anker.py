"""Dive on Wide Mesh — Kontaktanker und Treffpunkte.

Das Problem, das hier gelöst wird: Zwei Menschen wollen sich im Netz immer
wieder finden, ohne dass ein Dritter erkennen kann, WER sich da trifft — und
ohne Verzeichnis, bei dem man nachschlagen könnte.

DIE LÖSUNG IN EINEM SATZ
------------------------
Zwei Menschen tauschen einmal persönlich ein Geheimnis. Daraus rechnen beide
Geräte täglich dieselbe, aber jeden Tag andere Treffpunkt-Adresse aus.

    Anker (einmal persönlich)  ->  KDF(Anker, Tag)  ->  Adresse für heute

Ein Beobachter sieht jeden Tag eine andere Zufallsadresse. Er kann sie nicht
vorausberechnen, nicht rückwärts auf den Anker schließen und zwei Tage nicht
als denselben Kontakt erkennen.

WARUM DAS KEIN HAUPTSCHLÜSSEL IST
---------------------------------
Der Anker gilt zwischen GENAU ZWEI Menschen. Er verknüpft nur die beiden, die
ihn ohnehin kennen. Das ist der Unterschied zu einem Hauptschlüssel, aus dem
alle eigenen Identitäten abgeleitet wären: Dort würde ein einziger Verlust
sämtliche Pseudonyme miteinander verbinden. Hier ist bei Verlust genau EIN
Kontakt betroffen — die Identitäten (siehe `fluechtig.Identitaet`) bleiben
weiterhin unabhängig voneinander.

DER ANKER IST ZUM VORLESEN GEMACHT
----------------------------------
Er wird persönlich weitergegeben — vorgelesen, abgetippt, als QR gezeigt.
Deshalb 32 Zeichen aus einem Alphabet ohne die Verwechslungspaare 0/O und
1/l/I, in Vierergruppen. 160 Bit Entropie, aber vorlesbar.
"""

import hashlib
import time

from . import crypto

# Ohne 0/O und 1/l/I — die vier Zeichen, an denen jedes Abtippen scheitert.
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
GRUPPE = 4
GRUPPEN = 8                       # 8 x 4 Zeichen = 32 -> 160 Bit
TAG = 86400


class UngueltigerAnker(ValueError):
    """Der eingegebene Code ist keiner — Tippfehler oder falsch abgelesen."""


def anker_erzeugen():
    """Ein neuer Anker, als vorlesbare Zeichenfolge."""
    roh = crypto.zufall(32)
    zeichen = []
    wert = int.from_bytes(roh, "big")
    for _ in range(GRUPPE * GRUPPEN):
        zeichen.append(ALPHABET[wert % len(ALPHABET)])
        wert //= len(ALPHABET)
    return "-".join("".join(zeichen[i:i + GRUPPE])
                    for i in range(0, len(zeichen), GRUPPE))


def anker_normalisieren(text):
    """Macht aus dem, was ein Mensch eintippt, den kanonischen Anker.

    Verzeiht Kleinschreibung, fehlende oder zusätzliche Striche, Leerzeichen
    und Zeilenumbrüche. Was danach nicht passt, ist wirklich falsch — und dann
    sagen wir das, statt still mit etwas anderem weiterzumachen.

    Absichtlich KEINE Korrektur verwechselbarer Zeichen: Das Alphabet enthält
    0/O und 1/l/I gar nicht erst. Taucht so ein Zeichen auf, wurde etwas
    anderes falsch gelesen, und wir können nicht raten, was. Ein geratener
    Anker führt zu einem stillen Fehlschlag beim Verbinden — die Fehlermeldung
    ist die freundlichere Antwort."""
    if not isinstance(text, str):
        raise UngueltigerAnker("Anker muss Text sein")
    erlaubt = set(ALPHABET)
    kern = "".join(c for c in text.strip().upper() if c in erlaubt)
    if len(kern) != GRUPPE * GRUPPEN:
        # Liste aus dem Alphabet ableiten statt tippen: L IST enthalten
        # (eindeutig, weil 1 und I fehlen), 0/O/1/I sind es nicht.
        verwechselt = sorted({c for c in text.upper()
                              if c in "0O1I" and c not in ALPHABET})
        hinweis = ""
        if verwechselt:
            hinweis = (" Die Zeichen %s kommen in einem Anker nie vor — dort "
                       "wurde vermutlich etwas falsch abgelesen."
                       % ", ".join(verwechselt))
        raise UngueltigerAnker(
            "Ein Anker hat %d Zeichen, dieser hat %d.%s"
            % (GRUPPE * GRUPPEN, len(kern), hinweis))
    return "-".join(kern[i:i + GRUPPE] for i in range(0, len(kern), GRUPPE))


def _anker_bytes(anker):
    kanon = anker_normalisieren(anker)
    return kanon.replace("-", "").encode("ascii")


def treffpunkt(anker, wann=None, fenster=TAG, laenge=20):
    """Die Adresse, unter der sich dieses Paar gerade trifft.

    Wechselt mit jedem Fenster (Standard: täglich). Aus der Adresse lässt
    sich der Anker nicht zurückrechnen — es ist ein Hash, kein Kennwort."""
    wann = time.time() if wann is None else wann
    scheibe = int(wann // fenster)
    return hashlib.blake2b(
        _anker_bytes(anker) + b"|" + str(scheibe).encode(),
        digest_size=laenge, person=b"dowos-treffpunkt").digest()


def treffpunkte_umfeld(anker, wann=None, fenster=TAG, laenge=20):
    """Gestern, heute, morgen.

    Zwei Geräte mit leicht verschiedenen Uhren oder über Mitternacht hinweg
    dürfen sich nicht verlieren. Deshalb hört man immer auf drei Adressen —
    das kostet nichts und rettet den Grenzfall."""
    wann = time.time() if wann is None else wann
    return [treffpunkt(anker, wann + versatz * fenster, fenster, laenge)
            for versatz in (-1, 0, 1)]


def paarschluessel(anker, zweck="nachricht"):
    """Ein gemeinsamer Schlüssel aus dem Anker allein.

    Nützlich, BEVOR ein Schlüsselaustausch stattgefunden hat: Die erste
    Kontaktaufnahme am Treffpunkt wird damit verschlüsselt. Danach übernimmt
    ein frischer X25519-Handschlag — der Anker verschlüsselt niemals den
    laufenden Verkehr, denn er ändert sich nie und hätte keine nachträgliche
    Geheimhaltung."""
    return crypto.ableiten(_anker_bytes(anker), "anker/" + zweck)


def sicherheitszahl(oeffentlich_a, oeffentlich_b):
    """Die Zahl, die zwei Menschen laut vergleichen, um den Mann in der Mitte
    auszuschließen.

    Ohne diesen Abgleich ist jede Ende-zu-Ende-Verschlüsselung wertlos: Sie
    schützt die Leitung, sagt aber nichts darüber, WER am anderen Ende sitzt.
    Beide Schlüssel werden sortiert, damit beide Seiten dieselbe Zahl sehen —
    sonst vergleicht man Äpfel mit Birnen und hält es für einen Angriff."""
    a, b = sorted([bytes(oeffentlich_a), bytes(oeffentlich_b)])
    roh = hashlib.blake2b(a + b, digest_size=15,
                          person=b"dowos-sicherheit").digest()
    zahl = int.from_bytes(roh, "big")
    bloecke = []
    for _ in range(6):
        bloecke.append("%05d" % (zahl % 100000))
        zahl //= 100000
    return " ".join(bloecke)
