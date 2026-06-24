# ToSMod quick start (run from this folder: C:\Uni\ToSMod)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "ToSMod — installing package in editable mode..."
python -m pip install -e ".[dev]" -q

Write-Host "Seeding demo database..."
python -m tosmod seed

Write-Host "Running tests..."
python -m tosmod test

Write-Host ""
Write-Host "Starting dashboard at http://127.0.0.1:5050"
Write-Host "Press Ctrl+C to stop."
python -m tosmod serve
