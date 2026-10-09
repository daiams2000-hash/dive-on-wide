import re


def umbrechen(text, breite):
    if breite < 1:
        raise ValueError("breite muss >= 1 sein")
    absaetze = [a for a in re.split(r"\n\s*\n", text.strip()) if a.strip()]
    ergebnis = []
    for absatz in absaetze:
        zeilen, aktuell = [], ""
        for wort in absatz.split():
            if len(wort) > breite:
                if aktuell:
                    zeilen.append(aktuell)
                    aktuell = ""
                while len(wort) > breite:
                    zeilen.append(wort[:breite])
                    wort = wort[breite:]
            if not wort:
                continue
            if not aktuell:
                aktuell = wort
            elif len(aktuell) + 1 + len(wort) <= breite:
                aktuell += " " + wort
            else:
                zeilen.append(aktuell)
                aktuell = wort
        if aktuell:
            zeilen.append(aktuell)
        ergebnis.append("\n".join(zeilen))
    return "\n\n".join(ergebnis)
