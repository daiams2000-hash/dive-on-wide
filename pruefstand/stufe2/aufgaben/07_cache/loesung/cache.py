import time


class Cache:
    def __init__(self, ttl, uhr=time.monotonic):
        self.ttl = ttl
        self.uhr = uhr
        self._daten = {}

    def setze(self, schluessel, wert):
        self._daten[schluessel] = (wert, self.uhr())

    def _gueltig(self, zeit):
        return self.uhr() - zeit < self.ttl

    def hole(self, schluessel, standard=None):
        if schluessel not in self._daten:
            return standard
        wert, zeit = self._daten[schluessel]
        if not self._gueltig(zeit):
            del self._daten[schluessel]
            return standard
        return wert

    def anzahl(self):
        for k in [k for k, (_, z) in self._daten.items() if not self._gueltig(z)]:
            del self._daten[k]
        return len(self._daten)
