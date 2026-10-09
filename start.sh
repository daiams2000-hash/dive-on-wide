#!/bin/bash
# Dive on Wide Starter — prüft Voraussetzungen und startet das System.
cd "$(dirname "$0")"

echo ""
echo "  Dive on Wide wird gestartet…"
echo ""

# .env fehlt (frischer Clone)? Aus der Vorlage anlegen.
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "  ℹ️  .env aus .env.example angelegt — bei Bedarf anpassen."
fi

# Python 3 vorhanden?
if ! command -v python3 &> /dev/null; then
  echo "  ❌ python3 nicht gefunden. Bitte Python 3 installieren (https://python.org)."
  exit 1
fi

# Ollama erreichbar? (nur Hinweis, kein Abbruch)
OLLAMA_URL=$(grep -E "^OLLAMA_BASE_URL=" .env 2>/dev/null | cut -d= -f2)
OLLAMA_URL=${OLLAMA_URL:-http://localhost:11434}
if curl -s --max-time 2 "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
  echo "  ✅ Ollama erreichbar unter $OLLAMA_URL"
else
  echo "  ⚠️  Ollama nicht erreichbar unter $OLLAMA_URL"
  echo "     → Starte Ollama mit:  ollama serve"
  echo "     → Oder passe OLLAMA_BASE_URL in der .env an."
fi

PORT=$(grep -E "^PORT=" .env 2>/dev/null | cut -d= -f2)
PORT=${PORT:-3000}
echo "  🌐 UI: http://localhost:$PORT"
echo ""

# Browser öffnen (macOS / Linux)
( sleep 1.5 && (open "http://localhost:$PORT" 2>/dev/null || xdg-open "http://localhost:$PORT" 2>/dev/null) ) &

exec python3 server.py
