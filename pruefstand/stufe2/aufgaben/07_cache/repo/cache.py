import time


class Cache:
    def __init__(self, ttl, uhr=time.monotonic):
        self.ttl = ttl
        self.uhr = uhr
        self._daten = {}

    def setze(self, schluessel, wert):
        self._daten[schluessel] = (wert, self.uhr())

    def hole(self, schluessel, standard=None):
        if schluessel not in self._daten:
            return standard
        wert, zeit = self._daten[schluessel]
        if self.uhr() - zeit < self.ttl:
            del self._daten[schluessel]
            return standard
        return wert
