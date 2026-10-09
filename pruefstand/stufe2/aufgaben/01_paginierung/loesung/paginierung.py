def seite(elemente, nummer, groesse=10):
    if nummer < 1:
        raise ValueError("Seiten beginnen bei 1")
    start = (nummer - 1) * groesse
    return elemente[start:start + groesse]


def seitenzahl(anzahl, groesse=10):
    return -(-anzahl // groesse)
