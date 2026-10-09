from geld import format_euro


def zusammenfassung(umsatz, kosten):
    return (f"Umsatz {format_euro(umsatz)}, Kosten {format_euro(kosten)}, "
            f"Ergebnis {format_euro(umsatz - kosten)}")
