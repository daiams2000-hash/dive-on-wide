# -*- coding: utf-8 -*-
"""Skills im offenen SKILL.md-Format — dasselbe wie bei Claude Code, Codex und Hermes.

Ein Skill ist ein Ordner mit einer `SKILL.md`:

    ---
    name: release-notes
    description: Schreibt Release-Notes aus der Git-Historie seit dem letzten Tag.
    ---
    1. `git describe --tags --abbrev=0` …

Der Agent sieht zunächst nur Name und Beschreibung aller Skills. Passt einer,
lädt er ihn mit dem Werkzeug `skill` — erst dann kommt der Inhalt in den
Kontext (progressive disclosure). Weitere Dateien im Skill-Ordner lassen sich
genauso nachladen.

Fundorte, später gefundene überschreiben nichts:
  1. Projekt: .dowos/skills/, .agents/skills/, .claude/skills/
  2. Dive on Wide: storage/werkbank/skills/ (in der Oberfläche anlegen und bearbeiten)

Ein Projekt-Skill ist Text wie DOWOS.md: Er kann nichts selbst ausführen. Was
der Agent daraufhin tut, geht durch dieselben Rechte, Regeln und Freigaben.
"""

import os
import re

PROJEKT_ORTE = (os.path.join(".dowos", "skills"), os.path.join(".agents", "skills"), os.path.join(".claude", "skills"))
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_SKILL = 20_000
MAX_DATEI = 20_000


def frontmatter(text):
    """(Felder, Rumpf) einer SKILL.md. Einfache YAML-Teilmenge: schluessel: wert."""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    felder = {}
    for zeile in m.group(1).splitlines():
        if ":" in zeile and not zeile.startswith((" ", "\t", "#")):
            k, v = zeile.split(":", 1)
            felder[k.strip()] = v.strip().strip('"').strip("'")
    return felder, m.group(2)


def _lesen(pfad, grenze):
    with open(pfad, encoding="utf-8", errors="replace") as f:
        return f.read(grenze + 1)


def finden(projekt=None, global_ordner=None):
    """{name: {"name", "beschreibung", "ordner", "herkunft"}} — Projekt vor global."""
    skills = {}
    orte = []
    if projekt:
        orte += [(os.path.join(projekt, o), "Projekt (%s)" % o) for o in PROJEKT_ORTE]
    if global_ordner:
        orte.append((global_ordner, "Dive on Wide"))
    for ort, herkunft in orte:
        if not os.path.isdir(ort):
            continue
        for eintrag in sorted(os.listdir(ort)):
            ordner = os.path.join(ort, eintrag)
            datei = os.path.join(ordner, "SKILL.md")
            if not os.path.isfile(datei):
                continue
            try:
                felder, _ = frontmatter(_lesen(datei, MAX_SKILL))
            except OSError:
                continue
            name = (felder.get("name") or eintrag).strip().lower()
            beschreibung = felder.get("description", "").strip()
            if not NAME_RE.match(name) or not beschreibung or name in skills:
                continue
            skills[name] = {"name": name, "beschreibung": beschreibung[:500], "ordner": os.path.realpath(ordner),
                            "herkunft": herkunft}
    return skills


def laden(skills, name, datei=None):
    """Inhalt eines Skills (oder einer Datei darin) für das Werkzeug `skill`."""
    s = skills.get(str(name or "").strip().lower())
    if not s:
        raise ValueError("Skill „%s“ gibt es nicht. Verfügbar: %s" % (name, ", ".join(sorted(skills)) or "keine"))
    if datei:
        voll = os.path.realpath(os.path.join(s["ordner"], datei))
        if not voll.startswith(s["ordner"] + os.sep) or not os.path.isfile(voll):
            raise ValueError("Datei „%s“ gibt es im Skill „%s“ nicht." % (datei, s["name"]))
        text = _lesen(voll, MAX_DATEI)
        return text[:MAX_DATEI] + ("\n… (gekürzt)" if len(text) > MAX_DATEI else "")
    _, rumpf = frontmatter(_lesen(os.path.join(s["ordner"], "SKILL.md"), MAX_SKILL))
    weitere = sorted(os.path.relpath(os.path.join(w, d), s["ordner"])
                     for w, _, ds in os.walk(s["ordner"]) for d in ds if d != "SKILL.md")[:40]
    return "Skill „%s“:\n%s%s" % (s["name"], rumpf.strip()[:MAX_SKILL],
                                  ("\n\nWeitere Dateien (mit skill {\"name\":…, \"datei\":…} laden): " + ", ".join(weitere))
                                  if weitere else "")


def speichern(global_ordner, name, beschreibung, inhalt):
    """Legt einen Dive-on-Wide-Skill an oder überschreibt ihn."""
    name = str(name or "").strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("Name: nur Kleinbuchstaben, Ziffern und Bindestriche (höchstens 64 Zeichen).")
    beschreibung = " ".join(str(beschreibung or "").split())
    if not beschreibung:
        raise ValueError("Ohne Beschreibung erkennt der Agent nie, wann der Skill passt.")
    if len(inhalt or "") > MAX_SKILL:
        raise ValueError("Skill zu lang (höchstens %d Zeichen)." % MAX_SKILL)
    ordner = os.path.join(global_ordner, name)
    os.makedirs(ordner, exist_ok=True)
    tmp = os.path.join(ordner, "SKILL.md.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("---\nname: %s\ndescription: %s\n---\n\n%s\n" % (name, beschreibung.replace("\n", " "),
                                                               str(inhalt or "").strip()))
    os.replace(tmp, os.path.join(ordner, "SKILL.md"))
    return {"name": name, "ordner": ordner}


def beschreibung_fuer_prompt(skills, grenze=40):
    zeilen = ["- %s: %s" % (s["name"], s["beschreibung"]) for s in list(skills.values())[:grenze]]
    if len(skills) > grenze:
        zeilen.append("- … %d weitere" % (len(skills) - grenze))
    return "\n".join(zeilen)


ENTWURF_PROMPT = """Du machst aus einem gelungenen Agentenlauf einen wiederverwendbaren Skill im SKILL.md-Format.
Der Skill soll beim nächsten ähnlichen Auftrag helfen: Vorgehen, Befehle, Fallstricke — nicht die konkrete Lösung dieses einen Falls.
Antworte GENAU in diesem Format:
NAME: kleinbuchstaben-mit-bindestrichen
BESCHREIBUNG: ein Satz, wann der Skill passt
---
<Anleitung in Markdown, nummerierte Schritte, höchstens 40 Zeilen>"""


def entwurf_lesen(text):
    m_name = re.search(r"^NAME:\s*([a-z0-9-]+)\s*$", text or "", re.M)
    m_beschr = re.search(r"^BESCHREIBUNG:\s*(.+)$", text or "", re.M)
    teile = re.split(r"^---\s*$", text or "", maxsplit=1, flags=re.M)
    if not (m_name and m_beschr and len(teile) == 2 and teile[1].strip()):
        raise ValueError("Der Entwurf des Modells hat nicht das erwartete Format.")
    return {"name": m_name.group(1)[:64], "beschreibung": m_beschr.group(1).strip()[:500], "inhalt": teile[1].strip()}
