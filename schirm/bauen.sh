#!/bin/bash
# Baut und startet den Wegwerf-Bildschirm für Computer-Use.
set -u
HIER="$(cd "$(dirname "$0")" && pwd)"
NAME="${1:-dowos-schirm}"
BILD="dowos/schirm"

rot() { printf "\033[31m%s\033[0m\n" "$1"; }
gruen() { printf "\033[32m%s\033[0m\n" "$1"; }

if ! command -v docker >/dev/null 2>&1; then
  rot "✗ Docker fehlt."
  echo "  Der eigene Bildschirm läuft in einem Container. Ohne Docker gibt es"
  echo "  ihn nicht — dann klickt der Agent auf deinem echten Schirm."
  echo "  macOS: Docker Desktop installieren."
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  rot "✗ Docker ist installiert, aber der Dienst antwortet nicht."
  echo "  Auf dem Mac: Docker Desktop starten, dann dieses Skript erneut."
  exit 1
fi

echo "  Baue $BILD …"
docker build -q -t "$BILD" "$HIER" >/dev/null || { rot "✗ Bauen fehlgeschlagen"; exit 1; }

docker rm -f "$NAME" >/dev/null 2>&1

# --network none      : kein Netz. Der Agent kann von dort nichts erreichen.
# --read-only         : das Abbild bleibt unveraendert; nur /tmp ist beschreibbar.
# --security-opt      : keine neuen Rechte, egal was drin passiert.
# --memory/--cpus     : er darf den Rechner nicht lahmlegen.
# KEIN Bind-Mount     : er sieht NICHTS vom Dateisystem des Wirts.
docker run -d --name "$NAME" \
  --network none \
  --read-only --tmpfs /tmp:rw,size=256m \
  --security-opt no-new-privileges \
  --memory 1g --cpus 1.5 \
  -e SCHIRM_BREITE="${SCHIRM_BREITE:-1280}" \
  -e SCHIRM_HOEHE="${SCHIRM_HOEHE:-800}" \
  -e SCHIRM_VNC="${SCHIRM_VNC:-0}" \
  "$BILD" >/dev/null || { rot "✗ Start fehlgeschlagen"; exit 1; }

for i in $(seq 1 60); do
  if docker exec -e DISPLAY=:99 "$NAME" xdpyinfo >/dev/null 2>&1; then
    gruen "✓ Bildschirm läuft im Container „$NAME“"
    echo
    echo "  Prüfen:    docker exec -e DISPLAY=:99 $NAME xdotool getdisplaygeometry"
    echo "  Zusehen:   SCHIRM_VNC=1 $0 $NAME   (dann Port 5900, nur localhost)"
    echo "  Beenden:   docker rm -f $NAME"
    echo
    echo "  In Dive on Wide: Einstellungen → Computer-Use → Bildschirm: „Container“."
    exit 0
  fi
  sleep 0.5
done
rot "✗ Der X-Server im Container kam nicht hoch."
docker logs "$NAME" 2>&1 | tail -20
exit 1
