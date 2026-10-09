class Konto:
    def __init__(self, inhaber, stand=0):
        self.inhaber = inhaber
        self.stand = stand
        self.buchungen = []

    @staticmethod
    def _pruefe(betrag):
        if betrag <= 0:
            raise ValueError("Betrag muss groesser als 0 sein")

    def einzahlen(self, betrag):
        self._pruefe(betrag)
        self.stand += betrag
        self.buchungen.append(("ein", betrag))

    def abheben(self, betrag):
        self._pruefe(betrag)
        if betrag > self.stand:
            raise ValueError("keine Deckung")
        self.stand -= betrag
        self.buchungen.append(("ab", betrag))
