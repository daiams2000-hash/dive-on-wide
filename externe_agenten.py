# -*- coding: utf-8 -*-
"""Externe Agenten — Claude Code und Codex als Unteragenten der Werkbank.

DeepSeek Harness bindet beide als „Profile Bundles“ ein, die ein Hauptagent
beauftragen kann. Dive on Wide macht es genauso, mit den Programmen, die auf dem
Rechner schon installiert und angemeldet sind:

    claude -p "<auftrag>" --output-format json --permission-mode <modus>
    codex exec --sandbox <modus> --skip-git-repo-check -o <datei> "<auftrag>"

Die Rechtestufe der Werkbank wird auf deren eigene Schranken abgebildet — nie
lockerer als bei Dive on Wide selbst:

| Dive on Wide  | Claude Code                         | Codex              |
|---------|-------------------------------------|--------------------|
| lesen   | --permission-mode plan              | --sandbox read-only |
| projekt | --permission-mode acceptEdits       | --sandbox workspace-write |
| voll    | acceptEdits (Befehle weiter gesperrt) | workspace-write   |

Was man wissen muss — die Oberfläche sagt es bei jeder Freigabe:
- Sie laufen **außerhalb der Sandbox von Dive on Wide**, mit ihren eigenen Schranken.
- Sie schicken Aufgabe und Projektinhalte an ihren Anbieter (Anthropic, OpenAI).
- **Standardmäßig aus** (Einstellung WERKBANK_EXTERN), und jeder einzelne Aufruf
  braucht eine Freigabe — auch bei „nicht nachfragen“. Ohne jemanden, der
  zustimmen kann (Rhythmus), laufen sie nie.
"""

import json
import os
import shutil
import subprocess
import tempfile

AGENTEN = {
    "claude": {"programm": "claude", "name": "Claude Code", "anbieter": "Anthropic"},
    "codex": {"programm": "codex", "name": "Codex", "anbieter": "OpenAI"},
}
FRIST = 900


def befehl(agent, programm, auftrag, stufe, ausgabe=None):
    auftrag = "Aufgabe: " + auftrag if auftrag.lstrip().startswith("-") else auftrag     # nie als Schalter lesen
    if agent == "claude":
        modus = "plan" if stufe == "lesen" else "acceptEdits"
        return [programm, "-p", auftrag, "--output-format", "json", "--permission-mode", modus]
    if agent == "codex":
        sandbox = "read-only" if stufe == "lesen" else "workspace-write"
        return [programm, "exec", "--sandbox", sandbox, "--skip-git-repo-check", "-o", ausgabe, auftrag]
    raise ValueError("Unbekannter Agent „%s“." % agent)


class Externe:
    def __init__(self, erlaubt=(), programme=None, frist=FRIST):
        self.erlaubt = tuple(a for a in erlaubt if a in AGENTEN)
        self.programme = dict(programme or {})
        self.frist = frist

    @classmethod
    def aus_einstellung(cls, text, programme=None):
        return cls([t.strip().lower() for t in str(text or "").split(",") if t.strip()], programme)

    def programm(self, agent):
        return self.programme.get(agent) or shutil.which(AGENTEN[agent]["programm"])

    def verfuegbar(self):
        return [a for a in self.erlaubt if self.programm(a)]

    def hinweis(self, agent):
        a = AGENTEN[agent]
        return "%s läuft außerhalb der Dive-on-Wide-Sandbox mit eigenen Schranken und sendet Auftrag und Projektinhalte an %s" % (
            a["name"], a["anbieter"])

    def ausfuehren(self, agent, auftrag, ordner, stufe):
        """(erfolgreich, Bericht)."""
        if agent not in self.verfuegbar():
            return False, "%s ist nicht verfügbar." % agent
        ausgabe = None
        if agent == "codex":
            fd, ausgabe = tempfile.mkstemp(prefix="dowos-codex-", suffix=".txt")
            os.close(fd)
        try:
            p = subprocess.run(befehl(agent, self.programm(agent), auftrag, stufe, ausgabe), cwd=ordner,
                               capture_output=True, text=True, timeout=self.frist, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return False, "Nach %d Minuten abgebrochen." % (self.frist // 60)
        except OSError as e:
            return False, "Konnte %s nicht starten: %s" % (agent, e)
        try:
            if agent == "claude":
                try:
                    d = json.loads(p.stdout)
                    return (p.returncode == 0 and not d.get("is_error")), str(d.get("result") or "")
                except ValueError:
                    return p.returncode == 0, (p.stdout or p.stderr)[-8000:]
            text = ""
            try:
                with open(ausgabe, encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                pass
            return p.returncode == 0, (text or p.stdout or p.stderr)[-8000:]
        finally:
            if ausgabe:
                try:
                    os.remove(ausgabe)
                except OSError:
                    pass
