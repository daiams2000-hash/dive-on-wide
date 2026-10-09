from standard import STANDARD


def lade(nutzer):
    ergebnis = dict(STANDARD)
    ergebnis.update(nutzer)
    return ergebnis
