# Dive on Wide in einer VM — der Schutzmodus

Stand 27.09.2026. **Mac mit Lima: erprobt** (Ubuntu 26.04, siehe `ABNAHME.md`, „Erster Lauf auf echtem Linux“). **Windows mit WSL2: nicht erprobt** — die Schritte sind geschrieben, nicht belegt.

## Wofür

Dive on Wide schützt Befehle des Agenten mit einer Sandbox (macOS: `sandbox-exec`, Linux: bubblewrap). Für **Computer-Use** und den **Browser-Agenten** gibt es keine solche Grenze: Sie bewegen echte Maus, echte Tastatur, einen echten Browser — mit deinen Anmeldungen. In einer VM erreicht der Agent nur, was in der VM liegt.

Der Preis: Computer-Use steuert dann den Bildschirm **der VM**, nicht deinen. Das passt für Aufgaben im Netz mit einem abgeschotteten Browser; es passt nicht, wenn der Agent deine eigenen Programme bedienen soll.

## Drei Regeln, ohne die die Wand eine Tür hat

1. **Keine Ordner freigeben.** Lima teilt sonst dein Heimatverzeichnis, WSL2 hängt alle Windows-Laufwerke beschreibbar unter `/mnt/c` ein.
2. **Auf dem Gastgeber `AUTH_REQUIRE_LOCAL=1`**, solange die VM läuft (Einstellungen → Zugänge → „Auch auf diesem Rechner Schlüssel verlangen“). Aus einer Lima-VM kommen Verbindungen beim Gastgeber als `127.0.0.1` an — ohne diese Einstellung ist das Dive on Wide des Gastgebers aus der VM heraus mit Besitzerrechten erreichbar (`SICHERHEIT.md`, Befund 11).
3. **Das Ollama des Gastgebers ist aus der VM ohne Anmeldung erreichbar** — gewollt, damit die VM rechnen kann, aber es kann auch Modelle laden und löschen. Wer das nicht will, betreibt Ollama in der VM (langsam: keine Grafikkarte) oder auf einem eigenen Rechner.

## Mac mit Lima (erprobt)

```bash
brew install lima
limactl create --name=dowos --cpus=4 --memory=4 --disk=20 --mount-none \
  --set '.portForwards=[{"guestPort":3000,"hostPort":3300}] | .containerd.system=false | .containerd.user=false' \
  template:ubuntu
limactl start dowos
limactl copy Dive on Wide-<version>-Linux.tar.gz dowos:~/
limactl shell dowos
```

In der VM:

```bash
tar -xzf DiveOnWide-*-Linux.tar.gz && cd DiveOnWide-*-Linux
python3 install.py --pruefen          # zeigt, was fehlt
sudo sh -c 'apt-get update -q && apt-get install -y bubblewrap'   # die Sandbox
sudo sh werkzeuge/bwrap_freischalten.sh                          # nur Ubuntu ab 24.04 (AppArmor)
./dowos-starten
```

Auf dem Mac `http://localhost:3300` öffnen. In der Einrichtung als Ollama-Adresse `http://host.lima.internal:11434` eintragen — die Modelle des Macs erscheinen, die Modellwahl richtet sich nach dem Speicher des Macs.

Download-Größen (gemessen): Lima 38 MB, Ubuntu-Abbild 941 MB, die VM belegt danach rund 2,5 GB. Lima lädt im Standard zusätzlich `nerdctl` (261 MB) — mit `.containerd…=false` wird es nicht eingerichtet, geladen wird es beim Anlegen trotzdem.

**Computer-Use in der VM:** Ein virtueller Bildschirm genügt.

```bash
sudo apt-get install -y xvfb xdotool maim
Xvfb :99 -screen 0 1280x800x24 &
DISPLAY=:99 ./dowos-starten
```

Für den vollständigen Lauf braucht es zusätzlich das Grounding-Modell (Einstellungen → Computer-Use) und ein bildfähiges Planungsmodell.

**Grenzen:** Das Mesh erreicht aus der VM keine Geräte im WLAN (die VM sitzt hinter NAT). Keine Grafikkarte in der VM — die Modelle rechnen auf dem Mac.

## Windows mit WSL2 (nicht erprobt)

```powershell
wsl --install -d Ubuntu
```

Dann in Ubuntu `/etc/wsl.conf` anlegen — **beide Abschnitte sind Pflicht**, sonst ist die VM keine Wand:

```ini
[automount]
enabled = false          # keine Windows-Laufwerke unter /mnt/c

[interop]
enabled = false          # Linux darf keine Windows-Programme (.exe) starten
appendWindowsPath = false
```

Danach in PowerShell `wsl --shutdown`, WSL neu öffnen. Weiter wie bei Lima ab `tar -xzf`. Ollama auf Windows ist aus WSL2 über die Adresse des Windows-Rechners erreichbar (im Standard-Netzmodus nicht über `localhost`); Ollama muss dafür auf `0.0.0.0` hören (`OLLAMA_HOST=0.0.0.0`) — **das öffnet es auch fürs WLAN**. Mit Nvidia-Karte kann Ollama stattdessen in WSL2 laufen (CUDA wird durchgereicht).

**Beim Außentest zu belegen:** dass `/mnt/c` wirklich fehlt, dass `cmd.exe` aus WSL nicht startet, und von wo Verbindungen aus WSL2 beim Dive on Wide auf Windows ankommen (Befund 11).
