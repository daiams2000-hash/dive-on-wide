"""Volldurchlauf: ALLE Pfade über ZWEI echte Instanzen.

Jede Wirkung wird auf der GEGENSEITE geprüft, nie beim Absender. Genau dafür
gibt es dieses Skript zusätzlich zum Testlauf: Zwölf Schreibpfade waren einmal
still kaputt, während der Testlauf grün blieb und jede Ansicht richtig aussah —
weil Ansichten nur lesen.

Startet beide Instanzen selbst (mit eigenem Temp-Speicher, nie dem echten
Ordner) und räumt sie hinterher weg.
"""
import atexit
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WURZEL = os.path.dirname(APP)
A, B = "http://localhost:3121", "http://localhost:3122"
gut, schlecht = [], []
_prozesse = []


def _starten(port):
    skript = (
        "import os,sys,tempfile\n"
        "sys.path.insert(0, %r)\n"
        "import server\n"
        "d = tempfile.mkdtemp(prefix='dowos-lauf-')\n"
        "import atexit, shutil\n"
        "atexit.register(shutil.rmtree, d, True)\n"
        "server.STORAGE_DIR = d\n"
        "server.DB_PATH = os.path.join(d, 'v.db')\n"
        "os.makedirs(os.path.join(d,'artifacts'), exist_ok=True)\n"
        "os.makedirs(os.path.join(d,'workspaces'), exist_ok=True)\n"
        "server.ENV['PORT'] = %r\n"
        "server.main()\n" % (APP, str(port))
    )
    # Eigener Mesh-Port: Die beiden Instanzen sollen einander finden und NUR
    # einander — echte Geraete im selben Netz (Besitzer testet gerade mit
    # einem zweiten Rechner) wuerden sonst jede Zahl verfaelschen.
    umgebung = dict(os.environ, DOWOS_MESH_PORT="47871")
    p = subprocess.Popen([sys.executable, "-c", skript], env=umgebung,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _prozesse.append(p)


def _aufraeumen():
    for p in _prozesse:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


atexit.register(_aufraeumen)


def p(name, bed, zusatz=""):
    (gut if bed else schlecht).append(name)
    print("  %s %s%s" % ("✓" if bed else "✗", name,
                         ("  — " + str(zusatz)) if zusatz and not bed else ""))


def ruf(basis, pfad, daten=None, frist=25):
    url = basis + pfad
    if daten is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(daten).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=frist) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"fehler": "HTTP %d" % e.code}
    except Exception as e:
        return {"fehler": str(e)}


for _port in (3121, 3122):
    _starten(_port)
time.sleep(6)

print("=" * 72)
print("VOLLDURCHLAUF — zwei Instanzen, Wirkung immer drüben geprüft")
print("=" * 72)

print("\n1) GRUNDZUSTAND")
for name, basis in (("A", A), ("B", B)):
    h = ruf(basis, "/api/health")
    p("%s antwortet" % name, "ok" in h or "models" in h, h)

print("\n2) PWA-HÜLLE (ohne Schlüssel erreichbar)")
for pfad in ("/manifest.webmanifest", "/sw.js", "/icon.svg"):
    try:
        with urllib.request.urlopen(A + pfad, timeout=8) as r:
            p("%s (HTTP %d)" % (pfad, r.status), r.status == 200)
    except Exception as e:
        p(pfad, False, e)

print("\n3) NETZ STARTEN UND FINDEN")
for basis in (A, B):
    ruf(basis, "/api/mesh/start", {"betriebsart": "klause"})
time.sleep(8)
la, lb = ruf(A, "/api/mesh"), ruf(B, "/api/mesh")
# Genau EINER, nicht "mindestens einer": Ein dritter Knoten im selben LAN
# verfaelscht jede Zahl danach. Die Meldung sagt deshalb, was sie gesehen hat
# — sonst sucht man den Fehler im Multicast, wo keiner ist. (Genau das ist
# einmal passiert: eine vergessene Testinstanz lief noch mit.)
def _nachbarn_pruefen(name, lage):
    n = lage.get("nachbarn")
    p(name, n == 1,
      "sah %s Nachbarn — bei mehr als einem läuft noch ein weiteres Dive on Wide "
      "im selben Netz auf dem Pruefport (lsof -i :47871 zeigt, welches)" % n if n != 1 else n)

_nachbarn_pruefen("A sieht B", la)
_nachbarn_pruefen("B sieht A", lb)
p("Compute beidseitig gleich",
  abs(la.get("compute_gesamt_gb", 0) - lb.get("compute_gesamt_gb", 0)) < 0.5)
# Zwei Faelle, und beide sind richtig:
#
#   * Es wird etwas angeboten -> nutzbar muss deutlich darunter liegen, weil
#     dreifache Redundanz zwei Drittel kostet.
#   * Es wird NICHTS angeboten -> dann muss auch nutzbar 0 sein.
#
# Der zweite Fall ist kein Ausfall, sondern der Statthalter bei der Arbeit: Auf
# einem Rechner mit wenig freiem Speicher gibt Dive on Wide nichts ab. Die alte
# Fassung prüfte stur `0 < 0` und meldete das als Fehler — auf einer Maschine,
# die gerade unter Last stand, war die Freigabe damit blockiert, obwohl sich
# das System exakt so verhielt, wie es soll.
_gesamt = la.get("compute_gesamt_gb", 0) or 0
_nutzbar = (la.get("modelle") or {}).get("nutzbar_gb", 99)
_grund = (la.get("beitrag") or {}).get("begruendung", "")
if _gesamt > 0:
    p("Redundanz eingerechnet (%.1f GB -> %.1f GB nutzbar)" % (_gesamt, _nutzbar),
      _nutzbar < _gesamt / 2.5)
else:
    p("Nichts angeboten, also auch nichts nutzbar", _nutzbar == 0,
      "Grund des Statthalters: %s" % (_grund or "unbekannt"))
    print("     (Dieses Gerät gibt gerade nichts ab — %s)" % (_grund or "kein Grund genannt"))
if la.get("nur_dieser_rechner"):
    print("     (Hinweis: Rundruf nur über Loopback — %s)" % la.get("wege_fehler"))

print("\n4) BOTE — Anker, Erkennung, versiegelte Nachricht")
ank = ruf(A, "/api/mesh/anker", {}).get("anker", "")
p("Anker erzeugt (%d Zeichen)" % len(ank), len(ank) == 39)
ruf(A, "/api/mesh/kontakt", {"name": "Ben", "anker": ank})
ruf(B, "/api/mesh/kontakt", {"name": "Anna", "anker": ank.lower().replace("-", " ")})
time.sleep(8)
ka = (ruf(A, "/api/mesh/kontakte").get("kontakte") or [{}])[0]
kb = (ruf(B, "/api/mesh/kontakte").get("kontakte") or [{}])[0]
p("A erkennt B am Anker", ka.get("online"), ka)
p("B erkennt A (abgetippte Schreibweise)", kb.get("online"), kb)
p("Sicherheitszahl beidseitig gleich",
  ka.get("sicherheitszahl") == kb.get("sicherheitszahl") and ka.get("sicherheitszahl"))
ruf(A, "/api/mesh/senden", {"name": "Ben", "text": "Volldurchlauf-Nachricht"})
time.sleep(2)
chat_b = ruf(B, "/api/mesh/chat?name=Anna").get("verlauf", [])
p("Nachricht kommt DRÜBEN an",
  any(n["text"] == "Volldurchlauf-Nachricht" for n in chat_b), chat_b)

print("\n5) FORUM — Faden verbreitet sich")
f = ruf(A, "/api/mesh/faden-neu", {"titel": "Durchlauf", "text": "Erster Beitrag",
                                   "stunden": 2})
fid = f.get("id", "")
p("Faden eröffnet", len(fid) == 40, f)
time.sleep(4)
faeden_b = ruf(B, "/api/mesh/faeden").get("faeden", [])
p("Faden ist DRÜBEN sichtbar", any(x["titel"] == "Durchlauf" for x in faeden_b),
  [x.get("titel") for x in faeden_b])
if fid:
    ruf(B, "/api/mesh/beitrag", {"id": fid, "text": "Antwort von drüben"})
    time.sleep(4)
    gelesen = ruf(A, "/api/mesh/faden?id=" + fid)
    p("Antwort kommt ZURÜCK",
      any(b["text"] == "Antwort von drüben" for b in gelesen.get("beitraege", [])),
      gelesen.get("beitraege"))
    ruf(A, "/api/mesh/faden-zu", {"id": fid})
    time.sleep(3)
    zu = ruf(B, "/api/mesh/faeden").get("faeden", [])
    p("Schließen wirkt DRÜBEN", any(x["id"] == fid and x["geschlossen"] for x in zu))

print("\n6) TICKET + QR")
t = ruf(A, "/api/mesh/ticket-neu", {})
p("Ticket kompakt (%d Zeichen)" % len(t.get("ticket", "")),
  150 < len(t.get("ticket", "")) < 260)
p("QR erzeugt (%d Byte)" % len(t.get("qr", "")), len(t.get("qr", "")) > 5000)
p("QR ist gültiges SVG",
  t.get("qr", "").startswith("<svg") and t.get("qr", "").endswith("</svg>"))

print("\n7) MODELLE — Netzanbieter")
prov = [x.get("type") for x in ruf(A, "/api/models").get("providers", [])]
p("Netzwerk-Anbieter erscheint", "mesh" in prov, prov)

print("\n8) VERLASSEN — nichts bleibt übrig")
ruf(A, "/api/mesh/stop", {})
time.sleep(1)
p("A ist draußen", not ruf(A, "/api/mesh").get("laeuft"))
p("Kontakte weg", ruf(A, "/api/mesh/kontakte").get("kontakte") == [])
p("Netzwerk-Anbieter verschwindet mit",
  "mesh" not in [x.get("type") for x in ruf(A, "/api/models").get("providers", [])])

print("\n" + "=" * 72)
print("%d bestanden, %d gescheitert" % (len(gut), len(schlecht)))
for n in schlecht:
    print("   GESCHEITERT:", n)
sys.exit(1 if schlecht else 0)
