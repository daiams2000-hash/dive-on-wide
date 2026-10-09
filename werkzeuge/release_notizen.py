#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release-Notizen für GitHub, Discord und X — aus CHANGELOG.md, nicht aus dem Gedächtnis.

    python3 werkzeuge/release_notizen.py 0.5.0              # schreibt ausgabe/release-0.5.0/
    python3 werkzeuge/release_notizen.py 0.5.0 --pruefen    # nur prüfen, ob es einen Abschnitt gibt

Erzeugt drei Texte:
  github.md   — der Abschnitt der Version aus CHANGELOG.md (Release-Seite auf GitHub)
  discord.txt — Kurzfassung für #releases (höchstens 2000 Zeichen, Discord-Markdown)
  x.txt       — Entwurf für X (höchstens 280 Zeichen; ein Link zählt dort 23). Gepostet wird von Hand.

Eine Vorab-Version (mit Bindestrich) ohne eigenen Abschnitt bekommt die Commit-Betreffzeilen seit dem
letzten Tag. Eine stabile Version ohne Abschnitt ist ein Fehler: Wer veröffentlicht, schreibt auf, was neu ist.
"""
import argparse
import os
import re
import subprocess
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISCORD_GRENZE = 2000
X_GRENZE, X_LINK = 280, 23
X_KOMMENTAR = re.compile(r"<!--\s*x:.*?-->\s*", re.S)
STANDARD_LINK = "https://github.com/daiams2000-hash/dive-on-wide"


def abschnitt(changelog, version):
    """Der Text unter „## <version> …“ bis zur nächsten Versionsüberschrift."""
    m = re.search(r"^## %s\b[^\n]*\n(.*?)(?=^## |\Z)" % re.escape(version), changelog, re.M | re.S)
    if not m:
        return None, None
    kopf = re.match(r"^## [^\n]*", changelog[m.start():]).group(0)[3:].strip()
    return kopf, m.group(1).strip().rstrip("-").strip()


def commits_seit_letztem_tag(version):
    try:
        tags = subprocess.run(["git", "tag", "--sort=-creatordate"], cwd=APP, capture_output=True, text=True).stdout.split()
        vorher = next((t for t in tags if t != "v" + version), None)
        bereich = "%s..HEAD" % vorher if vorher else "HEAD~20..HEAD"
        zeilen = subprocess.run(["git", "log", "--format=%s", bereich], cwd=APP, capture_output=True, text=True).stdout
    except OSError:
        return []
    return [z.strip() for z in zeilen.splitlines() if z.strip() and not z.startswith("Version ")]


def kurzfassung(text, grenze):
    """Erste Absätze bis zur Grenze, an Absatzgrenzen geschnitten."""
    aus = ""
    for absatz in re.split(r"\n\s*\n", text):
        if len(aus) + len(absatz) + 2 > grenze:
            break
        aus += ("\n\n" if aus else "") + absatz
    return aus or text[:grenze - 1] + "…"


def x_entwurf(version, kopf, text, link):
    titel = re.sub(r"^\S+\s*[—-]\s*", "", kopf or "").strip() or "neue Version"
    eigen = re.search(r"<!--\s*x:\s*(.+?)\s*-->", text, re.S)
    text = X_KOMMENTAR.sub("", text)
    punkte = [z[2:].strip() for z in text.splitlines() if z.startswith("- ")]
    if eigen:
        # Eine eigene X-Fassung im CHANGELOG (<!-- x: … -->) schlägt jede Kürzung.
        satz = re.sub(r"\s+", " ", eigen.group(1)).strip()
    elif punkte and text.lstrip().startswith("- "):
        # Vorabversion aus Commit-Zeilen: Anzahl und die erste Änderung, keine gequetschte Liste
        satz = "%d Änderungen, u. a.: %s" % (len(punkte), punkte[0]) if len(punkte) > 1 else punkte[0]
    else:
        satz = re.split(r"(?<=[.!?])\s", re.sub(r"\s+", " ", text).strip())[0]
    satz = re.sub(r"[*_`#>]", "", satz)
    rumpf = "Dive on Wide %s — %s\n\n%s" % (version, titel, satz)
    platz = X_GRENZE - X_LINK - 2
    if len(rumpf) > platz:
        # An einer Wortgrenze kürzen, nie mitten im Wort („depend…“).
        rumpf = rumpf[:platz - 1].rsplit(" ", 1)[0].rstrip(" ,;:—-") + "…"
    return "%s\n\n%s" % (rumpf, link)


def erzeugen(version, changelog_text, link=STANDARD_LINK, commits=None):
    kopf, text = abschnitt(changelog_text, version)
    vorab = "-" in version
    if text is None:
        if not vorab:
            raise SystemExit("CHANGELOG.md hat keinen Abschnitt „## %s“. Erst aufschreiben, was neu ist." % version)
        liste = commits if commits is not None else commits_seit_letztem_tag(version)
        kopf, text = "%s — Vorabversion" % version, "\n".join("- " + c for c in liste[:40]) or "- (keine Änderungen)"
    hinweis = "\n\n> Vorabversion für Tester — nicht für den Alltag." if vorab else ""
    sichtbar = X_KOMMENTAR.sub("", text)
    github = "%s%s\n" % (sichtbar, hinweis)
    discord = "## 🚀 Dive on Wide %s\n%s" % (version, kurzfassung(sichtbar, DISCORD_GRENZE - 200))
    discord += "%s\n\n%s" % ("\n-# Vorabversion für Tester" if vorab else "", link)
    return {"github.md": github, "discord.txt": discord[:DISCORD_GRENZE], "x.txt": x_entwurf(version, kopf, text, link)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("--pruefen", action="store_true")
    ap.add_argument("--link", default=os.environ.get("DOWOS_LINK", STANDARD_LINK))
    ap.add_argument("--ziel", default=None)
    a = ap.parse_args()
    version = a.version.lstrip("v")
    changelog = open(os.path.join(APP, "CHANGELOG.md"), encoding="utf-8").read()
    texte = erzeugen(version, changelog, a.link)
    if a.pruefen:
        print("ok: %d Zeichen GitHub, %d Discord, %d X" % tuple(len(texte[k]) for k in ("github.md", "discord.txt", "x.txt")))
        return 0
    ziel = a.ziel or os.path.join(os.path.dirname(APP), "ausgabe", "release-" + version)
    os.makedirs(ziel, exist_ok=True)
    for name, inhalt in texte.items():
        with open(os.path.join(ziel, name), "w", encoding="utf-8") as f:
            f.write(inhalt)
    print("Release-Notizen in %s" % ziel)
    print("\n--- X-Entwurf (%d Zeichen, Link zählt %d) ---\n%s" % (
        len(texte["x.txt"]) - len(a.link) + X_LINK, X_LINK, texte["x.txt"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
