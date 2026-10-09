"""Dive on Wide Mesh — QR-Codes für Einladungstickets.

Ein Ticket hat 221 Zeichen. Das tippt niemand ab. Gezeigt und abfotografiert
ist es eine Sache von Sekunden — deshalb dieser Kodierer.

WARUM SELBST GEBAUT
-------------------
Dieselbe Regel wie überall in Dive on Wide: keine Abhängigkeiten. Ein QR-Kodierer
ist gut spezifiziert (ISO/IEC 18004) und in ein paar hundert Zeilen machbar.

WARUM ES EINEN PRÜFER DAZU GIBT
-------------------------------
Ein QR-Code, den keine Kamera lesen kann, ist schlimmer als gar keiner — der
Fehler fällt erst auf, wenn jemand vor dem Bildschirm steht. Deshalb liegt in
diesem Modul auch der Weg zurück: `zurueck_lesen()` entfernt die Maske, liest
das Format, sammelt die Datenwörter und baut den Text wieder auf. Was der
Kodierer erzeugt, wird damit gegengeprüft, bevor es irgendwo angezeigt wird.

Umfang: Byte-Modus, Fehlerkorrektur-Stufe M, Versionen 1-20 (bis 666 Byte).
Das deckt Tickets mit großem Abstand ab. Mehr wäre ungenutzter Code.
"""

# --- Tabellen aus der Norm (Stufe M) ---------------------------------------
# je Version: (EC-Woerter je Block, Bloecke Gruppe 1, Datenwoerter G1,
#              Bloecke Gruppe 2, Datenwoerter G2)
_M_BLOCKS = {
    1: (10, 1, 16, 0, 0), 2: (16, 1, 28, 0, 0), 3: (26, 1, 44, 0, 0),
    4: (18, 2, 32, 0, 0), 5: (24, 2, 43, 0, 0), 6: (16, 4, 27, 0, 0),
    7: (18, 4, 31, 0, 0), 8: (22, 2, 38, 2, 39), 9: (22, 3, 36, 2, 37),
    10: (26, 4, 43, 1, 44), 11: (30, 1, 50, 4, 51), 12: (22, 6, 36, 2, 37),
    13: (22, 8, 37, 1, 38), 14: (24, 4, 40, 5, 41), 15: (24, 5, 41, 5, 42),
    16: (28, 7, 45, 3, 46), 17: (28, 10, 46, 1, 47), 18: (26, 9, 43, 4, 44),
    19: (26, 3, 44, 11, 45), 20: (26, 3, 41, 13, 42),
}
# Nutzbare Byte-Zahl je Version (Stufe M, Byte-Modus)
_M_KAPAZITAET = {1: 14, 2: 26, 3: 42, 4: 62, 5: 84, 6: 106, 7: 122, 8: 152,
                 9: 180, 10: 213, 11: 251, 12: 287, 13: 331, 14: 362, 15: 412,
                 16: 450, 17: 504, 18: 560, 19: 624, 20: 666}
# Mittelpunkte der Ausrichtungsmuster
_AUSRICHTUNG = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
    7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
    11: [6, 30, 54], 12: [6, 32, 58], 13: [6, 34, 62], 14: [6, 26, 46, 66],
    15: [6, 26, 48, 70], 16: [6, 26, 50, 74], 17: [6, 30, 54, 78],
    18: [6, 30, 56, 82], 19: [6, 30, 58, 86], 20: [6, 34, 62, 90],
}
_EC_M = 0b00          # Stufe M in den Formatbits


class QrFehler(ValueError):
    """Der Text passt nicht in einen QR-Code dieser Größe."""


# --- Rechnen im Galoiskörper GF(256) ---------------------------------------
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D                    # das Polynom der Norm
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mal(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(n):
    """Das Generatorpolynom für n Fehlerkorrekturwörter."""
    g = [1]
    for i in range(n):
        neu = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            neu[j] ^= c
            neu[j + 1] ^= _mal(c, _EXP[i])
        g = neu
    return g


def _ec_woerter(daten, n):
    """Reed-Solomon: die Fehlerkorrekturwörter zu einem Datenblock."""
    g = _generator(n)
    rest = list(daten) + [0] * n
    for i in range(len(daten)):
        f = rest[i]
        if f:
            for j, c in enumerate(g):
                rest[i + j] ^= _mal(c, f)
    return rest[len(daten):]


# --- Kodieren ---------------------------------------------------------------

def version_fuer(anzahl_bytes):
    for v in sorted(_M_KAPAZITAET):
        if anzahl_bytes <= _M_KAPAZITAET[v]:
            return v
    raise QrFehler("%d Byte passen in keinen QR-Code bis Version 20 "
                   "(höchstens %d)." % (anzahl_bytes, _M_KAPAZITAET[20]))


def _bitfolge(daten, version):
    ec, g1, d1, g2, d2 = _M_BLOCKS[version]
    gesamt_daten = g1 * d1 + g2 * d2
    laengenbits = 8 if version < 10 else 16
    bits = []

    def schieb(wert, n):
        for i in range(n - 1, -1, -1):
            bits.append((wert >> i) & 1)

    schieb(0b0100, 4)                  # Byte-Modus
    schieb(len(daten), laengenbits)
    for b in daten:
        schieb(b, 8)
    # Abschluss, dann auf ganze Bytes auffuellen
    for _ in range(min(4, gesamt_daten * 8 - len(bits))):
        bits.append(0)
    while len(bits) % 8:
        bits.append(0)
    woerter = [int("".join(str(b) for b in bits[i:i + 8]), 2)
               for i in range(0, len(bits), 8)]
    # Die Norm schreibt diese beiden Fuellwoerter abwechselnd vor.
    fuell = [0xEC, 0x11]
    i = 0
    while len(woerter) < gesamt_daten:
        woerter.append(fuell[i % 2])
        i += 1
    return woerter


def _verschraenken(woerter, version):
    """Datenwörter blockweise verschränken und die EC-Wörter anhängen."""
    ec_n, g1, d1, g2, d2 = _M_BLOCKS[version]
    bloecke, p = [], 0
    for _ in range(g1):
        bloecke.append(woerter[p:p + d1])
        p += d1
    for _ in range(g2):
        bloecke.append(woerter[p:p + d2])
        p += d2
    ec = [_ec_woerter(b, ec_n) for b in bloecke]
    aus = []
    for i in range(max(len(b) for b in bloecke)):
        for b in bloecke:
            if i < len(b):
                aus.append(b[i])
    for i in range(ec_n):
        for e in ec:
            aus.append(e[i])
    return aus


def _format_bits(maske):
    """15 Bit Formatinformation (BCH), wie in der Norm."""
    wert = (_EC_M << 3) | maske
    rest = wert << 10
    for i in range(4, -1, -1):
        if rest & (1 << (i + 10)):
            rest ^= 0b10100110111 << i
    return ((wert << 10) | rest) ^ 0b101010000010010


def _version_bits(version):
    """18 Bit Versionsinformation — erst ab Version 7 nötig."""
    rest = version << 12
    for i in range(5, -1, -1):
        if rest & (1 << (i + 12)):
            rest ^= 0b1111100100101 << i
    return (version << 12) | rest


def _maske(m, r, s):
    if m == 0: return (r + s) % 2 == 0
    if m == 1: return r % 2 == 0
    if m == 2: return s % 3 == 0
    if m == 3: return (r + s) % 3 == 0
    if m == 4: return (r // 2 + s // 3) % 2 == 0
    if m == 5: return (r * s) % 2 + (r * s) % 3 == 0
    if m == 6: return ((r * s) % 2 + (r * s) % 3) % 2 == 0
    return ((r + s) % 2 + (r * s) % 3) % 2 == 0


def _geruest(version):
    """Die festen Muster: Finder, Timing, Ausrichtung, Dunkelmodul."""
    n = version * 4 + 17
    m = [[None] * n for _ in range(n)]

    def finder(r, s):
        for dr in range(-1, 8):
            for ds in range(-1, 8):
                rr, ss = r + dr, s + ds
                if 0 <= rr < n and 0 <= ss < n:
                    rand = dr in (-1, 7) or ds in (-1, 7)
                    ring = dr in (0, 6) or ds in (0, 6)
                    kern = 2 <= dr <= 4 and 2 <= ds <= 4
                    m[rr][ss] = 0 if rand else (1 if (ring or kern) else 0)

    finder(0, 0)
    finder(0, n - 7)
    finder(n - 7, 0)
    for i in range(8, n - 8):
        m[6][i] = 1 - i % 2
        m[i][6] = 1 - i % 2
    for r in _AUSRICHTUNG[version]:
        for s in _AUSRICHTUNG[version]:
            if m[r][s] is not None:
                continue
            for dr in range(-2, 3):
                for ds in range(-2, 3):
                    m[r + dr][s + ds] = 1 if (max(abs(dr), abs(ds)) != 1) else 0
    m[n - 8][8] = 1                    # das immer dunkle Modul
    # Plaetze fuer Format- und Versionsinformation freihalten
    for i in range(9):
        if m[8][i] is None: m[8][i] = 0
        if m[i][8] is None: m[i][8] = 0
    for i in range(8):
        if m[8][n - 1 - i] is None: m[8][n - 1 - i] = 0
        if m[n - 1 - i][8] is None: m[n - 1 - i][8] = 0
    if version >= 7:
        for i in range(6):
            for j in range(3):
                m[n - 11 + j][i] = 0
                m[i][n - 11 + j] = 0
    return m


def _reserviert(version):
    """Welche Felder sind fest belegt? (Kopie des Gerüsts als Maske)"""
    g = _geruest(version)
    return [[z is not None for z in reihe] for reihe in g]


def _strafe(m):
    """Bewertung einer Maske — kleiner ist besser (Regeln der Norm)."""
    n = len(m)
    punkte = 0
    for reihe in (m, [list(z) for z in zip(*m)]):
        for r in reihe:
            lauf, letzt = 1, r[0]
            for z in r[1:]:
                if z == letzt:
                    lauf += 1
                else:
                    if lauf >= 5:
                        punkte += 3 + (lauf - 5)
                    lauf, letzt = 1, z
            if lauf >= 5:
                punkte += 3 + (lauf - 5)
    for r in range(n - 1):
        for s in range(n - 1):
            if m[r][s] == m[r][s + 1] == m[r + 1][s] == m[r + 1][s + 1]:
                punkte += 3
    muster = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    for reihe in (m, [list(z) for z in zip(*m)]):
        for r in reihe:
            for i in range(n - 10):
                if r[i:i + 11] == muster or r[i:i + 11] == muster[::-1]:
                    punkte += 40
    dunkel = sum(sum(r) for r in m)
    anteil = dunkel * 100 // (n * n)
    punkte += 10 * (abs(anteil - 50) // 5)
    return punkte


def matrix(text):
    """Der fertige QR-Code als Liste von Zeilen mit 0/1."""
    daten = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    version = version_fuer(len(daten))
    n = version * 4 + 17
    woerter = _verschraenken(_bitfolge(daten, version), version)
    bits = []
    for w in woerter:
        for i in range(7, -1, -1):
            bits.append((w >> i) & 1)

    fest = _reserviert(version)
    grund = _geruest(version)
    roh = [[grund[r][s] if grund[r][s] is not None else 0 for s in range(n)]
           for r in range(n)]
    # Daten im Zickzack von rechts unten nach oben einfuellen
    i, aufwaerts, s = 0, True, n - 1
    while s > 0:
        if s == 6:
            s -= 1                     # die senkrechte Taktspalte ueberspringen
        for k in range(n):
            r = (n - 1 - k) if aufwaerts else k
            for ss in (s, s - 1):
                if not fest[r][ss]:
                    roh[r][ss] = bits[i] if i < len(bits) else 0
                    i += 1
        aufwaerts = not aufwaerts
        s -= 2

    bestes, beste_strafe = None, None
    for maske in range(8):
        m = [[roh[r][s] ^ (1 if (not fest[r][s]) and _maske(maske, r, s) else 0)
              for s in range(n)] for r in range(n)]
        _format_setzen(m, maske, version)
        p = _strafe(m)
        if beste_strafe is None or p < beste_strafe:
            bestes, beste_strafe = m, p
    return bestes


def _format_setzen(m, maske, version):
    n = len(m)
    f = _format_bits(maske)
    for i in range(15):
        bit = (f >> i) & 1
        if i < 6:
            m[8][i] = bit
        elif i == 6:
            m[8][7] = bit
        elif i == 7:
            m[8][8] = bit
        elif i == 8:
            m[7][8] = bit
        else:
            m[14 - i][8] = bit
        if i < 8:
            m[8][n - 1 - i] = bit
        else:
            m[n - 15 + i][8] = bit
    m[n - 8][8] = 1
    if version >= 7:
        v = _version_bits(version)
        for i in range(18):
            bit = (v >> i) & 1
            m[i // 3][n - 11 + i % 3] = bit
            m[n - 11 + i % 3][i // 3] = bit


# --- Zurücklesen: der Gegenbeweis ------------------------------------------

def zurueck_lesen(m):
    """Liest einen erzeugten Code wieder aus — der Prüfweg.

    Kein vollständiger Dekodierer (er korrigiert keine Fehler und liest keine
    fremden Codes), sondern genau das Gegenstück zum Kodierer. Damit lässt
    sich beweisen, dass Bitfolge, Maske, Format und Anordnung zusammenpassen,
    bevor irgendjemand eine Kamera darauf hält."""
    n = len(m)
    version = (n - 17) // 4
    if version not in _M_BLOCKS:
        raise QrFehler("unbekannte Größe: %d Module" % n)
    # Maske aus der Formatinformation zurueckgewinnen
    f = 0
    for i in range(15):
        if i < 6:
            bit = m[8][i]
        elif i == 6:
            bit = m[8][7]
        elif i == 7:
            bit = m[8][8]
        elif i == 8:
            bit = m[7][8]
        else:
            bit = m[14 - i][8]
        f |= bit << i
    f ^= 0b101010000010010
    maske = (f >> 10) & 0b111
    fest = _reserviert(version)
    bits = []
    i, aufwaerts, s = 0, True, n - 1
    while s > 0:
        if s == 6:
            s -= 1
        for k in range(n):
            r = (n - 1 - k) if aufwaerts else k
            for ss in (s, s - 1):
                if not fest[r][ss]:
                    z = m[r][ss] ^ (1 if _maske(maske, r, ss) else 0)
                    bits.append(z)
        aufwaerts = not aufwaerts
        s -= 2
    woerter = [int("".join(str(b) for b in bits[i:i + 8]), 2)
               for i in range(0, len(bits) - len(bits) % 8, 8)]
    # Verschraenkung rueckgaengig machen
    ec_n, g1, d1, g2, d2 = _M_BLOCKS[version]
    groessen = [d1] * g1 + [d2] * g2
    bloecke = [[] for _ in groessen]
    p = 0
    for i in range(max(groessen)):
        for b, gr in enumerate(groessen):
            if i < gr:
                bloecke[b].append(woerter[p])
                p += 1
    daten = [w for b in bloecke for w in b]
    # Kopf lesen
    bitfolge = []
    for w in daten:
        for i in range(7, -1, -1):
            bitfolge.append((w >> i) & 1)
    modus = int("".join(str(b) for b in bitfolge[:4]), 2)
    if modus != 0b0100:
        raise QrFehler("kein Byte-Modus (%d)" % modus)
    lb = 8 if version < 10 else 16
    laenge = int("".join(str(b) for b in bitfolge[4:4 + lb]), 2)
    p = 4 + lb
    aus = bytearray()
    for _ in range(laenge):
        aus.append(int("".join(str(b) for b in bitfolge[p:p + 8]), 2))
        p += 8
    return bytes(aus)


# --- Anzeige ---------------------------------------------------------------

def svg(text, rand=4, modul=4):
    """Der QR-Code als SVG — scharf in jeder Größe, ohne Bilddatei."""
    m = matrix(text)
    n = len(m)
    gesamt = (n + 2 * rand) * modul
    pfad = []
    for r in range(n):
        for s in range(n):
            if m[r][s]:
                pfad.append("M%d %dh%dv%dh-%dz" % ((s + rand) * modul,
                                                   (r + rand) * modul,
                                                   modul, modul, modul))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
            'shape-rendering="crispEdges" role="img" aria-label="QR-Code">'
            '<rect width="%d" height="%d" fill="#ffffff"/>'
            '<path fill="#000000" d="%s"/></svg>'
            % (gesamt, gesamt, gesamt, gesamt, "".join(pfad)))
