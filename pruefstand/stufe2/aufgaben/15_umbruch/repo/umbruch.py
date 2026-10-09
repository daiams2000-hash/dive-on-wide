def umbrechen(text, breite):
    woerter = text.split(" ")
    zeilen, aktuell = [], ""
    for w in woerter:
        if len(aktuell) + len(w) > breite:
            zeilen.append(aktuell)
            aktuell = w
        else:
            aktuell += " " + w
    zeilen.append(aktuell)
    return "\n".join(zeilen)
