def reihenfolge(pakete):
    # pakete: dict name -> liste von abhaengigkeiten
    ergebnis = []

    def besuche(name):
        for dep in pakete.get(name, []):
            besuche(dep)
        ergebnis.append(name)

    for name in pakete:
        besuche(name)
    return ergebnis
