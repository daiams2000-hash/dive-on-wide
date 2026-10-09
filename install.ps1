# Dive on Wide — Einstieg für Windows.
#
# Tut absichtlich fast nichts: prüft Python, holt install.py, übergibt.
#
#     powershell -ExecutionPolicy Bypass -File install.ps1
#     powershell -ExecutionPolicy Bypass -File install.ps1 --alles
$ErrorActionPreference = "Stop"
$Quelle = if ($env:DOWOS_INSTALLER) { $env:DOWOS_INSTALLER }
          else { "https://raw.githubusercontent.com/daiams2000-hash/dive-on-wide/main/install.py" }

function Finde-Python {
  # Nicht nur finden, sondern starten: Frisches Windows 11 hat ein "python.exe",
  # das nur den Microsoft Store anbietet (App-Ausfuehrungsalias). Get-Command
  # findet es trotzdem (Windows-VM, 29.09.2026).
  foreach ($k in @("python", "python3", "py")) {
    $p = Get-Command $k -ErrorAction SilentlyContinue
    if (-not $p) { continue }
    try {
      $ok = & $p.Source -c "import sys; print(sys.version_info >= (3, 9))" 2>$null
      if ("$ok".Trim() -eq "True") { return $p.Source }
    } catch { }
  }
  return $null
}

$Python = Finde-Python
if (-not $Python) {
  Write-Host "  Python 3.9 oder neuer fehlt - Dive on Wide ist ein Python-Programm."
  Write-Host "  Installieren: https://www.python.org/downloads/windows/ (Add python.exe to PATH anhaken)"
  Write-Host "  oder:         winget install Python.Python.3.12"
  Write-Host "  Danach dieses Fenster schliessen und neu oeffnen (PATH)."
  exit 1
}

# Liegt install.py daneben? Dann den nehmen.
$Hier = Split-Path -Parent $MyInvocation.MyCommand.Path
$Neben = Join-Path $Hier "install.py"
if (Test-Path $Neben) { & $Python $Neben @args; exit $LASTEXITCODE }

$Tmp = Join-Path $env:TEMP ("dowos-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Path $Tmp | Out-Null
$Ziel = Join-Path $Tmp "install.py"
Write-Host "  Hole den Installer ..."
Invoke-WebRequest -Uri $Quelle -OutFile $Ziel -UseBasicParsing
Write-Host "  Er liegt unter $Ziel - du kannst ihn vorher lesen."
& $Python $Ziel @args
exit $LASTEXITCODE
