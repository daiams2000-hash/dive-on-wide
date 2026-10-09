class Lager:
    def __init__(self, artikel=None):
        self.artikel = [] if artikel is None else artikel

    def _finde(self, name):
        for a in self.artikel:
            if a["name"] == name:
                return a
        return None

    def hinzufuegen(self, name, menge=1):
        a = self._finde(name)
        if a:
            a["menge"] += menge
        else:
            self.artikel.append({"name": name, "menge": menge})

    def entnehmen(self, name, menge=1):
        a = self._finde(name)
        if a is None:
            raise ValueError("unbekannt: %s" % name)
        if menge > a["menge"]:
            raise ValueError("zu wenig Bestand: %s" % name)
        a["menge"] -= menge

    def bestand(self, name):
        a = self._finde(name)
        return a["menge"] if a else 0
