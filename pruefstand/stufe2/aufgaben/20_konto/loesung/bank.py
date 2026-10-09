def _sichern(konten):
    return [(k, k.stand, list(k.buchungen)) for k in konten]


def _zuruecksetzen(sicherung):
    for k, stand, buchungen in sicherung:
        k.stand, k.buchungen = stand, buchungen


def ueberweisen(von, nach, betrag):
    sicherung = _sichern([von, nach])
    try:
        von.abheben(betrag)
        nach.einzahlen(betrag)
    except Exception:
        _zuruecksetzen(sicherung)
        raise


def sammelueberweisung(von, auftraege):
    beteiligt = [von] + [n for n, _ in auftraege]
    einmalig = list({id(k): k for k in beteiligt}.values())
    sicherung = _sichern(einmalig)
    try:
        for nach, betrag in auftraege:
            ueberweisen(von, nach, betrag)
    except Exception:
        _zuruecksetzen(sicherung)
        raise
