class Konto:
    def __init__(self, inhaber, stand=0):
        self.inhaber = inhaber
        self.stand = stand
        self.buchungen = []

    def einzahlen(self, betrag):
        self.stand += betrag
        self.buchungen.append(("ein", betrag))

    def abheben(self, betrag):
        self.stand -= betrag
        self.buchungen.append(("ab", betrag))
