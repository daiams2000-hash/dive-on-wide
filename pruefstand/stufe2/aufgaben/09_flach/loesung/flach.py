def flach(daten, prefix=""):
    ergebnis = {}
    if isinstance(daten, dict):
        paare = daten.items()
    else:
        paare = enumerate(daten)
    for k, v in paare:
        schluessel = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, (dict, list)) and v:
            ergebnis.update(flach(v, schluessel))
        else:
            ergebnis[schluessel] = v
    return ergebnis
