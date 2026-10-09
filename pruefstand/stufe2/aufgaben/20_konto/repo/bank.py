def ueberweisen(von, nach, betrag):
    von.abheben(betrag)
    nach.einzahlen(betrag)


def sammelueberweisung(von, auftraege):
    # auftraege: Liste von (zielkonto, betrag)
    for nach, betrag in auftraege:
        ueberweisen(von, nach, betrag)
