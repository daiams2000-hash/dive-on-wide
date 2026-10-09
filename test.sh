#!/bin/bash
# Dive on Wide Testlauf — prüft das komplette System gegen einen Mock-Ollama.
# Es muss nichts installiert und kein Ollama gestartet sein.
cd "$(dirname "$0")"
exec python3 tests/run_tests.py "$@"
