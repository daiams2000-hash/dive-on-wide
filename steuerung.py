"""Dive on Wide — Bildschirm, Maus und Tastatur, plattformunabhängig.

WARUM DIESE DATEI EXISTIERT
---------------------------
Computer-Use war macOS-fest verdrahtet: `screencapture` für das Bild,
`cliclick` für Maus und Tastatur, `osascript` für die Bildschirmgröße. Auf
Linux fehlte damit nicht eine Funktion, sondern die ganze Fähigkeit — und die
Fehlermeldungen erklärten einem Linux-Nutzer geduldig, er möge doch bitte
`brew install cliclick` ausführen. Das ist schlimmer als gar keine Meldung.

Hier steckt alles, was das Betriebssystem angeht, hinter einer Schnittstelle.
Jede Rückseite sagt selbst, welche Werkzeuge sie braucht, ob sie darf, und wie
man nachhilft — **in den Worten der jeweiligen Plattform**.

DREI RÜCKSEITEN
---------------
* **macOS** — `screencapture` + `cliclick`. Rechte: Bildschirmaufnahme und
  Bedienungshilfen, beide über die Systemeinstellungen.
* **Linux/X11** — Bild über `maim`, `scrot`, `import` (ImageMagick) oder
  `gnome-screenshot`, je nachdem was da ist; Steuerung über `xdotool`.
* **Linux/Wayland** — Bild über `grim`, Steuerung über `ydotool` (Tastatur
  zusätzlich `wtype`, wenn vorhanden). Wayland lässt fremde Programme
  absichtlich NICHT einfach den Schirm lesen oder Eingaben schicken; das ist
  kein Fehler, sondern der Zweck. Deshalb sagt diese Rückseite ausdrücklich,
  dass es an der Sitzungsart liegen kann, und nennt den X11-Ausweg.

EHRLICHE LAGE
-------------
Entwickelt und gemessen wurde auf macOS. Die Linux-Rückseiten sind vollständig
geschrieben und ihre Befehlszeilen sind mit untergeschobenen Werkzeugen auf dem
PATH geprüft — **auf einem echten X11- oder Wayland-Sitzungsserver sind sie
nie gelaufen**. Das steht so in README und Diagnose. Wer es zuerst ausprobiert,
soll wissen, dass er der Erste ist.
"""

import os
import platform
import shutil
import subprocess
import time
import tempfile


class SteuerungFehler(RuntimeError):
    """Etwas ging schief — mit einem Satz, der weiterhilft."""


# ---------------------------------------------------------------------------
# Welche Plattform, und welche Sitzungsart?
# ---------------------------------------------------------------------------

def plattform_erkennen(umgebung=None):
    """macos · linux-x11 · linux-wayland · windows · unbekannt.

    Unter Linux entscheidet nicht der Kernel, sondern die Sitzung: XDG_SESSION_TYPE
    ist die verlässliche Auskunft, WAYLAND_DISPLAY und DISPLAY sind die Notnägel.
    Ein Wayland-Rechner mit laufendem XWayland hat beides gesetzt — dann gilt
    Wayland, weil `xdotool` dort zwar startet, aber an echten Wayland-Fenstern
    wirkungslos bleibt. Genau diese Sorte lautloser Wirkungslosigkeit ist es,
    die Computer-Use kaputt aussehen lässt, ohne je einen Fehler zu zeigen."""
    u = umgebung if umgebung is not None else os.environ
    erzwungen = u.get("DOWOS_PLATTFORM", "").strip().lower()
    if erzwungen:
        return erzwungen
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    if system == "linux":
        art = (u.get("XDG_SESSION_TYPE") or "").strip().lower()
        if art == "wayland" or (not art and u.get("WAYLAND_DISPLAY")):
            return "linux-wayland"
        if art == "x11" or u.get("DISPLAY"):
            return "linux-x11"
        # Kein Anzeigeserver: eine Konsole ohne Schirm. Nicht „kaputt“ —
        # es gibt schlicht nichts zu fotografieren, und das ist zu sagen.
        return "linux-ohne-anzeige"
    return "unbekannt"


def _lauf(befehl, frist=15):
    return subprocess.run(befehl, capture_output=True, timeout=frist)


class Rueckseite:
    """Was jede Plattform können muss."""

    name = "unbekannt"
    beschreibung = "unbekanntes System"

    # -- Werkzeuge --------------------------------------------------------
    def foto_werkzeug(self):
        """Pfad des Programms, das ein Bildschirmfoto macht — oder ""."""
        raise NotImplementedError

    def steuer_werkzeug(self):
        """Pfad des Programms für Maus und Tastatur — oder ""."""
        raise NotImplementedError

    # -- Rechte -----------------------------------------------------------
    def darf_lesen(self):
        """Kommt WIRKLICH ein Bild heraus? Nicht: ist das Werkzeug da?"""
        raise NotImplementedError

    def darf_steuern(self):
        raise NotImplementedError

    # -- Tun --------------------------------------------------------------
    def foto(self):
        """Ein Bildschirmfoto als PNG-Bytes."""
        raise NotImplementedError

    def skala(self, png_breite):
        """Bildpixel je logischem Punkt. Auf Retina 2.0, sonst meist 1.0."""
        return 1.0

    def klick(self, x, y, doppelt=False):
        raise NotImplementedError

    def tippen(self, text):
        raise NotImplementedError

    def taste(self, name):
        raise NotImplementedError

    # -- Erklären ---------------------------------------------------------
    def hinweis_foto(self):
        return ""

    def hinweis_steuern(self):
        return ""


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------

class MacOS(Rueckseite):
    name = "macos"
    beschreibung = "macOS (screencapture + cliclick)"

    TASTEN = {
        "return": "return", "esc": "esc", "tab": "tab", "space": "space",
        "delete": "delete", "arrow-up": "arrow-up", "arrow-down": "arrow-down",
        "arrow-left": "arrow-left", "arrow-right": "arrow-right",
    }

    def foto_werkzeug(self):
        return shutil.which("screencapture") or ""

    def steuer_werkzeug(self):
        return shutil.which("cliclick") or ""

    def darf_lesen(self):
        """Ein Pixel fotografieren. Ohne das Recht sagt macOS nicht Nein —
        screencapture liefert einfach eine leere Datei, und das merkte man
        früher erst mitten im Lauf."""
        if not self.foto_werkzeug():
            return False
        fd, pfad = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            _lauf(["screencapture", "-x", "-R", "0,0,1,1", pfad], frist=10)
            return os.path.getsize(pfad) > 0
        except Exception:
            return False
        finally:
            try:
                os.unlink(pfad)
            except OSError:
                pass

    def darf_steuern(self):
        """`cliclick -V` warnt selbst, wenn das Recht fehlt. Billiger und
        harmloser als ein Probeklick, der ja etwas auslösen würde."""
        if not self.steuer_werkzeug():
            return False
        try:
            r = _lauf(["cliclick", "-V"], frist=10)
            text = (r.stdout + r.stderr).decode("utf-8", "replace").lower()
            return "accessibility privileges not enabled" not in text
        except Exception:
            return False

    def foto(self):
        fd, pfad = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            r = _lauf(["screencapture", "-x", pfad])
            if r.returncode != 0 or not os.path.getsize(pfad):
                raise SteuerungFehler(
                    "screencapture lieferte kein Bild: %s"
                    % r.stderr.decode("utf-8", "replace")[:200])
            with open(pfad, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(pfad)
            except OSError:
                pass

    def skala(self, png_breite):
        """Auf Retina ist der Screenshot doppelt so breit wie die logische
        Fläche; ohne Umrechnung klickte das System um Faktor 2 daneben."""
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "Finder" to get bounds of window of desktop'],
                capture_output=True, timeout=5, text=True)
            teile = [int(x) for x in r.stdout.replace(" ", "").split(",")]
            logisch = teile[2] - teile[0]
            if logisch > 0:
                return png_breite / float(logisch)
        except Exception:
            pass
        return 1.0

    def klick(self, x, y, doppelt=False):
        self._cliclick(("dc" if doppelt else "c") + ":%d,%d" % (x, y))

    def tippen(self, text):
        self._cliclick("t:" + text)

    def taste(self, name):
        self._cliclick("kp:" + self.TASTEN.get(name, name))

    def _cliclick(self, *args):
        subprocess.run(["cliclick", *args], check=True, timeout=15,
                       capture_output=True)

    def hinweis_foto(self):
        if not self.foto_werkzeug():
            return ("screencapture fehlt — das gehört eigentlich zu macOS. "
                    "Ist der PATH beschnitten?")
        return ("Das Recht „Bildschirmaufnahme“ fehlt — Dive on Wide bekommt vom "
                "System nur ein leeres Bild. Erteilen unter: "
                "Systemeinstellungen → Datenschutz & Sicherheit → "
                "Bildschirmaufnahme, dort das Programm eintragen, in dem Dive on Wide "
                "läuft (Terminal/iTerm), danach dieses Programm neu starten.")

    def hinweis_steuern(self):
        if not self.steuer_werkzeug():
            return ("Für die Steuerung (Klicken/Tippen) fehlt cliclick. "
                    "Installation: brew install cliclick. Danach fragt macOS "
                    "einmalig nach der Berechtigung „Bedienungshilfen“.")
        return ("cliclick ist installiert, hat aber kein Recht auf "
                "„Bedienungshilfen“ — Klicks und Tastendrücke laufen ins Leere, "
                "ohne Fehlermeldung.")


# ---------------------------------------------------------------------------
# Linux / X11
# ---------------------------------------------------------------------------

class LinuxX11(Rueckseite):
    name = "linux-x11"
    beschreibung = "Linux/X11 (xdotool)"

    # Reihenfolge ist Absicht: maim und scrot sind schlank und schweigen,
    # import gehoert zu ImageMagick (oft ohnehin da), gnome-screenshot ist
    # der letzte Ausweg und macht auf manchen Systemen ein Geraeusch.
    FOTO_KANDIDATEN = (
        ("maim", ["maim", "-u", "{pfad}"]),
        ("scrot", ["scrot", "-o", "-z", "{pfad}"]),
        ("import", ["import", "-window", "root", "{pfad}"]),
        ("gnome-screenshot", ["gnome-screenshot", "-f", "{pfad}"]),
    )

    TASTEN = {
        "return": "Return", "esc": "Escape", "tab": "Tab", "space": "space",
        "delete": "BackSpace", "arrow-up": "Up", "arrow-down": "Down",
        "arrow-left": "Left", "arrow-right": "Right",
    }

    def _foto_befehl(self):
        for name, vorlage in self.FOTO_KANDIDATEN:
            if shutil.which(name):
                return name, vorlage
        return "", None

    def foto_werkzeug(self):
        return self._foto_befehl()[0]

    def steuer_werkzeug(self):
        return "xdotool" if shutil.which("xdotool") else ""

    def darf_lesen(self):
        """Auf X11 gibt es keine Rechteabfrage — entweder der Anzeigeserver
        ist erreichbar oder nicht. Also wirklich ein Bild ziehen: Ein leeres
        oder fehlendes Ergebnis heißt DISPLAY stimmt nicht, und das ist die
        Auskunft, die man braucht."""
        if not self.foto_werkzeug():
            return False
        try:
            return len(self.foto()) > 0
        except Exception:
            return False

    def darf_steuern(self):
        """`xdotool getactivewindow` fragt den Server, ohne etwas anzufassen.
        Es gibt harmlos einen Fehler zurück, wenn keine Sitzung erreichbar
        ist — anders als ein Probeklick, der wirklich etwas auslösen würde."""
        if not self.steuer_werkzeug():
            return False
        try:
            r = _lauf(["xdotool", "getactivewindow"], frist=10)
            if r.returncode == 0:
                return True
            # Kein aktives Fenster ist KEIN Rechteproblem — ein leerer
            # Arbeitsplatz zaehlt trotzdem als bedienbar. Nur wenn der Server
            # selbst fehlt, ist es wirklich aus.
            text = (r.stdout + r.stderr).decode("utf-8", "replace").lower()
            return "unable to open display" not in text and "cannot open" not in text
        except Exception:
            return False

    def foto(self):
        name, vorlage = self._foto_befehl()
        if not name:
            raise SteuerungFehler(self.hinweis_foto())
        fd, pfad = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            r = _lauf([t.replace("{pfad}", pfad) for t in vorlage])
            if r.returncode != 0 or not os.path.getsize(pfad):
                raise SteuerungFehler(
                    "%s lieferte kein Bild: %s. Steht DISPLAY richtig (%s)?"
                    % (name, r.stderr.decode("utf-8", "replace")[:160],
                       os.environ.get("DISPLAY") or "nicht gesetzt"))
            with open(pfad, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(pfad)
            except OSError:
                pass

    def skala(self, png_breite):
        """X11 kennt keine Retina-Verdopplung wie macOS: Der Screenshot ist
        so breit wie der Schirm, und xdotool rechnet in denselben Pixeln.
        Bei aktivem HiDPI-Faktor stimmt das ebenfalls, weil beide Seiten die
        gleiche Pixelfläche sehen."""
        return 1.0

    def klick(self, x, y, doppelt=False):
        # Erst bewegen, dann klicken: xdotool kann beides in einem Aufruf,
        # aber getrennt ist es nachvollziehbarer, wenn etwas schiefgeht.
        # OHNE --sync: Das wartet auf ein Bewegungsereignis, und steht der
        # Zeiger schon am Ziel, kommt nie eins — 15 s Haenger, dann Absturz
        # (erster Lauf auf echtem X11, 27.09.2026: Xvfb startet den Zeiger
        # genau in der Mitte; auf dem Desktop trifft es jeden zweiten Klick
        # auf dieselbe Stelle).
        self._xdotool("mousemove", str(int(x)), str(int(y)))
        time.sleep(0.05)
        self._xdotool("click", "--repeat", "2" if doppelt else "1", "1")

    def tippen(self, text):
        # --clearmodifiers, damit ein haengendes Shift nicht alles gross macht.
        self._xdotool("type", "--clearmodifiers", "--delay", "12", text)

    def taste(self, name):
        self._xdotool("key", "--clearmodifiers", self.TASTEN.get(name, name))

    def _xdotool(self, *args):
        subprocess.run(["xdotool", *args], check=True, timeout=15,
                       capture_output=True)

    def hinweis_foto(self):
        if not self.foto_werkzeug():
            return ("Kein Screenshot-Werkzeug gefunden. Eines davon "
                    "installieren: sudo apt install maim  (oder scrot, "
                    "imagemagick, gnome-screenshot).")
        return ("Das Werkzeug ist da, liefert aber kein Bild. Auf X11 heißt "
                "das fast immer: DISPLAY zeigt auf keine erreichbare Sitzung "
                "(gerade: %s). Bei einem Dienst ohne Anmeldung ist das normal — "
                "Computer-Use braucht eine laufende grafische Sitzung."
                % (os.environ.get("DISPLAY") or "nicht gesetzt"))

    def hinweis_steuern(self):
        if not self.steuer_werkzeug():
            return ("Für die Steuerung (Klicken/Tippen) fehlt xdotool. "
                    "Installation: sudo apt install xdotool  (Fedora: sudo dnf "
                    "install xdotool, Arch: sudo pacman -S xdotool).")
        return ("xdotool ist da, erreicht aber keinen Anzeigeserver "
                "(DISPLAY=%s). Computer-Use braucht eine laufende grafische "
                "Sitzung auf demselben Rechner."
                % (os.environ.get("DISPLAY") or "nicht gesetzt"))


# ---------------------------------------------------------------------------
# Linux / Wayland
# ---------------------------------------------------------------------------

class LinuxWayland(Rueckseite):
    """Wayland verweigert Fremdzugriff mit Absicht.

    Unter X11 kann jedes Programm den Schirm lesen und jedem Fenster Tasten
    schicken. Wayland hat genau das abgestellt — das ist keine Lücke, die man
    umgeht, sondern der Grund, warum es Wayland gibt. Deshalb ist diese
    Rückseite die einzige, die auch bei vorhandenen Werkzeugen sagen kann
    „geht hier prinzipiell nicht", und den ehrlichen Ausweg nennt: eine
    X11-Sitzung wählen."""

    name = "linux-wayland"
    beschreibung = "Linux/Wayland (grim + ydotool)"

    TASTEN = {
        "return": "28:1 28:0", "esc": "1:1 1:0", "tab": "15:1 15:0",
        "space": "57:1 57:0", "delete": "14:1 14:0",
        "arrow-up": "103:1 103:0", "arrow-down": "108:1 108:0",
        "arrow-left": "105:1 105:0", "arrow-right": "106:1 106:0",
    }
    # wtype kennt Namen statt Linux-Tastencodes — angenehmer, wenn vorhanden.
    WTYPE_TASTEN = {
        "return": "Return", "esc": "Escape", "tab": "Tab", "space": "space",
        "delete": "BackSpace", "arrow-up": "Up", "arrow-down": "Down",
        "arrow-left": "Left", "arrow-right": "Right",
    }

    def foto_werkzeug(self):
        for name in ("grim", "spectacle", "gnome-screenshot"):
            if shutil.which(name):
                return name
        return ""

    def steuer_werkzeug(self):
        return "ydotool" if shutil.which("ydotool") else ""

    def darf_lesen(self):
        if not self.foto_werkzeug():
            return False
        try:
            return len(self.foto()) > 0
        except Exception:
            return False

    def darf_steuern(self):
        """ydotool braucht einen laufenden Dienst (ydotoold) und Zugriff auf
        /dev/uinput. Ohne beides nimmt es Befehle an und tut nichts — dieselbe
        lautlose Wirkungslosigkeit wie cliclick ohne Bedienungshilfen. Also
        wirklich nachfragen statt annehmen."""
        if not self.steuer_werkzeug():
            return False
        try:
            r = _lauf(["ydotool", "debug"], frist=5)
            text = (r.stdout + r.stderr).decode("utf-8", "replace").lower()
            if "backend unavailable" in text or "failed to connect" in text:
                return False
            if r.returncode != 0 and "socket" in text:
                return False
        except Exception:
            pass
        # Der Dienst muss laufen; sein Socket ist die verlaesslichste Auskunft.
        sock = os.environ.get("YDOTOOL_SOCKET", "/tmp/.ydotool_socket")
        return os.path.exists(sock) or os.access("/dev/uinput", os.W_OK)

    def foto(self):
        name = self.foto_werkzeug()
        if not name:
            raise SteuerungFehler(self.hinweis_foto())
        fd, pfad = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            befehl = {"grim": ["grim", pfad],
                      "spectacle": ["spectacle", "-b", "-n", "-o", pfad],
                      "gnome-screenshot": ["gnome-screenshot", "-f", pfad]}[name]
            r = _lauf(befehl)
            if r.returncode != 0 or not os.path.getsize(pfad):
                raise SteuerungFehler(
                    "%s lieferte kein Bild: %s. Unter Wayland darf nicht jedes "
                    "Programm den Schirm lesen — auf GNOME braucht es dafür das "
                    "Portal, auf wlroots-Compositoren (Sway, Hyprland) genügt "
                    "grim." % (name, r.stderr.decode("utf-8", "replace")[:160]))
            with open(pfad, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(pfad)
            except OSError:
                pass

    def klick(self, x, y, doppelt=False):
        # ydotool bewegt absolut nur mit --absolute; ohne das Flag waere es
        # eine RELATIVE Bewegung und der Zeiger liefe mit jedem Klick weiter.
        self._ydotool("mousemove", "--absolute", "-x", str(int(x)),
                      "-y", str(int(y)))
        self._ydotool("click", "0xC0")          # linke Taste, runter+hoch
        if doppelt:
            self._ydotool("click", "0xC0")

    def tippen(self, text):
        if shutil.which("wtype"):
            subprocess.run(["wtype", text], check=True, timeout=15,
                           capture_output=True)
            return
        self._ydotool("type", "--key-delay", "12", text)

    def taste(self, name):
        if shutil.which("wtype"):
            subprocess.run(["wtype", "-k", self.WTYPE_TASTEN.get(name, name)],
                           check=True, timeout=15, capture_output=True)
            return
        code = self.TASTEN.get(name)
        if not code:
            raise SteuerungFehler("Taste %r ist unter Wayland nicht belegt." % name)
        self._ydotool("key", *code.split())

    def _ydotool(self, *args):
        subprocess.run(["ydotool", *args], check=True, timeout=15,
                       capture_output=True)

    def hinweis_foto(self):
        if not self.foto_werkzeug():
            # Der X11-Hinweis gehoert in BEIDE Zweige. Wer hier landet, hat
            # meist gar kein Werkzeug — und ihm nur `apt install grim` zu
            # sagen verschweigt, dass Wayland danach immer noch dazwischen
            # stehen kann. Der kuerzeste Weg zum Ziel ist die Sitzungswahl.
            return ("Kein Screenshot-Werkzeug für Wayland gefunden. "
                    "Installation: sudo apt install grim  (auf GNOME/KDE "
                    "alternativ gnome-screenshot bzw. spectacle). Beachte: "
                    "Unter Wayland entscheidet der Compositor, wer den Schirm "
                    "sehen darf — das Werkzeug allein genügt womöglich nicht. "
                    "Sicherste Abhilfe: bei der Anmeldung eine X11-Sitzung "
                    "wählen.")
        return ("Das Werkzeug ist da, bekommt aber kein Bild. Unter Wayland ist "
                "das oft gewollt: Der Compositor entscheidet, wer den Schirm "
                "sehen darf. Auf wlroots (Sway, Hyprland) genügt grim; auf "
                "GNOME läuft es über das Portal. Sicherste Abhilfe: bei der "
                "Anmeldung eine X11-Sitzung wählen.")

    def hinweis_steuern(self):
        if not self.steuer_werkzeug():
            return ("Für die Steuerung fehlt ydotool. Installation: sudo apt "
                    "install ydotool — danach den Dienst starten (sudo systemctl "
                    "enable --now ydotoold), sonst nimmt ydotool Befehle an und "
                    "tut nichts.")
        return ("ydotool ist installiert, aber sein Dienst antwortet nicht. "
                "Starten: sudo systemctl enable --now ydotoold. Ohne ihn fehlt "
                "der Zugriff auf /dev/uinput, und Klicks verschwinden lautlos. "
                "Wenn es dabei bleibt: bei der Anmeldung eine X11-Sitzung "
                "wählen — dort ist der Weg deutlich kürzer.")


class ImContainer(Rueckseite):
    """Ein eigener Bildschirm im Container — die eigentliche Sicherheitsgrenze.

    WARUM DAS DER WICHTIGSTE TEIL VON COMPUTER-USE IST
    --------------------------------------------------
    Ohne ihn bewegt der Agent den echten Zeiger auf dem echten Rechner. Jede
    Fehlentscheidung trifft echte Dateien, echte Konten, echte offene Fenster.
    Deshalb ist die Steuerung standardmäßig aus, deshalb muss jede Aktion
    einzeln freigegeben werden — lauter Notbremsen um ein Problem herum, das
    besser gar nicht entsteht.

    Hier bekommt der Agent einen **eigenen Bildschirm**: ein X-Server ohne
    Grafikkarte (Xvfb) in einem Container, mit `xdotool` und `maim` darin.
    Was dort schiefgeht, geht in einem Wegwerf-Behälter schief. Der Container
    läuft ohne Netz und ohne Zugriff auf das Wirtsdateisystem — er kann nichts
    kaputtmachen, was ihm nicht ausdrücklich gegeben wurde.

    **Kein Sicherheitsversprechen ohne Kleingedrucktes.** Ein Container ist
    kein Hypervisor. Wer aus ihm ausbricht, steht auf dem Wirt — das ist
    seltener als ein Fehlklick, aber nicht unmöglich. Er ist eine *echte*
    Grenze und trotzdem eine schwächere als eine vollständige VM. Das steht so
    auch in SECURITY.md; das Gegenteil zu behaupten wäre schlimmer, als die
    Grenze gar nicht zu haben.

    Alles läuft über `docker exec`. Die Befehle sind dieselben wie bei
    Linux/X11 — es ist derselbe X-Server, nur woanders.
    """

    name = "container"
    beschreibung = "eigener Bildschirm im Container (Xvfb + xdotool)"

    TASTEN = LinuxX11.TASTEN

    def __init__(self, behaelter="dowos-schirm", anzeige=":99"):
        self.behaelter = behaelter
        self.anzeige = anzeige

    # -- Lage des Containers ----------------------------------------------
    def docker_da(self):
        return bool(shutil.which("docker"))

    def docker_laeuft(self):
        if not self.docker_da():
            return False
        try:
            return _lauf(["docker", "info"], frist=10).returncode == 0
        except Exception:
            return False

    def behaelter_laeuft(self):
        if not self.docker_laeuft():
            return False
        try:
            r = _lauf(["docker", "inspect", "-f", "{{.State.Running}}",
                       self.behaelter], frist=10)
            return r.returncode == 0 and r.stdout.strip() == b"true"
        except Exception:
            return False

    def _drin(self, *befehl, **kw):
        """Einen Befehl IM Container ausführen."""
        return _lauf(["docker", "exec", "-e", "DISPLAY=" + self.anzeige,
                      self.behaelter, *befehl], **kw)

    # -- Schnittstelle -----------------------------------------------------
    def foto_werkzeug(self):
        if not self.behaelter_laeuft():
            return ""
        try:
            return "maim" if self._drin("which", "maim", frist=8).returncode == 0 else ""
        except Exception:
            return ""

    def steuer_werkzeug(self):
        if not self.behaelter_laeuft():
            return ""
        try:
            return ("xdotool"
                    if self._drin("which", "xdotool", frist=8).returncode == 0
                    else "")
        except Exception:
            return ""

    def darf_lesen(self):
        if not self.foto_werkzeug():
            return False
        try:
            return len(self.foto()) > 0
        except Exception:
            return False

    def darf_steuern(self):
        """Im Container gibt es keine Rechteabfrage — entweder der X-Server
        antwortet oder nicht. Also wirklich fragen."""
        if not self.steuer_werkzeug():
            return False
        try:
            r = self._drin("xdotool", "getdisplaygeometry", frist=8)
            return r.returncode == 0
        except Exception:
            return False

    def foto(self):
        """maim schreibt nach stdout — so muss nichts aus dem Container
        kopiert werden, und es bleibt keine Datei zurück."""
        if not self.behaelter_laeuft():
            raise SteuerungFehler(self.hinweis_foto())
        r = self._drin("maim", "-u", frist=20)
        if r.returncode != 0 or not r.stdout:
            raise SteuerungFehler(
                "maim im Container lieferte kein Bild: %s"
                % r.stderr.decode("utf-8", "replace")[:200])
        return r.stdout

    def skala(self, png_breite):
        return 1.0

    def klick(self, x, y, doppelt=False):
        self._x("mousemove", str(int(x)), str(int(y)))   # ohne --sync, siehe XdotoolRueckseite.klick
        time.sleep(0.05)
        self._x("click", "--repeat", "2" if doppelt else "1", "1")

    def tippen(self, text):
        self._x("type", "--clearmodifiers", "--delay", "12", text)

    def taste(self, name):
        self._x("key", "--clearmodifiers", self.TASTEN.get(name, name))

    def _x(self, *args):
        r = self._drin("xdotool", *args)
        if r.returncode != 0:
            raise SteuerungFehler(
                "xdotool im Container: %s"
                % r.stderr.decode("utf-8", "replace")[:200])

    # -- Erklären ----------------------------------------------------------
    def _warum_nicht(self):
        if not self.docker_da():
            return ("Docker fehlt. Der eigene Bildschirm für Computer-Use läuft "
                    "in einem Container — ohne Docker gibt es ihn nicht. "
                    "macOS: Docker Desktop installieren.")
        if not self.docker_laeuft():
            return ("Docker ist installiert, aber der Dienst antwortet nicht. "
                    "Auf dem Mac: Docker Desktop starten. Ohne ihn bleibt nur "
                    "der echte Bildschirm — und darauf klickt der Agent dann "
                    "wirklich.")
        if not self.behaelter_laeuft():
            return ("Der Container %r läuft nicht. Anlegen und starten mit "
                    "menueleiste/../schirm/bauen.sh — oder in den Einstellungen "
                    "auf den echten Bildschirm zurückschalten."
                    % self.behaelter)
        return ""

    def hinweis_foto(self):
        grund = self._warum_nicht()
        if grund:
            return grund
        return ("Der Container läuft, liefert aber kein Bild. Fehlt darin maim, "
                "oder steht DISPLAY (%s) nicht auf dem Xvfb-Schirm?"
                % self.anzeige)

    def hinweis_steuern(self):
        grund = self._warum_nicht()
        if grund:
            return grund
        return ("Der Container läuft, aber xdotool erreicht keinen X-Server "
                "unter DISPLAY=%s. Läuft Xvfb darin?" % self.anzeige)


class OhneAnzeige(Rueckseite):
    """Linux ohne grafische Sitzung — ein Server. Nichts ist kaputt."""

    name = "linux-ohne-anzeige"
    beschreibung = "Linux ohne grafische Sitzung"

    def foto_werkzeug(self): return ""
    def steuer_werkzeug(self): return ""
    def darf_lesen(self): return False
    def darf_steuern(self): return False

    def foto(self):
        raise SteuerungFehler(self.hinweis_foto())

    def hinweis_foto(self):
        return ("Auf diesem Rechner läuft keine grafische Sitzung (weder "
                "DISPLAY noch WAYLAND_DISPLAY ist gesetzt). Computer-Use "
                "steuert einen Bildschirm — ohne Bildschirm gibt es nichts zu "
                "steuern. Alles andere in Dive on Wide läuft hier normal weiter.")

    hinweis_steuern = hinweis_foto


def png_aus_bgra(bgra, breite, hoehe):
    """PNG (RGB, 8 Bit) aus BGRA-Zeilen von oben nach unten — nur Standardbibliothek."""
    import struct
    import zlib
    rgb = bytearray(breite * hoehe * 3)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    zeile = breite * 3
    roh = b"".join(b"\x00" + bytes(rgb[i * zeile:(i + 1) * zeile]) for i in range(hoehe))

    def block(art, daten):
        return (struct.pack(">I", len(daten)) + art + daten
                + struct.pack(">I", zlib.crc32(art + daten) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n" + block(b"IHDR", struct.pack(">IIBBBBB", breite, hoehe, 8, 2, 0, 0, 0))
            + block(b"IDAT", zlib.compress(roh, 6)) + block(b"IEND", b""))


class Windows(Rueckseite):
    """Windows über die Win32-API — ohne zusätzliches Programm.

    Bild: BitBlt vom Bildschirm-DC, GetDIBits als BGRA, PNG aus der Standard-
    bibliothek. Maus: SetCursorPos + SendInput. Tippen: SendInput mit
    KEYEVENTF_UNICODE, damit Umlaute und Sonderzeichen ankommen, egal welches
    Tastaturlayout eingestellt ist. Der Prozess erklärt sich DPI-bewusst, sonst
    liefert Windows bei 125/150 % Skalierung ein verkleinertes Bild und klickt
    daneben. Wichtig: Das geht nur in einer Sitzung MIT Desktop — ein Dienst oder
    eine SSH-Sitzung sieht einen schwarzen Schirm, und das wird gesagt."""

    name = "windows"
    beschreibung = "Windows (Win32: BitBlt und SendInput)"
    TASTEN = {"return": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20, "delete": 0x08,
              "arrow-up": 0x26, "arrow-down": 0x28, "arrow-left": 0x25, "arrow-right": 0x27}

    def __init__(self):
        self._dpi_gesetzt = False

    def _user32(self):
        import ctypes
        u = ctypes.windll.user32
        if not self._dpi_gesetzt:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (OSError, AttributeError):
                try:
                    u.SetProcessDPIAware()
                except (OSError, AttributeError):
                    pass
            self._dpi_gesetzt = True
        return u

    def foto_werkzeug(self):
        return "win32" if os.name == "nt" else ""

    steuer_werkzeug = foto_werkzeug

    def darf_lesen(self):
        try:
            bild = self._bgra()
        except (OSError, SteuerungFehler, AttributeError):
            return False
        return any(bild[0][i] for i in range(0, min(len(bild[0]), 400000), 97))

    def darf_steuern(self):
        return self.darf_lesen()

    def _bgra(self):
        import ctypes
        from ctypes import wintypes
        u = self._user32()
        g = ctypes.windll.gdi32
        # Alle Handles ausdrücklich typisieren: Ohne argtypes presst ctypes 64-Bit-Handles in
        # 32 Bit — das zweite Bildschirmfoto scheiterte mit OverflowError (Windows-VM, 30.09.2026).
        H, B = wintypes.HDC, wintypes.HBITMAP
        u.GetDC.argtypes, u.GetDC.restype = [wintypes.HWND], H
        u.ReleaseDC.argtypes = [wintypes.HWND, H]
        g.CreateCompatibleDC.argtypes, g.CreateCompatibleDC.restype = [H], H
        g.CreateCompatibleBitmap.argtypes, g.CreateCompatibleBitmap.restype = [H, ctypes.c_int, ctypes.c_int], B
        g.SelectObject.argtypes, g.SelectObject.restype = [H, wintypes.HGDIOBJ], wintypes.HGDIOBJ
        g.BitBlt.argtypes = [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H,
                             ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        g.GetDIBits.argtypes = [H, B, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g.DeleteDC.argtypes = [H]
        breite, hoehe = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        if breite <= 0 or hoehe <= 0:
            raise SteuerungFehler("Kein Bildschirm in dieser Sitzung (Dienst oder SSH ohne Desktop).")
        schirm = u.GetDC(None)
        speicher = g.CreateCompatibleDC(schirm)
        bitmap = g.CreateCompatibleBitmap(schirm, breite, hoehe)
        alt = g.SelectObject(speicher, bitmap)
        try:
            if not g.BitBlt(speicher, 0, 0, breite, hoehe, schirm, 0, 0, 0x00CC0020 | 0x40000000):
                raise SteuerungFehler("BitBlt lieferte kein Bild (keine Desktop-Sitzung?).")

            class Kopf(ctypes.Structure):
                _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                            ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                            ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                            ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
            kopf = Kopf(ctypes.sizeof(Kopf), breite, -hoehe, 1, 32, 0, 0, 0, 0, 0, 0)   # -hoehe: von oben
            puffer = ctypes.create_string_buffer(breite * hoehe * 4)
            if not g.GetDIBits(speicher, bitmap, 0, hoehe, ctypes.cast(puffer, ctypes.c_void_p),
                               ctypes.cast(ctypes.byref(kopf), ctypes.c_void_p), 0):
                raise SteuerungFehler("GetDIBits lieferte keine Bilddaten.")
            return puffer.raw, breite, hoehe
        finally:
            g.SelectObject(speicher, alt)
            g.DeleteObject(bitmap)
            g.DeleteDC(speicher)
            u.ReleaseDC(None, schirm)

    def foto(self):
        bgra, breite, hoehe = self._bgra()
        return png_aus_bgra(bgra, breite, hoehe)

    def _senden(self, eingaben):
        import ctypes
        from ctypes import wintypes

        class Maus(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class Taste(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class Vereint(ctypes.Union):
            _fields_ = [("mi", Maus), ("ki", Taste)]

        class Eingabe(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", Vereint)]

        liste = (Eingabe * len(eingaben))()
        for i, (art, werte) in enumerate(eingaben):
            liste[i].type = art
            if art == 0:
                liste[i].u.mi = Maus(0, 0, 0, werte, 0, 0)
            else:
                vk, scan, flags = werte
                liste[i].u.ki = Taste(vk, scan, flags, 0, 0)
        gesendet = self._user32().SendInput(len(eingaben), liste, ctypes.sizeof(Eingabe))
        if gesendet != len(eingaben):
            raise SteuerungFehler("SendInput wurde blockiert (%d von %d) — läuft ein Programm mit "
                                  "Administratorrechten im Vordergrund?" % (gesendet, len(eingaben)))

    def klick(self, x, y, doppelt=False):
        u = self._user32()
        if not u.SetCursorPos(int(x), int(y)):
            raise SteuerungFehler("Der Mauszeiger ließ sich nicht setzen.")
        einmal = [(0, 0x0002), (0, 0x0004)]                 # links drücken, loslassen
        self._senden(einmal * (2 if doppelt else 1))

    def tippen(self, text):
        eingaben = []
        for zeichen in text:
            codes = [int.from_bytes(zeichen.encode("utf-16-le")[i:i + 2], "little")
                     for i in range(0, len(zeichen.encode("utf-16-le")), 2)]
            for c in codes:                                    # Zeichen außerhalb der BMP: zwei Einheiten
                eingaben += [(1, (0, c, 0x0004)), (1, (0, c, 0x0004 | 0x0002))]
        if eingaben:
            self._senden(eingaben)

    def taste(self, name):
        vk = self.TASTEN.get(name)
        if vk is None:
            raise SteuerungFehler("Unbekannte Taste: %s" % name)
        self._senden([(1, (vk, 0, 0)), (1, (vk, 0, 0x0002))])

    def hinweis_foto(self):
        if os.name != "nt":
            return "Die Windows-Steuerung läuft nur unter Windows."
        return ("Kein Bild vom Bildschirm. Windows gibt es nur in einer angemeldeten Sitzung mit Desktop "
                "heraus — nicht in einem Dienst und nicht über SSH. Dive on Wide im angemeldeten Benutzer starten "
                "(„Dive on Wide starten.cmd“).")

    hinweis_steuern = hinweis_foto


class Fremd(Rueckseite):
    """Alles Übrige — ehrlich unbedient statt halb gebaut."""

    name = "unbekannt"
    beschreibung = "nicht unterstütztes System"

    def __init__(self, kennung="unbekannt"):
        self.name = kennung
        if kennung == "windows":
            self.beschreibung = "Windows (Computer-Use noch nicht gebaut)"

    def foto_werkzeug(self): return ""
    def steuer_werkzeug(self): return ""
    def darf_lesen(self): return False
    def darf_steuern(self): return False

    def foto(self):
        raise SteuerungFehler(self.hinweis_foto())

    def hinweis_foto(self):
        if self.name == "windows":
            return ("Computer-Use ist für Windows noch nicht gebaut. Der Rest "
                    "von Dive on Wide läuft hier vollständig. Wer es angehen will: "
                    "Bild und Eingaben gehen über die Win32-API (BitBlt bzw. "
                    "SendInput) — die Stelle dafür ist steuerung.py.")
        return ("Computer-Use kennt dieses System nicht (%s). Unterstützt sind "
                "macOS und Linux (X11/Wayland)." % platform.system())

    hinweis_steuern = hinweis_foto


# ---------------------------------------------------------------------------
# Auswahl
# ---------------------------------------------------------------------------

_RUECKSEITEN = {
    "container": ImContainer,
    "macos": MacOS,
    "linux-x11": LinuxX11,
    "linux-wayland": LinuxWayland,
    "linux-ohne-anzeige": OhneAnzeige,
    "windows": Windows,
}


def schirm_waehlen(wunsch="auto"):
    """Welcher Bildschirm — der echte oder der im Container?

    „auto" heißt: **Wenn ein Container-Bildschirm läuft, nimm ihn.** Nicht aus
    Bequemlichkeit, sondern weil das die sichere Wahl ist: Was dort schiefgeht,
    geht in einem Wegwerf-Behälter schief. Läuft keiner, bleibt der echte
    Bildschirm — und die Oberfläche sagt dann deutlich, dass der Agent auf dem
    wirklichen Schirm klickt.

    Die Reihenfolge ist Absicht. Andersherum („nimm den echten, außer jemand
    stellt um") wäre die riskante Wahl die Voreinstellung, und Voreinstellungen
    sind das, was fast alle behalten."""
    wunsch = (wunsch or "auto").strip().lower()
    if wunsch == "container":
        return "container"
    if wunsch in ("echt", "system", "wirt"):
        return plattform_erkennen()
    if ImContainer().behaelter_laeuft():
        return "container"
    return plattform_erkennen()


def rueckseite(kennung=None):
    """Die passende Rückseite für dieses System. Nie zwischengespeichert:
    Die Sitzungsart kann sich ändern (Abmelden, X11 statt Wayland wählen),
    und ein festgehaltener Wert wäre dann falsch, ohne es zu zeigen."""
    kennung = kennung or plattform_erkennen()
    klasse = _RUECKSEITEN.get(kennung)
    return klasse() if klasse else Fremd(kennung)


def lage(kennung=None):
    """Ein vollständiger, ehrlicher Statusbericht für die Anzeige."""
    r = rueckseite(kennung)
    foto_da = bool(r.foto_werkzeug())
    steuer_da = bool(r.steuer_werkzeug())
    darf_lesen = r.darf_lesen() if foto_da else False
    darf_steuern = r.darf_steuern() if steuer_da else False
    bericht = {
        "plattform": r.name,
        "beschreibung": r.beschreibung,
        "foto_werkzeug": r.foto_werkzeug(),
        "steuer_werkzeug": r.steuer_werkzeug(),
        "darf_lesen": darf_lesen,
        "darf_steuern": darf_steuern,
        "hinweise": [],
        # Auf macOS gemessen; die Linux-Wege sind geschrieben und ihre Aufrufe
        # geprueft, aber nie auf einem echten Anzeigeserver gelaufen. Wer sie
        # zuerst benutzt, soll wissen, dass er der Erste ist.
        "erprobt": r.name in ("macos", "windows"),     # Windows: VM 30.09.2026 — Bild, Klick, Unicode-Tippen, Taste
        # Ist das hier eine echte Grenze — oder der Bildschirm des Besitzers?
        "abgeschottet": r.name == "container",
    }
    # Doppelte Hinweise weglassen: Fehlen Bild UND Steuerung aus DERSELBEN
    # Ursache (kein Docker, keine Sitzung, kein Bildschirm), stand der Satz
    # zweimal da. Das liest sich wie zwei Probleme, wo eines ist.
    for noetig, text in ((not darf_lesen, r.hinweis_foto()),
                         (not darf_steuern, r.hinweis_steuern())):
        if noetig and text and text not in bericht["hinweise"]:
            bericht["hinweise"].append(text)
    return bericht
