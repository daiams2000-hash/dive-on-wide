from geld import format_euro


def zeile(beschreibung, menge, preis):
    return f"{beschreibung}: {menge} x {format_euro(preis)} = {format_euro(menge * preis)}"


def gesamt(positionen):
    return "Gesamt: " + format_euro(sum(m * p for _, m, p in positionen))
