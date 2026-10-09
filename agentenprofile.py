# -*- coding: utf-8 -*-
"""Agentenprofile — ein Zuschnitt des Werkbank-Agenten für eine Art von Aufgabe.

Vorbilder: die Subagenten von Claude Code (`.claude/agents/*.md`) und die
Presets bzw. der Creator-Modus von DeepSeek Harness. Ein Profil legt fest,
welche Werkzeuge der Agent hat, mit welchen Rechten er höchstens arbeitet,
welches Modell und welche zusätzlichen Anweisungen er bekommt:

    ---
    name: reviewer
    description: Prüft Änderungen auf echte Fehler, ändert nichts
    werkzeuge: liste, lesen, suchen, ausfuehren      (oder im Claude-Format: tools: Read, Grep, Bash)
    stufe: lesen
    modell: ollama@@qwen3.6-35b-a3b-text:ud-q3kxl    (leer oder „inherit“: das eingestellte)
    max_schritte: 20
    ---
    Du prüfst Code wie ein erfahrener Kollege …

Ein Profil lässt sich für einen ganzen Auftrag wählen oder vom Agenten selbst
für Unteragenten (`delegieren {"profil": "reviewer", …}`).

**Ein Profil gibt nie mehr Rechte.** Rechtestufe und Freigabe wirken als
Obergrenze: Es gilt die strengere Einstellung von Auftrag und Profil. Ein Profil
aus einem fremden Repository kann den Agenten also nur einschränken.

Fundorte (frühere gewinnen): Projekt `.dowos/agenten/`, `.claude/agents/` ·
Dive on Wide `storage/werkbank/agenten/` · `~/.claude/agents/` · eingebaut `agenten_eingebaut/`.
"""

import os
import re

from agentskills import frontmatter

PROJEKT_ORTE = (os.path.join(".dowos", "agenten"), os.path.join(".claude", "agents"))
NUTZER_ORTE = (os.path.join("~", ".claude", "agents"),)
EINGEBAUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agenten_eingebaut")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_PROFIL = 20_000

STUFEN = ("lesen", "projekt", "voll")
FREIGABEN = ("nie", "befehle", "alles")
DOWOS_WERKZEUGE = ("liste", "lesen", "suchen", "ersetzen", "schreiben", "ausfuehren", "plan", "delegieren", "skill",
                   "merken", "erinnern", "websuche", "webseite", "mcp", "extern")
# Werkzeugnamen von Claude Code → Dive on Wide
CLAUDE_WERKZEUGE = {"read": ("lesen",), "grep": ("suchen",), "glob": ("liste", "suchen"), "ls": ("liste",),
                    "edit": ("ersetzen",), "multiedit": ("ersetzen",), "write": ("schreiben",),
                    "notebookedit": ("ersetzen",), "bash": ("ausfuehren",), "websearch": ("websuche",),
                    "webfetch": ("webseite",), "task": ("delegieren",), "todowrite": ("plan",)}


def werkzeuge_lesen(text):
    """„lesen, suchen“ oder „Read, Grep, Bash(npm test:*)“ → Tupel von Dive-on-Wide-Werkzeugen, unbekannte fallen weg."""
    aus = []
    for teil in re.split(r"[,\s]+", re.sub(r"\([^)]*\)", "", str(text or ""))):
        t = teil.strip().lower()
        if not t:
            continue
        if t.startswith("mcp__"):
            t = "mcp"
        for w in (t,) if t in DOWOS_WERKZEUGE else CLAUDE_WERKZEUGE.get(t, ()):
            if w not in aus:
                aus.append(w)
    return tuple(aus)


def _profil(datei, herkunft, name_vorgabe):
    with open(datei, encoding="utf-8", errors="replace") as f:
        felder, rumpf = frontmatter(f.read(MAX_PROFIL))
    name = (felder.get("name") or name_vorgabe).strip().lower()
    beschreibung = (felder.get("description") or felder.get("beschreibung") or "").strip()
    if not NAME_RE.match(name) or not beschreibung:
        return None
    roh_werkzeuge = felder.get("werkzeuge") or felder.get("tools") or ""
    modell = (felder.get("modell") or felder.get("model") or "").strip()
    if modell.lower() in ("inherit", "sonnet", "opus", "haiku"):       # Claude-Namen gibt es hier nicht
        modell = ""
    try:
        max_schritte = max(1, min(int(felder["max_schritte"]), 200)) if felder.get("max_schritte") else None
    except ValueError:
        max_schritte = None
    return {"name": name, "beschreibung": beschreibung[:500], "herkunft": herkunft, "datei": datei,
            "werkzeuge": werkzeuge_lesen(roh_werkzeuge) if roh_werkzeuge.strip() else None,
            "stufe": felder.get("stufe") if felder.get("stufe") in STUFEN else None,
            "freigabe": felder.get("freigabe") if felder.get("freigabe") in FREIGABEN else None,
            "modell": modell, "max_schritte": max_schritte,
            "planmodus": str(felder.get("planmodus", "")).lower() in ("ja", "true", "1"),
            "zusatz": rumpf.strip()[:MAX_PROFIL]}


def finden(projekt=None, global_ordner=None, nutzer_orte=NUTZER_ORTE, eingebaut=EINGEBAUT):
    orte = [(os.path.join(projekt, o), "Projekt (%s)" % o) for o in PROJEKT_ORTE] if projekt else []
    if global_ordner:
        orte.append((global_ordner, "Dive on Wide"))
    orte += [(os.path.expanduser(o), o) for o in nutzer_orte]
    if eingebaut:
        orte.append((eingebaut, "eingebaut"))
    profile = {}
    for ort, herkunft in orte:
        if not os.path.isdir(ort):
            continue
        for d in sorted(os.listdir(ort)):
            if not d.endswith(".md") or d.startswith("."):
                continue
            try:
                p = _profil(os.path.join(ort, d), herkunft, d[:-3])
            except OSError:
                continue
            if p and p["name"] not in profile:
                profile[p["name"]] = p
    return profile


def strenger(a, b, reihenfolge):
    """Die strengere zweier Einstellungen; None zählt nicht."""
    werte = [x for x in (a, b) if x in reihenfolge]
    if not werte:
        return None
    return min(werte, key=reihenfolge.index) if reihenfolge is STUFEN else max(werte, key=reihenfolge.index)


def anwenden(profil, stufe, freigabe):
    """(stufe, freigabe) nach dem Profil — nie lockerer als eingestellt."""
    if not profil:
        return stufe, freigabe
    return strenger(stufe, profil.get("stufe"), STUFEN) or stufe, strenger(freigabe, profil.get("freigabe"), FREIGABEN) or freigabe


def speichern(global_ordner, daten):
    name = str(daten.get("name") or "").strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("Name: Kleinbuchstaben, Ziffern, - und _ (höchstens 64 Zeichen).")
    beschreibung = " ".join(str(daten.get("beschreibung") or "").split())
    if not beschreibung:
        raise ValueError("Ohne Beschreibung weiß niemand, wofür das Profil gedacht ist.")
    zeilen = ["---", "name: " + name, "description: " + beschreibung]
    werkzeuge = werkzeuge_lesen(daten.get("werkzeuge") if isinstance(daten.get("werkzeuge"), str)
                                else ", ".join(daten.get("werkzeuge") or []))
    if werkzeuge:
        zeilen.append("werkzeuge: " + ", ".join(werkzeuge))
    for k, erlaubt in (("stufe", STUFEN), ("freigabe", FREIGABEN)):
        if daten.get(k):
            if daten[k] not in erlaubt:
                raise ValueError("%s: %s" % (k, " | ".join(erlaubt)))
            zeilen.append("%s: %s" % (k, daten[k]))
    if str(daten.get("modell") or "").strip():
        zeilen.append("modell: " + str(daten["modell"]).strip().replace("\n", " "))
    if daten.get("max_schritte"):
        zeilen.append("max_schritte: %d" % max(1, min(int(daten["max_schritte"]), 200)))
    if daten.get("planmodus"):
        zeilen.append("planmodus: ja")
    inhalt = str(daten.get("zusatz") or "").strip()
    if len(inhalt) > MAX_PROFIL:
        raise ValueError("Anweisungen zu lang (höchstens %d Zeichen)." % MAX_PROFIL)
    os.makedirs(global_ordner, exist_ok=True)
    datei = os.path.join(global_ordner, name + ".md")
    with open(datei + ".tmp", "w", encoding="utf-8") as f:
        f.write("\n".join(zeilen) + "\n---\n\n" + inhalt + "\n")
    os.replace(datei + ".tmp", datei)
    return {"name": name, "datei": datei}


def loeschen(global_ordner, name):
    name = str(name or "").strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("Ungültiger Name.")
    datei = os.path.join(global_ordner, name + ".md")
    if not os.path.isfile(datei):
        raise LookupError("Dieses Profil gibt es in Dive on Wide nicht.")
    os.remove(datei)
    return {"ok": True}
