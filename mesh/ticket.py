"""Dive on Wide Mesh — Einladungstickets für die Weite.

Im eigenen Netz finden sich Knoten von allein (Multicast). Über das Internet
geht das nicht: Es gibt keinen Rundruf und — auf eigenen Wunsch — auch keinen
Verzeichnisdienst, bei dem man nachschlagen könnte.

Ein Ticket schließt genau diese Lücke. Es enthält alles, was ein fremder
Knoten braucht, um EINEN bekannten Knoten anzusprechen:

    Betriebsart · Adresse · Port · Signaturschlüssel des Einladenden

WARUM DAS NUR EINMAL GEBRAUCHT WIRD
-----------------------------------
Nach dem ersten Kontakt tauschen Knoten ihre Nachbarn untereinander aus
(siehe `knoten.nachbarn_teilen`). Ein einziges Ticket führt damit ins ganze
Netz. Es ist ein Türöffner, kein Ausweis — und deshalb muss es auch nicht
geheim bleiben: Wer es hat, darf mitreden, aber nichts mitlesen. Nachrichten
sind unabhängig davon Ende-zu-Ende verschlüsselt.

WAS EIN TICKET NICHT IST
------------------------
Es ist kein Kontaktanker. Der Anker macht aus zwei Fremden Freunde, die sich
immer wiederfinden; das Ticket macht aus einem Fremden nur einen Nachbarn.
Beides zu vermischen wäre gefährlich: Ein Ticket wird weitergegeben, ein
Anker niemals.
"""

import base64
import struct
import time

from . import crypto

MARKE = "DOWOS1"
MAX_ALTER = 14 * 86400        # ein Ticket veraltet, damit alte Adressen sterben


class UngueltigesTicket(ValueError):
    """Der Text ist kein Ticket, ist veraltet oder wurde verändert."""


def _b64(rohbytes):
    return base64.urlsafe_b64encode(rohbytes).decode("ascii").rstrip("=")


def _unb64(text):
    fehlend = (-len(text)) % 4
    return base64.urlsafe_b64decode(text + "=" * fehlend)


def _packen(kern):
    """Kompaktes Binaerformat statt JSON.

    Ein Ticket wird abgetippt, abfotografiert und weitergereicht — jedes Byte
    zaehlt. JSON mit hex-kodierten Schluesseln kam auf 405 Zeichen; dieselbe
    Angabe binaer und base64-verpackt braucht rund die Haelfte, und in einen
    QR-Code passt sie damit deutlich bequemer.

    Aufbau: art(1) | host(1+n) | port(2) | sig(32) | dh(32) | zeit(4) | notiz(1+n)"""
    host = kern["host"].encode("utf-8")[:255]
    notiz = kern.get("notiz", "").encode("utf-8")[:80]
    art = 1 if kern["art"] == "klause" else 2
    return (bytes([art, len(host)]) + host
            + struct.pack("<H", int(kern["port"]))
            + kern["sig"] + kern["dh"]
            + struct.pack("<I", int(kern["zeit"]))
            + bytes([len(notiz)]) + notiz)


def _entpacken(roh):
    try:
        art = {1: "klause", 2: "weite"}[roh[0]]
        n = roh[1]
        p = 2
        host = roh[p:p + n].decode("utf-8")
        p += n
        (port,) = struct.unpack("<H", roh[p:p + 2])
        p += 2
        sig_pub = roh[p:p + 32]
        p += 32
        dh_pub = roh[p:p + 32]
        p += 32
        (zeit,) = struct.unpack("<I", roh[p:p + 4])
        p += 4
        m = roh[p]
        p += 1
        notiz = roh[p:p + m].decode("utf-8", "replace")
        if len(sig_pub) != 32 or len(dh_pub) != 32:
            raise ValueError
        return {"art": art, "host": host, "port": port, "sig": sig_pub,
                "dh": dh_pub, "zeit": zeit, "notiz": notiz}
    except Exception:
        raise UngueltigesTicket("Das Ticket ist beschädigt oder unvollständig.")


def erzeugen(identitaet, betriebsart, host, port, notiz=""):
    """Ein Ticket bauen und signieren.

    Signiert, damit ein Tippfehler oder eine Veränderung unterwegs sofort
    auffällt, statt still auf eine falsche Adresse zu zeigen. Die eigentliche
    Echtheit klärt sich beim Verbinden: Nur wer den privaten Schlüssel hat,
    kann einen gültigen Ruf senden."""
    if not host:
        raise UngueltigesTicket("Ohne erreichbare Adresse gibt es kein Ticket.")
    if betriebsart not in ("klause", "weite"):
        raise UngueltigesTicket("Unbekannte Betriebsart: %r" % betriebsart)
    kern = {"art": betriebsart, "host": str(host), "port": int(port),
            "sig": identitaet.sign_oeffentlich, "dh": identitaet.dh_oeffentlich,
            "zeit": int(time.time()), "notiz": str(notiz)[:80]}
    roh = _packen(kern)
    sig = identitaet.signieren(roh)
    return MARKE + "." + _b64(roh + sig)


def lesen(text, jetzt=None):
    """Fremdes Ticket prüfen und auswerten. Wirft mit klarer Begründung."""
    if not isinstance(text, str):
        raise UngueltigesTicket("Ticket muss Text sein.")
    # Menschen kopieren mit Zeilenumbrüchen und Leerzeichen.
    sauber = "".join(text.split())
    if sauber.lower().startswith("dowos://"):
        sauber = sauber[8:]
    teile = sauber.split(".")
    if len(teile) != 2 or teile[0] != MARKE:
        raise UngueltigesTicket(
            "Das sieht nicht wie ein Dive-on-Wide-Ticket aus. Es beginnt mit „%s."
            % MARKE)
    try:
        alles = _unb64(teile[1])
    except Exception:
        raise UngueltigesTicket("Das Ticket ist beschädigt oder unvollständig.")
    if len(alles) < 64 + 40:
        raise UngueltigesTicket("Das Ticket ist zu kurz.")
    roh, sig = alles[:-64], alles[-64:]
    kern = _entpacken(roh)
    if not crypto.ed25519_pruefen(kern["sig"], roh, sig):
        raise UngueltigesTicket(
            "Die Signatur passt nicht — das Ticket wurde verändert.")
    jetzt = time.time() if jetzt is None else jetzt
    alter = jetzt - kern["zeit"]
    if alter > MAX_ALTER:
        raise UngueltigesTicket(
            "Das Ticket ist %d Tage alt und damit abgelaufen. Bitte ein neues "
            "erzeugen lassen." % int(alter / 86400))
    if alter < -3600:
        raise UngueltigesTicket("Das Ticket kommt aus der Zukunft.")
    if not (1 <= kern["port"] <= 65535):
        raise UngueltigesTicket("Der Port im Ticket liegt außerhalb 1-65535.")
    return {"art": kern["art"], "host": kern["host"], "port": kern["port"],
            "sig_pub": kern["sig"], "dh_pub": kern["dh"],
            "knoten_id": crypto.ableiten(kern["sig"], "knoten-id", 20).hex(),
            "zeit": kern["zeit"], "notiz": kern["notiz"]}


def als_link(ticket_text):
    """Dieselbe Angabe als anklickbarer Link — bequemer weiterzugeben."""
    return "dowos://" + ticket_text
