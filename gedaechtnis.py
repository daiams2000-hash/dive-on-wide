# -*- coding: utf-8 -*-
"""Gedächtnis des Werkbank-Agenten — was über einen Lauf hinaus gilt.

Zwei Teile, nach dem Vorbild von Claude Codes Auto-Memory und Hermes:

1. **Notizen** (`merken`): kurze, dauerhafte Fakten — „Tests laufen mit make
   test“, „der Nutzer will deutsche Kommentare“. Getrennt für das Projekt und
   global. Sie stehen bei jedem Lauf im System-Prompt, die neuesten zuerst und
   begrenzt, damit sie den Kontext nicht auffressen.
2. **Erinnerung** (`erinnern`): Volltextsuche über alle früheren Werkbank-Läufe
   (Aufgabe, Gedanken, Zusammenfassung, geänderte Dateien) mit SQLite FTS5 —
   „wie haben wir das letzte Mal den Export gelöst?“.

Beides liegt in Dive on Wide (storage/werkbank/), nicht im Projekt: Ein Repository
soll nicht mitschleppen, was der Agent über seinen Nutzer gelernt hat.
"""

import hashlib
import json
import os
import re
import sqlite3
import time

NOTIZ_GRENZE = 300           # Zeichen je Notiz
PROMPT_GRENZE = 3000         # Zeichen aller Notizen im Prompt
MAX_NOTIZEN = 200


def _kennung(ordner):
    return hashlib.sha256(os.path.realpath(ordner).encode()).hexdigest()[:16]


class Gedaechtnis:
    def __init__(self, ablage):
        self.ablage = ablage
        self.notizen_dir = os.path.join(ablage, "gedaechtnis")

    # ------------------------------------------------------------- Notizen ---
    def _datei(self, ordner=None):
        return os.path.join(self.notizen_dir, ("projekt_%s.json" % _kennung(ordner)) if ordner else "global.json")

    def notizen(self, ordner=None):
        try:
            with open(self._datei(ordner), encoding="utf-8") as f:
                d = json.load(f)
            return d.get("notizen", [])
        except (OSError, ValueError):
            return []

    def _schreiben(self, ordner, notizen):
        os.makedirs(self.notizen_dir, exist_ok=True)
        pfad = self._datei(ordner)
        tmp = pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ordner": os.path.realpath(ordner) if ordner else None, "notizen": notizen[-MAX_NOTIZEN:]},
                      f, ensure_ascii=False, indent=1)
        os.replace(tmp, pfad)

    def merken(self, text, ordner=None):
        text = " ".join(str(text or "").split())
        if not text:
            raise ValueError("Leere Notiz.")
        if len(text) > NOTIZ_GRENZE:
            raise ValueError("Notiz zu lang (höchstens %d Zeichen) — Notizen sind kurze, dauerhafte Fakten." % NOTIZ_GRENZE)
        liste = self.notizen(ordner)
        if any(n["text"].lower() == text.lower() for n in liste):
            return False
        liste.append({"text": text, "zeit": time.time()})
        self._schreiben(ordner, liste)
        return True

    def ersetzen(self, notizen, ordner=None):
        """Für die Oberfläche: der Nutzer bearbeitet die Liste als Ganzes."""
        sauber = []
        for n in notizen:
            text = " ".join(str(n.get("text", n) if isinstance(n, dict) else n).split())[:NOTIZ_GRENZE]
            if text:
                sauber.append({"text": text, "zeit": n.get("zeit", time.time()) if isinstance(n, dict) else time.time()})
        self._schreiben(ordner, sauber)
        return sauber

    def fuer_prompt(self, ordner):
        teile = []
        for titel, liste in (("dieses Projekt", self.notizen(ordner)), ("allgemein", self.notizen(None))):
            zeilen, laenge = [], 0
            for n in reversed(liste):                 # die neuesten zuerst
                if laenge + len(n["text"]) > PROMPT_GRENZE // 2:
                    break
                zeilen.append("- " + n["text"])
                laenge += len(n["text"])
            if zeilen:
                teile.append("Gemerkt (%s):\n%s" % (titel, "\n".join(zeilen)))
        return "\n".join(teile)

    # ---------------------------------------------------------- Erinnerung ---
    def _db(self):
        os.makedirs(self.ablage, exist_ok=True)
        conn = sqlite3.connect(os.path.join(self.ablage, "erinnerung.db"), timeout=10)
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS laeufe USING fts5(lauf UNINDEXED, ordner UNINDEXED, "
                     "zeit UNINDEXED, aufgabe, zusammenfassung, gedanken, dateien, tokenize='unicode61 remove_diacritics 2')")
        conn.execute("CREATE TABLE IF NOT EXISTS gelesen (lauf TEXT PRIMARY KEY, mtime REAL)")
        return conn

    def einlesen(self):
        """Neue oder geänderte Verläufe in den Suchindex übernehmen. Gibt die Zahl zurück."""
        conn = self._db()
        bekannt = dict(conn.execute("SELECT lauf, mtime FROM gelesen").fetchall())
        neu = 0
        for n in os.listdir(self.ablage):
            if not n.endswith(".json"):
                continue
            pfad = os.path.join(self.ablage, n)
            lauf, mtime = n[:-5], os.path.getmtime(pfad)
            if bekannt.get(lauf) == mtime:
                continue
            try:
                with open(pfad, encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(d, dict) or "ergebnis" not in d:     # vertrauen.json und Ähnliches
                conn.execute("INSERT OR REPLACE INTO gelesen VALUES (?,?)", (lauf, mtime))
                continue
            e = d.get("ergebnis") or {}
            gedanken = " ".join(v.get("gedanke") or "" for v in e.get("verlauf", []))[:20000]
            dateien = " ".join(e.get("geaendert", []))
            conn.execute("DELETE FROM laeufe WHERE lauf=?", (lauf,))
            conn.execute("INSERT INTO laeufe VALUES (?,?,?,?,?,?,?)",
                         (lauf, os.path.realpath(d.get("ordner") or ""), d.get("zeit", mtime), d.get("aufgabe", ""),
                          e.get("zusammenfassung", "") + " " + (e.get("frage") or ""), gedanken, dateien))
            conn.execute("INSERT OR REPLACE INTO gelesen VALUES (?,?)", (lauf, mtime))
            neu += 1
        conn.commit()
        conn.close()
        return neu

    def erinnern(self, suche, ordner=None, anzahl=5, ausser=None):
        """Die passendsten früheren Läufe; die des Projekts zuerst."""
        woerter = re.findall(r"\w{3,}", str(suche or ""), re.U)
        if not woerter:
            raise ValueError("Suche braucht mindestens ein Wort mit drei Buchstaben.")
        self.einlesen()
        ausdruck = " OR ".join('"%s"' % w.replace('"', "") for w in woerter[:12])
        conn = self._db()
        try:
            zeilen = conn.execute("SELECT lauf, ordner, zeit, aufgabe, zusammenfassung, dateien, bm25(laeufe) AS rang "
                                  "FROM laeufe WHERE laeufe MATCH ? ORDER BY rang LIMIT 50", (ausdruck,)).fetchall()
        finally:
            conn.close()
        eigen = os.path.realpath(ordner) if ordner else None
        zeilen = [z for z in zeilen if z[0] != ausser]
        zeilen.sort(key=lambda z: (z[1] != eigen, z[6]))
        return [{"lauf": z[0], "ordner": z[1], "zeit": z[2], "aufgabe": z[3][:300], "zusammenfassung": z[4][:600].strip(),
                 "dateien": z[5], "dieses_projekt": z[1] == eigen} for z in zeilen[:anzahl]]
