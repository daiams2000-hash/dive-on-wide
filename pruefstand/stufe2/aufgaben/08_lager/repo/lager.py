class Lager:
    def __init__(self, artikel=[]):
        self.artikel = artikel

    def hinzufuegen(self, name, menge=1):
        for a in self.artikel:
            if a["name"] == name:
                a["menge"] += menge
                return
        self.artikel.append({"name": name, "menge": menge})

    def entnehmen(self, name, menge=1):
        for a in self.artikel:
            if a["name"] == name:
                a["menge"] -= menge
                return

    def bestand(self, name):
        for a in self.artikel:
            if a["name"] == name:
                return a["menge"]
        return 0
