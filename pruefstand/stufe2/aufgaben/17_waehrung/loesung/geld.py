from decimal import Decimal, ROUND_HALF_UP


def format_euro(betrag):
    d = Decimal(str(betrag)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if d == 0:
        d = Decimal("0.00")
    vorzeichen = "-" if d < 0 else ""
    ganz, _, rest = f"{abs(d):.2f}".partition(".")
    return vorzeichen + f"{int(ganz):,}".replace(",", ".") + "," + rest + " €"
