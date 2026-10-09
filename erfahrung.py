# -*- coding: utf-8 -*-
"""Erfahrung: wie sich jedes Modell im Alltag auf DIESEM Rechner schlägt.

Gemessene Güte (modell_guete.json) stammt aus vollständigen Prüfläufen — selten, aufwendig. Erfahrung kommt aus
jedem echten Werkbank-Lauf: fertig, festgefahren, Schrittlimit, Fehler, Speichernot, und wie lange. Gezählt, nicht
geschätzt; nichts davon verlässt den Rechner. Der Lotse nennt diese Zahlen, wenn er Modelle empfiehlt — so wird
die Empfehlung mit jedem Lauf genauer (erste Stufe des Lernkreislaufs).
"""
import json
import os
import threading
import time

AUSGAENGE = ("fertig", "frage", "festgefahren", "limit", "zeit", "fehler", "speichernot", "abgebrochen")


class Erfahrung:
    def __init__(self, pfad):
        self.pfad = pfad
        self._sperre = threading.Lock()

    def lesen(self):
        try:
            with open(self.pfad, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def buchen(self, modell, ausgang, sekunden=None, art="werkbank", jetzt=None):
        modell = str(modell or "").split("@@")[-1].strip()
        if not modell:
            return
        ausgang = ausgang if ausgang in AUSGAENGE else "fehler"
        with self._sperre:
            d = self.lesen()
            m = d.setdefault(modell, {})
            e = m.setdefault(art, {"laeufe": 0, "sekunden": 0.0, "zuletzt": 0})
            e["laeufe"] += 1
            e[ausgang] = e.get(ausgang, 0) + 1
            if sekunden:
                e["sekunden"] = round(e.get("sekunden", 0.0) + float(sekunden), 1)
            e["zuletzt"] = int(jetzt or time.time())
            os.makedirs(os.path.dirname(self.pfad) or ".", exist_ok=True)
            tmp = self.pfad + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.pfad)

    def zusammenfassung(self, art="werkbank", mindestens=1):
        """[(modell, Text)] — absteigend nach Anzahl der Läufe."""
        aus = []
        for modell, m in self.lesen().items():
            e = m.get(art)
            if not e or e.get("laeufe", 0) < mindestens:
                continue
            n = e["laeufe"]
            teile = ["%d Läufe" % n, "%d fertig (%d %%)" % (e.get("fertig", 0) + e.get("frage", 0),
                                                            round(100 * (e.get("fertig", 0) + e.get("frage", 0)) / n))]
            for k, wort in (("festgefahren", "festgefahren"), ("limit", "am Schrittlimit"), ("speichernot", "Speichernot"),
                            ("fehler", "Fehler")):
                if e.get(k):
                    teile.append("%d %s" % (e[k], wort))
            if e.get("sekunden"):
                teile.append("Ø %.1f min" % (e["sekunden"] / n / 60))
            aus.append((n, modell, ", ".join(teile)))
        return [(modell, text) for n, modell, text in sorted(aus, reverse=True)]
