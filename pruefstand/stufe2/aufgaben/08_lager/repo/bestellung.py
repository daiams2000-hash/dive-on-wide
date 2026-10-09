def verarbeite(bestellung, lager):
    # bestellung: dict name -> menge
    for name, menge in bestellung.items():
        lager.entnehmen(name, menge)
    return lager
