# -*- coding: utf-8 -*-
"""Kleine Eigenheiten der Betriebssysteme, an einer Stelle ausgeglichen.

Gefunden in der Windows-VM am 29.09.2026 (Windows 11 25H2, ARM64, deutsch):

- Umgeleitete Ausgabe ist dort Windows-1252. Ein ╔ im Startbanner beendete
  den Server, sobald seine Ausgabe in eine Datei oder einen Dienst ging.
- Eine Verbindung zu einem geschlossenen Port auf dem eigenen Rechner scheitert
  unter Windows nicht sofort, sondern nach 2 Sekunden. „localhost“ fragt erst
  IPv6 (::1), dann IPv4 — Ollama hört standardmäßig nur auf 127.0.0.1. Jede
  Modellanfrage wartete so 2 Sekunden umsonst; /api/health brauchte 8 s.

install.py hat eine eigene Kopie von ausgabe_absichern: Es wird auch einzeln
heruntergeladen und muss ohne dieses Modul laufen.
"""
import os
import socket
import sys


def ausgabe_absichern():
    """Nicht darstellbare Zeichen ersetzen statt abstürzen; in Dateien und Pipes UTF-8."""
    for strom in (sys.stdout, sys.stderr):
        try:
            if strom.isatty():
                strom.reconfigure(errors="replace")
            else:
                strom.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


_getaddrinfo = socket.getaddrinfo


def _ipv4_zuerst(host, *args, **kwargs):
    ergebnis = _getaddrinfo(host, *args, **kwargs)
    if isinstance(host, str) and host.lower() == "localhost":
        ergebnis = sorted(ergebnis, key=lambda e: e[0] != socket.AF_INET)
    return ergebnis


def localhost_ipv4_zuerst(immer=False):
    """Windows: „localhost“ zuerst als 127.0.0.1 versuchen. IPv6 bleibt als zweiter Weg."""
    if (os.name == "nt" or immer) and socket.getaddrinfo is not _ipv4_zuerst:
        socket.getaddrinfo = _ipv4_zuerst


def konsole_deutsch(umgebung=None, windows_sprache=None):
    """Spricht das System Deutsch? Für Texte im Terminal (Banner, Startfehler).

    DOWOS_SPRACHE=de/en entscheidet ausdrücklich; sonst LC_ALL, LC_MESSAGES, LANG
    (POSIX) bzw. die Anzeigesprache von Windows. Alles andere bekommt Englisch.
    """
    u = os.environ if umgebung is None else umgebung
    wahl = (u.get("DOWOS_SPRACHE") or "").strip().lower()
    if wahl in ("de", "en"):
        return wahl == "de"
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        wert = (u.get(name) or "").strip()
        if wert:
            return wert.lower().startswith("de")
    if windows_sprache is None and os.name == "nt":
        try:
            import ctypes
            windows_sprache = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        except (AttributeError, OSError):
            windows_sprache = 0
    return bool(windows_sprache) and (windows_sprache & 0x3FF) == 0x07  # LANG_GERMAN
