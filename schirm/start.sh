#!/bin/bash
# Startet den Bildschirm im Container. Läuft als PID 1.
set -u
BREITE="${SCHIRM_BREITE:-1280}"
HOEHE="${SCHIRM_HOEHE:-800}"

# -screen 0 BxHx24: ein Bildschirm, 24 Bit Farbtiefe. -nolisten tcp, damit der
# X-Server nichts aus dem Netz annimmt — er soll nur von innen bedient werden.
Xvfb "$DISPLAY" -screen 0 "${BREITE}x${HOEHE}x24" -nolisten tcp &
for i in $(seq 1 50); do
  xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
  sleep 0.1
done

openbox &

# VNC nur, wenn ausdruecklich gewuenscht. Ein offener VNC-Port ohne Passwort
# waere eine Einladung — deshalb ist er standardmaessig aus, und wenn er an
# ist, horcht er nur auf der Container-Adresse.
if [ "${SCHIRM_VNC:-0}" = "1" ]; then
  x11vnc -display "$DISPLAY" -forever -shared -nopw -localhost -quiet &
  echo "  VNC an: docker port <container> 5900 — nur über localhost."
fi

echo "  Bildschirm bereit: ${BREITE}x${HOEHE} auf $DISPLAY"
# PID 1 muss laufen bleiben, sonst endet der Container sofort.
tail -f /dev/null
