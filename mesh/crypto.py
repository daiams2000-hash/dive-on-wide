"""Dive on Wide Mesh — Kryptoschicht.

Die Grundlage des dezentralen Knotens: Signaturen (Ed25519), Schlüssel-
einigung (X25519) und authentifizierte Verschlüsselung (XChaCha20-Poly1305).

WARUM HIER ETWAS SELBST GEBAUT WIRD
-----------------------------------
Dive on Wide hat null Abhängigkeiten — das ist Prinzip, nicht Bequemlichkeit. Die
Python-Standardbibliothek bringt aber weder Ed25519 noch ChaCha20 mit. Also
gibt es zwei Wege, und dieses Modul nimmt beide:

    Stufe 1  PyNaCl (libsodium)  — geprüft, konstantzeitig, schnell.
             Wird automatisch benutzt, wenn installiert.
    Stufe 2  reines Python       — läuft überall ohne Installation.

EHRLICHE GRENZE DER STUFE 2 — bitte lesen:
Reines Python ist NICHT konstantzeitig. Ein Angreifer, der auf DEMSELBEN
Rechner misst (Zeit, Cache), kann daraus Schlüsselmaterial ableiten. Gegen
einen Netzwerk-Angreifer ist die Konstruktion solide; gegen einen lokalen
Angreifer ist sie es nicht. Wer ein echtes Bedrohungsmodell hat, installiert
PyNaCl — `backend_info()` sagt jederzeit, was gerade läuft, und der Knoten
weist beim Start darauf hin. Vorgetäuscht wird nichts.

Alle Primitive sind gegen die offiziellen Testvektoren geprüft
(RFC 8439 ChaCha20/Poly1305/AEAD, RFC 7748 X25519, RFC 8032 Ed25519,
draft-irtf-cfrg-xchacha für XChaCha20) — siehe Gruppe `mesh` im Testlauf.
"""

import hashlib
import hmac
import os
import secrets
import struct

# ---------------------------------------------------------------------------
# Rückhalt-Erkennung
# ---------------------------------------------------------------------------

try:                                     # pragma: no cover - abhängig vom Host
    import nacl.bindings as _sodium
    _HAT_SODIUM = True
except Exception:
    _sodium = None
    _HAT_SODIUM = False


def backend_info():
    """Was läuft gerade — und was heißt das für die Sicherheit?"""
    if _HAT_SODIUM:
        return {"backend": "libsodium (PyNaCl)", "konstantzeitig": True,
                "hinweis": ""}
    return {"backend": "reines Python (stdlib)", "konstantzeitig": False,
            "hinweis": "Nicht konstantzeitig — gegen einen Angreifer auf DIESEM "
                       "Rechner nicht geeignet. Für ein echtes Bedrohungsmodell: "
                       "pip3 install pynacl"}


# ---------------------------------------------------------------------------
# Zufall und Vergleiche
# ---------------------------------------------------------------------------

def zufall(n):
    """Kryptografisch sichere Zufallsbytes."""
    return secrets.token_bytes(n)


def gleich(a, b):
    """Zeitunabhängiger Vergleich — nie `==` auf Geheimnissen benutzen."""
    return hmac.compare_digest(bytes(a), bytes(b))


# ---------------------------------------------------------------------------
# ChaCha20 (RFC 8439)
# ---------------------------------------------------------------------------

_KONST = (0x61707865, 0x3320646e, 0x79622d32, 0x6b206574)   # "expand 32-byte k"
_M32 = 0xffffffff


def _rotl(v, n):
    return ((v << n) | (v >> (32 - n))) & _M32


def _viertelrunde(s, a, b, c, d):
    s[a] = (s[a] + s[b]) & _M32; s[d] = _rotl(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & _M32; s[b] = _rotl(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b]) & _M32; s[d] = _rotl(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & _M32; s[b] = _rotl(s[b] ^ s[c], 7)


def _doppelrunden(s, runden=20):
    for _ in range(runden // 2):
        _viertelrunde(s, 0, 4, 8, 12)
        _viertelrunde(s, 1, 5, 9, 13)
        _viertelrunde(s, 2, 6, 10, 14)
        _viertelrunde(s, 3, 7, 11, 15)
        _viertelrunde(s, 0, 5, 10, 15)
        _viertelrunde(s, 1, 6, 11, 12)
        _viertelrunde(s, 2, 7, 8, 13)
        _viertelrunde(s, 3, 4, 9, 14)


def _zustand(key, zaehler, nonce):
    s = list(_KONST)
    s += list(struct.unpack("<8I", key))
    s.append(zaehler & _M32)
    s += list(struct.unpack("<3I", nonce))
    return s


def chacha20_block(key, zaehler, nonce):
    """Ein 64-Byte-Schlüsselstrom-Block. key 32 B, nonce 12 B."""
    s = _zustand(key, zaehler, nonce)
    w = list(s)
    _doppelrunden(w)
    return struct.pack("<16I", *[(w[i] + s[i]) & _M32 for i in range(16)])


def chacha20_xor(key, zaehler, nonce, daten):
    """Ver- und Entschlüsselung sind dieselbe Operation (XOR mit dem Strom)."""
    aus = bytearray(len(daten))
    for versatz in range(0, len(daten), 64):
        block = chacha20_block(key, zaehler + versatz // 64, nonce)
        stueck = daten[versatz:versatz + 64]
        for i, b in enumerate(stueck):
            aus[versatz + i] = b ^ block[i]
    return bytes(aus)


def hchacha20(key, nonce16):
    """Ableitung für die 24-Byte-Nonce-Variante (XChaCha20).

    Wie ChaCha20, aber OHNE die abschließende Addition des Anfangszustands —
    genau das macht daraus eine Schlüsselableitung statt eines Stroms."""
    s = list(_KONST) + list(struct.unpack("<8I", key)) + list(struct.unpack("<4I", nonce16))
    _doppelrunden(s)
    return struct.pack("<8I", *(s[0:4] + s[12:16]))


# ---------------------------------------------------------------------------
# Poly1305 (RFC 8439)
# ---------------------------------------------------------------------------

_P1305 = (1 << 130) - 5


def poly1305(nachricht, schluessel):
    """Einmal-Authentikator. schluessel = 32 B (r | s)."""
    r = int.from_bytes(schluessel[:16], "little") & 0x0ffffffc0ffffffc0ffffffc0fffffff
    s = int.from_bytes(schluessel[16:32], "little")
    akku = 0
    for versatz in range(0, len(nachricht), 16):
        stueck = nachricht[versatz:versatz + 16]
        n = int.from_bytes(stueck + b"\x01", "little")
        akku = ((akku + n) * r) % _P1305
    return ((akku + s) & ((1 << 128) - 1)).to_bytes(16, "little")


def _pad16(daten):
    rest = len(daten) % 16
    return b"\x00" * (16 - rest) if rest else b""


# ---------------------------------------------------------------------------
# AEAD: ChaCha20-Poly1305 und XChaCha20-Poly1305
# ---------------------------------------------------------------------------

class EntschluesselungFehlgeschlagen(Exception):
    """Das Siegel passt nicht — verändert, falscher Schlüssel oder Fremdpaket.

    Bewusst ohne Detail: Wer entschlüsselt, darf nie erfahren, WORAN es lag.
    Sonst wird die Fehlermeldung zum Orakel."""


def _aead_chacha20_poly1305_seal(key, nonce12, klartext, aad=b""):
    einmal = chacha20_block(key, 0, nonce12)[:32]
    geheim = chacha20_xor(key, 1, nonce12, klartext)
    material = (aad + _pad16(aad) + geheim + _pad16(geheim)
                + struct.pack("<Q", len(aad)) + struct.pack("<Q", len(geheim)))
    return geheim + poly1305(material, einmal)


def _aead_chacha20_poly1305_open(key, nonce12, paket, aad=b""):
    if len(paket) < 16:
        raise EntschluesselungFehlgeschlagen()
    geheim, siegel = paket[:-16], paket[-16:]
    einmal = chacha20_block(key, 0, nonce12)[:32]
    material = (aad + _pad16(aad) + geheim + _pad16(geheim)
                + struct.pack("<Q", len(aad)) + struct.pack("<Q", len(geheim)))
    if not gleich(poly1305(material, einmal), siegel):
        raise EntschluesselungFehlgeschlagen()
    return chacha20_xor(key, 1, nonce12, geheim)


NONCE_LAENGE = 24        # XChaCha20 — groß genug für Zufallsnoncen
SCHLUESSEL_LAENGE = 32
SIEGEL_LAENGE = 16


def versiegeln(key, klartext, aad=b"", nonce=None):
    """XChaCha20-Poly1305. Gibt nonce||geheimtext||siegel zurück.

    Die 24-Byte-Nonce ist der Grund für die X-Variante: sie darf gefahrlos
    zufällig gewählt werden. Bei 12 Byte müsste ein Zähler geführt werden —
    und der überlebt einen reinen RAM-Knoten nicht, der jederzeit neu startet."""
    if len(key) != SCHLUESSEL_LAENGE:
        raise ValueError("Schlüssel muss 32 Byte haben")
    nonce = nonce or zufall(NONCE_LAENGE)
    if len(nonce) != NONCE_LAENGE:
        raise ValueError("Nonce muss 24 Byte haben")
    if _HAT_SODIUM:
        paket = _sodium.crypto_aead_xchacha20poly1305_ietf_encrypt(
            bytes(klartext), bytes(aad), bytes(nonce), bytes(key))
        return bytes(nonce) + paket
    unter = hchacha20(bytes(key), bytes(nonce[:16]))
    return bytes(nonce) + _aead_chacha20_poly1305_seal(
        unter, b"\x00\x00\x00\x00" + bytes(nonce[16:24]), bytes(klartext), bytes(aad))


def entsiegeln(key, paket, aad=b""):
    """Gegenstück zu versiegeln(). Wirft bei jeder Abweichung."""
    if len(key) != SCHLUESSEL_LAENGE:
        raise ValueError("Schlüssel muss 32 Byte haben")
    if len(paket) < NONCE_LAENGE + SIEGEL_LAENGE:
        raise EntschluesselungFehlgeschlagen()
    nonce, rest = bytes(paket[:NONCE_LAENGE]), bytes(paket[NONCE_LAENGE:])
    if _HAT_SODIUM:
        try:
            return _sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(
                rest, bytes(aad), nonce, bytes(key))
        except Exception:
            raise EntschluesselungFehlgeschlagen()
    unter = hchacha20(bytes(key), nonce[:16])
    return _aead_chacha20_poly1305_open(
        unter, b"\x00\x00\x00\x00" + nonce[16:24], rest, bytes(aad))


# ---------------------------------------------------------------------------
# Curve25519 — gemeinsame Feldarithmetik für X25519 und Ed25519
# ---------------------------------------------------------------------------

_Q = (1 << 255) - 19
_L = (1 << 252) + 27742317777372353535851937790883648493


def _inv(x):
    return pow(x, _Q - 2, _Q)


# --- X25519 (RFC 7748): Schlüsseleinigung ----------------------------------

_A24 = 121665


def _clamp(k):
    k = bytearray(k)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    return int.from_bytes(k, "little")


def x25519(geheim32, punkt32):
    """Montgomery-Leiter. Gibt das gemeinsame Geheimnis (32 B) zurück."""
    if _HAT_SODIUM:
        return _sodium.crypto_scalarmult(bytes(geheim32), bytes(punkt32))
    k = _clamp(geheim32)
    u = int.from_bytes(punkt32, "little") & ((1 << 255) - 1)
    x1, x2, z2, x3, z3, getauscht = u, 1, 0, u, 1, 0
    for t in range(254, -1, -1):
        kt = (k >> t) & 1
        if getauscht ^ kt:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        getauscht = kt
        a = (x2 + z2) % _Q; aa = (a * a) % _Q
        b = (x2 - z2) % _Q; bb = (b * b) % _Q
        e = (aa - bb) % _Q
        c = (x3 + z3) % _Q
        d = (x3 - z3) % _Q
        da = (d * a) % _Q
        cb = (c * b) % _Q
        x3 = pow(da + cb, 2, _Q)
        z3 = (x1 * pow(da - cb, 2, _Q)) % _Q
        x2 = (aa * bb) % _Q
        z2 = (e * (aa + _A24 * e)) % _Q
    if getauscht:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return ((x2 * _inv(z2)) % _Q).to_bytes(32, "little")


_BASIS9 = (9).to_bytes(32, "little")


def x25519_schluesselpaar():
    """(geheim, oeffentlich) für die Schlüsseleinigung."""
    geheim = bytearray(zufall(32))
    geheim[0] &= 248
    geheim[31] &= 127
    geheim[31] |= 64
    return bytes(geheim), x25519(bytes(geheim), _BASIS9)


# --- Ed25519 (RFC 8032): Signaturen ----------------------------------------

_D = (-121665 * _inv(121666)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)


def _wiederherstellen_x(y, vorzeichen):
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    if (x * x - xx) % _Q != 0:
        return None
    if x % 2 != vorzeichen:
        x = _Q - x
    return x


_BY = (4 * _inv(5)) % _Q
_BX = _wiederherstellen_x(_BY, 0)
_B = (_BX, _BY, 1, (_BX * _BY) % _Q)          # erweiterte Koordinaten


def _punkt_add(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = ((y1 - x1) * (y2 - x2)) % _Q
    b = ((y1 + x1) * (y2 + x2)) % _Q
    c = (2 * t1 * t2 * _D) % _Q
    d = (2 * z1 * z2) % _Q
    e, f, g, h = (b - a) % _Q, (d - c) % _Q, (d + c) % _Q, (b + a) % _Q
    return ((e * f) % _Q, (g * h) % _Q, (f * g) % _Q, (e * h) % _Q)


def _punkt_mal(p, e):
    ergebnis = (0, 1, 1, 0)                    # neutrales Element
    while e > 0:
        if e & 1:
            ergebnis = _punkt_add(ergebnis, p)
        p = _punkt_add(p, p)
        e >>= 1
    return ergebnis


def _kodieren(p):
    x, y, z, _t = p
    zi = _inv(z)
    x, y = (x * zi) % _Q, (y * zi) % _Q
    return ((y & ~(1 << 255)) | ((x & 1) << 255)).to_bytes(32, "little")


def _dekodieren(s):
    i = int.from_bytes(s, "little")
    vorzeichen = i >> 255
    y = i & ((1 << 255) - 1)
    x = _wiederherstellen_x(y, vorzeichen)
    if x is None:
        return None
    return (x, y, 1, (x * y) % _Q)


def _h(m):
    return hashlib.sha512(m).digest()


def ed25519_schluesselpaar(saat=None):
    """(geheim32, oeffentlich32). Ohne Saat: frischer Zufall."""
    saat = bytes(saat) if saat is not None else zufall(32)
    if len(saat) != 32:
        raise ValueError("Saat muss 32 Byte haben")
    if _HAT_SODIUM:
        oeff, _voll = _sodium.crypto_sign_seed_keypair(saat)
        return saat, oeff
    h = bytearray(_h(saat)[:32])
    h[0] &= 248
    h[31] &= 127
    h[31] |= 64
    a = int.from_bytes(h, "little")
    return saat, _kodieren(_punkt_mal(_B, a))


def ed25519_signieren(saat32, oeffentlich32, nachricht):
    if _HAT_SODIUM:
        _oeff, voll = _sodium.crypto_sign_seed_keypair(bytes(saat32))
        return _sodium.crypto_sign(bytes(nachricht), voll)[:64]
    hs = _h(bytes(saat32))
    h = bytearray(hs[:32])
    h[0] &= 248
    h[31] &= 127
    h[31] |= 64
    a = int.from_bytes(h, "little")
    r = int.from_bytes(_h(hs[32:] + bytes(nachricht)), "little") % _L
    rp = _kodieren(_punkt_mal(_B, r))
    k = int.from_bytes(_h(rp + bytes(oeffentlich32) + bytes(nachricht)), "little") % _L
    s = (r + k * a) % _L
    return rp + s.to_bytes(32, "little")


def ed25519_pruefen(oeffentlich32, nachricht, signatur):
    """True/False — wirft nie, damit ein Fremdpaket keinen Ablauf sprengt."""
    try:
        if len(signatur) != 64 or len(oeffentlich32) != 32:
            return False
        if _HAT_SODIUM:
            try:
                _sodium.crypto_sign_open(bytes(signatur) + bytes(nachricht),
                                         bytes(oeffentlich32))
                return True
            except Exception:
                return False
        rp, s_roh = bytes(signatur[:32]), bytes(signatur[32:])
        s = int.from_bytes(s_roh, "little")
        if s >= _L:
            return False                        # nicht-kanonisch → ablehnen
        a = _dekodieren(bytes(oeffentlich32))
        r = _dekodieren(rp)
        if a is None or r is None:
            return False
        k = int.from_bytes(_h(rp + bytes(oeffentlich32) + bytes(nachricht)),
                           "little") % _L
        links = _punkt_mal(_B, s)
        rechts = _punkt_add(r, _punkt_mal(a, k))
        return gleich(_kodieren(links), _kodieren(rechts))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Schlüsselableitung
# ---------------------------------------------------------------------------

def ableiten(geheimnis, zweck, laenge=32):
    """BLAKE2b als KDF — ein Geheimnis, viele voneinander unabhängige Schlüssel.

    `zweck` trennt die Verwendungen: derselbe Rohschlüssel darf nie zugleich
    Nachrichten verschlüsseln und Knoten-IDs bilden."""
    return hashlib.blake2b(bytes(geheimnis), digest_size=laenge,
                           person=b"dowos-mesh-kdf",
                           salt=hashlib.blake2b(zweck.encode("utf-8"),
                                                digest_size=16).digest()).digest()


def sitzungsschluessel(eigen_geheim, fremd_oeffentlich, zweck="nachricht"):
    """X25519 + KDF → gemeinsamer Sitzungsschlüssel für XChaCha20-Poly1305."""
    return ableiten(x25519(eigen_geheim, fremd_oeffentlich), zweck)
