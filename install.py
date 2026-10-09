#!/usr/bin/env python3
"""Dive on Wide — Installer für macOS, Linux und Windows.

EINE DATEI. Reine Standardbibliothek, keine Fremdpakete — dieselbe Zusage wie
Dive on Wide selbst. Wer sie hat, kann Dive on Wide einrichten:

    python3 install.py                 einrichten oder aktualisieren
    python3 install.py --pruefen       nur nachsehen, nichts anfassen
    python3 install.py --alles         auch das Optionale (verteiltes Rechnen …)
    python3 install.py --ja            keine Rückfragen (für Skripte)

WAS DIESER INSTALLER ANDERS MACHT
---------------------------------
**Er installiert nichts ungefragt.** Vor jedem Schritt steht, WELCHER BEFEHL
gleich ausgeführt wird — im Klartext, zum Mitlesen. Wer bei einem Schritt nein
sagt, bekommt danach gesagt, was ohne ihn fehlt. Ein Installer, der im
Hintergrund Paketmanager anwirft und Systemrechte einsammelt, ist genau das
Gegenteil von dem, was Dive on Wide verspricht.

**Er sagt, was er NICHT weiß.** Entwickelt und gemessen wurde auf macOS. Linux
(Ubuntu-VM, 27.09.2026) und Windows 11 (VM, ARM64, 29.09.2026) sind mit Installer,
Einrichtung und ganzer Testsuite gelaufen; ein echter x64-PC mit Nvidia-Karte und
WSL2 noch nicht. Das steht auch im Ergebnisbericht, nicht nur hier.

**Er ist zugleich das Aktualisierungswerkzeug.** Ein zweiter Lauf holt die
neueste Fassung und rührt nichts an, was schon stimmt.
"""

import argparse
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def _deutsch():
    """Spricht das System Deutsch? (wie plattform.konsole_deutsch — der Installer läuft allein)"""
    wahl = (os.environ.get("DOWOS_SPRACHE") or "").strip().lower()
    if wahl in ("de", "en"):
        return wahl == "de"
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        wert = (os.environ.get(name) or "").strip()
        if wert:
            return wert.lower().startswith("de")
    if os.name == "nt":
        try:
            import ctypes
            return (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x07
        except (AttributeError, OSError):
            pass
    return False


DEUTSCH = _deutsch()
# Englisch für alle Ausgaben; Schlüssel ist der deutsche Text, wie er im Code steht.
ENGLISCH = {
    'Das ist kein Installationsordner, sondern %s.': 'That is not an installation folder but %s.',
    'In %s liegt keine Dive-on-Wide-Installation (server.py und frontend/ fehlen).': 'There is no Dive on Wide installation in %s (server.py and frontend/ are missing).',
    '%s ist eine Git-Arbeitskopie — die entfernt dieser Installer nicht.': '%s is a Git working copy — this installer does not remove that.',
    '\n  Dive on Wide — Entfernen': '\n  Dive on Wide — Uninstall',
    'Dive on Wide läuft noch. Erst beenden (Fenster schließen oder Ctrl+C), dann erneut.': 'Dive on Wide is still running. Quit it first (close the window or Ctrl+C), then try again.',
    'die Installation samt deiner Daten (Chats, Wissen, Projekte in storage/)': 'the installation including your data (chats, knowledge, projects in storage/)',
    'selbst gebautes llama.cpp und Hilfsdateien': 'self-built llama.cpp and helper files',
    'das Browserprofil des Browser-Agenten': "the browser agent's browser profile",
    'Was entfernt werden kann': 'What can be removed',
    'Zuerst deine Daten sichern nach %s?': 'Back up your data to %s first?',
    'Gesichert: %s': 'Backed up: %s',
    'Entfernen: %s?': 'Remove: %s?',
    'Nicht vollständig entfernt (Datei in Benutzung?): %s': 'Not completely removed (file in use?): %s',
    'Entfernt: %s': 'Removed: %s',
    'Bleibt bewusst': 'Deliberately kept',
    'Programme, die der Installer mit deiner Zustimmung installiert hat (Ollama, git, cmake, cliclick, xdotool, bubblewrap) — sie gehören dir und werden vielleicht anderswo gebraucht.': 'Programs the installer installed with your consent (Ollama, git, cmake, cliclick, xdotool, bubblewrap) — they are yours and may be needed elsewhere.',
    'Deine Ollama-Modelle (~/.ollama). Einzeln entfernen: ollama rm <name>': 'Your Ollama models (~/.ollama). Remove individually: ollama rm <name>',
    'Autostart-Einträge legt Dive on Wide keine an; am System wurde nichts weiter verändert.': 'Dive on Wide creates no autostart entries; nothing else on the system was changed.',
    'Nur nachsehen, nichts installieren': 'Only look, install nothing',
    'Dive on Wide wieder entfernen (sichert vorher deine Daten, fragt je Teil)': 'Remove Dive on Wide again (backs up your data first, asks per part)',
    'Wohin Dive on Wide soll (Vorgabe: %s)': 'Where Dive on Wide should go (default: %s)',
    'kein Paketmanager gefunden': 'no package manager found',
    'Programm nicht gefunden: %s': 'Program not found: %s',
    'Zeitüberschreitung': 'Timed out',
    'Dive on Wide selbst — es ist ein Python-Programm': 'Dive on Wide itself — it is a Python program',
    'Dieses Python ist %s, gebraucht wird mindestens %d.%d. Ein laufendes Python kann sich nicht selbst ersetzen — bitte eine neuere Fassung installieren und den Installer damit erneut starten.': 'This Python is %s, at least %d.%d is needed. A running Python cannot replace itself — please install a newer version and start the installer again with it.',
    '\n      Oder ein Ollama auf einem anderen Rechner nutzen: dessen Adresse trägst du in der Einrichtung von Dive on Wide ein (z. B. http://host.lima.internal:11434 aus einer Lima-VM).': '\n      Or use Ollama on another computer: enter its address in the Dive on Wide setup (e.g. http://host.lima.internal:11434 from a Lima VM).',
    'die Sprachmodelle auf diesem Gerät': 'the language models on this device',
    'Ollama bietet für Linux ein Installationsskript an:\n        curl -fsSL https://ollama.com/install.sh -o ollama-install.sh\n        less ollama-install.sh      # erst lesen\n        sh ollama-install.sh        # dann ausführen\n      Bewusst in zwei Schritten: „curl … | sh“ führt Code aus, den niemand gesehen hat.': 'Ollama offers an install script for Linux:\n        curl -fsSL https://ollama.com/install.sh -o ollama-install.sh\n        less ollama-install.sh      # read it first\n        sh ollama-install.sh        # then run it\n      Deliberately in two steps: “curl … | sh” runs code nobody has looked at.',
    'Von https://ollama.com/download herunterladen und installieren.': 'Download and install it from https://ollama.com/download.',
    'Kein Homebrew gefunden. Es ist der übliche Weg auf dem Mac: https://brew.sh — danach diesen Installer erneut starten.': 'No Homebrew found. It is the usual way on the Mac: https://brew.sh — then start this installer again.',
    'Kein winget/scoop/choco gefunden. winget gehört zu aktuellen Windows-Fassungen; sonst %s von Hand installieren.': 'No winget/scoop/choco found. winget ships with current Windows versions; otherwise install %s by hand.',
    '%s über den Paketmanager deiner Verteilung installieren (apt/dnf/pacman/zypper/apk).': "Install %s with your distribution's package manager (apt/dnf/pacman/zypper/apk).",
    'Befehle des Agenten ohne Netz und nur im Projekt': 'agent commands without network and only inside the project',
    'keine unter Windows — jeder Befehl wird einzeln freigegeben': 'none on Windows — every command is approved one by one',
    'bubblewrap ist installiert, darf aber keine Namensräume anlegen. Unter Ubuntu bringt das Paket ein AppArmor-Profil mit (/etc/apparmor.d/bwrap-userns-restrict) — nach der Installation einmal „sudo systemctl reload apparmor“. Ohne Sandbox fragt die Werkbank vor jedem Befehl.': 'bubblewrap is installed but may not create namespaces. On Ubuntu the package brings an AppArmor profile (/etc/apparmor.d/bwrap-userns-restrict) — after installing, run “sudo systemctl reload apparmor” once. Without a sandbox the Workbench asks before every command.',
    'bubblewrap über den Paketmanager installieren (Paket „bubblewrap“).': 'Install bubblewrap with the package manager (package “bubblewrap”).',
    'llama.cpp mit RPC': 'llama.cpp with RPC',
    'ein Modell über mehrere Geräte rechnen lassen': 'let one model compute across several devices',
    'Am einfachsten: das offizielle Paket für dieses System von github.com/ggml-org/llama.cpp/releases (mit GGML_RPC=ON gebaut) nach %s entpacken. Mit --alles baut dieser Installer es stattdessen aus dem Quelltext (git und cmake, gut zehn Minuten).': 'Easiest: unpack the official package for this system from github.com/ggml-org/llama.cpp/releases (built with GGML_RPC=ON) to %s. With --alles this installer builds it from source instead (git and cmake, a good ten minutes).',
    '%s fehlt — ohne das lässt sich llama.cpp nicht bauen.': '%s is missing — llama.cpp cannot be built without it.',
    'Quelltext ist da, hole Neuerungen …': 'Source is there, fetching updates …',
    'Hole llama.cpp …': 'Fetching llama.cpp …',
    'Richte ein (GGML_RPC=ON) …': 'Configuring (GGML_RPC=ON) …',
    'Übersetze mit %s Kernen — das dauert. Kaffee.': 'Compiling with %s cores — this takes a while. Coffee.',
    'Übersetzen fehlgeschlagen: %s': 'Compiling failed: %s',
    'Gebaut, aber die Programme laufen nicht von %s aus oder kennen kein --rpc.': 'Built, but the programs do not run from %s or do not know --rpc.',
    'Fertig. Dive on Wide findet sie unter %s von allein.': 'Done. Dive on Wide finds them under %s on its own.',
    'Bildschirmsteuerung': 'Screen control',
    'Computer-Use: Maus und Tastatur bewegen': 'Computer use: move mouse and keyboard',
    ' Danach fragt macOS einmalig nach der Berechtigung „Bedienungshilfen“ für das Programm, in dem Dive on Wide läuft.': ' After that macOS asks once for the “Accessibility” permission for the program Dive on Wide runs in.',
    ' Unter Wayland genügt das oft nicht — dort entscheidet der Compositor, wer den Schirm sehen darf; am kürzesten ist eine X11-Sitzung.': ' Under Wayland this is often not enough — there the compositor decides who may see the screen; the shortest way is an X11 session.',
    'Fehlt noch: %s.%s': 'Still missing: %s.%s',
    'Freigaben nicht abrufbar (HTTP %d). Solange es kein öffentliches Repo gibt, ist das erwartet — dann mit --von <paket.zip> arbeiten.': 'Releases not available (HTTP %d). As long as there is no public repo, that is expected — then work with --von <package.zip>.',
    'Freigaben nicht abrufbar: %s': 'Releases not available: %s',
    'Lade %s …': 'Downloading %s …',
    'Quelltext holen und aktualisieren': 'fetch and update source code',
    'llama.cpp mit RPC bauen': 'build llama.cpp with RPC',
    '[automatisch ja]': '[automatically yes]',
    'Was auf diesem Rechner da ist': 'What this computer already has',
    'fehlt — ': 'missing — ',
    '  Es wird nichts installiert, ohne dass du zustimmst.\n': '  Nothing is installed without your consent.\n',
    'Das geht so nicht weiter': "This can't go on like this",
    '%s fehlt.': '%s is missing.',
    'Auszuführen: ': 'To run: ',
    'Ausführen?': 'Run it?',
    'Dive on Wide holen': 'Getting Dive on Wide',
    'Keine Quelle. Mit --von <paket.zip> ein Paket angeben.': 'No source. Give a package with --von <package.zip>.',
    'Fertig': 'Done',
    'Was ohne die abgelehnten Schritte fehlt': 'What is missing without the declined steps',
    'Nichts davon ist endgültig: Dive on Wide sagt bei jeder Funktion selbst, was ihr fehlt, und dieser Installer lässt sich jederzeit erneut starten.': 'None of this is final: Dive on Wide tells you for each feature what it is missing, and this installer can be run again at any time.',
    'Ehrlich zum Erprobungsstand': 'Honest testing status',
    'Dieser Installer ist auf macOS entwickelt und in VMs erprobt (Ubuntu 26.04, Windows 11), auf echter %s-Hardware aber noch kaum. Wenn etwas schiefgeht, ist das ein Fehler und keine Absicht.': 'This installer was developed on macOS and tested in VMs (Ubuntu 26.04, Windows 11), but hardly on real %s hardware yet. If something goes wrong, that is a bug, not intent.',
    'Richtet Dive on Wide ein oder bringt es auf den neuesten Stand.': 'Sets up Dive on Wide or brings it up to date.',
    'Ein lokales Paket oder eine URL statt der neuesten Freigabe': 'A local package or a URL instead of the latest release',
    'Auch das Optionale anbieten (verteiltes Rechnen, Steuerung)': 'Also offer the optional parts (distributed computing, screen control)',
    'Alle Fragen mit ja beantworten (für Skripte)': 'Answer every question with yes (for scripts)',
    '\n  Abgebrochen. Es wurde nichts weiter verändert.': '\n  Cancelled. Nothing else was changed.',
    'Nur nachgesehen — nichts angefasst': 'Only looked — nothing touched',
    '\n  Dive on Wide — Einrichtung': '\n  Dive on Wide — Setup',
}


def T(text):
    return text if DEUTSCH else ENGLISCH.get(text, text)

# Wo Dive on Wide landet. Ein eigener Ordner im Benutzerverzeichnis: kein sudo,
# keine Systemordner, restlos loeschbar.
ZIEL_VORGABE = os.path.join(os.path.expanduser("~"), "DowOS")

# Woher die neueste Fassung kommt. Solange es kein oeffentliches Repo gibt,
# scheitert das ehrlich — und `--von <datei.zip>` nimmt ein lokales Paket.
HERKUNFT = os.environ.get(
    "DOWOS_HERKUNFT",
    "https://api.github.com/repos/daiams2000-hash/dive-on-wide/releases/latest")

MIN_PYTHON = (3, 9)


# ---------------------------------------------------------------------------
# Ausgabe
# ---------------------------------------------------------------------------

class Stift:
    """Farbe, wo sie ankommt — und sonst nicht.

    Windows-Terminals ohne ANSI-Unterstuetzung bekaemen sonst Steuerzeichen
    mitten in den Text. Wer die Ausgabe in eine Datei leitet, auch."""

    an = sys.stdout.isatty() and os.environ.get("TERM") != "dumb"

    @classmethod
    def _f(cls, code, text):
        return "\033[%sm%s\033[0m" % (code, text) if cls.an else text

    @classmethod
    def gut(cls, t): return cls._f("32", t)
    @classmethod
    def warn(cls, t): return cls._f("33", t)
    @classmethod
    def schlecht(cls, t): return cls._f("31", t)
    @classmethod
    def fett(cls, t): return cls._f("1", t)
    @classmethod
    def leise(cls, t): return cls._f("2", t)


def kopf(text):
    print("\n" + Stift.fett(text))
    print(Stift.leise("─" * min(len(text), 68)))


def zeile(zeichen, text, farbe=None):
    print("  %s %s" % (zeichen, farbe(text) if farbe else text))


# ---------------------------------------------------------------------------
# Wo sind wir?
# ---------------------------------------------------------------------------

class Lage:
    """Was für ein Rechner ist das, und womit installiert man hier?"""

    def __init__(self):
        self.system = platform.system()          # Darwin / Linux / Windows
        self.arch = platform.machine()
        self.mac = self.system == "Darwin"
        self.linux = self.system == "Linux"
        self.windows = self.system == "Windows"
        self.paketmanager = self._paketmanager()

    def _paketmanager(self):
        """Der Paketmanager DIESES Rechners — oder None.

        Bewusst keine Rangliste ueber Systeme hinweg: Auf einem Debian mit
        installiertem Homebrew soll trotzdem apt gewinnen, denn das ist der
        Weg, den das System selbst pflegt."""
        if self.mac:
            return "brew" if shutil.which("brew") else None
        if self.windows:
            for k in ("winget", "choco", "scoop"):
                if shutil.which(k):
                    return k
            return None
        for k in ("apt-get", "dnf", "pacman", "zypper", "apk"):
            if shutil.which(k):
                return k
        return None

    def braucht_sudo(self):
        """Verlangt der Paketmanager erhöhte Rechte?

        `os.geteuid` gibt es auf Windows NICHT. Die Kurzschluss-Auswertung von
        `self.linux and …` würde das heute abfangen — aber nur, solange die
        Reihenfolge stimmt. Wer die Bedingung einmal umstellt, bricht Windows,
        und der Fehler wäre ein AttributeError mitten im Installer. Deshalb
        ausdrücklich nachfragen, statt sich auf die Reihenfolge zu verlassen."""
        if not self.linux:
            return False
        if self.paketmanager not in ("apt-get", "dnf", "pacman", "zypper", "apk"):
            return False
        holen = getattr(os, "geteuid", None)
        return holen() != 0 if holen else True

    def __str__(self):
        return "%s %s (%s)" % (self.system, self.arch,
                               self.paketmanager or T("kein Paketmanager gefunden"))


def lauf(befehl, frist=None, still=True):
    """Einen Befehl ausführen. Gibt (erfolg, ausgabe) zurück."""
    try:
        r = subprocess.run(befehl, capture_output=still, timeout=frist,
                           text=True)
        return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")) if still else ""
    except FileNotFoundError:
        return False, T("Programm nicht gefunden: %s") % befehl[0]
    except subprocess.TimeoutExpired:
        return False, T("Zeitüberschreitung")
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------

class Baustein:
    """Eine Voraussetzung: wie man sie prüft, wie man sie bekommt.

    `pflicht` heißt: Ohne das läuft Dive on Wide gar nicht. Alles andere schaltet
    einzelne Fähigkeiten frei — und die Diagnose in Dive on Wide sagt später
    genau, welche fehlt. Deshalb darf hier jeder Schritt abgelehnt werden."""

    def __init__(self, schluessel, name, wofuer, pflicht=False):
        self.schluessel = schluessel
        self.name = name
        self.wofuer = wofuer
        self.pflicht = pflicht

    def da(self, lage):
        raise NotImplementedError

    def fassung(self, lage):
        return ""

    def befehl(self, lage):
        """Der Befehl, der ihn installiert — oder None, wenn es keinen gibt."""
        return None

    def hinweis(self, lage):
        """Was zu tun ist, wenn es keinen Befehl gibt."""
        return ""


class PythonBaustein(Baustein):
    def __init__(self):
        Baustein.__init__(self, "python", "Python 3",
                          T("Dive on Wide selbst — es ist ein Python-Programm"), pflicht=True)

    def da(self, lage):
        return sys.version_info >= MIN_PYTHON

    def fassung(self, lage):
        return "%d.%d.%d" % sys.version_info[:3]

    def befehl(self, lage):
        # Wer diesen Installer ausfuehrt, HAT Python. Ein zu altes laesst sich
        # aber nicht unter sich selbst austauschen — das waere ein Ast, auf dem
        # man saegt. Deshalb nur der Hinweis.
        return None

    def hinweis(self, lage):
        return (T("Dieses Python ist %s, gebraucht wird mindestens %d.%d. "
                "Ein laufendes Python kann sich nicht selbst ersetzen — bitte "
                "eine neuere Fassung installieren und den Installer damit "
                "erneut starten.") % (self.fassung(lage), MIN_PYTHON[0], MIN_PYTHON[1]))


# Ollama muss nicht auf DIESEM Rechner laufen (erster Linux-Lauf 27.09.2026:
# Dive on Wide in einer VM, Ollama auf dem Gastgeber mit der Grafikkarte).
ANDERSWO = (T("\n      Oder ein Ollama auf einem anderen Rechner nutzen: dessen Adresse trägst du "
            "in der Einrichtung von Dive on Wide ein (z. B. http://host.lima.internal:11434 aus einer Lima-VM)."))


class OllamaBaustein(Baustein):
    def __init__(self):
        Baustein.__init__(self, "ollama", "Ollama",
                          T("die Sprachmodelle auf diesem Gerät"))

    def da(self, lage):
        return bool(shutil.which("ollama"))

    def fassung(self, lage):
        ok, aus = lauf(["ollama", "--version"], frist=10)
        m = re.search(r"(\d+\.\d+\.\d+)", aus or "")
        return m.group(1) if m else ("vorhanden" if ok else "")

    def befehl(self, lage):
        if lage.mac and lage.paketmanager == "brew":
            return ["brew", "install", "ollama"]
        if lage.windows:
            if lage.paketmanager == "winget":
                return ["winget", "install", "--id", "Ollama.Ollama",
                        "-e", "--accept-package-agreements",
                        "--accept-source-agreements"]
            if lage.paketmanager == "scoop":
                return ["scoop", "install", "ollama"]
        return None

    def hinweis(self, lage):
        if lage.linux:
            # Ollama liefert fuer Linux ein eigenes Skript. Es NICHT blind
            # durch die Shell zu jagen ist Absicht: „curl … | sh" laedt Code
            # und fuehrt ihn sofort aus, ohne dass jemand hineingesehen hat.
            return (T("Ollama bietet für Linux ein Installationsskript an:\n"
                    "        curl -fsSL https://ollama.com/install.sh -o ollama-install.sh\n"
                    "        less ollama-install.sh      # erst lesen\n"
                    "        sh ollama-install.sh        # dann ausführen\n"
                    "      Bewusst in zwei Schritten: „curl … | sh“ führt Code aus, "
                    "den niemand gesehen hat.") + ANDERSWO)
        return T("Von https://ollama.com/download herunterladen und installieren.") + ANDERSWO


def paket_befehl(lage, pakete):
    """Ein Installationsbefehl fuer EIN oder MEHRERE Pakete — oder None."""
    pm = lage.paketmanager
    vorne = ["sudo"] if lage.braucht_sudo() else []
    if pm == "brew":
        return ["brew", "install"] + pakete
    if pm == "apt-get":
        # Auf einem frischen Ubuntu ist die Paketliste veraltet: ohne „update“
        # endet install mit 404 (erster Lauf auf echtem Linux, 27.09.2026).
        return vorne + ["sh", "-c", "apt-get update -q && apt-get install -y " + " ".join(pakete)]
    if pm == "dnf":
        return vorne + ["dnf", "install", "-y"] + pakete
    if pm == "pacman":
        return vorne + ["pacman", "-S", "--noconfirm"] + pakete
    if pm == "zypper":
        return vorne + ["zypper", "install", "-y"] + pakete
    if pm == "apk":
        return vorne + ["apk", "add"] + pakete
    if pm == "winget" and len(pakete) == 1:
        return ["winget", "install", "--id", pakete[0], "-e",
                "--accept-package-agreements", "--accept-source-agreements"]
    if pm == "choco":
        return ["choco", "install", "-y"] + pakete
    if pm == "scoop":
        return ["scoop", "install"] + pakete
    return None


class WerkzeugBaustein(Baustein):
    """Ein Baustein, der einfach ein Programm im PATH ist."""

    def __init__(self, schluessel, name, wofuer, programm, pakete, pflicht=False):
        Baustein.__init__(self, schluessel, name, wofuer, pflicht)
        self.programm = programm
        self.pakete = pakete            # {"brew": "cmake", "apt-get": "cmake", …}

    def da(self, lage):
        return bool(shutil.which(self.programm))

    def befehl(self, lage):
        paket = self.pakete.get(lage.paketmanager)
        if not paket:
            return None
        return paket_befehl(lage, [paket] if isinstance(paket, str) else list(paket))

    def hinweis(self, lage):
        if not lage.paketmanager:
            if lage.mac:
                return (T("Kein Homebrew gefunden. Es ist der übliche Weg auf dem "
                        "Mac: https://brew.sh — danach diesen Installer erneut "
                        "starten."))
            if lage.windows:
                return (T("Kein winget/scoop/choco gefunden. winget gehört zu "
                        "aktuellen Windows-Fassungen; sonst %s von Hand "
                        "installieren.") % self.programm)
        if lage.linux:
            return (T("%s über den Paketmanager deiner Verteilung installieren "
                    "(apt/dnf/pacman/zypper/apk).") % self.programm)
        return "%s von Hand installieren." % self.programm


class SandboxBaustein(Baustein):
    """Die Grenze um die Befehle des Agenten — auf Linux bubblewrap.

    Fehlte im Installer, bis Dive on Wide zum ersten Mal auf echtem Linux lief
    (27.09.2026): Die Werkbank hatte dort keine Sandbox, und niemand sagte es.
    macOS bringt sandbox-exec mit; unter Windows gibt es keine — dort muss
    jeder Befehl einzeln freigegeben werden."""

    def __init__(self):
        Baustein.__init__(self, "sandbox", "Sandbox",
                          T("Befehle des Agenten ohne Netz und nur im Projekt"))

    def da(self, lage):
        if not lage.linux:
            return True
        bwrap = shutil.which("bwrap")
        if not bwrap:
            return False
        # Vorhanden heisst nicht lauffaehig: Ubuntu ab 24.04 sperrt
        # unprivilegierte Namensraeume per AppArmor, ausser fuer Programme mit Profil.
        ok, _ = lauf([bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                      "--unshare-net", "true"], frist=15)
        return ok

    def fassung(self, lage):
        if lage.mac:
            return "sandbox-exec (eingebaut)"
        if lage.windows:
            return T("keine unter Windows — jeder Befehl wird einzeln freigegeben")
        return "bubblewrap" if self.da(lage) else ""

    def befehl(self, lage):
        if not lage.linux or shutil.which("bwrap"):
            return None
        return paket_befehl(lage, ["bubblewrap"])

    def hinweis(self, lage):
        if lage.linux and shutil.which("bwrap"):
            return (T("bubblewrap ist installiert, darf aber keine Namensräume anlegen. "
                    "Unter Ubuntu bringt das Paket ein AppArmor-Profil mit "
                    "(/etc/apparmor.d/bwrap-userns-restrict) — nach der Installation "
                    "einmal „sudo systemctl reload apparmor“. Ohne Sandbox fragt die "
                    "Werkbank vor jedem Befehl."))
        return T("bubblewrap über den Paketmanager installieren (Paket „bubblewrap“).")


class LlamaRpcBaustein(Baustein):
    """llama.cpp MIT RPC — die Voraussetzung für verteiltes Rechnen.

    Der wichtigste und zugleich unangenehmste Baustein. Was Paketmanager
    ausliefern, ist **ohne** RPC übersetzt: kein `ggml-rpc-server`, kein
    `--rpc` in `llama-server`. Man merkt das erst, wenn verteilte Inferenz
    wortlos nicht geht. Selbst bauen ist die einzige Abhilfe."""

    def __init__(self):
        Baustein.__init__(self, "llama-rpc", T("llama.cpp mit RPC"),
                          T("ein Modell über mehrere Geräte rechnen lassen"))

    def ort(self):
        return os.path.join(os.path.expanduser("~"), ".dowos", "llama-rpc", "bin")

    def _programm(self, name):
        eigen = os.path.join(self.ort(), name + (".exe" if os.name == "nt" else ""))
        if os.path.isfile(eigen) and os.access(eigen, os.X_OK):
            return eigen
        return shutil.which(name) or ""

    def da(self, lage):
        rpc = self._programm("ggml-rpc-server") or self._programm("rpc-server")
        server = self._programm("llama-server")
        if not (rpc and server):
            return False
        ok, aus = lauf([server, "--help"], frist=20)
        return "--rpc" in (aus or "")

    def fassung(self, lage):
        return "gebaut" if self.da(lage) else ""

    def befehl(self, lage):
        return None          # wird gebaut, nicht installiert — siehe bauen()

    def hinweis(self, lage):
        return (T("Am einfachsten: das offizielle Paket für dieses System von "
                "github.com/ggml-org/llama.cpp/releases (mit GGML_RPC=ON gebaut) nach %s "
                "entpacken. Mit --alles baut dieser Installer es stattdessen aus dem "
                "Quelltext (git und cmake, gut zehn Minuten).") % self.ort())

    def bauen(self, lage, sprich):
        """Holt den Quelltext, baut mit RPC, prüft nach und legt ab.

        Die Prüfung am Ende ist kein Zierrat: Ein Bau, der die RPC-Flagge
        still verliert, sieht genau wie ein gelungener aus."""
        for noetig in ("git", "cmake"):
            if not shutil.which(noetig):
                sprich("✗", T("%s fehlt — ohne das lässt sich llama.cpp nicht "
                            "bauen.") % noetig, Stift.schlecht)
                return False
        quelle = os.path.join(os.path.expanduser("~"), ".dowos", "llama.cpp")
        os.makedirs(os.path.dirname(quelle), exist_ok=True)
        if os.path.isdir(os.path.join(quelle, ".git")):
            sprich("→", T("Quelltext ist da, hole Neuerungen …"))
            lauf(["git", "-C", quelle, "pull", "--ff-only"], frist=300)
        else:
            sprich("→", T("Hole llama.cpp …"))
            ok, aus = lauf(["git", "clone", "--depth", "1",
                            "https://github.com/ggml-org/llama.cpp", quelle],
                           frist=900)
            if not ok:
                sprich("✗", "Klonen fehlgeschlagen: %s" % aus[-160:], Stift.schlecht)
                return False
        bau = os.path.join(quelle, "build")
        sprich("→", T("Richte ein (GGML_RPC=ON) …"))
        ok, aus = lauf(["cmake", "-S", quelle, "-B", bau, "-DGGML_RPC=ON",
                        "-DCMAKE_BUILD_TYPE=Release"], frist=600)
        if not ok:
            sprich("✗", "cmake fehlgeschlagen: %s" % aus[-200:], Stift.schlecht)
            return False
        kerne = str(os.cpu_count() or 4)
        sprich("→", T("Übersetze mit %s Kernen — das dauert. Kaffee.") % kerne)
        # Der Rechenknoten heisst ggml-rpc-server, NICHT rpc-server. Wer den
        # alten Namen nimmt, bekommt „No rule to make target" und haelt das
        # Feature fuer unmoeglich.
        ok, aus = lauf(["cmake", "--build", bau, "--config", "Release",
                        "-j", kerne, "--target",
                        "ggml-rpc-server", "llama-server", "llama-cli"],
                       frist=3600)
        if not ok:
            sprich("✗", T("Übersetzen fehlgeschlagen: %s") % aus[-300:], Stift.schlecht)
            return False
        quell_bin = os.path.join(bau, "bin")
        ziel = self.ort()
        os.makedirs(ziel, exist_ok=True)
        for name in os.listdir(quell_bin):
            try:
                shutil.copy2(os.path.join(quell_bin, name),
                             os.path.join(ziel, name))
            except Exception:
                pass
        if lage.mac:
            # Die Programme suchen ihre Bibliotheken ueber @rpath, und cmake
            # traegt dort den ABSOLUTEN Pfad des Bauordners ein. Kopiert man
            # sie weg, starten sie nicht mehr — mit einer Meldung, die aussieht,
            # als fehle eine Bibliothek, obwohl sie danebenliegt.
            for p in ("llama-server", "llama-cli", "ggml-rpc-server"):
                pfad = os.path.join(ziel, p)
                if os.path.isfile(pfad):
                    lauf(["install_name_tool", "-add_rpath", "@loader_path", pfad],
                         frist=60)
                    lauf(["codesign", "-f", "-s", "-", pfad], frist=120)
        if not self.da(lage):
            sprich("✗", T("Gebaut, aber die Programme laufen nicht von %s aus "
                        "oder kennen kein --rpc.") % ziel, Stift.schlecht)
            return False
        sprich("✓", T("Fertig. Dive on Wide findet sie unter %s von allein.") % ziel,
               Stift.gut)
        return True


class SteuerungBaustein(Baustein):
    """Maus und Tastatur — je nach System etwas anderes."""

    def __init__(self):
        Baustein.__init__(self, "steuerung", T("Bildschirmsteuerung"),
                          T("Computer-Use: Maus und Tastatur bewegen"))

    def _noetig(self, lage):
        if lage.mac:
            return [("cliclick", {"brew": "cliclick"})]
        if lage.linux:
            return [("xdotool", {"apt-get": "xdotool", "dnf": "xdotool",
                                 "pacman": "xdotool", "zypper": "xdotool",
                                 "apk": "xdotool"}),
                    ("maim", {"apt-get": "maim", "dnf": "maim",
                              "pacman": "maim", "zypper": "maim", "apk": "maim"})]
        return []

    def da(self, lage):
        if lage.windows:
            return True  # Windows bringt Bildschirmfoto, Maus und Tastatur selbst mit (steuerung.Windows)
        noetig = self._noetig(lage)
        return bool(noetig) and all(shutil.which(p) for p, _ in noetig)

    def befehl(self, lage):
        fehlt = [pk.get(lage.paketmanager) for p, pk in self._noetig(lage) if not shutil.which(p)]
        if not fehlt or not all(fehlt):
            return None
        # Alle fehlenden auf einmal — vorher nur das erste (xdotool ohne maim).
        return paket_befehl(lage, fehlt)

    def hinweis(self, lage):
        fehlt = [p for p, _ in self._noetig(lage) if not shutil.which(p)]
        if not fehlt:
            return ""
        # Der Zusatz muss zum SYSTEM passen. Hier stand der macOS-Satz fuer
        # alle — auf Linux also ein Rat zu „Bedienungshilfen", die es dort gar
        # nicht gibt. Dieselbe Sorte Fehler, die steuerung.py schon einmal
        # hatte: richtige Werkzeuge, falsche Erklaerung.
        if lage.mac:
            zusatz = (T(" Danach fragt macOS einmalig nach der Berechtigung "
                      "„Bedienungshilfen“ für das Programm, in dem Dive on Wide läuft."))
        elif lage.linux:
            zusatz = (T(" Unter Wayland genügt das oft nicht — dort entscheidet "
                      "der Compositor, wer den Schirm sehen darf; am kürzesten "
                      "ist eine X11-Sitzung."))
        else:
            zusatz = ""
        return T("Fehlt noch: %s.%s") % (", ".join(fehlt), zusatz)


# ---------------------------------------------------------------------------
# Dive on Wide holen
# ---------------------------------------------------------------------------

def neueste_fassung(herkunft, sprich):
    """Wo liegt die neueste Fassung? Gibt (url, name) zurück.

    Fragt die GitHub-Freigaben ab. Schlägt das fehl, wird das GESAGT und nicht
    stillschweigend eine alte Fassung genommen — „aktuell halten" heißt, den
    Fehlschlag zu bemerken."""
    try:
        anfrage = urllib.request.Request(
            herkunft, headers={"Accept": "application/vnd.github+json",
                               "User-Agent": "dowos-installer"})
        with urllib.request.urlopen(anfrage, timeout=30) as r:
            daten = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sprich("✗", T("Freigaben nicht abrufbar (HTTP %d). Solange es kein "
                    "öffentliches Repo gibt, ist das erwartet — dann mit "
                    "--von <paket.zip> arbeiten.") % e.code, Stift.warn)
        return None, ""
    except Exception as e:
        sprich("✗", T("Freigaben nicht abrufbar: %s") % str(e)[:120], Stift.warn)
        return None, ""
    for anhang in daten.get("assets", []):
        name = anhang.get("name", "")
        if name.endswith(".zip") and "quelltext" not in name.lower():
            return anhang.get("browser_download_url"), name
    return daten.get("zipball_url"), (daten.get("tag_name") or "quelltext") + ".zip"


def holen(quelle, sprich):
    """Lädt (oder kopiert) ein Paket und gibt den lokalen Pfad zurück."""
    if os.path.exists(quelle):
        return quelle
    ziel = os.path.join(tempfile.mkdtemp(prefix="dowos-"),
                        os.path.basename(quelle.split("?")[0]) or "dowos.zip")
    sprich("→", T("Lade %s …") % quelle.split("/")[-1][:60])
    try:
        anfrage = urllib.request.Request(
            quelle, headers={"User-Agent": "dowos-installer"})
        with urllib.request.urlopen(anfrage, timeout=600) as r, \
                open(ziel, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception as e:
        sprich("✗", "Herunterladen fehlgeschlagen: %s" % str(e)[:140], Stift.schlecht)
        return None
    return ziel


def auspacken(paket, ziel, sprich):
    """Packt aus und legt die Dateien nach `ziel`.

    Eigene Daten bleiben: storage/ und .env werden NIE überschrieben. Ein
    Update, das die Chats des Nutzers wegräumt, wäre kein Update."""
    import zipfile
    with zipfile.ZipFile(paket) as z:
        namen = z.namelist()
        # Paketzips haben einen Ordner obendrueber (Dive-on-Wide-0.1035/…) —
        # den herausrechnen, damit die Dateien nicht doppelt verschachtelt landen.
        oben = os.path.commonprefix([n for n in namen if not n.startswith("__")])
        if "/" in oben:
            oben = oben[:oben.index("/") + 1]
        else:
            oben = ""
        bewahrt = ("storage/", ".env")
        gezaehlt = 0
        for n in namen:
            if n.endswith("/"):
                continue
            rel = n[len(oben):] if oben and n.startswith(oben) else n
            if not rel or rel.startswith(".."):
                continue                       # Zip-Slip: niemals nach draussen
            if any(rel == b or rel.startswith(b) for b in bewahrt):
                if os.path.exists(os.path.join(ziel, rel)):
                    continue                   # eigene Daten bleiben
            pfad = os.path.join(ziel, rel)
            if not os.path.abspath(pfad).startswith(os.path.abspath(ziel)):
                continue                       # dito
            os.makedirs(os.path.dirname(pfad), exist_ok=True)
            with z.open(n) as q, open(pfad, "wb") as f:
                shutil.copyfileobj(q, f)
            gezaehlt += 1
        sprich("✓", "%d Dateien nach %s" % (gezaehlt, ziel), Stift.gut)
    # Ausfuehrbarkeit ueberlebt das Zippen nicht immer.
    for name in ("start.sh", "werkzeuge/llamacpp_rpc_bauen.sh"):
        p = os.path.join(ziel, name)
        if os.path.isfile(p):
            os.chmod(p, 0o755)
    return True


def starter_schreiben(ziel, lage, sprich):
    """Legt einen Starter an, der zum System passt.

    Auf Windows war bisher GAR nichts da: start.sh ist ein Bash-Skript. Wer
    Dive on Wide dort ausprobieren wollte, musste selbst herausfinden, dass
    `python server.py` der Weg ist."""
    geschrieben = []
    if lage.windows:
        p = os.path.join(ziel, "Dive on Wide starten.cmd")
        with open(p, "w", encoding="utf-8", newline="\r\n") as f:
            f.write("@echo off\r\n"
                    "cd /d \"%~dp0\"\r\n"
                    "if not exist .env if exist .env.example copy .env.example .env >nul\r\n"
                    "where python >nul 2>nul || (\r\n"
                    "  echo Python 3 fehlt. Installieren mit:\r\n"
                    "  echo     winget install Python.Python.3.12\r\n"
                    "  pause & exit /b 1\r\n"
                    ")\r\n"
                    "start \"\" http://localhost:3000\r\n"
                    "python server.py\r\n"
                    "pause\r\n")
        geschrieben.append(p)
    else:
        # Nicht „dowos“: So heißt der Werkbank-Agent fürs Terminal (dowos werkbank …). Ein Starter mit
        # demselben Namen überschrieb ihn — ./dowos werkbank startete dann den Server.
        p = os.path.join(ziel, "dowos-starten")
        with open(p, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n"
                    "# Startet Dive on Wide. Von überall aufrufbar.\n"
                    'cd "$(dirname "$0")" || exit 1\n'
                    '[ -f .env ] || { [ -f .env.example ] && cp .env.example .env; }\n'
                    'exec python3 server.py "$@"\n')
        os.chmod(p, 0o755)
        geschrieben.append(p)
    for g in geschrieben:
        sprich("✓", "Starter: %s" % g, Stift.gut)
    return geschrieben


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------

def bausteine():
    return [PythonBaustein(), OllamaBaustein(),
            WerkzeugBaustein("git", "git", T("Quelltext holen und aktualisieren"),
                             "git", {"brew": "git", "apt-get": "git",
                                     "dnf": "git", "pacman": "git",
                                     "zypper": "git", "apk": "git",
                                     "winget": "Git.Git", "choco": "git",
                                     "scoop": "git"}),
            WerkzeugBaustein("cmake", "cmake", T("llama.cpp mit RPC bauen"),
                             "cmake", {"brew": "cmake", "apt-get": "cmake",
                                       "dnf": "cmake", "pacman": "cmake",
                                       "zypper": "cmake", "apk": "cmake",
                                       "winget": "Kitware.CMake",
                                       "choco": "cmake", "scoop": "cmake"}),
            SandboxBaustein(),
            LlamaRpcBaustein(),
            SteuerungBaustein()]


def fragen(text, ja_zu_allem=False, vorgabe=True):
    """Eine Frage, die man auch verneinen darf.

    `vorgabe` ist bewusst „ja" fuer alles, was der Nutzer angefordert hat, und
    „nein" fuer alles, was Systemrechte braucht — im Zweifel passiert nichts."""
    if ja_zu_allem:
        print("  %s %s" % (text, Stift.leise(T("[automatisch ja]"))))
        return True
    auswahl = "[J/n]" if vorgabe else "[j/N]"
    try:
        antwort = input("  %s %s " % (text, auswahl)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    if not antwort:
        return vorgabe
    return antwort in ("j", "ja", "y", "yes")


def bericht(lage, liste, sprich):
    """Was ist da, was fehlt — vor allem anderen."""
    kopf(T("Was auf diesem Rechner da ist"))
    zeile("·", "System: %s" % lage)
    fehlend = []
    for b in liste:
        vorhanden = b.da(lage)
        f = b.fassung(lage)
        marke = "✓" if vorhanden else ("✗" if b.pflicht else "·")
        farbe = Stift.gut if vorhanden else (Stift.schlecht if b.pflicht else Stift.warn)
        zeile(marke, "%-22s %s" % (b.name, f if vorhanden else T("fehlt — ") + b.wofuer),
              farbe)
        if not vorhanden:
            fehlend.append(b)
    return fehlend


def einrichten(args):
    lage = Lage()
    def sprich(z, t, farbe=None):
        zeile(z, t, farbe)

    print(Stift.fett(T("\n  Dive on Wide — Einrichtung")))
    print(Stift.leise(T("  Es wird nichts installiert, ohne dass du zustimmst.\n")))

    liste = bausteine()
    fehlend = bericht(lage, liste, sprich)

    # Pflichtbausteine, die fehlen, sind ein hartes Ende.
    hart = [b for b in fehlend if b.pflicht]
    if hart:
        kopf(T("Das geht so nicht weiter"))
        for b in hart:
            zeile("✗", b.hinweis(lage) or (T("%s fehlt.") % b.name), Stift.schlecht)
        return 1

    if args.pruefen:
        kopf(T("Nur nachgesehen — nichts angefasst"))
        for b in fehlend:
            befehl = b.befehl(lage)
            zeile("→", "%s: %s" % (b.name,
                  shlex.join(befehl) if befehl else (b.hinweis(lage) or "von Hand")))
        return 0

    # Optionale Bausteine — einzeln fragen.
    offen = []
    for b in fehlend:
        if b.schluessel in ("llama-rpc", "steuerung") and not args.alles:
            offen.append(b)
            continue
        kopf("%s — %s" % (b.name, b.wofuer))
        befehl = b.befehl(lage)
        if befehl:
            print("  " + Stift.leise(T("Auszuführen: ") + shlex.join(befehl)))
            if lage.braucht_sudo() and befehl[0] == "sudo":
                print("  " + Stift.warn("Das fragt nach deinem Passwort."))
            if fragen(T("Ausführen?"), args.ja):
                ok, aus = lauf(befehl, frist=1800, still=False)
                zeile("✓" if ok else "✗", "%s %s" % (b.name,
                      "installiert" if ok else "fehlgeschlagen"),
                      Stift.gut if ok else Stift.schlecht)
            else:
                offen.append(b)
        elif isinstance(b, LlamaRpcBaustein):
            print("  " + Stift.leise(b.hinweis(lage)))
            if fragen("Jetzt bauen?", args.ja, vorgabe=False):
                if not b.bauen(lage, sprich):
                    offen.append(b)
            else:
                offen.append(b)
        else:
            zeile("→", b.hinweis(lage) or "Von Hand installieren.", Stift.warn)
            offen.append(b)

    # Dive on Wide selbst holen.
    kopf(T("Dive on Wide holen"))
    ziel = os.path.abspath(args.ziel)
    quelle = args.von
    if not quelle:
        url, name = neueste_fassung(HERKUNFT, sprich)
        quelle = url
        if url:
            zeile("→", "Neueste Freigabe: %s" % name)
    if not quelle:
        zeile("✗", T("Keine Quelle. Mit --von <paket.zip> ein Paket angeben."),
              Stift.schlecht)
        return 1
    paket = holen(quelle, sprich)
    if not paket:
        return 1
    os.makedirs(ziel, exist_ok=True)
    if not auspacken(paket, ziel, sprich):
        return 1
    starter_schreiben(ziel, lage, sprich)

    # Was fehlt jetzt noch — und was heisst das?
    kopf(T("Fertig"))
    zeile("✓", "Dive on Wide liegt in %s" % ziel, Stift.gut)
    if lage.windows:
        zeile("→", 'Starten: "Dive on Wide starten.cmd" doppelklicken')
    else:
        zeile("→", "Starten: %s" % os.path.join(ziel, "dowos-starten"))
    zeile("→", "Dann im Browser: http://localhost:3000")
    if offen:
        kopf(T("Was ohne die abgelehnten Schritte fehlt"))
        for b in offen:
            zeile("·", "%s → %s" % (b.name, b.wofuer), Stift.warn)
        print("  " + Stift.leise(
            T("Nichts davon ist endgültig: Dive on Wide sagt bei jeder Funktion selbst, "
            "was ihr fehlt, und dieser Installer lässt sich jederzeit erneut "
            "starten.")))
    if not lage.mac:
        kopf(T("Ehrlich zum Erprobungsstand"))
        zeile("!", T("Dieser Installer ist auf macOS entwickelt und in VMs erprobt "
                   "(Ubuntu 26.04, Windows 11), auf echter %s-Hardware aber noch kaum. "
                   "Wenn etwas schiefgeht, ist das ein Fehler und keine Absicht.")
                   % lage.system, Stift.warn)
    return 0



def ausgabe_absichern():
    """Windows: Umgeleitete Ausgabe (Datei, Pipe, Dienst) ist dort Windows-1252.
    Ein ╔ oder ✓ darin beendete den ganzen Prozess mit UnicodeEncodeError
    (Windows-VM, 29.09.2026). Nicht darstellbare Zeichen werden ersetzt;
    in Dateien und Pipes geht UTF-8."""
    for strom in (sys.stdout, sys.stderr):
        try:
            if strom.isatty():
                strom.reconfigure(errors="replace")
            else:
                strom.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _ordnergroesse(pfad):
    gesamt = 0
    for wurzel, _, dateien in os.walk(pfad):
        for d in dateien:
            try:
                gesamt += os.path.getsize(os.path.join(wurzel, d))
            except OSError:
                pass
    return gesamt


def _mb(n):
    return "%.0f MB" % (n / 1e6) if n >= 1e6 else "%.0f kB" % (n / 1e3)


def ist_installation(ziel):
    """Nur löschen, was sicher eine Dive-on-Wide-Installation ist — nie das Home, nie eine Git-Arbeitskopie."""
    ziel = os.path.abspath(ziel)
    if ziel in (os.path.abspath(os.path.expanduser("~")), os.path.abspath(os.sep)) or len(ziel) < 6:
        return False, T("Das ist kein Installationsordner, sondern %s.") % ziel
    if not (os.path.isfile(os.path.join(ziel, "server.py")) and os.path.isdir(os.path.join(ziel, "frontend"))):
        return False, T("In %s liegt keine Dive-on-Wide-Installation (server.py und frontend/ fehlen).") % ziel
    if os.path.isdir(os.path.join(ziel, ".git")):
        return False, T("%s ist eine Git-Arbeitskopie — die entfernt dieser Installer nicht.") % ziel
    return True, ""


def _laeuft_noch():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%s/api/version" % os.environ.get("PORT", "3000"), timeout=2):
            return True
    except Exception:
        return False


def entfernen(args):
    """Dive on Wide wieder entfernen: erst die eigenen Daten sichern, dann jedes Teil einzeln bestätigen."""
    ziel = os.path.abspath(args.ziel)
    print(Stift.fett(T("\n  Dive on Wide — Entfernen")))
    ok, grund = ist_installation(ziel)
    if not ok:
        zeile("✗", grund, Stift.schlecht)
        return 1
    if _laeuft_noch():
        zeile("✗", T("Dive on Wide läuft noch. Erst beenden (Fenster schließen oder Ctrl+C), dann erneut."), Stift.schlecht)
        return 1
    daten = os.path.join(ziel, "storage")
    teile = [(ziel, T("die Installation samt deiner Daten (Chats, Wissen, Projekte in storage/)"))]
    for extra, was in ((os.path.expanduser("~/.dowos"), T("selbst gebautes llama.cpp und Hilfsdateien")),
                       (os.path.expanduser("~/.dowos-browserprofil"), T("das Browserprofil des Browser-Agenten"))):
        if os.path.isdir(extra):
            teile.append((extra, was))
    kopf(T("Was entfernt werden kann"))
    for pfad, was in teile:
        zeile("·", "%s  (%s) — %s" % (pfad, _mb(_ordnergroesse(pfad)), was))
    if os.path.isdir(daten) and os.listdir(daten):
        sicherung = os.path.join(os.path.dirname(ziel), "DowOS-Sicherung-%s.zip" % time.strftime("%Y%m%d-%H%M"))
        if fragen(T("Zuerst deine Daten sichern nach %s?") % sicherung, args.ja, vorgabe=True):
            import zipfile
            with zipfile.ZipFile(sicherung, "w", zipfile.ZIP_DEFLATED) as z:
                for wurzel, _, dateien in os.walk(daten):
                    for d in dateien:
                        voll = os.path.join(wurzel, d)
                        z.write(voll, os.path.relpath(voll, ziel))
                env = os.path.join(ziel, ".env")
                if os.path.isfile(env):
                    z.write(env, ".env")
            zeile("✓", T("Gesichert: %s") % sicherung, Stift.gut)
    entfernt = []
    for pfad, was in teile:
        # Die Installation selbst nie automatisch: Ein --ja in einem Skript darf keine Daten vernichten.
        if fragen(T("Entfernen: %s?") % pfad, args.ja and pfad != ziel, vorgabe=False):
            shutil.rmtree(pfad, ignore_errors=True)
            if os.path.exists(pfad):
                zeile("✗", T("Nicht vollständig entfernt (Datei in Benutzung?): %s") % pfad, Stift.warn)
            else:
                entfernt.append(pfad)
                zeile("✓", T("Entfernt: %s") % pfad, Stift.gut)
    kopf(T("Bleibt bewusst"))
    zeile("·", T("Programme, die der Installer mit deiner Zustimmung installiert hat (Ollama, git, cmake, cliclick, "
                 "xdotool, bubblewrap) — sie gehören dir und werden vielleicht anderswo gebraucht."))
    zeile("·", T("Deine Ollama-Modelle (~/.ollama). Einzeln entfernen: ollama rm <name>"))
    zeile("·", T("Autostart-Einträge legt Dive on Wide keine an; am System wurde nichts weiter verändert."))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(
        description=T("Richtet Dive on Wide ein oder bringt es auf den neuesten Stand."))
    p.add_argument("--ziel", default=ZIEL_VORGABE,
                   help=T("Wohin Dive on Wide soll (Vorgabe: %s)") % ZIEL_VORGABE)
    p.add_argument("--von", default="",
                   help=T("Ein lokales Paket oder eine URL statt der neuesten Freigabe"))
    p.add_argument("--pruefen", action="store_true",
                   help=T("Nur nachsehen, nichts installieren"))
    p.add_argument("--entfernen", action="store_true",
                   help=T("Dive on Wide wieder entfernen (sichert vorher deine Daten, fragt je Teil)"))
    p.add_argument("--alles", action="store_true",
                   help=T("Auch das Optionale anbieten (verteiltes Rechnen, Steuerung)"))
    p.add_argument("--ja", action="store_true",
                   help=T("Alle Fragen mit ja beantworten (für Skripte)"))
    args = p.parse_args(argv)
    try:
        return entfernen(args) if args.entfernen else einrichten(args)
    except KeyboardInterrupt:
        print(T("\n  Abgebrochen. Es wurde nichts weiter verändert."))
        return 130


if __name__ == "__main__":
    ausgabe_absichern()
    sys.exit(main())
