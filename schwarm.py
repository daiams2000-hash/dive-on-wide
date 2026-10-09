# -*- coding: utf-8 -*-
"""Der Schwarm: ein Planer, mehrere kleine Arbeiter, in Runden.

Ablauf je Runde:
  1. Der PLANER (meist das größte Modell) sieht Ziel, bisherige Dateien und den letzten Befund und verteilt
     Aufgaben — je Aufgabe genau eine Datei, damit sich keine zwei Arbeiter in die Quere kommen.
  2. Die ARBEITER (z. B. drei 4-B-Modelle) werden gleichzeitig beauftragt. Ob sie auch gleichzeitig RECHNEN, entscheidet
     der Modell-Server — auf einem Rechner mit einer Grafikkarte kaum (gemessen 08.10.2026: Faktor 0,85 mit drei
     verschiedenen Modellen, 1,0 mit demselben). Der Gewinn ist der Kontext: Jeder bekommt einen FRISCHEN — nur seine
     Aufgabe und die Dateien, die der Planer ihm ausdrücklich mitgibt. So passt jede Aufgabe auch in kleine Kontexte.
  3. Zusammenführen: Die Dateien landen im Projektordner. Python-Dateien werden kompiliert, auf Wunsch laufen Tests.
  4. Der Planer prüft das Ganze und entscheidet: fertig, oder eine neue Runde mit genau benannten Nacharbeiten.

Alles, was mit Modellen spricht, kommt als Funktion herein (`fragen(modell, nachrichten) -> text`) — die Tests
laufen damit ohne echtes Modell, und später kann ein Arbeiter genauso gut im Fischernetz sitzen.
"""
import concurrent.futures
import json
import os
import py_compile
import re
import subprocess
import time

MAX_AUFGABEN = 6
MAX_DATEI_ZEICHEN = 12000        # so viel einer Datei bekommt ein Arbeiter höchstens als Kontext
MAX_UEBERBLICK_ZEICHEN = 1500    # je Datei im Überblick für den Planer

PLANER_PROMPT = (
    "Du bist der Planer eines Teams aus mehreren kleinen Sprachmodellen. Ihr baut gemeinsam ein Projekt aus Dateien. "
    "Verteile die Arbeit dieser Runde in höchstens %d Aufgaben. Regeln:\n"
    "- Jede Aufgabe erzeugt oder ersetzt GENAU EINE Datei (relativer Pfad, nur Buchstaben, Ziffern, _ - . /).\n"
    "- Zwei Aufgaben dürfen nie dieselbe Datei haben.\n"
    "- Die Anweisung ist vollständig: Der Arbeiter sieht NUR sie und die Dateien in \"kontext\" — nicht das Ziel, nicht "
    "die anderen Aufgaben. Nenne Funktionsnamen, Schnittstellen und Formate ausdrücklich, damit die Teile zusammenpassen.\n"
    "- \"kontext\": Pfade vorhandener Dateien, die der Arbeiter lesen muss (höchstens 3).\n"
    "- Verteile in JEDER Runde ALLE noch offenen Dateien auf einmal — die Arbeiter arbeiten gleichzeitig. In der ersten "
    "Runde also jede Datei, die das Ziel nennt.\n"
    "- Für eine Testdatei: Schreib die Testfälle selbst in die Anweisung — Eingabe und erwartetes Ergebnis, das du "
    "vorher SORGFÄLTIG nachgerechnet hast. Der Arbeiter rechnet nicht selbst.\n"
    "- Nach einer fehlgeschlagenen Prüfung: Entscheide zuerst, ob der Code oder der Test falsch ist (rechne nach), und "
    "lass nur die falsche Datei neu schreiben. Sag in der Anweisung genau, was falsch war.\n"
    "- Ist alles erledigt, gib eine leere Liste und \"fertig\": true.\n"
    "Antworte NUR mit JSON: {\"plan\": \"ein Satz\", \"fertig\": false, \"aufgaben\": [{\"datei\": \"…\", "
    "\"anweisung\": \"…\", \"kontext\": [\"…\"]}]}")

ARBEITER_PROMPT = (
    "Du bist ein sorgfältiger Entwickler in einem Team. Du bekommst genau eine Aufgabe für genau eine Datei. "
    "Liefere den VOLLSTÄNDIGEN Inhalt dieser Datei in einem einzigen Codeblock (```), ohne Erklärung davor oder "
    "danach. Halte dich exakt an die genannten Namen und Schnittstellen.")

PRUEFER_PROMPT = (
    "Du bist der Planer und prüfst, was dein Team geliefert hat. Du siehst das Ziel, alle Dateien (gekürzt) und das "
    "Ergebnis der automatischen Prüfung. Entscheide: Ist das Ziel erreicht und passt alles zusammen? "
    "Antworte NUR mit JSON: {\"fertig\": true/false, \"befund\": \"was fehlt oder falsch ist, konkret mit Datei\"}")


def json_aus(text):
    """Das erste JSON-Objekt im Text — Modelle schreiben gern etwas davor oder ```json darum."""
    text = text or ""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    kandidat = m.group(1) if m else text[text.find("{"):text.rfind("}") + 1]
    try:
        return json.loads(kandidat)
    except Exception:
        return {}


def code_aus(text):
    """Inhalt des (längsten) Codeblocks — oder der ganze Text, wenn es keinen gibt."""
    bloecke = re.findall(r"```[\w+.-]*\n(.*?)```", text or "", re.S)
    if bloecke:
        return max(bloecke, key=len).rstrip() + "\n"
    return (text or "").strip() + "\n"


def sicherer_pfad(wurzel, rel):
    rel = (rel or "").strip().lstrip("/")
    if not rel or not re.match(r"^[\w./-]{1,160}$", rel) or ".." in rel.split("/"):
        return None
    pfad = os.path.realpath(os.path.join(wurzel, rel))
    return pfad if pfad.startswith(os.path.realpath(wurzel) + os.sep) else None


def dateien_lesen(wurzel):
    aus = {}
    for ort, _, namen in os.walk(wurzel):
        if "__pycache__" in ort:
            continue
        for n in namen:
            p = os.path.join(ort, n)
            try:
                aus[os.path.relpath(p, wurzel)] = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                pass
    return dict(sorted(aus.items()))


def pruefen(wurzel, testbefehl="", zeitlimit=120, ausfuehren=None):
    """Automatische Prüfung: Python kompilieren, auf Wunsch einen Testbefehl ausführen. -> (ok, text)."""
    teile, ok = [], True
    for rel, _ in dateien_lesen(wurzel).items():
        if rel.endswith(".py"):
            try:
                py_compile.compile(os.path.join(wurzel, rel), doraise=True)
            except py_compile.PyCompileError as e:
                ok = False
                teile.append("Syntaxfehler in %s: %s" % (rel, str(e.msg).strip()[-400:]))
    if testbefehl and ok:
        if ausfuehren:
            code, ausgabe = ausfuehren(testbefehl, wurzel, zeitlimit)
        else:
            try:
                r = subprocess.run(testbefehl, shell=True, cwd=wurzel, capture_output=True, text=True, timeout=zeitlimit)
                code, ausgabe = r.returncode, (r.stdout + r.stderr)
            except subprocess.TimeoutExpired:
                code, ausgabe = 124, "Zeitlimit überschritten"
        ok = code == 0
        teile.append("Tests (`%s`): %s\n%s" % (testbefehl, "bestanden" if ok else "FEHLGESCHLAGEN, Code %s" % code,
                                                ausgabe[-1500:]))
    return ok, "\n".join(teile) or "Keine Auffälligkeiten (Python-Dateien kompilieren)."


def ueberblick(dateien):
    return "\n\n".join("### %s (%d Zeichen)\n%s%s" % (rel, len(t), t[:MAX_UEBERBLICK_ZEICHEN],
                                                       "\n…" if len(t) > MAX_UEBERBLICK_ZEICHEN else "")
                       for rel, t in dateien.items()) or "(noch keine Dateien)"


def laufen(ziel, wurzel, fragen, planer, arbeiter, runden=3, testbefehl="", melden=lambda t: None,
           abbrechen=lambda: False, ausfuehren=None):
    """Den Schwarm arbeiten lassen. -> {"fertig", "runden", "dateien", "protokoll", "befund"}"""
    os.makedirs(wurzel, exist_ok=True)
    arbeiter = [a for a in arbeiter if a] or [planer]
    protokoll, befund, fertig, letzte_pruefung = [], "", False, ""
    for runde in range(1, max(1, runden) + 1):
        if abbrechen():
            break
        dateien = dateien_lesen(wurzel)
        melden("Runde %d: Der Planer verteilt die Arbeit …" % runde)
        roh = fragen(planer, [
            {"role": "system", "content": PLANER_PROMPT % MAX_AUFGABEN},
            {"role": "user", "content": "ZIEL:\n%s\n\nRUNDE %d von höchstens %d.\n\nVORHANDENE DATEIEN:\n%s\n\n"
                                        "BEFUND DER LETZTEN PRÜFUNG:\n%s" % (ziel, runde, runden, ueberblick(dateien),
                                                                          befund or "(erste Runde)")}])
        plan = json_aus(roh)
        aufgaben, gesehen = [], set()
        for a in (plan.get("aufgaben") or [])[:MAX_AUFGABEN]:
            if not isinstance(a, dict):
                continue
            pfad = sicherer_pfad(wurzel, a.get("datei"))
            if not pfad or pfad in gesehen or not (a.get("anweisung") or "").strip():
                continue
            gesehen.add(pfad)
            aufgaben.append(dict(a, _pfad=pfad))
        if not aufgaben:
            # „Fertig“ zählt nur, wenn die letzte Prüfung bestanden hat — der Planer darf rote Tests nicht
            # wegerklären (07.10.2026: fertig gemeldet, obwohl ein Test fehlschlug).
            fertig = bool(plan.get("fertig")) and bool(dateien) and not letzte_pruefung and runde > 1
            if plan.get("fertig") and letzte_pruefung:
                befund = "Der Planer hielt das Ziel für erreicht, aber die Prüfung schlägt fehl:\n" + letzte_pruefung
            protokoll.append({"runde": runde, "plan": plan.get("plan", ""), "aufgaben": [], "hinweis":
                              "keine Aufgaben" + ("" if plan else " (Plan unlesbar)")})
            if fertig or not plan:
                break
            if letzte_pruefung:
                continue      # nächste Runde: der Planer bekommt den Befund noch einmal ausdrücklich
            continue
        melden("Runde %d: %d Aufgaben an %d Arbeiter — %s" % (runde, len(aufgaben), min(len(arbeiter), len(aufgaben)),
                                                              ", ".join(a["datei"] for a in aufgaben)))

        def arbeite(i_aufgabe):
            i, a = i_aufgabe
            modell = arbeiter[i % len(arbeiter)]
            kontext = []
            for rel in (a.get("kontext") or [])[:3]:
                if rel in dateien:
                    kontext.append("### %s\n```\n%s\n```" % (rel, dateien[rel][:MAX_DATEI_ZEICHEN]))
            alt = dateien.get(a["datei"])
            if alt is not None and a["datei"] not in (a.get("kontext") or []):
                kontext.append("### %s (bisherige Fassung, wird ersetzt)\n```\n%s\n```" % (a["datei"], alt[:MAX_DATEI_ZEICHEN]))
            if alt is not None and letzte_pruefung:
                kontext.append("### Ergebnis der letzten Prüfung\n```\n%s\n```" % letzte_pruefung[-2500:])
            t0 = time.time()
            try:
                text = fragen(modell, [{"role": "system", "content": ARBEITER_PROMPT},
                                       {"role": "user", "content": "DATEI: %s\n\nAUFGABE:\n%s%s" % (
                                           a["datei"], a["anweisung"], "\n\nKONTEXT:\n" + "\n\n".join(kontext) if kontext else "")}])
            except Exception as e:
                # Ein hängender oder abgestürzter Arbeiter hält die Runde nicht auf: Die Datei bleibt, wie sie war, und
                # der Planer sieht in der nächsten Runde, dass sie fehlt oder alt ist.
                return a, modell, None, round(time.time() - t0, 1)
            return a, modell, code_aus(text), round(time.time() - t0, 1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(arbeiter))) as pool:
            ergebnisse = list(pool.map(arbeite, enumerate(aufgaben)))
        for a, modell, inhalt, sek in ergebnisse:
            if inhalt is None:
                melden("Runde %d: %s — Arbeiter ohne Ergebnis (%s, %s s), die Datei bleibt unverändert"
                       % (runde, a["datei"], str(modell).split("@@")[-1], sek))
                continue
            os.makedirs(os.path.dirname(a["_pfad"]), exist_ok=True)
            with open(a["_pfad"], "w", encoding="utf-8") as f:
                f.write(inhalt)
            melden("Runde %d: %s fertig (%s, %s s)" % (runde, a["datei"], str(modell).split("@@")[-1], sek))
        ok, pruefung = pruefen(wurzel, testbefehl, ausfuehren=ausfuehren)
        letzte_pruefung = "" if ok else pruefung
        melden("Runde %d: Prüfung — %s" % (runde, "ok" if ok else "Probleme"))
        urteil = json_aus(fragen(planer, [
            {"role": "system", "content": PRUEFER_PROMPT},
            {"role": "user", "content": "ZIEL:\n%s\n\nDATEIEN:\n%s\n\nAUTOMATISCHE PRÜFUNG:\n%s" % (
                ziel, ueberblick(dateien_lesen(wurzel)), pruefung)}]))
        fertig = bool(urteil.get("fertig")) and ok
        befund = (urteil.get("befund") or "").strip() + ("" if ok else "\n" + pruefung)
        protokoll.append({"runde": runde, "plan": plan.get("plan", ""), "pruefung": pruefung, "befund": befund,
                          "planer": (urteil.get("befund") or "").strip(), "ok": ok,
                          "aufgaben": [{"datei": a["datei"], "modell": m, "sekunden": s} for a, m, _, s in ergebnisse]})
        if fertig:
            break
    return {"fertig": fertig, "runden": len(protokoll), "dateien": sorted(dateien_lesen(wurzel)),
            "protokoll": protokoll, "befund": befund}


def bericht(ziel, erg, wurzel):
    heim = os.path.expanduser("~")
    ordner = "~" + wurzel[len(heim):] if wurzel.startswith(heim + os.sep) else wurzel   # kein Nutzername im Bericht
    zeilen = ["# 🐝 Schwarm: %s" % ziel[:80], "",
              "**Ergebnis:** %s nach %d Runde(n). **Ordner:** `%s`" % ("✅ fertig" if erg["fertig"] else "⚠️ nicht fertig",
                                                                     erg["runden"], ordner), ""]
    for r in erg["protokoll"]:
        zeilen.append("## Runde %d — %s" % (r["runde"], r.get("plan") or ""))
        for a in r.get("aufgaben", []):
            zeilen.append("- `%s` · %s · %s s" % (a["datei"], str(a["modell"]).split("@@")[-1], a["sekunden"]))
        if r.get("pruefung"):
            # Testausgaben in einen Codeblock — Zeilen wie „=====“ zerschossen sonst die Darstellung (09.10.2026)
            zeilen += ["", "**Prüfung:** %s" % ("✅ bestanden" if r.get("ok") else "❌ nicht bestanden"),
                       "```text", r["pruefung"].strip()[-700:], "```"]
        if r.get("planer"):
            zeilen += ["", "**Befund des Planers:** " + r["planer"][:500]]
        zeilen.append("")
    for rel, text in dateien_lesen(wurzel).items():
        sprache = os.path.splitext(rel)[1].lstrip(".")
        zeilen += ["## `%s`" % rel, "```" + sprache, text[:8000].rstrip(), "```", ""]
    return "\n".join(zeilen)
