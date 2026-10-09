# -*- coding: utf-8 -*-
"""Eigene Befehle für den Werkbank-Agenten — wie Slash-Commands in Claude Code
und Prompts in Codex.

Ein Befehl ist eine Markdown-Datei; ihr Name ist der Befehl:

    .dowos/befehle/review-pr.md        →  /review-pr 123
    ---
    description: Prüft einen Pull Request
    argument-hint: <nummer>
    ---
    Prüfe den Pull Request #$1. Achte besonders auf $ARGUMENTS …

Platzhalter: `$ARGUMENTS` (alles hinter dem Befehl), `$1` … `$9` (einzelne,
Anführungszeichen gruppieren). Unterordner werden zu Namensteilen mit „:“
(`frontend/test.md` → `/frontend:test`).

Fundorte, Projekt vor global:
  1. Projekt: .dowos/befehle/, .claude/commands/, .agents/commands/
  2. Dive on Wide: storage/werkbank/befehle/  ·  ~/.claude/commands/  ·  ~/.codex/prompts/
  3. eingebaut: befehle_eingebaut/ (/init, /review) — jeder Fundort davor überschreibt sie

Bewusst NICHT übernommen, obwohl Claude Code es kennt:
- `!befehl` (Shell-Ausgabe beim Einsetzen): Ein fremdes Repository könnte so
  Befehle ausführen lassen, bevor irgendeine Freigabe gefragt wurde.
- `allowed-tools`: Eine Datei im Projekt gibt keine Rechte. Was der Agent
  daraufhin tut, geht durch dieselben Rechte, Regeln und Freigaben.
Ein Befehl ist also reiner Text, wie DOWOS.md.
"""

import os
import re
import shlex

from agentskills import frontmatter

PROJEKT_ORTE = (os.path.join(".dowos", "befehle"), os.path.join(".claude", "commands"),
                os.path.join(".agents", "commands"))
NUTZER_ORTE = (os.path.join("~", ".claude", "commands"), os.path.join("~", ".codex", "prompts"))
EINGEBAUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "befehle_eingebaut")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,63}$")
MAX_BEFEHL = 20_000


def finden(projekt=None, global_ordner=None, nutzer_orte=NUTZER_ORTE, eingebaut=EINGEBAUT):
    """{name: {"name", "beschreibung", "hinweis", "datei", "herkunft"}} — Projekt vor global."""
    orte = [(os.path.join(projekt, o), "Projekt (%s)" % o) for o in PROJEKT_ORTE] if projekt else []
    if global_ordner:
        orte.append((global_ordner, "Dive on Wide"))
    orte += [(os.path.expanduser(o), o) for o in nutzer_orte]
    if eingebaut:
        orte.append((eingebaut, "eingebaut"))
    befehle = {}
    for ort, herkunft in orte:
        if not os.path.isdir(ort):
            continue
        for wurzel, ordner, dateien in os.walk(ort):
            ordner[:] = sorted(o for o in ordner if not o.startswith("."))
            for d in sorted(dateien):
                if not d.endswith(".md") or d.startswith("."):
                    continue
                pfad = os.path.join(wurzel, d)
                name = os.path.relpath(pfad, ort)[:-3].replace(os.sep, ":").lower()
                if not NAME_RE.match(name) or name in befehle:
                    continue
                try:
                    with open(pfad, encoding="utf-8", errors="replace") as f:
                        felder, rumpf = frontmatter(f.read(MAX_BEFEHL))
                except OSError:
                    continue
                if not rumpf.strip():
                    continue
                erste = next((z.strip("# ").strip() for z in rumpf.splitlines() if z.strip()), "")
                befehle[name] = {"name": name, "beschreibung": (felder.get("description") or erste)[:200],
                                 "hinweis": felder.get("argument-hint", "")[:100], "datei": pfad,
                                 "herkunft": herkunft}
    return befehle


def _argumente(text):
    try:
        return shlex.split(text)
    except ValueError:                      # offenes Anführungszeichen: dann eben nach Leerraum
        return text.split()


def einsetzen(vorlage, argumente):
    teile = _argumente(argumente)
    hatte_platzhalter = bool(re.search(r"\$(ARGUMENTS|[1-9])", vorlage))
    text = re.sub(r"\$([1-9])", lambda m: teile[int(m.group(1)) - 1] if int(m.group(1)) <= len(teile) else "", vorlage)
    text = text.replace("$ARGUMENTS", argumente.strip())
    if argumente.strip() and not hatte_platzhalter:
        # Wie Claude Code: Argumente ohne Platzhalter gehen nicht verloren.
        text = text.rstrip() + "\n\nARGUMENTS: " + argumente.strip()
    return text.strip()


def anwenden(aufgabe, befehle):
    """(Aufgabe, Befehlsname oder None). Beginnt die Aufgabe mit /name und gibt es
    den Befehl, wird sie durch den eingesetzten Befehl ersetzt."""
    m = re.match(r"^/([A-Za-z0-9][\w:-]*)(?:\s+(.*))?$", (aufgabe or "").strip(), re.S)
    if not m or m.group(1).lower() not in befehle:
        return aufgabe, None
    b = befehle[m.group(1).lower()]
    with open(b["datei"], encoding="utf-8", errors="replace") as f:
        _, rumpf = frontmatter(f.read(MAX_BEFEHL))
    return einsetzen(rumpf, m.group(2) or ""), b["name"]


def speichern(global_ordner, name, beschreibung, inhalt):
    name = str(name or "").strip().lower().lstrip("/")
    if not NAME_RE.match(name) or ":" in name:
        raise ValueError("Name: Kleinbuchstaben, Ziffern, - und _ (höchstens 64 Zeichen).")
    if not str(inhalt or "").strip():
        raise ValueError("Ein Befehl ohne Text tut nichts.")
    if len(inhalt) > MAX_BEFEHL:
        raise ValueError("Befehl zu lang (höchstens %d Zeichen)." % MAX_BEFEHL)
    os.makedirs(global_ordner, exist_ok=True)
    pfad = os.path.join(global_ordner, name + ".md")
    kopf = "---\ndescription: %s\n---\n\n" % " ".join(str(beschreibung).split()) if str(beschreibung or "").strip() else ""
    with open(pfad + ".tmp", "w", encoding="utf-8") as f:
        f.write(kopf + str(inhalt).strip() + "\n")
    os.replace(pfad + ".tmp", pfad)
    return {"name": name, "datei": pfad}


def loeschen(global_ordner, name):
    """Nur Dive-on-Wide-eigene Befehle; Projekt- und Nutzerdateien fasst Dive on Wide nicht an."""
    name = str(name or "").strip().lower()
    if not NAME_RE.match(name) or ":" in name:
        raise ValueError("Ungültiger Name.")
    pfad = os.path.join(global_ordner, name + ".md")
    if not os.path.isfile(pfad):
        raise LookupError("Diesen Befehl gibt es in Dive on Wide nicht.")
    os.remove(pfad)
    return {"ok": True}
