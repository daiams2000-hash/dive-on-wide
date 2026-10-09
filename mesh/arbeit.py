"""Dive on Wide Mesh — Arbeitsnachweis gegen Spam.

Ein offenes P2P-Netz ohne Anmeldung hat keinen Türsteher. Wer nichts
kostet, wird geflutet. Der Arbeitsnachweis macht das Veröffentlichen für
den Absender spürbar teuer und für jeden Prüfer fast gratis — das ist die
ganze Idee, und nur diese Asymmetrie trägt.

BEWUSST LEICHT GEHALTEN
-----------------------
Der Nutzer will, dass jeder Mensch von seinem Gerät aus teilnehmen kann.
Ein Arbeitsnachweis, der ein Handy zehn Sekunden ausbremst oder den Akku
leert, widerspricht dem direkt. Deshalb ist die Voreinstellung niedrig
(Größenordnung Zehntelsekunde) und die Schwierigkeit pro Zweck getrennt
einstellbar: Ein Beitritt darf teurer sein als eine Chatnachricht.

Das schützt gegen Fluten aus Versehen und gegen billige Massenposter. Es
schützt NICHT gegen einen entschlossenen Angreifer mit einer Grafikkarte —
dagegen hilft kein leichter Arbeitsnachweis, sondern nur, Peers, die sich
danebenbenehmen, fallen zu lassen. Das gehört in die Nachbarschaftspflege
der DHT, nicht hierher.
"""

import hashlib
import struct
import time

# Voreinstellungen je Zweck. Bits, nicht Millisekunden — die Hardware ist
# überall anders, die Bits sind es nicht.
SCHWIERIGKEIT = {
    "nachricht": 12,        # Chatnachricht: soll flüssig bleiben
    "veroeffentlichen": 16,  # Datensatz in die DHT legen
    "beitritt": 18,         # neuer Knoten im Netz: darf spürbar sein
}

MAX_BITS = 32               # Obergrenze, damit sich niemand selbst aussperrt


def _hash(daten, nonce):
    return hashlib.blake2b(bytes(daten) + struct.pack("<Q", nonce),
                           digest_size=32, person=b"dowos-mesh-pow").digest()


def _fuehrende_nullbits(h):
    bits = 0
    for byte in h:
        if byte:
            return bits + (8 - byte.bit_length())
        bits += 8
    return bits


def erfuellt(daten, nonce, bits):
    """Genügt dieser Nachweis? Eine Hashberechnung — das ist die Prüfseite."""
    if bits <= 0:
        return True
    if bits > MAX_BITS or nonce < 0 or nonce > 0xFFFFFFFFFFFFFFFF:
        return False
    return _fuehrende_nullbits(_hash(daten, nonce)) >= bits


def finden(daten, bits, frist=30.0):
    """Sucht einen gültigen Nachweis. Gibt den Nonce zurück.

    `frist` ist eine Notbremse: Auf einem langsamen Gerät darf die Suche
    nicht endlos laufen und die Oberfläche einfrieren. Läuft sie ab, fliegt
    eine Ausnahme — dann ist die Schwierigkeit für dieses Gerät zu hoch, und
    das ist eine Information, keine Panne."""
    if bits <= 0:
        return 0
    if bits > MAX_BITS:
        raise ValueError("Schwierigkeit über der Obergrenze (%d Bit)" % MAX_BITS)
    ende = time.monotonic() + frist
    nonce = 0
    while True:
        if erfuellt(daten, nonce, bits):
            return nonce
        nonce += 1
        if nonce % 4096 == 0 and time.monotonic() > ende:
            raise TimeoutError(
                "Kein Arbeitsnachweis mit %d Bit in %.0f s gefunden — für "
                "dieses Gerät ist die Schwierigkeit zu hoch." % (bits, frist))


def bits_fuer(zweck):
    """Schwierigkeit für einen Zweck; unbekannte Zwecke sind teuer."""
    return SCHWIERIGKEIT.get(zweck, SCHWIERIGKEIT["veroeffentlichen"])


def kosten_schaetzen(bits, proben=2000):
    """Wie lange dauert das auf DIESEM Gerät? Für ehrliche Diagnose.

    Misst die Hashrate und rechnet hoch, statt einen Erfahrungswert von
    fremder Hardware zu behaupten."""
    daten = b"messung"
    start = time.perf_counter()
    for nonce in range(proben):
        _hash(daten, nonce)
    dauer = time.perf_counter() - start
    rate = proben / dauer if dauer > 0 else 0.0
    versuche = 2 ** bits
    return {"bits": bits, "hashes_pro_sekunde": rate,
            "erwartete_sekunden": (versuche / rate) if rate else float("inf")}
