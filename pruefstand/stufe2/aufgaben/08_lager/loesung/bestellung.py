def verarbeite(bestellung, lager):
    for name, menge in bestellung.items():
        if menge > lager.bestand(name) or lager._finde(name) is None:
            raise ValueError("nicht lieferbar: %s" % name)
    for name, menge in bestellung.items():
        lager.entnehmen(name, menge)
    return lager
