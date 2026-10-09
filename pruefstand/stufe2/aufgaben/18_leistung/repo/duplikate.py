def gemeinsame(a, b):
    return [x for x in a if x in b]


def doppelte(werte):
    ergebnis = []
    for i, x in enumerate(werte):
        if x in werte[i + 1:] and x not in ergebnis:
            ergebnis.append(x)
    return ergebnis
