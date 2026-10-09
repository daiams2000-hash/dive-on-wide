from decimal import Decimal, ROUND_HALF_UP


def _runden(wert):
    return float(Decimal(wert).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def brutto(netto, mwst=19):
    return _runden(Decimal(str(netto)) * (Decimal(100) + Decimal(str(mwst))) / Decimal(100))


def summe(positionen):
    return _runden(sum((Decimal(str(m)) * Decimal(str(p)) for m, p in positionen), Decimal(0)))
