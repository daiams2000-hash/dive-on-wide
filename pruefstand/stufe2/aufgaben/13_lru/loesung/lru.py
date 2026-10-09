from collections import OrderedDict


class LRUCache:
    def __init__(self, kapazitaet):
        if kapazitaet < 1:
            raise ValueError("kapazitaet muss >= 1 sein")
        self.kapazitaet = kapazitaet
        self._d = OrderedDict()

    def setze(self, schluessel, wert):
        if schluessel in self._d:
            self._d.move_to_end(schluessel)
        self._d[schluessel] = wert
        if len(self._d) > self.kapazitaet:
            self._d.popitem(last=False)

    def hole(self, schluessel, standard=None):
        if schluessel not in self._d:
            return standard
        self._d.move_to_end(schluessel)
        return self._d[schluessel]

    def __len__(self):
        return len(self._d)

    def __contains__(self, schluessel):
        return schluessel in self._d
