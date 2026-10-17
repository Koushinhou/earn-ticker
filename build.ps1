# Build a single-file, windowed EarnTicker.exe into dist\
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name EarnTicker `
    --icon assets\earnticker.ico `
    --exclude-module tkinter `
    run.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Get-Item dist\EarnTicker.exe | Select-Object FullName, Length
