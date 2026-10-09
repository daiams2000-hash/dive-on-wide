TABELLE = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
           (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def zu_roemisch(n):
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 3999:
        raise ValueError("nur 1 bis 3999")
    teile = []
    for wert, zeichen in TABELLE:
        while n >= wert:
            teile.append(zeichen)
            n -= wert
    return "".join(teile)


def von_roemisch(text):
    s = text.strip().upper()
    pos, summe = 0, 0
    for wert, zeichen in TABELLE:
        while s.startswith(zeichen, pos):
            summe += wert
            pos += len(zeichen)
    if not s or pos != len(s) or summe == 0 or summe > 3999:
        raise ValueError("ungueltige roemische Zahl: %r" % text)
    if zu_roemisch(summe) != s:
        raise ValueError("nicht kanonisch: %r" % text)
    return summe
