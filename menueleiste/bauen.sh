#!/bin/bash
# Baut das Menüleisten-Symbol. Optional — Dive on Wide läuft ohne es vollständig.
#
# Ergebnis ist eine richtige .app, kein nacktes Programm: macOS zeigt ein
# Menüleisten-Symbol nur aus einem Programmbündel zuverlässig an, und nur so
# überlebt es einen Neustart des Terminals, aus dem es gestartet wurde.
set -u
HIER="$(cd "$(dirname "$0")" && pwd)"
ZIEL="$HIER/Dive on Wide Menü.app"

rot() { printf "\033[31m%s\033[0m\n" "$1"; }
gruen() { printf "\033[32m%s\033[0m\n" "$1"; }

if ! command -v swiftc >/dev/null 2>&1; then
  rot "✗ swiftc fehlt."
  echo "  Das Menü braucht die Xcode-Befehlszeilenwerkzeuge:"
  echo "      xcode-select --install"
  echo "  Danach dieses Skript erneut ausführen."
  echo
  echo "  Das ist KEIN Beinbruch: Dive on Wide läuft ohne das Menü vollständig."
  echo "  Das Symbol in der Ecke ist Zubehör, keine Voraussetzung."
  exit 1
fi

rm -rf "$ZIEL"
mkdir -p "$ZIEL/Contents/MacOS"

cat > "$ZIEL/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Dive on Wide Menü</string>
  <key>CFBundleDisplayName</key><string>Dive on Wide Menü</string>
  <key>CFBundleIdentifier</key><string>os.dow.menue</string>
  <key>CFBundleExecutable</key><string>DowOSMenu</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <!-- Kein Dock-Symbol und kein Eintrag im App-Umschalter: Das Ding gehoert
       in die Menueleiste und sonst nirgendwohin. -->
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST

echo "  Übersetze …"
if ! swiftc -O -o "$ZIEL/Contents/MacOS/DowOSMenu" "$HIER/DowOSMenu.swift" 2>/tmp/dowos-menue.log; then
  rot "✗ Übersetzen fehlgeschlagen:"
  tail -20 /tmp/dowos-menue.log
  rm -rf "$ZIEL"
  exit 1
fi

# Ad-hoc signieren. Ohne Signatur weigert sich macOS auf manchen Systemen,
# das Buendel zu starten — mit einer wortlosen Meldung, die niemand deuten kann.
codesign --force --deep --sign - "$ZIEL" >/dev/null 2>&1 || true

gruen "✓ Gebaut: $ZIEL"
echo
echo "  Starten:        open \"$ZIEL\""
echo "  Automatisch:    Systemeinstellungen → Allgemein → Anmeldeobjekte → +"
echo "  Beenden:        im Menü selbst „Menü beenden“ (Dive on Wide läuft weiter)"
