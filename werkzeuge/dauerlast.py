#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dauerbetrieb unter echter Last: stundenlang arbeiten, nicht nur antworten.

Der Leerlauf-Dauertest (50 Minuten Leseabfragen) zeigte keinerlei Wachstum.
Das sagt aber nur etwas ueber Leseabfragen. Ein Leck entsteht typischerweise
genau dort, wo wirklich gearbeitet wird: Threads je Lauf, offene Verbindungen
zum Modell, Zeilen in der Datenbank, Dateien im Arbeitsordner, Eintraege in
der Inbox. Genau das macht dieses Werkzeug — im Kreis, stundenlang.

Ein Durchgang ist eine kleine Arbeitsschicht:

1. **Chat** — eine Frage ans Modell, ueber den Streaming-Weg.
2. **Skill** — ein echter Skill-Lauf im Hintergrund, bis zum Artefakt.
3. **Coding-Agent** — Code schreiben und ausfuehren lassen (Sandbox an).
4. **Artefakt** — anlegen, lesen, loeschen.

Danach wird gemessen: Arbeitsspeicher, offene Dateizeiger, Threads,
Datenbankgroesse, Zahl der Laeufe und der Inbox-Eintraege. Wer nur RSS
ansieht, uebersieht das haeufigere Leck — Threads, die nie enden.

Gemessen wird nur der Dive-on-Wide-Prozess. Ollama laeuft daneben und hat seinen
eigenen Speicher; ihn mitzuzaehlen wuerde die Zahlen unbrauchbar machen.

    python3 werkzeuge/dauerlast.py [--stunden 4] [--port 3093]

Alles laeuft in einem eigenen Wegwerf-Speicher. Der echte Ordner wird nicht
angefasst — auch nicht gelesen.
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
START = time.time()
_ENDE = {"jetzt": False}


def melden(t):
    std = (time.time() - START) / 3600.0
    print("[%5.2f h] %s" % (std, t), flush=True)


def _abbrechen(*_):
    _ENDE["jetzt"] = True
    melden("Abbruchsignal — beende nach diesem Durchgang.")


def hole(basis, pfad, schluessel, rumpf=None, frist=600):
    daten = json.dumps(rumpf).encode() if rumpf is not None else None
    req = urllib.request.Request(basis + pfad, data=daten,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + schluessel})
    with urllib.request.urlopen(req, timeout=frist) as r:
        roh = r.read().decode("utf-8", "replace")
    try:
        return json.loads(roh)
    except ValueError:
        return {"_roh": roh[:400]}


def messwerte(pid):
    """RSS in MB, offene Dateizeiger, Threads — mit Bordmitteln."""
    rss = threads = fds = None
    try:
        aus = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                             capture_output=True, timeout=10).stdout.split()
        if aus:
            rss = int(aus[0]) / 1024.0
    except Exception:
        pass
    try:
        aus = subprocess.run(["ps", "-M", "-p", str(pid)],
                             capture_output=True, timeout=10).stdout.decode()
        threads = max(0, len(aus.strip().splitlines()) - 1)
    except Exception:
        pass
    try:
        aus = subprocess.run(["lsof", "-p", str(pid)],
                             capture_output=True, timeout=40).stdout.decode()
        fds = max(0, len(aus.strip().splitlines()) - 1)
    except Exception:
        pass
    return rss, fds, threads


def warten_auf_lauf(basis, schluessel, lauf_id, grenze=900):
    """Auf das Ende eines Hintergrundlaufs warten. Gibt (zustand, sekunden)."""
    t0 = time.time()
    while time.time() - t0 < grenze:
        try:
            d = hole(basis, "/api/runs/" + lauf_id, schluessel, frist=30)
        except Exception:
            time.sleep(3)
            continue
        z = d.get("status") or ""
        if z in ("done", "error", "cancelled"):
            return z, time.time() - t0
        time.sleep(3)
    return "zeitueberschreitung", time.time() - t0


def chat_einmal(basis, schluessel, modell, frage):
    """Der Streaming-Weg - genau der, den die Oberflaeche benutzt.

    Gibt den zusammengesetzten TEXT zurueck, nicht nur die Byteszahl: Ein
    Fehlerpaket ist auch ein paar hundert Bytes lang. Wer nur zaehlt, haelt
    einen stillen Ausfall fuer Arbeit."""
    rumpf = json.dumps({"model": modell, "web": False, "messages": [
        {"role": "system", "content": "Antworte in einem Satz."},
        {"role": "user", "content": frage}]}).encode()
    req = urllib.request.Request(basis + "/api/chat", data=rumpf,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + schluessel})
    text = []
    with urllib.request.urlopen(req, timeout=600) as r:
        for zeile in r:
            zeile = zeile.strip()
            if not zeile:
                continue
            try:
                d = json.loads(zeile.decode("utf-8", "replace"))
            except ValueError:
                continue
            if d.get("error"):
                raise RuntimeError(str(d["error"])[:200])
            # Das Format ist Ollamas: {"message": {"content": "..."}, "done": …}.
            # Auf der obersten Ebene steht KEIN content — wer dort sucht, haelt
            # jede Antwort fuer leer.
            stueck = (d.get("message") or {}).get("content", "")
            if isinstance(stueck, str):
                text.append(stueck)
    return "".join(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stunden", type=float, default=4.0, help="harte Grenze")
    ap.add_argument("--port", type=int, default=3093)
    ap.add_argument("--modell", default="", help="leer = Voreinstellung der Instanz")
    a = ap.parse_args(argv)
    grenze = a.stunden * 3600
    signal.signal(signal.SIGINT, _abbrechen)
    signal.signal(signal.SIGTERM, _abbrechen)

    ablage = tempfile.mkdtemp(prefix="dowos-dauerlast-")
    speicher = os.path.join(ablage, "storage")
    os.makedirs(speicher)
    bericht_pfad = os.path.join(ablage, "verlauf.json")
    basis = "http://127.0.0.1:%d" % a.port
    umgebung = dict(os.environ, PORT=str(a.port), STORAGE_DIR=speicher)
    log = open(os.path.join(ablage, "server.log"), "w")
    proc = subprocess.Popen([sys.executable, "server.py"], cwd=APP,
                            env=umgebung, stdout=log, stderr=subprocess.STDOUT)
    melden("Server PID %d auf Port %d · Speicher %s" % (proc.pid, a.port, ablage))
    melden("Grenze %.1f h. Abbruch mit Strg-C beendet sauber nach dem Durchgang."
           % a.stunden)

    verlauf, fehler = [], []
    try:
        # --- hochfahren -------------------------------------------------
        schluessel = ""
        for _ in range(90):
            time.sleep(1)
            try:
                with open(os.path.join(ablage, "server.log")) as f:
                    for zeile in f:
                        if "dow_" in zeile:
                            teil = zeile[zeile.index("dow_"):].split()[0]
                            schluessel = teil.strip().strip(".,")
                            break
            except Exception:
                pass
            if schluessel:
                break
        if not schluessel:
            melden("Kein Zugangsschlüssel im Protokoll gefunden — Abbruch.")
            return 1
        for _ in range(60):
            try:
                hole(basis, "/api/health", schluessel, frist=5)
                break
            except Exception:
                time.sleep(1)
        else:
            melden("Server kam nicht hoch — Abbruch.")
            return 1
        hole(basis, "/api/settings", schluessel,
             {"SANDBOX_ENABLED": "1", "EINRICHTUNG_FERTIG": "1"})
        gesund = hole(basis, "/api/health", schluessel)
        modell = a.modell or ""
        skills = hole(basis, "/api/skills", schluessel)
        skill_id = ""
        for s in (skills if isinstance(skills, list) else skills.get("skills", [])):
            if "Zusammenfassen" in (s.get("name") or ""):
                skill_id = s.get("id")
                break
        melden("Bereit: %s Modelle, Sandbox %s, Skill %s"
               % (gesund.get("models"), gesund.get("sandbox_enabled"),
                  skill_id[:8] or "(keiner)"))

        # --- Arbeitsschichten -------------------------------------------
        runde, vorher_dateien, letzte_antwort = 0, 0, ""
        while time.time() - START < grenze and not _ENDE["jetzt"]:
            runde += 1
            t0 = time.time()
            schritte = {}
            try:
                antwort = chat_einmal(
                    basis, schluessel, modell,
                    "Nenne eine Primzahl zwischen %d und %d." % (runde, runde + 50))
                schritte["chat"] = len(antwort)
                letzte_antwort = antwort[:120]
                # Eine LEERE Antwort ist ein Ausfall, auch ohne Fehlermeldung.
                # Eine kurze ist keiner: Auf "Nenne eine Primzahl" ist "47"
                # die richtige Antwort. Eine Mindestlaenge von 5 Zeichen
                # zaehlte sie als Fehler — der erste Lauf meldete deshalb in
                # jeder Runde einen Ausfall, den es nicht gab.
                if not antwort.strip():
                    fehler.append("Runde %d Chat: leere Antwort (%r)"
                                  % (runde, antwort[:60]))
            except Exception as e:
                fehler.append("Runde %d Chat: %s" % (runde, str(e)[:120]))
                schritte["chat"] = -1
                letzte_antwort = ""

            if skill_id:
                try:
                    r = hole(basis, "/api/skills/%s/run" % skill_id, schluessel,
                             {"input": "Dive on Wide ist ein lokaler Agenten-Arbeitsplatz. "
                                       "Runde %d." % runde})
                    z, sek = warten_auf_lauf(basis, schluessel, r.get("run_id", ""))
                    schritte["skill"] = "%s/%.0fs" % (z, sek)
                    if z != "done":
                        fehler.append("Runde %d Skill: %s" % (runde, z))
                except Exception as e:
                    fehler.append("Runde %d Skill: %s" % (runde, str(e)[:120]))
                    schritte["skill"] = "fehler"

            try:
                r = hole(basis, "/api/sandbox/agent", schluessel,
                         {"task": "Schreibe rechne%d.py mit einer Funktion "
                                  "verdopple(x), die x*2 zurueckgibt, und pruefe "
                                  "sie mit einem assert." % runde,
                          "workspace": "last", "iterations": 2})
                z, sek = warten_auf_lauf(basis, schluessel, r.get("run_id", ""))
                schritte["agent"] = "%s/%.0fs" % (z, sek)
                if z != "done":
                    fehler.append("Runde %d Agent: %s" % (runde, z))
            except Exception as e:
                fehler.append("Runde %d Agent: %s" % (runde, str(e)[:120]))
                schritte["agent"] = "fehler"

            try:
                art = hole(basis, "/api/artifacts", schluessel,
                           {"filename": "last%d.md" % runde, "title": "Last %d" % runde,
                            "content": "# Runde %d\n\n%s" % (runde, "x" * 500)})
                hole(basis, "/api/artifacts/" + art.get("id", ""), schluessel)
                req = urllib.request.Request(
                    basis + "/api/artifacts/" + art.get("id", ""), method="DELETE",
                    headers={"Authorization": "Bearer " + schluessel})
                urllib.request.urlopen(req, timeout=60).read()
                schritte["artefakt"] = "ok"
            except Exception as e:
                fehler.append("Runde %d Artefakt: %s" % (runde, str(e)[:120]))
                schritte["artefakt"] = "fehler"

            if proc.poll() is not None:
                melden("SERVER GESTORBEN (Rückgabe %s) in Runde %d"
                       % (proc.returncode, runde))
                fehler.append("Server gestorben in Runde %d" % runde)
                break

            # Der Beleg, dass wirklich gearbeitet wurde: Dateien im
            # Arbeitsordner des Agenten. Eine Runde, die "done" meldet, aber
            # nichts hinterlaesst, ist kein Erfolg, sondern ein stiller Ausfall.
            arbeitsordner = os.path.join(speicher, "workspaces", "last")
            try:
                dateien = len([x for x in os.listdir(arbeitsordner)
                               if os.path.isfile(os.path.join(arbeitsordner, x))])
            except Exception:
                dateien = 0
            if runde > 1 and dateien <= vorher_dateien:
                fehler.append("Runde %d: der Agent hinterliess keine neue Datei "
                              "(%d unveraendert)" % (runde, dateien))
            vorher_dateien = dateien
            rss, fds, threads = messwerte(proc.pid)
            db = os.path.join(speicher, "dowos.db")
            db_mb = os.path.getsize(db) / (1024.0 ** 2) if os.path.exists(db) else None
            try:
                posteingang = hole(basis, "/api/notifications", schluessel, frist=60)
                nachrichten = len(posteingang if isinstance(posteingang, list)
                                  else posteingang.get("notifications", []))
            except Exception:
                nachrichten = None
            eintrag = {"runde": runde, "t_h": (time.time() - START) / 3600.0,
                       "sekunden": round(time.time() - t0, 1),
                       "rss_mb": round(rss, 1) if rss else None,
                       "fds": fds, "threads": threads,
                       "db_mb": round(db_mb, 2) if db_mb else None,
                       "inbox": nachrichten, "fehler": len(fehler),
                       "agent_dateien": dateien, "chat_probe": letzte_antwort,
                       "schritte": schritte}
            verlauf.append(eintrag)
            melden("Runde %3d in %4.0fs · RSS %6s MB · FDs %4s · Threads %3s "
                   "· DB %5s MB · Inbox %4s · Dateien %3d · Fehler %d · %s"
                   % (runde, eintrag["sekunden"], eintrag["rss_mb"], fds, threads,
                      eintrag["db_mb"], nachrichten, dateien, len(fehler),
                      " ".join("%s=%s" % kv for kv in schritte.items())))
            if runde == 1 or runde % 20 == 0:
                melden("    Probe der Modellantwort: %r" % letzte_antwort[:90])
            with open(bericht_pfad, "w") as f:
                json.dump(verlauf, f, ensure_ascii=False, indent=1)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        log.close()

    # --- Bericht --------------------------------------------------------
    # Der Bericht geht ZUSAETZLICH in den eigenen Wegwerf-Ordner. Am
    # 23.09.2026 schrieb ein abgebrochener Vorlauf seinen Abschlussbericht in
    # dieselbe Protokolldatei, die der Nachfolger schon geleert hatte — die
    # Zeilen landeten mitten im neuen Lauf, und der Bericht eines Laufs, den
    # ich selbst beendet hatte, sah aus wie ein Absturz des neuen.
    zeilen_bericht = []

    def sag(text=""):
        zeilen_bericht.append(text)
        print(text, flush=True)

    sag("\n=== Dauerbetrieb unter Last ===")
    sag("Dauer %.2f h · %d Runden · %d Fehler" % ((time.time() - START) / 3600.0,
                                                       len(verlauf), len(fehler)))
    if not verlauf:
        sag("Keine vollstaendige Runde — ohne Messpunkte keine Aussage.")
        return 1
    for feld, name, einheit in (("rss_mb", "RSS", "MB"), ("fds", "Dateizeiger", ""),
                                ("threads", "Threads", ""), ("db_mb", "Datenbank", "MB"),
                                ("inbox", "Inbox", ""),
                                ("agent_dateien", "Agent-Dateien", "")):
        werte = [v[feld] for v in verlauf if v.get(feld) is not None]
        if not werte:
            continue
        anfang = sum(werte[:3]) / len(werte[:3])
        ende = sum(werte[-3:]) / len(werte[-3:])
        wuchs = ende - anfang
        prozent = (100 * wuchs / anfang) if anfang else 0
        sag("%-12s %8.1f %-2s -> %8.1f %-2s  (%+.1f, %+.0f %%)  Hoechstwert %s"
              % (name, anfang, einheit, ende, einheit, wuchs, prozent, max(werte)))
    sag("\nEine Datenbank, die waechst, ist KEIN Leck — sie sammelt Arbeit.")
    sag("Threads und Dateizeiger duerfen dagegen nicht mitwachsen: Sie sind je")
    sag("Lauf da und muessen danach wieder weg sein.")
    if fehler:
        sag("\nFehler (%d), die ersten zehn:" % len(fehler))
        for f in fehler[:10]:
            sag("  ! %s" % f)
    sag("\nVerlauf: %s" % bericht_pfad)
    mit_bericht = os.path.join(ablage, "bericht.txt")
    with open(mit_bericht, "w", encoding="utf-8") as f:
        f.write("\n".join(zeilen_bericht) + "\n")
    print("Bericht:  %s" % mit_bericht, flush=True)
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
