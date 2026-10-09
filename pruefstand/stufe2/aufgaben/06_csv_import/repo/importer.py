def lese_kunden(text):
    zeilen = text.strip().split("\n")
    kopf = zeilen[0].split(",")
    kunden = []
    for zeile in zeilen[1:-1]:
        werte = zeile.split(",")
        kunden.append(dict(zip(kopf, werte)))
    return kunden
