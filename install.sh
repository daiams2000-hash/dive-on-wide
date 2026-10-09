#!/bin/sh
# Dive on Wide — Einstieg für macOS und Linux.
#
# Diese Datei tut absichtlich fast nichts: Sie prüft, ob Python 3 da ist, holt
# install.py und übergibt. Alles Weitere steht dort — in einer Datei, die man
# lesen kann, bevor man sie ausführt.
#
#     sh install.sh                 einrichten oder aktualisieren
#     sh install.sh --pruefen       nur nachsehen
#     sh install.sh --alles         auch verteiltes Rechnen einrichten
set -eu

QUELLE="${DOWOS_INSTALLER:-https://raw.githubusercontent.com/daiams2000-hash/dive-on-wide/main/install.py}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "  Python 3 fehlt — Dive on Wide ist ein Python-Programm."
  if [ "$(uname -s)" = "Darwin" ]; then
    echo "  Auf dem Mac:  xcode-select --install"
  else
    echo "  Auf Debian/Ubuntu:  sudo apt-get install python3"
    echo "  Auf Fedora:         sudo dnf install python3"
    echo "  Auf Arch:           sudo pacman -S python"
  fi
  exit 1
fi

# Liegt install.py daneben (aus einem Paket oder Clone)? Dann den nehmen —
# keinen Grund, etwas herunterzuladen, was schon da ist.
HIER="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$HIER/install.py" ]; then
  exec python3 "$HIER/install.py" "$@"
fi

TMP="$(mktemp -d)"
echo "  Hole den Installer …"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$QUELLE" -o "$TMP/install.py"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$TMP/install.py" "$QUELLE"
else
  echo "  Weder curl noch wget vorhanden."; exit 1
fi
echo "  Er liegt unter $TMP/install.py — du kannst ihn vorher lesen."
exec python3 "$TMP/install.py" "$@"
