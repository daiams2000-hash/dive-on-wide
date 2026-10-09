#!/bin/bash
cd "$(dirname "$0")"
clear
case "${LC_ALL:-${LANG:-}}" in de*) DE=1 ;; *) DE= ;; esac
[ -n "$DE" ] && echo "  Dive on Wide wird gestartet …" || echo "  Starting Dive on Wide …"
if ! command -v python3 >/dev/null 2>&1; then
  [ -n "$DE" ] && echo "  ❌ python3 fehlt. Installieren mit:  xcode-select --install" \
               || echo "  ❌ python3 is missing. Install it with:  xcode-select --install"
  read -p "  [Enter]"; exit 1
fi
[ -f .env ] || cp .env.example .env 2>/dev/null
( sleep 2; open http://localhost:3000 ) &
python3 server.py
read -p "  [Enter]"
