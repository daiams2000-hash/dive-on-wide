# -*- coding: utf-8 -*-
"""Was dieser Rechner fuer Sprachmodelle hergibt — und welche dazu passen.

    scan()                 CPU, Arbeitsspeicher, Grafikkarte(n) samt VRAM, freier Platz
    stufe(bytes, hw)       passt / knapp / zu_gross — je nachdem, WO das Modell rechnet
    empfehlungen(hw, …)    je Rolle das beste passende Modell aus modellempfehlungen.json

Reine Standardbibliothek. Jede Erkennung ist ein kleiner Zerleger, der mit
aufgezeichneten Ausgaben getestet wird — auf einem Mac laesst sich weder
nvidia-smi noch Windows ausprobieren, und eine falsche Empfehlung fuehrt einen
neuen Nutzer direkt in einen Speicherfehler.
"""
import json
import os
import platform
import re
import shutil
import subprocess

APP = os.path.dirname(os.path.abspath(__file__))
KATALOG = os.path.join(APP, "modellempfehlungen.json")
GIB = 1024 ** 3


def _lauf(befehl, frist=8):
    try:
        r = subprocess.run(befehl, capture_output=True, timeout=frist)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


# ------------------------------------------------------------------ Zerleger
def nvidia_zerlegen(text):
    """`nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits` → [{name, vram_gib}]"""
    aus = []
    for zeile in (text or "").splitlines():
        teile = [t.strip() for t in zeile.split(",")]
        if len(teile) >= 2 and re.match(r"^\d+(\.\d+)?$", teile[-1]):
            aus.append({"name": ",".join(teile[:-1]), "vram_gib": round(float(teile[-1]) / 1024, 1), "art": "nvidia"})
    return aus


def cpuinfo_zerlegen(text):
    """/proc/cpuinfo → Name der CPU (x86: model name, ARM: Hardware/Model)."""
    for schluessel in ("model name", "Model", "Hardware", "cpu model"):
        m = re.search(r"^%s\s*:\s*(.+)$" % re.escape(schluessel), text or "", re.M)
        if m:
            return m.group(1).strip()
    return ""


def mac_grafik_zerlegen(text):
    """`system_profiler SPDisplaysDataType -json` → [{name, kerne}] (Apple: gemeinsamer Speicher)."""
    try:
        eintraege = json.loads(text or "{}").get("SPDisplaysDataType") or []
    except ValueError:
        return []
    aus = []
    for e in eintraege:
        name = e.get("sppci_model") or e.get("_name") or ""
        kerne = e.get("sppci_cores")
        vram = e.get("spdisplays_vram") or e.get("spdisplays_vram_shared") or ""
        g = {"name": name, "art": "apple" if "Apple" in name else "andere", "kerne": int(kerne) if str(kerne).isdigit() else None}
        m = re.match(r"(\d+)\s*(GB|MB)", str(vram))
        if m and g["art"] != "apple":
            g["vram_gib"] = round(int(m.group(1)) / (1 if m.group(2) == "GB" else 1024), 1)
        aus.append(g)
    return aus


def windows_grafik_zerlegen(text):
    """PowerShell `Get-CimInstance Win32_VideoController | Select Name,AdapterRAM | ConvertTo-Json`.

    AdapterRAM ist ein 32-Bit-Feld: Ueber 4 GB zeigt Windows dort Unsinn. Fuer
    Nvidia fragt scan() deshalb zuerst nvidia-smi; hier bleibt nur der Name
    und ein VRAM-Wert, wenn er glaubwuerdig ist."""
    try:
        d = json.loads(text or "[]")
    except ValueError:
        return []
    aus = []
    for e in (d if isinstance(d, list) else [d]):
        name = str(e.get("Name") or "")
        ram = e.get("AdapterRAM") or 0
        g = {"name": name, "art": "nvidia" if "NVIDIA" in name.upper() else "amd" if ("AMD" in name.upper() or "RADEON" in name.upper())
             else "intel" if "INTEL" in name.upper() else "andere"}
        if isinstance(ram, (int, float)) and 512 * 1024 ** 2 <= ram < 3.9 * GIB:   # 0xFFF00000 = uebergelaufen
            g["vram_gib"] = round(ram / GIB, 1)
        aus.append(g)
    return aus


def amd_linux_vram():
    """Linux/AMD: /sys/class/drm/card*/device/mem_info_vram_total (Bytes)."""
    aus = []
    for karte in sorted(os.listdir("/sys/class/drm")) if os.path.isdir("/sys/class/drm") else []:
        p = os.path.join("/sys/class/drm", karte, "device", "mem_info_vram_total")
        if re.match(r"^card\d+$", karte) and os.path.isfile(p):
            try:
                aus.append({"name": "AMD-Grafik (%s)" % karte, "vram_gib": round(int(open(p).read().strip()) / GIB, 1), "art": "amd"})
            except (OSError, ValueError):
                pass
    return aus


def _windows_cpu_name():
    """Der Name, den Windows selbst anzeigt („Intel(R) Core(TM) i7-12700H“), aus der
    Registry. platform.processor() liefert nur „Intel64 Family 6 Model 154 …“ —
    in der VM stand da „ARMv8 (64-bit) Family 8 Model 0“ (29.09.2026)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return " ".join(str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).split())
    except (ImportError, OSError):
        return ""


def _ram_bordmittel():
    """Arbeitsspeicher ohne das mesh-Paket: POSIX über sysconf, Windows über
    GlobalMemoryStatusEx — os.sysconf gibt es dort nicht (Windows-VM, 29.09.2026)."""
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GIB, 1)
    except (ValueError, OSError, AttributeError):
        pass
    if os.name == "nt":
        try:
            import ctypes

            class Lage(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            lage = Lage()
            lage.dwLength = ctypes.sizeof(Lage)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(lage)):
                return round(lage.ullTotalPhys / GIB, 1)
        except (OSError, AttributeError, ValueError):
            pass
    return None


# --------------------------------------------------------------------- Scan
def scan(ollama_ordner=None):
    system, arch = platform.system(), platform.machine()
    hw = {"system": system, "arch": arch, "cpu": {"name": "", "kerne": os.cpu_count()}, "ram_gib": None,
          "gpus": [], "unified": False, "platte_frei_gib": None, "hinweise": []}
    try:
        from mesh import ressourcen
        gesamt, _ = ressourcen.speicher()
        hw["ram_gib"] = round(gesamt / GIB, 1) if gesamt else None
    except Exception:
        pass
    if not hw["ram_gib"]:
        hw["ram_gib"] = _ram_bordmittel()
    if system == "Darwin":
        hw["cpu"]["name"] = _lauf(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        hw["gpus"] = mac_grafik_zerlegen(_lauf(["system_profiler", "SPDisplaysDataType", "-json"], frist=15))
        hw["unified"] = arch == "arm64"
    else:
        if system == "Linux":
            try:
                hw["cpu"]["name"] = cpuinfo_zerlegen(open("/proc/cpuinfo").read())
            except OSError:
                pass
        elif system == "Windows":
            hw["cpu"]["name"] = _windows_cpu_name() or platform.processor()
        if shutil.which("nvidia-smi"):
            hw["gpus"] = nvidia_zerlegen(_lauf(["nvidia-smi", "--query-gpu=name,memory.total",
                                                "--format=csv,noheader,nounits"]))
        if not hw["gpus"] and system == "Linux":
            hw["gpus"] = amd_linux_vram()
        if not hw["gpus"] and system == "Windows":
            hw["gpus"] = windows_grafik_zerlegen(_lauf(["powershell", "-NoProfile", "-Command",
                                                        "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json"]))
    ort = ollama_ordner or os.path.expanduser("~")
    try:
        hw["platte_frei_gib"] = round(shutil.disk_usage(ort).free / GIB, 1)
    except OSError:
        pass
    hw["rechnet_auf"], hw["modell_speicher_gib"] = _modell_speicher(hw)
    if hw["rechnet_auf"] == "CPU":
        hw["hinweise"].append("Keine nutzbare Grafikkarte erkannt — Modelle rechnen auf der CPU. Das geht, "
                              "ist aber deutlich langsamer; kleine Modelle (bis ~8 B) sind hier die richtige Wahl.")
    if hw["platte_frei_gib"] is not None and hw["platte_frei_gib"] < 20:
        hw["hinweise"].append("Nur %.0f GB frei auf der Platte — ein mittleres Modell braucht 5–20 GB." % hw["platte_frei_gib"])
    if hw["modell_speicher_gib"] and hw["modell_speicher_gib"] < 8:
        hw["hinweise"].append(KLEIN_HINWEIS)
    return hw


# Kleine Hardware (8-GB-VM, nur CPU, 05./06.10.2026): Ollamas Modellprozess wuchs über viele Agentenschritte auf
# 7,6 GB — sein Prompt-Cache darf standardmäßig bis 8 GB belegen — und Linux beendete ihn. Kleinerer Kontext half
# nicht; mit abgeschaltetem Cache lief die Roadmap durch (4,7 GB, kein Abbruch).
KLEIN_HINWEIS = ("Wenig Speicher für Modelle: Für lange Werkbank-Läufe Ollamas Prompt-Cache abschalten "
                 "(Umgebungsvariable LLAMA_ARG_CACHE_RAM=0, dann Ollama neu starten) — sonst wächst der Modellprozess, "
                 "bis das System ihn beendet. Wie das geht, sagt der Lotse (🧭) und docs/FAQ.md.")


def empfohlener_kontext(hw):
    """NUM_CTX passend zum Rechner: 16 384 nur, wo genug Speicher für Modell und Kontext da ist."""
    return 16384 if (hw.get("modell_speicher_gib") or 0) >= 12 else 8192


def _vram(hw):
    return max((g.get("vram_gib") or 0) for g in hw["gpus"]) if hw["gpus"] else 0


def _modell_speicher(hw):
    """(wo, GiB) — wie viel Speicher ein Modell hier realistisch bekommt."""
    ram = hw.get("ram_gib") or 0
    if hw.get("unified"):
        return "Apple-GPU (gemeinsamer Speicher)", round(ram * 0.75, 1)     # Metal-Grenze, geeicht am 27B/35B
    vram = _vram(hw)
    if vram >= 4:
        art = next((g["art"] for g in hw["gpus"] if (g.get("vram_gib") or 0) == vram), "gpu")
        return "%s-Grafikkarte (%.0f GB VRAM)" % ({"nvidia": "Nvidia", "amd": "AMD"}.get(art, "Grafik"), vram), vram
    return "CPU", round(ram / 2, 1)


def stufe(groesse_bytes, hw):
    """passt / knapp / zu_gross — und dieselben Schwellen wie server.speicher_stufe auf dem Mac."""
    if not groesse_bytes:
        return None
    gib = groesse_bytes / GIB
    ram = hw.get("ram_gib") or 0
    if hw.get("unified"):
        if gib * 1.2 <= ram / 2:
            return "passt"
        return "knapp" if gib * 1.1 <= ram * 0.75 else "zu_gross"
    vram = _vram(hw)
    if vram >= 4:
        if gib * 1.2 <= vram:
            return "passt"
        # Was nicht in den VRAM passt, rechnet teilweise auf der CPU — geht, aber langsamer.
        return "knapp" if gib * 1.1 <= vram + ram * 0.5 else "zu_gross"
    if not ram:
        return None
    if gib * 1.2 <= ram / 2:
        return "passt"
    # Ohne Grafikkarte teilt sich das Modell den Speicher mit allem anderen und
    # rechnet langsam — „knapp“ ist hier enger gefasst als auf dem Mac.
    return "knapp" if gib * 1.1 <= ram * 0.6 else "zu_gross"


# ----------------------------------------------------------- Empfehlungen
ROLLEN = {
    "werkbank": "Code & Werkbank — der Agent, der in Projekten arbeitet",
    "allround": "Allrounder — Chat, Texte, Recherche",
    "bild": "Sieht Bilder — Screenshots, Fotos, Computer-Use",
    "klein": "Klein & schnell — Orchestrator, schwache Geräte, Nebenbei",
}


def katalog_laden(pfad=KATALOG):
    with open(pfad, encoding="utf-8") as f:
        return json.load(f)["modelle"]


def _kern(name):
    """Modell und Quantisierung ohne Herkunft und Zusätze: „hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q3_K_XL“ und
    eine selbst benannte Kopie „qwen3.6-35b-a3b-text:ud-q3kxl“ sind dasselbe Modell (05.10.2026: der Lotse hielt
    das installierte 35B für „nicht installiert“)."""
    n = name.lower().split("@@")[-1].split("/")[-1]
    basis, _, quant = n.partition(":")
    basis = re.sub(r"-(gguf|text|instruct|it|chat)\b", "", basis)
    quant = re.sub(r"[^a-z0-9]", "", quant)
    return basis, quant


GROESSE_RE = re.compile(r"^(e?\d+(\.\d+)?b|a\d+(\.\d+)?b)$")


def _familie_groesse(name):
    """„qwen3.5:4b“ und „qwen3.5-4b:q8“ → („qwen3.5“, {„4b“}): Familie und Größe, egal ob die Größe im Namen oder
    im Tag steht. 09.10.2026: Der Lotse nannte das installierte qwen3.5-4b:q8 „nicht installiert“, weil der Katalog
    es als qwen3.5:4b führt. Quantisierung und Zusätze (text, instruct, latest …) zählen nicht."""
    n = name.lower().split("@@")[-1].split("/")[-1]
    teile = re.split(r"[:\-_]", n)
    groessen = {x for x in teile if GROESSE_RE.match(x)}
    familie = teile[0]
    return familie, frozenset(groessen)


def _installiert(tag, installiert):
    t = tag.lower()
    if any(n.lower().split("@@")[-1] in (t, t + ":latest") for n in installiert or ()):
        return True
    basis, quant = _kern(tag)
    if quant and any(_kern(n) == (basis, quant) for n in installiert or ()):
        return True
    # Offizieller Tag ohne eigene Quantisierung (qwen3.5:4b): jede installierte Fassung derselben Familie und Größe zählt
    familie, groessen = _familie_groesse(tag)
    if groessen and not t.startswith("hf.co/"):
        return any(_familie_groesse(n) == (familie, groessen) for n in installiert or ())
    return False


def empfehlungen(hw, katalog=None, installiert=()):
    """{rolle: {"beste": eintrag|None, "weitere": [..], "text": …}} — Reihenfolge im Katalog = Vorrang."""
    katalog = katalog if katalog is not None else katalog_laden()
    aus = {}
    for rolle, text in ROLLEN.items():
        kandidaten = []
        for m in katalog:
            if rolle not in m.get("rollen", []):
                continue
            s = stufe(int(m["groesse_gb"] * 1e9), hw)
            if s in ("passt", "knapp"):
                kandidaten.append(dict(m, stufe=s, installiert=_installiert(m["tag"], installiert)))
        # Das beste Modell zuerst (Katalog-Reihenfolge), auch wenn es nur knapp passt —
        # auf dem Entwicklungs-Mac war genau so eines das gemessen beste. Ist es knapp,
        # steht das beste BEQUEM passende gleich daneben.
        beste = kandidaten[0] if kandidaten else None
        weitere = kandidaten[1:3]
        if beste and beste["stufe"] == "knapp":
            bequem = next((k for k in kandidaten if k["stufe"] == "passt"), None)
            if bequem and bequem not in weitere:
                weitere = [bequem] + weitere[:1]
        # Zum Vergleich: alles, was für diese Rolle wirklich gemessen wurde — auch Modelle, die hier nicht passen.
        # So sieht man, WARUM die Empfehlung so ausfällt (z. B. Qwen 3.8 27B: gleich gut, aber 4× langsamer).
        vergleich = [{"name": m["name"], "gemessen": m["gemessen"]} for m in katalog
                     if rolle in m.get("rollen", []) and m.get("gemessen") and (not beste or m["tag"] != beste["tag"])]
        aus[rolle] = {"text": text, "beste": beste, "weitere": weitere, "vergleich": vergleich}
    return aus
