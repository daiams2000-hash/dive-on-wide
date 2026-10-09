@echo off
cd /d "%~dp0"
if not exist .env if exist .env.example copy .env.example .env >nul
rem Ein Python suchen, das wirklich laeuft. Frisches Windows 11 hat ein
rem "python.exe", das nur den Microsoft Store anbietet (App-Ausfuehrungsalias);
rem "where python" findet es trotzdem (Windows-VM, 29.09.2026).
set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY (
  echo.
  echo   Python 3.9 or newer is missing - Dive on Wide is a Python program.
  echo   Python 3.9 oder neuer fehlt - Dive on Wide ist ein Python-Programm.
  echo.
  echo   Install:  https://www.python.org/downloads/windows/
  echo             tick "Add python.exe to PATH" during setup
  echo   or:       winget install Python.Python.3.12
  echo.
  echo   Then close this window and open the starter again.
  echo   Danach dieses Fenster schliessen und den Starter neu oeffnen.
  pause
  exit /b 1
)
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:3000"
%PY% server.py
pause
