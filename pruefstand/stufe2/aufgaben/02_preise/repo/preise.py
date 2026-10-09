def brutto(netto, mwst=19):
    return round(netto * (1 + mwst / 100), 2)


def summe(positionen):
    # positionen: Liste von (menge, einzelpreis)
    return sum(m * p for m, p in positionen)
