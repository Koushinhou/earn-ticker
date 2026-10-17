# Build a single-file, windowed EarnTicker.exe from EarnTicker.spec (the only
# build definition: icon, bundled data files, excludes).
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1 [-DistPath dist2]
param([string]$DistPath = "dist")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

& $python -m PyInstaller --noconfirm --clean --distpath $DistPath EarnTicker.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Get-Item (Join-Path $DistPath "EarnTicker.exe") | Select-Object FullName, Length
