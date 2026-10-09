# -*- coding: utf-8 -*-
"""Checkpunkte — jeder Stand eines Projekts, zu dem man zurück kann.

Der Werkbank-Agent ändert echte Dateien. Wer ihn arbeiten lässt, muss jeden
Schritt zurücknehmen können — auch das, was ein Befehl nebenbei verändert hat
(ein Formatierer, ein Generator, ein `rm`). Deshalb wird nicht mitgeschrieben,
was der Agent *sagt*, sondern festgehalten, was *auf der Platte liegt*.

Aufbau wie ein kleines Git, aber ohne Git:
- Jeder Dateiinhalt liegt einmal unter seinem SHA-256 in `objekte/`.
- Ein Checkpunkt ist eine Liste „Pfad → Inhalt“ als JSON.
- Unveränderte Dateien werden anhand von Größe und Änderungszeit erkannt und
  nicht neu gelesen; ein Checkpunkt eines mittleren Projekts kostet Millisekunden.

Bewusst **nicht** Git: Es gibt Rechner ohne Git, Workspaces ohne Repository,
und das Git des Nutzers (Index, Branch, Stash) wird nie angefasst. Das `.git`
des Projekts liegt außerhalb der Checkpunkte und wird auch beim Zurücksetzen
nicht berührt.

Grenzen, die gesagt werden statt verschwiegen:
- Ordner wie `.git`, `node_modules`, `.venv` werden übergangen — wie in der Werkbank.
- Was `.gitignore` oder `.dowosignore` im Projektordner ausschließt, wird weder
  gesichert noch beim Zurücksetzen angefasst. Das ist die sichere Richtung: Eine
  Datenbank oder ein Build-Ordner, der zu Recht ignoriert wird, darf nie auf einen
  alten Stand gedreht oder gelöscht werden.
- Dateien über `GROSS_AB` werden nicht gesichert. Beim Zurücksetzen bleiben sie,
  wie sie sind, und das Ergebnis nennt sie.
- Symbolische Links werden weder gesichert noch verändert.
- Projekte mit mehr als `MAX_DATEIEN` Dateien lehnen Checkpunkte ab (ZuGross).
"""

import difflib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time

UEBERGANGEN = ("__pycache__", ".git", "node_modules", ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache")
RUHIG_NS = 2_000_000_000       # so alt muss eine Datei sein, damit Größe+Zeit ihren Inhalt verbürgen
MAX_DATEIEN = 20000
GROSS_AB = 10 * 1024 * 1024
BEHALTEN = 60

_sperren = {}
_sperren_lock = threading.Lock()


class ZuGross(Exception):
    """Das Projekt ist zu groß für Checkpunkte."""


def _sperre(schluessel):
    with _sperren_lock:
        return _sperren.setdefault(schluessel, threading.Lock())


def projekt_kennung(ordner):
    return hashlib.sha256(os.path.realpath(ordner).encode("utf-8")).hexdigest()[:16]


class Checkpunkte:
    """Checkpunkte eines Projektordners, abgelegt unter `basis/<kennung>/`."""

    def __init__(self, basis, projekt):
        self.projekt = os.path.realpath(projekt)
        self.ablage = os.path.join(basis, projekt_kennung(self.projekt))
        self.objekte = os.path.join(self.ablage, "objekte")
        self.staende = os.path.join(self.ablage, "staende")
        self._lock = _sperre(self.ablage)

    # ------------------------------------------------------------ Erfassen ---
    def _muster(self):
        """Ausschlussmuster aus .gitignore und .dowosignore — die gängige Teilmenge.

        Unterstützt: Namen und Globs (`*.log`, `storage/`), Pfade mit `/`,
        Kommentare. Negationen (`!`) werden übergangen: Im Zweifel bleibt eine
        Datei ausgeschlossen und damit unangetastet."""
        muster = []
        for name in (".gitignore", ".dowosignore"):
            try:
                with open(os.path.join(self.projekt, name), encoding="utf-8", errors="replace") as f:
                    zeilen = f.read().splitlines()
            except OSError:
                continue
            for z in zeilen:
                z = z.strip()
                if not z or z.startswith("#") or z.startswith("!"):
                    continue
                nur_ordner = z.endswith("/")
                z = z.strip("/")
                if z:
                    muster.append((z, nur_ordner, "/" in z))
        return muster

    @staticmethod
    def _ignoriert(rel, ist_ordner, muster):
        name = rel.rsplit("/", 1)[-1]
        for m, nur_ordner, mit_pfad in muster:
            if nur_ordner and not ist_ordner:
                continue
            if (fnmatch.fnmatch(rel, m) if mit_pfad else fnmatch.fnmatch(name, m)):
                return True
        return False

    def _zwischenspeicher(self):
        try:
            with open(os.path.join(self.ablage, "kennzahlen.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _schreibe_json(self, pfad, daten):
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        tmp = pfad + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False)
        os.replace(tmp, pfad)

    def _objekt(self, h):
        return os.path.join(self.objekte, h[:2], h[2:])

    def erfassen(self, speichern=True):
        """Aktueller Stand: ({pfad: {"h": sha, "x": ausführbar}}, [zu große Dateien]).

        Mit speichern=True landen neue Inhalte gleich in der Ablage."""
        alt = self._zwischenspeicher()
        muster = self._muster()
        neu_kennzahlen, stand, gross = {}, {}, []
        for wurzel, ordner, dateien in os.walk(self.projekt):
            basis = os.path.relpath(wurzel, self.projekt).replace(os.sep, "/")
            basis = "" if basis == "." else basis + "/"
            ordner[:] = sorted(o for o in ordner if o not in UEBERGANGEN
                               and not os.path.islink(os.path.join(wurzel, o))
                               and not self._ignoriert(basis + o, True, muster))
            for d in dateien:
                voll = os.path.join(wurzel, d)
                rel = basis + d
                if self._ignoriert(rel, False, muster):
                    continue
                try:
                    st = os.lstat(voll)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):
                    continue
                if len(stand) + len(gross) >= MAX_DATEIEN:
                    raise ZuGross("Mehr als %d Dateien — für Checkpunkte zu groß." % MAX_DATEIEN)
                if st.st_size > GROSS_AB:
                    gross.append(rel)
                    continue
                kennung = [st.st_size, st.st_mtime_ns]
                bekannt = alt.get(rel)
                # Größe und Zeit gleich heißt nur dann „unverändert“, wenn die Datei beim
                # letzten Hinsehen schon RUHIG war (Zeit mindestens 2 s alt). Sonst kann sie
                # sich im selben Zeittakt geändert haben — Windows zählt die Dateizeit nur
                # alle ~15 ms weiter, FAT/exFAT alle 2 s. Ein Zurücksetzen ließ dann die neue
                # Fassung stehen, und die Sicherung davor hielt die alte fest (Windows-VM,
                # 29.09.2026). Dieselbe Regel wie „racily clean“ bei git.
                ruhig = bekannt and len(bekannt) > 3 and bekannt[3] - bekannt[1] > RUHIG_NS
                if ruhig and bekannt[:2] == kennung and (not speichern or os.path.exists(self._objekt(bekannt[2]))):
                    h = bekannt[2]
                else:
                    try:
                        with open(voll, "rb") as f:
                            inhalt = f.read()
                    except OSError:
                        continue
                    h = hashlib.sha256(inhalt).hexdigest()
                    if speichern and not os.path.exists(self._objekt(h)):
                        os.makedirs(os.path.dirname(self._objekt(h)), exist_ok=True)
                        tmp = self._objekt(h) + ".tmp"
                        with open(tmp, "wb") as f:
                            f.write(inhalt)
                        os.replace(tmp, self._objekt(h))
                neu_kennzahlen[rel] = kennung + [h, time.time_ns()]
                stand[rel] = {"h": h, "x": bool(st.st_mode & stat.S_IXUSR)}
        if speichern:
            self._schreibe_json(os.path.join(self.ablage, "kennzahlen.json"), neu_kennzahlen)
        return stand, gross

    # ------------------------------------------------------------- Sichern ---
    def liste(self):
        """Alle Checkpunkte, neueste zuerst (ohne Dateilisten)."""
        aus = []
        try:
            namen = os.listdir(self.staende)
        except OSError:
            return aus
        for n in namen:
            if not n.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.staende, n), encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            d.pop("dateien", None)
            aus.append(d)
        aus.sort(key=lambda d: (d["zeit"], d["id"]), reverse=True)
        return aus

    def laden(self, kennung):
        if not re.match(r"^[\w-]+$", kennung or ""):
            raise ValueError("Ungültige Checkpunkt-Kennung.")
        try:
            with open(os.path.join(self.staende, kennung + ".json"), encoding="utf-8") as f:
                return json.load(f)
        except OSError:
            raise ValueError("Checkpunkt „%s“ gibt es nicht." % kennung)

    def sichern(self, beschreibung, lauf="", schritt=0, nur_wenn_geaendert=False):
        """Hält den aktuellen Stand fest. Gibt den Checkpunkt zurück — oder None,
        wenn nur_wenn_geaendert und seit dem letzten Checkpunkt nichts anders ist."""
        with self._lock:
            stand, gross = self.erfassen()
            vorher = self.liste()
            if vorher:
                letzter = self.laden(vorher[0]["id"])
                if nur_wenn_geaendert and letzter["dateien"] == stand:
                    return None
                aenderung = unterschiede(letzter["dateien"], stand)
            else:
                aenderung = {"neu": sorted(stand), "geaendert": [], "geloescht": []}
            zeit = time.time()
            kennung = "%d-%s" % (int(zeit * 1000), hashlib.sha256(
                json.dumps(stand, sort_keys=True).encode()).hexdigest()[:8])
            cp = {"id": kennung, "zeit": zeit, "beschreibung": beschreibung[:200], "lauf": lauf,
                  "schritt": schritt, "anzahl": len(stand), "gross": gross,
                  "aenderung": {k: v[:200] for k, v in aenderung.items()},
                  "aenderung_zahl": {k: len(v) for k, v in aenderung.items()},
                  "dateien": stand}
            self._schreibe_json(os.path.join(self.staende, kennung + ".json"), cp)
            self._aufraeumen()
            return {k: v for k, v in cp.items() if k != "dateien"}

    def _aufraeumen(self):
        """Nur die letzten BEHALTEN Checkpunkte bleiben; verwaiste Inhalte gehen."""
        alle = self.liste()
        if len(alle) <= BEHALTEN:
            return
        for d in alle[BEHALTEN:]:
            try:
                os.remove(os.path.join(self.staende, d["id"] + ".json"))
            except OSError:
                pass
        gebraucht = set()
        for d in alle[:BEHALTEN]:
            try:
                gebraucht.update(v["h"] for v in self.laden(d["id"])["dateien"].values())
            except ValueError:
                continue
        for wurzel, _, dateien in os.walk(self.objekte):
            for d in dateien:
                if os.path.basename(wurzel) + d not in gebraucht:
                    try:
                        os.remove(os.path.join(wurzel, d))
                    except OSError:
                        pass

    # --------------------------------------------------------- Vergleichen ---
    def seit(self, kennung):
        """Was hat sich seit diesem Checkpunkt auf der Platte geändert?"""
        stand, _ = self.erfassen(speichern=False)
        return unterschiede(self.laden(kennung)["dateien"], stand)

    def diff(self, kennung, grenze=60000):
        """Unified Diff vom Checkpunkt zum aktuellen Stand, lesbar für Menschen."""
        alt = self.laden(kennung)["dateien"]
        stand, _ = self.erfassen(speichern=False)
        u = unterschiede(alt, stand)
        teile = []
        for pfad in u["geaendert"] + u["neu"] + u["geloescht"]:
            vorher = self._text(alt[pfad]["h"]) if pfad in alt else ""
            nachher = self._datei_text(pfad) if pfad in stand else ""
            if vorher is None or nachher is None:
                teile.append("Binärdatei %s geändert\n" % pfad)
                continue
            teile.append("".join(difflib.unified_diff(
                vorher.splitlines(True), nachher.splitlines(True),
                "a/" + pfad if pfad in alt else "/dev/null",
                "b/" + pfad if pfad in stand else "/dev/null")))
            if sum(map(len, teile)) > grenze:
                teile.append("\n… (weitere Änderungen ausgelassen)\n")
                break
        return "".join(t if t.endswith("\n") else t + "\n" for t in teile if t)

    def _text(self, h):
        try:
            with open(self._objekt(h), "rb") as f:
                return f.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def _datei_text(self, rel):
        try:
            with open(os.path.join(self.projekt, rel), "rb") as f:
                return f.read().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    # ------------------------------------------------------- Zurücksetzen ---
    def zuruecksetzen(self, kennung):
        """Stellt den Stand eines Checkpunkts wieder her.

        Vorher wird der aktuelle Stand selbst gesichert — auch das Zurücksetzen
        lässt sich also zurücknehmen, und eigene Änderungen des Nutzers, die
        seitdem entstanden sind, gehen nicht verloren."""
        ziel = self.laden(kennung)
        sicherung = self.sichern("Vor dem Zurücksetzen auf „%s“" % ziel["beschreibung"][:120])
        with self._lock:
            jetzt, gross = self.erfassen(speichern=False)
            u = unterschiede(ziel["dateien"], jetzt)
            fehlend = [h for h in {v["h"] for v in ziel["dateien"].values()} if not os.path.exists(self._objekt(h))]
            if fehlend:
                raise ValueError("Checkpunkt ist unvollständig (%d Inhalte fehlen) — nichts verändert." % len(fehlend))
            for pfad in u["neu"]:                       # seitdem entstanden → weg
                try:
                    os.remove(self._voll(pfad))
                except OSError:
                    pass
                self._leere_ordner_entfernen(os.path.dirname(self._voll(pfad)))
            for pfad in u["geaendert"] + u["geloescht"]:  # verändert oder verschwunden → zurück
                eintrag = ziel["dateien"][pfad]
                voll = self._voll(pfad)
                os.makedirs(os.path.dirname(voll), exist_ok=True)
                tmp = voll + ".dowos-tmp"
                shutil.copyfile(self._objekt(eintrag["h"]), tmp)
                modus = 0o755 if eintrag.get("x") else 0o644
                try:
                    os.chmod(tmp, modus)
                except OSError:
                    pass
                os.replace(tmp, voll)
            try:
                os.remove(os.path.join(self.ablage, "kennzahlen.json"))   # Zeiten stimmen nicht mehr
            except OSError:
                pass
        return {"ziel": {k: v for k, v in ziel.items() if k != "dateien"}, "sicherung": sicherung,
                "entfernt": u["neu"], "wiederhergestellt": u["geaendert"] + u["geloescht"],
                "nicht_gesichert": [p for p in gross]}

    def auspacken(self, kennung, ziel):
        """Schreibt den Stand eines Checkpunkts in einen leeren Ordner — das Projekt bleibt unberührt.
        Für Wiederholungen: Ein aufgezeichneter Lauf wird auf einer Kopie seines Startstands nachgespielt."""
        stand = self.laden(kennung)
        ziel = os.path.realpath(ziel)
        if os.path.exists(ziel) and os.listdir(ziel):
            raise ValueError("Zielordner ist nicht leer.")
        for rel, eintrag in stand["dateien"].items():
            voll = os.path.realpath(os.path.join(ziel, rel))
            if not voll.startswith(ziel + os.sep):
                raise ValueError("Pfad außerhalb des Ziels: %s" % rel)
            os.makedirs(os.path.dirname(voll), exist_ok=True)
            shutil.copyfile(self._objekt(eintrag["h"]), voll)
            if eintrag.get("x"):
                os.chmod(voll, 0o755)
        return len(stand["dateien"])

    def _voll(self, rel):
        voll = os.path.realpath(os.path.join(self.projekt, rel))
        if not voll.startswith(self.projekt + os.sep):
            raise ValueError("Pfad außerhalb des Projekts: %s" % rel)
        return voll

    def _leere_ordner_entfernen(self, ordner):
        while ordner.startswith(self.projekt + os.sep):
            try:
                os.rmdir(ordner)
            except OSError:
                return
            ordner = os.path.dirname(ordner)


def unterschiede(alt, neu):
    return {"neu": sorted(p for p in neu if p not in alt),
            "geaendert": sorted(p for p in neu if p in alt and neu[p] != alt[p]),
            "geloescht": sorted(p for p in alt if p not in neu)}
