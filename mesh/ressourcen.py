"""Dive on Wide Mesh — Geräteprofil und Ressourcen-Statthalter.

Der Satz „energetisch kohärent, harmonisch abgestimmt" hat hier eine harte
technische Bedeutung:

    Der Knoten darf das Gerät seines Besitzers niemals beeinträchtigen.

Daraus folgt alles Weitere. Ein fester Prozentsatz („jeder gibt 50 % RAM ab")
ist genau die falsche Antwort: Ob 8 GB frei sind, hängt nicht vom Gerät ab,
sondern davon, was der Besitzer GERADE tut. Deshalb misst dieses Modul
laufend und nimmt sofort zurück, statt einmal zu verteilen.

DER UNTERSCHIED ZWISCHEN BEITRAG UND DIEBSTAHL
----------------------------------------------
ist nicht die Absicht, sondern Sichtbarkeit und Widerrufbarkeit. Deshalb:
kein Beitrag ohne ausdrückliche Zustimmung, jederzeit ablesbar was abgegeben
wird, und ein Halt, der sofort wirkt und niemanden um Erlaubnis fragt.

WAS DIESES MODUL NICHT KANN
---------------------------
Es misst mit Bordmitteln des jeweiligen Systems. Wo eine Größe nicht
ermittelbar ist, steht `None` — nicht ein geschätzter Wert. Ein erfundener
Akkustand wäre schlimmer als gar keiner, weil darauf Entscheidungen fußen.
"""

import os
import platform
import re
import subprocess
import threading
import time

# ---------------------------------------------------------------------------
# Messung — je System mit Bordmitteln, ohne Zusatzpakete
# ---------------------------------------------------------------------------

def _lauf(befehl, frist=4):
    try:
        r = subprocess.run(befehl, capture_output=True, timeout=frist)
        return r.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def _speicher_macos():
    gesamt = _lauf(["sysctl", "-n", "hw.memsize"]).strip()
    gesamt = int(gesamt) if gesamt.isdigit() else None
    frei = None
    aus = _lauf(["vm_stat"])
    if aus:
        m = re.search(r"page size of (\d+) bytes", aus)
        seite = int(m.group(1)) if m else 4096
        werte = dict(re.findall(r"^(.+?):\s+(\d+)\.$", aus, re.M))
        def z(name):
            return int(werte.get(name, 0))
        # „Verfügbar" heißt: frei + das, was das System jederzeit hergeben kann.
        # Aktive Seiten zählen NICHT dazu — die gehören dem Besitzer.
        frei = (z("Pages free") + z("Pages inactive")
                + z("Pages speculative") + z("Pages purgeable")) * seite
    return gesamt, frei


def _speicher_linux():
    try:
        with open("/proc/meminfo") as f:
            werte = dict(re.findall(r"^(\w+):\s+(\d+) kB", f.read(), re.M))
        gesamt = int(werte.get("MemTotal", 0)) * 1024 or None
        # MemAvailable ist genau die Zahl, die der Kernel selbst als
        # „ohne Auslagern verfügbar" ansieht — besser als jede eigene Formel.
        frei = int(werte.get("MemAvailable", 0)) * 1024 or None
        return gesamt, frei
    except Exception:
        return None, None


def _kernel32(kernel32=None):
    if kernel32 is not None:
        return kernel32
    import ctypes
    return ctypes.windll.kernel32


def _speicher_windows(kernel32=None):
    """GlobalMemoryStatusEx — Bordmittel von Windows, kein Zusatzpaket.

    Fehlte bis zum 27.09.2026: Auf dem Windows-PC des Besitzers stand im Mesh
    „0 GB abgegeben“, weil „Speicher nicht messbar“ vorsichtig „nichts“ heisst."""
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    try:
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not _kernel32(kernel32).GlobalMemoryStatusEx(ctypes.byref(st)):
            return None, None
        return (int(st.ullTotalPhys) or None), (int(st.ullAvailPhys) or None)
    except Exception:
        return None, None


def speicher():
    """(gesamt_bytes, verfuegbar_bytes) — None, wo nicht ermittelbar."""
    system = platform.system()
    if system == "Darwin":
        return _speicher_macos()
    if system == "Linux":
        return _speicher_linux()
    if system == "Windows":
        return _speicher_windows()
    return None, None


def _strom_windows(kernel32=None):
    """GetSystemPowerStatus: ('netz'|'akku'|None, Prozent oder None)."""
    import ctypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
    try:
        st = SYSTEM_POWER_STATUS()
        if not _kernel32(kernel32).GetSystemPowerStatus(ctypes.byref(st)):
            return None, None
        prozent = None if st.BatteryLifePercent == 255 else int(st.BatteryLifePercent)
        if st.BatteryFlag & 128:                 # kein Akku: Standgerät
            return "netz", None
        if st.ACLineStatus == 1:
            return "netz", prozent
        if st.ACLineStatus == 0:
            return "akku", prozent
        return None, prozent
    except Exception:
        return None, None


def stromversorgung():
    """('netz'|'akku'|None, akkustand_prozent oder None)."""
    system = platform.system()
    if system == "Darwin":
        aus = _lauf(["pmset", "-g", "batt"])
        if not aus:
            return None, None
        quelle = ("netz" if "AC Power" in aus
                  else "akku" if "Battery Power" in aus else None)
        m = re.search(r"(\d+)%", aus)
        return quelle, (int(m.group(1)) if m else None)
    if system == "Linux":
        basis = "/sys/class/power_supply"
        try:
            for name in sorted(os.listdir(basis)):
                pfad = os.path.join(basis, name)
                if not os.path.exists(os.path.join(pfad, "capacity")):
                    continue
                with open(os.path.join(pfad, "capacity")) as f:
                    stand = int(f.read().strip())
                zustand = ""
                if os.path.exists(os.path.join(pfad, "status")):
                    with open(os.path.join(pfad, "status")) as f:
                        zustand = f.read().strip().lower()
                return ("akku" if zustand == "discharging" else "netz"), stand
        except Exception:
            pass
        return "netz", None            # kein Akku gefunden = Standgerät
    if system == "Windows":
        return _strom_windows()
    return None, None


def waermedrosselung():
    """True, wenn das System gerade wegen Hitze bremst. None = unbekannt.

    Ein gedrosseltes Gerät ist bereits am Limit — noch Last daraufzulegen
    ist genau das Gegenteil von harmonisch."""
    if platform.system() == "Darwin":
        aus = _lauf(["pmset", "-g", "therm"])
        m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", aus)
        if m:
            return int(m.group(1)) < 100
        return None
    if platform.system() == "Linux":
        try:
            for zone in sorted(os.listdir("/sys/class/thermal")):
                p = "/sys/class/thermal/%s/temp" % zone
                if os.path.exists(p):
                    with open(p) as f:
                        if int(f.read().strip()) / 1000.0 > 85:
                            return True
            return False
        except Exception:
            return None
    return None


def geraeteprofil():
    """Was ist das für ein Gerät und wie geht es ihm gerade?"""
    gesamt, frei = speicher()
    quelle, stand = stromversorgung()
    return {
        "system": platform.system(),
        "maschine": platform.machine(),
        "kerne": os.cpu_count(),
        "ram_gesamt": gesamt,
        "ram_verfuegbar": frei,
        "stromquelle": quelle,
        "akkustand": stand,
        "gedrosselt": waermedrosselung(),
        "gemessen_am": time.time(),
    }


# ---------------------------------------------------------------------------
# Der Statthalter
# ---------------------------------------------------------------------------

GB = 1024 ** 3


class Statthalter:
    """Entscheidet laufend, wie viel dieses Gerät gerade abgeben darf.

    Der Knoten fragt ihn vor jeder Zusage und in kurzen Abständen erneut.
    Sinkt der erlaubte Betrag, gibt der Knoten SOFORT zurück — die
    Ersatzknoten der Pipeline übernehmen. Das Gerät des Besitzers hat
    immer Vorrang, ausnahmslos.
    """

    def __init__(self, zustimmung=False, hoechstanteil=0.5,
                 reserve_gb=4.0, akku_mindestens=50, nur_am_netz=True,
                 hoechstens_gb=None):
        # Grundhaltung: aus. Beitrag entsteht durch eine Entscheidung des
        # Besitzers, nicht durch eine Voreinstellung.
        self.zustimmung = bool(zustimmung)
        self.hoechstanteil = float(hoechstanteil)
        self.reserve_gb = float(reserve_gb)     # bleibt dem Besitzer IMMER
        self.akku_mindestens = int(akku_mindestens)
        self.nur_am_netz = bool(nur_am_netz)
        self.hoechstens_gb = hoechstens_gb
        self._sperre = threading.RLock()
        self._zugesagt = 0                      # Bytes, die gerade laufen
        self._rueckruf = None
        self.letzte_begruendung = "noch nicht geprüft"

    # -- Zustimmung --------------------------------------------------------
    def erlauben(self):
        with self._sperre:
            self.zustimmung = True

    def anhalten(self, grund="vom Besitzer angehalten"):
        """Not-Aus. Wirkt sofort und fragt niemanden.

        Das ist die Zusage, die den Unterschied zwischen Beitrag und
        Diebstahl ausmacht — sie muss ohne Netz, ohne Mehrheit und ohne
        Gegenseite funktionieren."""
        with self._sperre:
            self.zustimmung = False
            frei = self._zugesagt
            self._zugesagt = 0
            self.letzte_begruendung = grund
        if frei and self._rueckruf:
            try:
                self._rueckruf(0, grund)
            except Exception:
                pass
        return frei

    def bei_rueckgabe(self, fn):
        """fn(neuer_betrag_bytes, grund) — wird bei jeder Kürzung gerufen."""
        self._rueckruf = fn

    # -- Die eigentliche Entscheidung -------------------------------------
    def abgebbar(self, profil=None):
        """Wie viele Bytes darf dieses Gerät JETZT abgeben? Kann 0 sein."""
        p = profil or geraeteprofil()
        with self._sperre:
            if not self.zustimmung:
                self.letzte_begruendung = ("keine Zustimmung — Beitrag ist "
                                           "standardmäßig aus")
                return 0
            if p["ram_gesamt"] is None or p["ram_verfuegbar"] is None:
                self.letzte_begruendung = ("Speicher nicht messbar auf %s — im "
                                           "Zweifel nichts abgeben" % p["system"])
                return 0
            if p["gedrosselt"]:
                self.letzte_begruendung = ("Gerät drosselt wegen Wärme — es ist "
                                           "schon am Limit")
                return 0
            if p["stromquelle"] == "akku":
                if self.nur_am_netz:
                    self.letzte_begruendung = "läuft auf Akku (nur am Netz erlaubt)"
                    return 0
                if p["akkustand"] is not None and p["akkustand"] < self.akku_mindestens:
                    self.letzte_begruendung = ("Akku bei %d %% unter der Schwelle "
                                               "von %d %%" % (p["akkustand"],
                                                              self.akku_mindestens))
                    return 0
            # Drei Schranken, die kleinste gewinnt:
            #   1. ein Anteil am GESAMTEN Speicher (Obergrenze der Großzügigkeit)
            #   2. das, was gerade frei ist, minus einer Reserve für den Besitzer
            #   3. eine feste Obergrenze, wenn der Besitzer eine gesetzt hat
            nach_anteil = p["ram_gesamt"] * self.hoechstanteil
            nach_frei = p["ram_verfuegbar"] - self.reserve_gb * GB
            erlaubt = min(nach_anteil, nach_frei)
            if self.hoechstens_gb is not None:
                erlaubt = min(erlaubt, self.hoechstens_gb * GB)
            if erlaubt < 0.25 * GB:
                self.letzte_begruendung = ("weniger als 256 MB übrig — zu wenig, "
                                           "um sinnvoll beizutragen")
                return 0
            self.letzte_begruendung = "in Ordnung"
            return int(erlaubt)

    def zusagen(self, bytes_gewuenscht, profil=None):
        """Bindet Speicher für eine Aufgabe. Gibt zu, was vertretbar ist."""
        with self._sperre:
            moeglich = self.abgebbar(profil)
            gewaehrt = max(0, min(int(bytes_gewuenscht), moeglich - self._zugesagt))
            self._zugesagt += gewaehrt
            return gewaehrt

    def freigeben(self, bytes_zurueck):
        with self._sperre:
            self._zugesagt = max(0, self._zugesagt - int(bytes_zurueck))
            return self._zugesagt

    def nachpruefen(self, profil=None):
        """Regelmäßig aufrufen. Kürzt die Zusage, wenn der Besitzer Platz braucht.

        Das ist der Kern der laufenden Rücknahme: Nicht einmal verteilen und
        hoffen, sondern ständig nachsehen und sofort zurückgeben."""
        with self._sperre:
            erlaubt = self.abgebbar(profil)
            if self._zugesagt <= erlaubt:
                return self._zugesagt, None
            vorher = self._zugesagt
            self._zugesagt = erlaubt
            grund = self.letzte_begruendung
        if self._rueckruf:
            try:
                self._rueckruf(erlaubt, grund)
            except Exception:
                pass
        return erlaubt, "gekürzt von %.1f auf %.1f GB — %s" % (
            vorher / GB, erlaubt / GB, grund)

    # -- Für die Anzeige ---------------------------------------------------
    def bericht(self, profil=None):
        """Was gerade passiert — in Worten, die ein Mensch versteht."""
        p = profil or geraeteprofil()
        erlaubt = self.abgebbar(p)
        return {
            "zustimmung": self.zustimmung,
            "abgegeben_gb": round(self._zugesagt / GB, 2),
            "erlaubt_gb": round(erlaubt / GB, 2),
            "ram_gesamt_gb": (round(p["ram_gesamt"] / GB, 1)
                              if p["ram_gesamt"] else None),
            "ram_verfuegbar_gb": (round(p["ram_verfuegbar"] / GB, 1)
                                  if p["ram_verfuegbar"] else None),
            "stromquelle": p["stromquelle"],
            "akkustand": p["akkustand"],
            "gedrosselt": p["gedrosselt"],
            "begruendung": self.letzte_begruendung,
        }


def beitrag_gb_stunden(bytes_abgegeben, sekunden):
    """Die Einheit, in der Beitrag gemessen wird — und auch verbraucht.

    Bewusst GB-Stunden und keine erfundene Punktwährung: Sie ist herleitbar,
    nachprüfbar und dieselbe Größe auf beiden Seiten der Rechnung. Ein Gerät,
    das doppelt so viel doppelt so lange abgibt, hat viermal beigetragen —
    das muss niemand glauben, das kann jeder nachrechnen."""
    return (bytes_abgegeben / GB) * (sekunden / 3600.0)
