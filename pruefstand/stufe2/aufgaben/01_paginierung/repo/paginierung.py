def seite(elemente, nummer, groesse=10):
    # Gibt die Elemente der Seite `nummer` zurueck (Seiten beginnen bei 1).
    start = nummer * groesse
    return elemente[start:start + groesse]


def seitenzahl(anzahl, groesse=10):
    return anzahl // groesse
