def zeile(beschreibung, menge, preis):
    betrag = menge * preis
    return f"{beschreibung}: {menge} x {preis:.2f} EUR = {betrag:.2f} EUR"


def gesamt(positionen):
    # positionen: Liste von (beschreibung, menge, preis)
    return f"Gesamt: {sum(m * p for _, m, p in positionen):.2f} EUR"
