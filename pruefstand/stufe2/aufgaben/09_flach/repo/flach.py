def flach(daten, prefix=""):
    ergebnis = {}
    for k, v in daten.items():
        schluessel = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            ergebnis.update(flach(v, schluessel))
        else:
            ergebnis[schluessel] = v
    return ergebnis
