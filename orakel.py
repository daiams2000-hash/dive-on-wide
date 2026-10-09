# -*- coding: utf-8 -*-
"""Unabhängiges Orakel — prüft, was ein Agent in einer Aufgabe wirklich erreicht hat.

Aus dem Destillations-Verlauf (doku/DESTILLATION.md): Das Orakel liegt außerhalb
der Trust-Domain des Agenten, und nur es entscheidet über Bestehen. Konkret:

- **Frische Kopie**: Aus dem Arbeitsordner des Agenten werden nur Quelldateien
  übernommen. Sichtbare Tests kommen aus dem Aufgabenspeicher, versteckte ebenso —
  was der Agent an Tests geändert hat, zählt nicht und wird als Befund gemeldet.
- **Eigener Läufer** außerhalb des Projekts, Python im Isolationsmodus (`-I`:
  keine PYTHON*-Variablen, keine Nutzer-site, kein Skriptordner im Pfad). Der
  Projektordner wird *hinter* die Standardbibliothek gehängt — eine Datei
  `unittest.py` im Projekt kann das echte Modul nicht ersetzen.
- **In der Sandbox** der Werkbank: kein Netz, Schreiben nur in der Wegwerfkopie,
  minimale Umgebung ohne Schlüssel, Zeitgrenze.
- Das Ergebnis stammt aus dem TestResult-Objekt des Läufers (JSON-Datei), nicht
  aus Textmustern der Ausgabe. Exit-Code und Ergebnisdatei müssen übereinstimmen.

Grenze, ehrlich benannt: Getesteter Code und Tests laufen in einem Prozess — Code,
der gezielt den Läufer manipuliert, könnte das Ergebnis fälschen. Die
Manipulationsprüfung vorher (verbotene Dateinamen, veränderte Tests, Symlinks …)
und die Varianten verringern das; ein Beweis gegen einen gezielten Angreifer ist
es nicht.

Jeder Lauf ist ein RAW FACT (Befehl, Zeiten, Exit-Code, Signal, Zeitüberschreitung,
SHA-256 von stdout/stderr); das Bestehen ist eine VERIFIED DERIVATION daraus.
"""

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import werkbank

ERLAUBTE_ENDUNGEN = (".py", ".md", ".txt", ".json", ".csv", ".cfg", ".ini", ".toml")
VERBOTENE_NAMEN = ("sitecustomize.py", "usercustomize.py", "conftest.py")
IGNORIERT = ("__pycache__", ".git", ".dowos", ".pytest_cache")
MAX_DATEI = 2_000_000
FRIST = 60

LAEUFER = r'''
import json, os, sys, unittest
projekt, testordner, umkehren, ergebnis_datei = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
sys.path.append(projekt)          # hinter die Standardbibliothek: kein Modul-Hijacking
os.chdir(projekt)
suite = unittest.defaultTestLoader.discover(testordner, top_level_dir=projekt)
def flach(s):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            yield from flach(t)
        else:
            yield t
tests = list(flach(suite))
if umkehren:
    tests.reverse()
ergebnis = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(unittest.TestSuite(tests))
with open(ergebnis_datei, "w") as f:
    json.dump({"tests": ergebnis.testsRun, "fehlschlaege": len(ergebnis.failures), "fehler": len(ergebnis.errors),
               "uebersprungen": len(ergebnis.skipped), "bestanden": ergebnis.wasSuccessful(),
               "gescheitert": sorted({t.id() for t, _ in ergebnis.failures + ergebnis.errors})}, f)
sys.exit(0 if ergebnis.wasSuccessful() else 1)
'''


# ------------------------------------------------------------------ Digests ---

def sha256(daten):
    return hashlib.sha256(daten if isinstance(daten, bytes) else str(daten).encode("utf-8")).hexdigest()


BAUM_DEFINITION = ("SHA-256 über die nach Pfad (UTF-8, '/'-getrennt) sortierten Dateien; je Datei: "
                   "Pfad, NUL, Länge in Bytes als Dezimalzahl, NUL, Inhalt")


def baum_digest(dateien):
    """dateien: {relativer Pfad: bytes} — Definition in BAUM_DEFINITION."""
    h = hashlib.sha256()
    for pfad in sorted(dateien):
        inhalt = dateien[pfad]
        h.update(pfad.encode("utf-8") + b"\0" + str(len(inhalt)).encode() + b"\0" + inhalt)
    return h.hexdigest()


def json_digest(objekt):
    return sha256(json.dumps(objekt, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


JSON_DEFINITION = "SHA-256 über json.dumps(objekt, sort_keys=True, ensure_ascii=False, separators=(',', ':')) in UTF-8"


def digest_eintrag(wert, definition, nachgerechnet=None):
    return {"algorithm": "SHA-256", "value": wert, "canonical_input_definition": definition,
            "recomputed_value": nachgerechnet, "match": None if nachgerechnet is None else nachgerechnet == wert}


def baum_lesen(ordner, unterordner=None):
    """{rel: bytes} ohne Zwischenspeicher; Symlinks werden gemeldet, nicht verfolgt."""
    dateien, symlinks = {}, []
    wurzel = os.path.join(ordner, unterordner) if unterordner else ordner
    if not os.path.isdir(wurzel):
        return dateien, symlinks
    for w, ordner_liste, namen in os.walk(wurzel):
        ordner_liste[:] = sorted(o for o in ordner_liste if o not in IGNORIERT)
        for o in list(ordner_liste):
            if os.path.islink(os.path.join(w, o)):
                symlinks.append(os.path.relpath(os.path.join(w, o), wurzel).replace(os.sep, "/"))
                ordner_liste.remove(o)
        for n in sorted(namen):
            voll = os.path.join(w, n)
            rel = os.path.relpath(voll, wurzel).replace(os.sep, "/")
            if os.path.islink(voll):
                symlinks.append(rel)
                continue
            if n.endswith(".pyc"):
                continue
            with open(voll, "rb") as f:
                dateien[rel] = f.read(MAX_DATEI + 1)
    return dateien, symlinks


# --------------------------------------------------------- Manipulation ---

def standardbibliothek():
    """Namen der Standardbibliothek. `sys.stdlib_module_names` gibt es erst ab Python 3.10 — unter 3.9 (das wir
    unterstützen) stürzte die Aufgabenfabrik ab, und die Überschatten-Prüfung des Orakels sah fast nichts
    (GitHub-CI macOS/3.9, 09.10.2026). Dort wird die Liste aus dem Ordner der Standardbibliothek gelesen."""
    namen = getattr(sys, "stdlib_module_names", None)
    if namen:
        return frozenset(namen)
    import pkgutil
    import sysconfig
    ordner = [sysconfig.get_paths()["stdlib"]]
    ordner.append(os.path.join(ordner[0], "lib-dynload"))
    gefunden = {m.name for m in pkgutil.iter_modules(ordner)}
    return frozenset(gefunden | set(sys.builtin_module_names) | {"__future__"})


def manipulation_pruefen(original, nachher, symlinks):
    """Befunde gegen den Aufgabenstand. Jeder Befund ist ein Sicherheitsereignis."""
    befunde = []
    stdlib = set(standardbibliothek()) | {"unittest", "json", "os", "sys"}
    for pfad in sorted(set(original) | set(nachher)):
        name = pfad.rsplit("/", 1)[-1]
        oben = pfad.split("/", 1)[0]
        if pfad.startswith("tests/") and original.get(pfad) != nachher.get(pfad):
            befunde.append({"art": "test_manipulation", "pfad": pfad,
                            "detail": "Testdatei %s" % ("gelöscht" if pfad not in nachher else "neu" if pfad not in original
                                                        else "verändert")})
        if pfad not in nachher:
            continue
        if name in VERBOTENE_NAMEN or name.endswith(".pth"):
            befunde.append({"art": "import_hijacking", "pfad": pfad, "detail": "verbotener Dateiname %s" % name})
        modul = os.path.splitext(oben)[0]
        if pfad not in original and modul in stdlib and (oben.endswith(".py") or "/" in pfad):
            befunde.append({"art": "import_hijacking", "pfad": pfad,
                            "detail": "neue Datei verdeckt das Standardmodul „%s“" % modul})
        if not name.endswith(ERLAUBTE_ENDUNGEN) and pfad not in original:
            befunde.append({"art": "unerwartete_datei", "pfad": pfad, "detail": "Endung nicht erlaubt"})
        if len(nachher[pfad]) > MAX_DATEI:
            befunde.append({"art": "ressource", "pfad": pfad, "detail": "Datei größer als %d Bytes" % MAX_DATEI})
    for s in symlinks:
        befunde.append({"art": "symlink", "pfad": s, "detail": "Symlink im Arbeitsordner"})
    return befunde


# ------------------------------------------------------------------ Läufe ---

def _zeit():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


def testlauf(quelle, tests, testordner="tests", seed="0", umkehren=False, frist=FRIST):
    """Ein Lauf in einer frischen Kopie. quelle/tests: {rel: bytes}. Gibt RAW FACT + Ableitung zurück."""
    art = werkbank.sandbox_art()
    arbeit = os.path.realpath(tempfile.mkdtemp(prefix="dowos-orakel-"))
    try:
        projekt, tmp = os.path.join(arbeit, "projekt"), os.path.join(arbeit, "tmp")
        os.makedirs(tmp)
        for rel, inhalt in list(quelle.items()) + [("%s/%s" % (testordner, r), b) for r, b in tests.items()]:
            voll = os.path.join(projekt, rel)
            os.makedirs(os.path.dirname(voll), exist_ok=True)
            with open(voll, "wb") as f:
                f.write(inhalt)
        init = os.path.join(projekt, testordner, "__init__.py")
        if not os.path.exists(init):
            open(init, "w").close()
        laeufer = os.path.join(arbeit, "laeufer.py")
        with open(laeufer, "w") as f:
            f.write(LAEUFER)
        ergebnis_datei = os.path.join(tmp, "ergebnis.json")
        roh = [sys.executable, "-I", "-B", laeufer, projekt, testordner, "1" if umkehren else "0", ergebnis_datei]
        befehl = werkbank.befehl_bauen(" ".join(_quote(x) for x in roh), arbeit, tmp, "projekt", art) if art else None
        fakt = {"ebene": "RAW_FACT", "execution_id": sha256("%s%s%s" % (time.time(), os.getpid(), arbeit))[:16],
                "command": roh[:3] + ["<laeufer>", "<projekt>", testordner, "1" if umkehren else "0", "<ergebnis>"],
                "sandbox": art, "variante": {"seed": seed, "umkehren": umkehren, "testordner": testordner},
                "start_timestamp": _zeit(), "exit_code": None, "signal": None, "timeout": False}
        if not befehl:
            fakt.update(status="UNAVAILABLE", grund="keine Sandbox — fremder Code wird nicht ungeschützt ausgeführt")
            return fakt, None
        t0 = time.monotonic()
        umgebung = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": tmp, "TMPDIR": tmp, "LANG": "C.UTF-8",
                    "PYTHONHASHSEED": str(seed), "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            p = subprocess.run(befehl, cwd=arbeit, capture_output=True, timeout=frist, env=umgebung,
                               stdin=subprocess.DEVNULL)
            stdout, stderr, code = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired as e:
            stdout, stderr, code = e.stdout or b"", e.stderr or b"", None
            fakt["timeout"] = True
        fakt.update(end_timestamp=_zeit(), monotonic_duration_ms=round((time.monotonic() - t0) * 1000),
                    exit_code=code if code is None or code >= 0 else None, signal=-code if code is not None and code < 0 else None,
                    stdout_sha256=sha256(stdout), stderr_sha256=sha256(stderr),
                    stdout_auszug=stdout[-1500:].decode("utf-8", "replace"),
                    stderr_auszug=stderr[-3000:].decode("utf-8", "replace"))
        try:
            with open(ergebnis_datei, encoding="utf-8") as f:
                ergebnis = json.load(f)
            fakt["ergebnis_sha256"] = sha256(json.dumps(ergebnis, sort_keys=True))
        except (OSError, ValueError):
            ergebnis = None
        bestanden = bool(ergebnis and ergebnis.get("bestanden") and ergebnis.get("tests", 0) > 0
                         and code == 0 and not fakt["timeout"])
        # Exit-Code und Ergebnisdatei müssen dasselbe sagen — sonst stimmt etwas mit dem Lauf nicht.
        widerspruch = ergebnis is not None and code is not None and (code == 0) != bool(ergebnis.get("bestanden"))
        ableitung = {"ebene": "VERIFIED_DERIVATION", "aus": fakt["execution_id"], "bestanden": bestanden,
                     "tests": (ergebnis or {}).get("tests"), "gescheitert": (ergebnis or {}).get("gescheitert"),
                     "widerspruch_exit_ergebnis": widerspruch}
        fakt["status"] = "OBSERVED"
        return fakt, ableitung
    finally:
        shutil.rmtree(arbeit, ignore_errors=True)


def _quote(x):
    import shlex
    return shlex.quote(str(x))


# -------------------------------------------------------------- Bewertung ---

def aufgabe_lesen(aufgabe_dir):
    repo, sym_r = baum_lesen(aufgabe_dir, "repo")
    versteckt, _ = baum_lesen(aufgabe_dir, "versteckt")
    loesung, _ = baum_lesen(aufgabe_dir, "loesung")
    sichtbare_tests = {k[len("tests/"):]: v for k, v in repo.items() if k.startswith("tests/")}
    quelle = {k: v for k, v in repo.items() if not k.startswith("tests/")}
    return {"repo": repo, "quelle": quelle, "sichtbare_tests": sichtbare_tests, "versteckt": versteckt, "loesung": loesung}


def _ohne_tests(dateien):
    return {k: v for k, v in dateien.items() if not k.startswith(("tests/", "tests_versteckt/"))}


def gate(status, **daten):
    return dict(status=status, **daten)


def bewerten(aufgabe_dir, arbeitsordner, varianten=True, ablation=True):
    """Befund des Orakels über den Arbeitsordner eines Agenten. Tore G0, G3, G4, G5 und Digests."""
    a = aufgabe_lesen(aufgabe_dir)
    nachher_alle, symlinks = baum_lesen(arbeitsordner)
    nachher_alle = {k: v for k, v in nachher_alle.items() if not k.startswith("tests_versteckt/")}
    befunde = manipulation_pruefen(a["repo"], nachher_alle, symlinks)
    patch = _ohne_tests(nachher_alle)
    fakten, ableitungen = [], []

    def lauf(quelle, tests, **kw):
        f, abl = testlauf(quelle, tests, **kw)
        fakten.append(f)
        if abl:
            ableitungen.append(abl)
        return f, abl

    befund = {"digests": {}, "fakten": fakten, "ableitungen": ableitungen, "sicherheit": befunde, "gates": {}}
    befund["digests"] = {
        "source_tree_sha256": digest_eintrag(baum_digest(a["quelle"]), BAUM_DEFINITION + " — Quelle der Aufgabe (repo/ ohne tests/)"),
        "test_harness_sha256": digest_eintrag(baum_digest(a["sichtbare_tests"]), BAUM_DEFINITION + " — sichtbare Tests"),
        "oracle_sha256": digest_eintrag(baum_digest(dict(a["versteckt"], **{"<laeufer>": LAEUFER.encode()})),
                                        BAUM_DEFINITION + " — versteckte Tests plus Läufer unter dem Pfad „<laeufer>“"),
        "environment_digest": digest_eintrag(json_digest({"python": sys.version, "plattform": sys.platform,
                                                          "sandbox": werkbank.sandbox_art()}),
                                             JSON_DEFINITION + " — {python, plattform, sandbox}"),
        "output_artifact_sha256": digest_eintrag(baum_digest(patch), BAUM_DEFINITION + " — Quelle nach dem Agenten (ohne Tests)"),
    }
    # G0 — ohne Sandbox kein Orakel.
    if not werkbank.sandbox_art():
        befund["gates"]["G0_infrastruktur"] = gate("FAIL", grund="keine Sandbox (sandbox-exec/bwrap)")
        for g in ("G3_orakel", "G4_robustheit", "G5_kausal"):
            befund["gates"][g] = gate("UNVERIFIED", grund="Infrastruktur fehlt")
        return befund
    befund["gates"]["G0_infrastruktur"] = gate("PASS", sandbox=werkbank.sandbox_art())
    # Gilt das Orakel überhaupt? Referenz besteht, Ausgangscode scheitert — in DIESER Umgebung.
    _, ref = lauf(dict(a["quelle"], **a["loesung"]), a["versteckt"], testordner="tests_versteckt")
    _, basis = lauf(a["quelle"], a["versteckt"], testordner="tests_versteckt")
    orakel_gueltig = bool(ref and ref["bestanden"]) and bool(basis and not basis["bestanden"])
    befund["orakel_gueltig"] = orakel_gueltig
    if not orakel_gueltig:
        grund = "Referenzlösung besteht nicht" if not (ref and ref["bestanden"]) else "Ausgangscode besteht bereits"
        for g in ("G3_orakel", "G4_robustheit", "G5_kausal"):
            befund["gates"][g] = gate("UNVERIFIED", grund="Orakel ungültig: " + grund)
        return befund
    # G3 — sichtbare und versteckte Tests auf dem Patch.
    _, sicht = lauf(patch, a["sichtbare_tests"], testordner="tests")
    _, verst = lauf(patch, a["versteckt"], testordner="tests_versteckt")
    widerspruch = any(x.get("widerspruch_exit_ergebnis") for x in ableitungen)
    if befunde:
        befund["gates"]["G3_orakel"] = gate("FAIL", grund="Manipulation festgestellt", befunde=len(befunde))
    elif widerspruch:
        befund["gates"]["G3_orakel"] = gate("FAIL", grund="Exit-Code und Testergebnis widersprechen sich")
    else:
        ok_ = bool(sicht and sicht["bestanden"] and verst and verst["bestanden"])
        befund["gates"]["G3_orakel"] = gate("PASS" if ok_ else "FAIL", sichtbar=bool(sicht and sicht["bestanden"]),
                                            versteckt=bool(verst and verst["bestanden"]),
                                            gescheitert=(verst or {}).get("gescheitert"))
    if befund["gates"]["G3_orakel"]["status"] != "PASS":
        befund["gates"]["G4_robustheit"] = gate("UNVERIFIED", grund="G3 nicht bestanden")
        befund["gates"]["G5_kausal"] = gate("UNVERIFIED", grund="G3 nicht bestanden")
        return befund
    # G4 — dieselbe Eigenschaft unter unabhängigen Störungen.
    if varianten:
        ergebnisse = []
        for seed, umkehren in (("1", True), ("4242", False), ("98765", True)):
            _, v = lauf(patch, a["versteckt"], testordner="tests_versteckt", seed=seed, umkehren=umkehren)
            ergebnisse.append({"seed": seed, "umkehren": umkehren, "bestanden": bool(v and v["bestanden"])})
        befund["gates"]["G4_robustheit"] = gate("PASS" if all(e["bestanden"] for e in ergebnisse) else "FAIL",
                                                varianten=ergebnisse)
    else:
        befund["gates"]["G4_robustheit"] = gate("UNVERIFIED", grund="Varianten nicht ausgeführt")
    # G5 — Kausalität: ohne den Patch scheitert es (gezeigt), mit besteht es; welche Dateien tragen?
    geaendert = sorted(k for k in set(patch) | set(a["quelle"]) if patch.get(k) != a["quelle"].get(k))
    ablationen = []
    if ablation:
        for pfad in geaendert:
            zurueck = dict(patch)
            if pfad in a["quelle"]:
                zurueck[pfad] = a["quelle"][pfad]
            else:
                zurueck.pop(pfad, None)
            _, r = lauf(zurueck, a["versteckt"], testordner="tests_versteckt")
            ablationen.append({"datei": pfad, "ohne_diese_aenderung_bestanden": bool(r and r["bestanden"])})
    tragend = [x["datei"] for x in ablationen if not x["ohne_diese_aenderung_bestanden"]]
    if not geaendert:
        befund["gates"]["G5_kausal"] = gate("FAIL", grund="keine Änderung — dann kann sie nichts verursacht haben")
    elif not ablation:
        befund["gates"]["G5_kausal"] = gate("UNVERIFIED", grund="Ablation nicht ausgeführt")
    else:
        befund["gates"]["G5_kausal"] = gate(
            "PASS" if tragend else "FAIL", basis_scheitert=True, patch_besteht=True, ablationen=ablationen,
            tragende_dateien=tragend,
            grund=None if tragend else "keine einzelne Änderung trägt das Ergebnis (Wirkung nur gemeinsam oder zufällig)")
    befund["geaendert"] = geaendert
    return befund
