"""Dive on Wide Mesh — ein Modell über mehrere Geräte verteilen.

WAS DOW.OS HIER TUT — UND WAS AUSDRÜCKLICH NICHT
------------------------------------------------
Ein Sprachmodell über Geräte aufzuteilen heißt: Jedes Gerät hält einige
Schichten im Speicher, und die Zwischenergebnisse wandern für JEDES Token
einmal durch die ganze Kette. Das ist Tensor-Arbeit, und die gehört nicht in
reines Python — dort wäre sie um Größenordnungen zu langsam.

Den Mechanismus dafür gibt es bereits: **llama.cpp mit RPC-Rückseite**. Man
startet auf jedem Gerät einen `rpc-server`, und ein Hauptprozess verteilt die
Schichten darauf. Dive on Wide erfindet das nicht neu. Seine Aufgabe ist die, die
sonst niemand übernimmt:

    * herausfinden, WELCHE Geräte mitrechnen können und mit wie viel Speicher
    * einen Schichtplan rechnen, der zu diesen Kapazitäten passt
    * den Hauptprozess mit dem richtigen Aufruf starten
    * das Ergebnis als ganz normales Modell in Dive on Wide anbieten
    * und ehrlich sagen, was fehlt, statt es zu behaupten

ERPROBUNGSSTAND: ES LÄUFT
-------------------------
Das ist nachgemessen, nicht behauptet. Auf dem Entwicklungsrechner wurde
llama.cpp mit `-DGGML_RPC=ON` gebaut, zwei Rechenknoten gestartet und ein
Modell darüber aufgeteilt: 40 Token in 0,88 Sekunden. Der entscheidende
Gegenbeweis: **Wird einer der beiden Rechenknoten abgeschaltet, stürzt die
Inferenz ab** — das Modell hängt also wirklich an beiden, es lief nicht
heimlich alles lokal.

Zwei Dinge, die dabei Zeit gekostet haben und deshalb hier stehen:

* **Das Programm heißt `ggml-rpc-server`,** nicht `rpc-server` (so hieß es
  früher). Wer nach dem alten Namen sucht, findet nichts und hält das Feature
  für unmöglich.
* **Auf macOS muss der Rechenknoten auf Metal festgenagelt werden** (`-d MTL0`).
  Ohne das greift er sich den BLAS-Rücken, und der kennt `RMS_NORM` nicht — er
  bricht mitten im ersten Durchlauf ab, und der Hauptprozess meldet nur
  „Remote RPC server crashed or returned malformed response". Diese Meldung
  sagt einem nicht, dass man ein Gerät auswählen muss.

Was Homebrew ausliefert, ist weiterhin OHNE RPC übersetzt. Die Diagnose sagt
das und nennt den Bauweg.
"""

import os
import platform
import shutil
import subprocess

# Eine Schicht braucht grob (Modellgröße / Schichtzahl) an Speicher. Dazu
# kommt der Zwischenspeicher für den Kontext, der beim Hauptprozess liegt.
KOPF_ZUSCHLAG_GB = 2.0        # Kontextspeicher und Verwaltung beim Hauptprozess
MIN_JE_KNOTEN_GB = 0.5        # darunter lohnt der Netzverkehr nicht


class VerteilungFehler(ValueError):
    """Der Plan geht nicht auf — mit Begründung."""


# Der Rechenknoten hiess frueher `rpc-server` und heisst heute
# `ggml-rpc-server`. Beide Namen werden gesucht — wer nur nach dem alten
# sucht, findet auf einem frischen Bau nichts und haelt das Feature fuer
# unmoeglich. Genau das ist hier passiert.
RPC_NAMEN = ("ggml-rpc-server", "rpc-server")


# Wohin `werkzeuge/llamacpp_rpc_bauen.sh` den fertigen Bau legt. Dort wird
# auch gesucht — sonst muesste jeder nach dem Bauen von Hand am PATH schrauben,
# und genau daran scheitert eine Einrichtung, die sonst funktioniert haette.
EIGENER_ORT = os.path.expanduser("~/.dowos/llama-rpc/bin")


def llama_cpp_anleitung(system=None, maschine=None):
    """Wie man auf DIESEM Betriebssystem zu llama.cpp mit Rechenknoten kommt.

    27.09.2026, erster Test auf einem Windows-PC: Der Nutzer bekam dort
    „brew install llama.cpp“ und ein Mac-Bauskript zu lesen. Die offiziellen
    Pakete (github.com/ggml-org/llama.cpp/releases) sind fuer alle drei Systeme
    mit GGML_RPC=ON uebersetzt und enthalten Rechenknoten, llama-server und
    llama-quantize — bauen muss niemand."""
    system = system or platform.system()
    # Die Architektur zaehlt mit: In der ARM-Linux-VM (27.09.2026) empfahl der
    # Hinweis „ubuntu-x64“ — das Paket startet dort gar nicht.
    arm = (maschine or platform.machine()).lower() in ("arm64", "aarch64")
    quelle = "github.com/ggml-org/llama.cpp/releases (neuestes Release)"
    if system == "Windows" and arm:
        return ("Offizielles llama.cpp für Windows auf ARM laden: %s → „llama-…-bin-win-cpu-arm64.zip“ "
                "(mit Nvidia-Grafik „…-bin-win-cuda-13.4-arm64.zip“ und „cudart-llama-bin-win-cuda-13.4-arm64.zip“). "
                "Alles nach %s entpacken und Dive on Wide neu starten. Beim ersten Start fragt die "
                "Windows-Firewall — nur „Private Netzwerke“ erlauben." % (quelle, EIGENER_ORT))
    if system == "Windows":
        return ("Offizielles llama.cpp für Windows laden: %s → „llama-…-bin-win-cpu-x64.zip“; "
                "mit Nvidia-Grafikkarte stattdessen „…-bin-win-cuda-12.4-x64.zip“ und dazu "
                "„cudart-llama-bin-win-cuda-12.4-x64.zip“, mit AMD- oder Intel-Grafik „…-bin-win-vulkan-x64.zip“. "
                "Alles nach %s entpacken und Dive on Wide neu starten. Beim ersten Start fragt die "
                "Windows-Firewall — nur „Private Netzwerke“ erlauben." % (quelle, EIGENER_ORT))
    if system == "Linux" and arm:
        return ("Offizielles llama.cpp für Linux auf ARM laden: %s → „llama-…-bin-ubuntu-arm64.tar.gz“; "
                "mit Grafik „…-bin-ubuntu-vulkan-arm64.tar.gz“, mit Nvidia „…-bin-ubuntu-cuda-13.4-arm64.tar.gz“. "
                "Die Programme daraus nach %s entpacken, Dive on Wide neu starten." % (quelle, EIGENER_ORT))
    if system == "Linux":
        return ("Offizielles llama.cpp für Linux laden: %s → „llama-…-bin-ubuntu-x64.tar.gz“; "
                "mit Nvidia-Grafikkarte „…-bin-ubuntu-cuda-12.8-x64.tar.gz“, mit anderer Grafik "
                "„…-bin-ubuntu-vulkan-x64.tar.gz“. Die Programme daraus nach %s entpacken, "
                "Dive on Wide neu starten." % (quelle, EIGENER_ORT))
    return ("Das llama.cpp aus Homebrew kann KEIN RPC. Offizielles Paket laden: %s → "
            "„llama-…-bin-macos-arm64.tar.gz“ (Apple-Chip) bzw. „…-bin-macos-x64.tar.gz“ (Intel). "
            "Die Programme daraus nach %s entpacken, dann einmal im Terminal "
            "„xattr -dr com.apple.quarantine %s“ (sonst blockiert macOS die heruntergeladenen "
            "Programme) und Dive on Wide neu starten." % (quelle, EIGENER_ORT, EIGENER_ORT))


def rpc_programm():
    return _finden(RPC_NAMEN)


def fuehrer_programm():
    """Der Hauptprozess, der die Kette anführt."""
    return _finden(("llama-server",))


def _finden(namen):
    """Erst am eigenen Ort, dann auf dem PATH.

    Der eigene Ort zuerst: Wer llama.cpp selbst gebaut hat, will DIESEN Bau —
    und nicht den aus Homebrew, der kein RPC kann und sonst gewinnen würde,
    weil er im PATH weiter vorn steht."""
    # Unter Windows heissen die Programme „….exe“ (27.09.2026: am eigenen Ort
    # haette Dive on Wide sie sonst nie gefunden, nur ueber den PATH).
    endungen = (".exe", "") if os.name == "nt" else ("",)
    for name in namen:
        for endung in endungen:
            pfad = os.path.join(EIGENER_ORT, name + endung)
            if os.path.isfile(pfad) and os.access(pfad, os.X_OK):
                return pfad
    for name in namen:
        pfad = shutil.which(name)
        if pfad:
            return pfad
    return ""


def rpc_lage():
    """Kann dieses Gerät bei verteilter Inferenz mitmachen?

    Prüft nach, statt anzunehmen. Beide Teile werden gebraucht: der
    Rechenknoten, um selbst Schichten zu halten, und ein `llama-server` MIT
    `--rpc`, um die Kette anzuführen. Homebrew liefert llama.cpp ohne RPC —
    das ist der häufigste Grund, warum es nicht geht, und die Meldung sagt es."""
    rpc = rpc_programm()
    server = fuehrer_programm()
    kann_fuehren = False
    if server:
        try:
            hilfe = subprocess.run([server, "--help"], capture_output=True,
                                   timeout=15).stdout.decode("utf-8", "replace")
            kann_fuehren = "--rpc" in hilfe
        except Exception:
            kann_fuehren = False
    lage = {
        "rpc_server": rpc or "",
        "llama_server": server or "",
        "kann_mitrechnen": bool(rpc),
        "kann_fuehren": kann_fuehren,
        "hinweise": [],
    }
    if not (rpc and kann_fuehren):
        fehlt = []
        if not rpc:
            fehlt.append("der Rechenknoten (%s)" % " oder ".join(RPC_NAMEN))
        if not server:
            fehlt.append("llama-server")
        elif not kann_fuehren:
            fehlt.append("ein llama-server MIT RPC (das vorhandene kennt --rpc nicht)")
        lage["hinweise"].append(
            "Es fehlt %s. %s Wer selbst baut: cmake -B build -DGGML_RPC=ON, dann "
            "cmake --build build --target ggml-rpc-server llama-server."
            % (" und ".join(fehlt), llama_cpp_anleitung()))
    lage["bereit"] = bool(rpc and kann_fuehren)
    return lage


def plan_erstellen(modell_gb, schichten, knoten, kopf_gb=KOPF_ZUSCHLAG_GB):
    """Verteilt die Schichten eines Modells auf die verfügbaren Geräte.

    `knoten` ist eine Liste von {"id", "adresse", "gb"} — was jedes Gerät
    beisteuert. Der erste Eintrag ist der Hauptprozess; er trägt zusätzlich
    den Kontextspeicher und bekommt deshalb weniger Schichten ab.

    Die Verteilung erfolgt nach Kapazitätsanteil, nicht gleichmäßig: Ein Gerät
    mit 32 GB soll mehr tragen als eins mit 4 GB. Gleichmäßig aufzuteilen
    hieße, sich am schwächsten Gerät zu orientieren und den Rest zu verschenken.
    """
    if modell_gb <= 0 or schichten <= 0:
        raise VerteilungFehler("Modellgröße und Schichtzahl müssen positiv sein.")
    if not knoten:
        raise VerteilungFehler("Kein Gerät verfügbar.")
    brauchbar = [k for k in knoten if float(k.get("gb") or 0) >= MIN_JE_KNOTEN_GB]
    if not brauchbar:
        raise VerteilungFehler(
            "Kein Gerät steuert mindestens %.1f GB bei — darunter kostet der "
            "Netzverkehr mehr, als das Gerät beiträgt." % MIN_JE_KNOTEN_GB)
    # Der Hauptprozess braucht zusätzlich Platz für den Kontext.
    frei = []
    for i, k in enumerate(brauchbar):
        gb = float(k["gb"]) - (kopf_gb if i == 0 else 0.0)
        frei.append(max(0.0, gb))
    gesamt = sum(frei)
    if gesamt < modell_gb:
        raise VerteilungFehler(
            "Zusammen stehen %.1f GB bereit, das Modell braucht %.1f GB. "
            "Es fehlen %.1f GB — ein kleineres Modell wählen oder ein Gerät "
            "mehr dazunehmen." % (gesamt, modell_gb, modell_gb - gesamt))

    # Schichten nach Kapazitätsanteil, aber nie mehr als das Gerät trägt.
    je_schicht = modell_gb / float(schichten)
    plan, vergeben = [], 0
    for i, k in enumerate(brauchbar):
        anteil = frei[i] / gesamt
        anzahl = int(round(schichten * anteil))
        moeglich = int(frei[i] / je_schicht)
        anzahl = max(0, min(anzahl, moeglich))
        plan.append({"id": k.get("id", ""), "adresse": k.get("adresse", ""),
                     "gb_frei": round(frei[i], 2), "schichten": anzahl,
                     "fuehrt": i == 0})
        vergeben += anzahl
    # Rundungsreste auf die Geräte mit dem meisten Luft verteilen.
    i = 0
    while vergeben < schichten and i < len(plan) * 4:
        eintrag = max(plan, key=lambda p: p["gb_frei"] - p["schichten"] * je_schicht)
        if (eintrag["schichten"] + 1) * je_schicht <= eintrag["gb_frei"]:
            eintrag["schichten"] += 1
            vergeben += 1
        else:
            break
        i += 1
    while vergeben > schichten:
        eintrag = max(plan, key=lambda p: p["schichten"])
        eintrag["schichten"] -= 1
        vergeben -= 1
    if vergeben < schichten:
        raise VerteilungFehler(
            "Nur %d von %d Schichten unterzubringen. Der Rest passt nirgends "
            "mehr hinein." % (vergeben, schichten))
    return {"modell_gb": round(modell_gb, 2), "schichten": schichten,
            "knoten": [p for p in plan if p["schichten"] > 0],
            "ungenutzt": [p for p in plan if p["schichten"] == 0]}


def erwartete_geschwindigkeit(plan, bandbreite_gb_s=50.0, hop_ms=1.0):
    """Was der Plan grob leisten wird — nach der Rechnung aus ARCHITEKTUR.md.

    Engpass ist die Speicherbandbreite, nicht die Rechenleistung: Je Token
    muss jedes Gewicht einmal gelesen werden. Dazu kommt je Stufe ein
    Netzsprung. Bewusst eine Schätzung, die als solche benannt ist."""
    stufen = len(plan["knoten"])
    if not stufen:
        return {"token_pro_sekunde": 0.0, "sekunden_je_token": 0.0, "stufen": 0}
    je_schicht = plan["modell_gb"] / float(plan["schichten"])
    lesezeit = sum(p["schichten"] * je_schicht for p in plan["knoten"]) / bandbreite_gb_s
    netzzeit = stufen * hop_ms / 1000.0
    dauer = lesezeit + netzzeit
    return {"stufen": stufen,
            "sekunden_je_token": round(dauer, 3),
            "token_pro_sekunde": round(1.0 / dauer, 2) if dauer else 0.0,
            "anteil_netz": round(netzzeit / dauer, 3) if dauer else 0.0,
            "geschaetzt": True}


def modell_datei_finden(name):
    """Den GGUF-Pfad zu einem Ollama-Modellnamen auflösen.

    llama.cpp braucht eine Datei, Ollama denkt in Namen. `ollama show
    --modelfile` nennt die Blobs mit `FROM` — der erste ist das Gewicht, die
    weiteren sind Projektoren oder Adapter. Der erste ist der, den wir wollen.

    Das ist bewusst der Weg über Ollamas eigene Auskunft und nicht über ein
    geratenes Verzeichnis: Wo Ollama seine Blobs ablegt, ist seine Sache und
    ändert sich."""
    if os.path.exists(str(name)):
        return str(name)                    # es ist schon ein Pfad
    if not shutil.which("ollama"):
        raise VerteilungFehler(
            "Ollama ist nicht da, und %r ist auch kein Dateipfad. Für ein "
            "verteiltes Modell braucht llama.cpp eine .gguf-Datei." % name)
    try:
        r = subprocess.run(["ollama", "show", "--modelfile", str(name)],
                           capture_output=True, timeout=30)
    except Exception as e:
        raise VerteilungFehler("Ollama antwortet nicht: %s" % e)
    if r.returncode != 0:
        raise VerteilungFehler(
            "Ollama kennt das Modell %r nicht: %s"
            % (name, r.stderr.decode("utf-8", "replace").strip()[:160]))
    for zeile in r.stdout.decode("utf-8", "replace").splitlines():
        zeile = zeile.strip()
        if zeile.startswith("FROM ") and not zeile.startswith("#"):
            pfad = zeile[5:].strip()
            if os.path.exists(pfad):
                return pfad
    raise VerteilungFehler(
        "Zu %r ließ sich keine Modelldatei finden. `ollama show --modelfile %s` "
        "nennt keinen vorhandenen Pfad." % (name, name))


def modell_masse_lesen(pfad):
    """Größe und Schichtzahl aus der GGUF-Datei selbst lesen.

    Bisher wurde beides aus dem NAMEN geraten („70b" → 35 GB, 80 Schichten).
    Das geht oft gut und manchmal daneben — bei einem umbenannten oder anders
    quantisierten Modell liegt man deutlich falsch, und der Plan verteilt dann
    Schichten, die es gar nicht gibt. Die Datei weiß es genau.

    GGUF-Kopf: Magie, Version, Tensorzahl, Schlüsselzahl, dann die Schlüssel
    als Paare. Gesucht wird `*.block_count` — so heißt die Schichtzahl dort,
    unabhängig von der Modellfamilie."""
    groesse_gb = os.path.getsize(pfad) / (1024 ** 3)
    schichten = 0
    try:
        with open(pfad, "rb") as f:
            if f.read(4) != b"GGUF":
                raise ValueError("keine GGUF-Datei")
            import struct as _s
            (version,) = _s.unpack("<I", f.read(4))
            if version < 2:
                raise ValueError("GGUF-Version %d wird nicht gelesen" % version)
            f.read(8)                                  # Tensorzahl
            (anzahl,) = _s.unpack("<Q", f.read(8))     # Schluesselzahl
            for _ in range(min(anzahl, 2000)):
                (laenge,) = _s.unpack("<Q", f.read(8))
                if laenge > 4096:
                    break                              # unplausibel, abbrechen
                schluessel = f.read(laenge).decode("utf-8", "replace")
                (typ,) = _s.unpack("<I", f.read(4))
                wert = _gguf_wert_lesen(f, typ)
                if schluessel.endswith(".block_count"):
                    schichten = int(wert)
                    break
    except Exception:
        pass          # Die Datei ist da, nur der Kopf nicht lesbar — dann raten.
    return {"gb": round(groesse_gb, 2), "schichten": schichten,
            "gelesen": schichten > 0}


_GGUF_FEST = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i",
              6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


def _gguf_wert_lesen(f, typ):
    """Einen GGUF-Wert überspringen oder lesen — je nach Typ."""
    import struct as _s
    if typ in _GGUF_FEST:
        form = _GGUF_FEST[typ]
        return _s.unpack(form, f.read(_s.calcsize(form)))[0]
    if typ == 8:                                        # Zeichenkette
        (laenge,) = _s.unpack("<Q", f.read(8))
        return f.read(laenge).decode("utf-8", "replace")
    if typ == 9:                                        # Feld
        (untertyp,) = _s.unpack("<I", f.read(4))
        (anzahl,) = _s.unpack("<Q", f.read(8))
        for _ in range(anzahl):
            _gguf_wert_lesen(f, untertyp)
        return None
    raise ValueError("unbekannter GGUF-Typ %d" % typ)


def rechenknoten_aufruf(port=50052, faeden=None, geraet=None, wirt="127.0.0.1"):
    """Der Aufruf, mit dem dieses Gerät Schichten für andere hält.

    `geraet` ist der Punkt, an dem es sonst scheitert. Der Rechenknoten sucht
    sich sein Rechenwerk selbst aus — und greift auf einem Mac gern BLAS
    (Accelerate) statt Metal. BLAS kennt `RMS_NORM` nicht und bricht mitten im
    ersten Durchlauf ab. Der Hauptprozess meldet dann nur „Remote RPC server
    crashed or returned malformed response", und diese Meldung sagt einem
    nicht, dass man ein Gerät auswählen muss. Also wird es hier ausgewählt.

    Auf macOS heißt die GPU `MTL0`; `llama-server --list-devices` nennt die
    vorhandenen. Wer nichts angibt, bekommt auf einem Mac die Voreinstellung
    MTL0 — auf anderen Systemen die Wahl von llama.cpp, die dort meist stimmt.

    `wirt` ist der zweite Punkt, an dem es sonst scheitert — und der heiklere.
    Voreingestellt ist `127.0.0.1`: Dann ist der Rechenknoten NUR von diesem
    Gerät erreichbar. Für ein verteiltes Modell muss er aber von anderen
    Geräten erreicht werden, also braucht er die LAN-Adresse. Mit `127.0.0.1`
    kündigt das Mesh eine Adresse an, unter der niemand antwortet, und der
    Hauptprozess bricht mit „Failed to connect" ab.

    **Und das ist eine Entscheidung mit Preis.** Die RPC-Schnittstelle von
    llama.cpp kennt KEINE Anmeldung. Wer den Knoten erreicht, kann ihn
    benutzen und zum Absturz bringen — llama.cpp sagt das selbst. Deshalb wird
    hier NICHT auf 0.0.0.0 gebunden, sondern auf genau die eine Adresse, unter
    der das Mesh diesen Knoten kennt. Und deshalb steht in der Oberfläche
    daneben, was das bedeutet."""
    programm = rpc_programm()
    if not programm:
        raise VerteilungFehler(
            "Kein Rechenknoten gefunden (gesucht: %s). %s"
            % (" oder ".join(RPC_NAMEN),
               " ".join(rpc_lage()["hinweise"]) or "Erst llama.cpp mit "
               "GGML_RPC=ON bauen."))
    if geraet is None and platform.system() == "Darwin":
        geraet = "MTL0"
    befehl = [programm, "-H", str(wirt), "-p", str(int(port))]
    if geraet:
        befehl += ["-d", geraet]
    if faeden:
        befehl += ["-t", str(int(faeden))]
    return befehl


def aufruf_bauen(plan, modell_pfad, port=8080, kontext=8192):
    """Der Aufruf, der die Kette anführt.

    llama.cpp erwartet die Rückseiten als Komma-Liste und die Zahl der
    Schichten, die überhaupt ausgelagert werden. Der Hauptprozess selbst
    steht NICHT in der --rpc-Liste; er ist der Aufrufer."""
    if not plan["knoten"]:
        raise VerteilungFehler("Der Plan enthält kein Gerät.")
    if not modell_pfad or not os.path.exists(modell_pfad):
        raise VerteilungFehler("Modelldatei nicht gefunden: %r" % modell_pfad)
    rueckseiten = [p["adresse"] for p in plan["knoten"] if not p["fuehrt"]]
    if not rueckseiten:
        raise VerteilungFehler(
            "Nur ein Gerät im Plan — dafür braucht es keine Verteilung.")
    for adresse in rueckseiten:
        if ":" not in str(adresse):
            raise VerteilungFehler(
                "Adresse %r hat keinen Port. Erwartet wird Rechner:Port." % adresse)
    # Die Aufteilung als Anteile — llama.cpp verteilt danach die Schichten.
    anteile = [str(p["schichten"]) for p in plan["knoten"]]
    return [
        "llama-server",
        "--model", modell_pfad,
        "--rpc", ",".join(rueckseiten),
        "--n-gpu-layers", str(plan["schichten"]),
        "--tensor-split", ",".join(anteile),
        "--ctx-size", str(int(kontext)),
        "--port", str(int(port)),
        "--host", "127.0.0.1",
    ]
