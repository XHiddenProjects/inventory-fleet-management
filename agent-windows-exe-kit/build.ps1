# Builds inventory-agent.exe from this folder.
# Run on any Windows machine with Python 3 installed (does NOT need to be
# the offline target machine - build it on a machine with internet, then
# copy the resulting .exe to the offline host).
#
# Usage:
#   cd build_kit
#   .\build.ps1

$ErrorActionPreference = "Stop"

$py = $null
foreach ($candidate in @("python", "python3")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source; break }
}
if (-not $py) {
    Write-Error "Python 3 is required to BUILD the exe (not to run it afterwards). Install from https://www.python.org/downloads/windows/ and re-run."
    exit 1
}

Write-Host "Installing build + runtime dependencies ..."
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet pyinstaller "psutil>=5.9,<8.0"

Write-Host "Building inventory-agent.exe ..."
& $py -m PyInstaller inventory_agent.spec --noconfirm

if (Test-Path "dist\inventory-agent.exe") {
    Write-Host ""
    Write-Host "Built: dist\inventory-agent.exe"
    Write-Host "Copy this single file to the offline host - no Python install needed there."
} else {
    Write-Error "Build did not produce dist\inventory-agent.exe - check the PyInstaller output above."
}
