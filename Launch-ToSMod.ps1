# ToSMod Launcher  —  robust Python detection + guided startup
# Works with Conda, venv, or plain Python installs on Windows.
param([string]$Mode = "")

Set-Location $PSScriptRoot
$ErrorActionPreference = "Stop"

# ── Python detection ────────────────────────────────────────────────────────

function Find-Python {
    $candidates = @()

    # 1. Whatever "python" resolves to in the current PATH (covers Conda-activated shell)
    $p = Get-Command python -ErrorAction SilentlyContinue
    if ($p) { $candidates += $p.Source }

    # 2. Common Conda install locations (covers cmd.exe double-click without Conda PATH)
    foreach ($base in @(
        (Join-Path $env:USERPROFILE 'anaconda3'),
        (Join-Path $env:USERPROFILE 'miniconda3'),
        (Join-Path $env:LOCALAPPDATA 'anaconda3'),
        (Join-Path $env:LOCALAPPDATA 'miniconda3'),
        'C:\anaconda3', 'C:\miniconda3',
        'C:\ProgramData\anaconda3', 'C:\ProgramData\miniconda3'
    )) {
        $exe = Join-Path $base 'python.exe'
        if (Test-Path $exe) { $candidates += $exe }
    }

    foreach ($c in $candidates) {
        try {
            & $c -m pip --version 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return $c }
        } catch { }
    }

    # 3. Windows py launcher as last resort
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            & py -3 -m pip --version 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) { return 'py:-3' }
        } catch { }
    }

    return $null
}

function Invoke-Py {
    # Runs Python and lets stdout/stderr flow to the console.
    # Caller checks $LASTEXITCODE after calling this.
    param([string[]]$PyArgs)
    if ($script:PY -eq 'py:-3') {
        & py -3 @PyArgs
    } else {
        & $script:PY @PyArgs
    }
}

function Install-Deps {
    Write-Host ''
    Write-Host '[ToSMod] Installing dependencies (first run may take a minute)...'
    Invoke-Py @('-m', 'pip', 'install', '-e', '.[dev]', '-q')
    if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
}

# ── Mode selection ───────────────────────────────────────────────────────────

$validModes = @('quick','full','docker','help','')
if ($Mode -notin $validModes) {
    Write-Host "[ERROR] Unknown mode '$Mode'. Use: quick / full / docker / help"
    exit 1
}

if ($Mode -eq '') {
    Write-Host ''
    Write-Host '=========================================='
    Write-Host '             ToSMod Launcher'
    Write-Host '=========================================='
    Write-Host '  1  Quick launch  (install + run dashboard)'
    Write-Host '  2  Full launch   (install + seed + tests + run)'
    Write-Host '  3  Docker launch (docker compose up --build)'
    Write-Host ''
    $choice = Read-Host 'Choose [1/2/3] and press Enter'
    switch ($choice) {
        '1' { $Mode = 'quick' }
        '2' { $Mode = 'full' }
        '3' { $Mode = 'docker' }
        default { Write-Host 'Invalid choice.'; exit 1 }
    }
}

if ($Mode -eq 'help') {
    Write-Host ''
    Write-Host 'Usage:'
    Write-Host '  .\Launch-ToSMod.ps1            (interactive menu)'
    Write-Host '  .\Launch-ToSMod.ps1 quick      (install + open browser + run)'
    Write-Host '  .\Launch-ToSMod.ps1 full        (install + seed + tests + run)'
    Write-Host '  .\Launch-ToSMod.ps1 docker      (docker compose up --build)'
    exit 0
}

if ($Mode -eq 'docker') {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host '[ERROR] Docker not found.'
        Write-Host 'Install Docker Desktop: https://www.docker.com/products/docker-desktop/'
        exit 1
    }
    if (-not (Test-Path '.env') -and (Test-Path '.env.example')) {
        Copy-Item '.env.example' '.env'
        Write-Host '[ToSMod] Created .env from .env.example — add your API keys before re-running.'
    }
    Write-Host '[ToSMod] Starting with Docker Compose (Ctrl+C to stop)...'
    docker compose up --build
    exit $LASTEXITCODE
}

# ── Quick / Full require Python ──────────────────────────────────────────────

$script:PY = Find-Python
if (-not $script:PY) {
    Write-Host ''
    Write-Host '[ERROR] No Python 3 interpreter with pip was found.'
    Write-Host ''
    Write-Host 'To fix this, choose one of:'
    Write-Host '  A  Install Python 3.10+ from https://www.python.org/downloads/'
    Write-Host '     Tick "Add Python to PATH" during install, then re-run this launcher.'
    Write-Host '  B  If you have Conda, open Anaconda Prompt and run:'
    Write-Host '       pip install -e .[dev]'
    Write-Host '       python -m tosmod serve'
    exit 1
}

Write-Host "[ToSMod] Using Python: $script:PY"

try {
    Install-Deps

    if ($Mode -eq 'full') {
        Write-Host '[ToSMod] Seeding demo data...'
        Invoke-Py @('-m', 'tosmod', 'seed')
        if ($LASTEXITCODE -ne 0) { throw 'seed failed' }

        Write-Host '[ToSMod] Running tests...'
        Invoke-Py @('-m', 'tosmod', 'test')
        if ($LASTEXITCODE -ne 0) { throw 'tests failed' }
    }

    Write-Host '[ToSMod] Opening browser at http://127.0.0.1:5050 ...'
    Start-Process 'http://127.0.0.1:5050'

    Write-Host '[ToSMod] Starting dashboard (Ctrl+C to stop)...'
    Invoke-Py @('-m', 'tosmod', 'serve')
}
catch {
    Write-Host ''
    Write-Host "[ERROR] $_"
    Write-Host 'See messages above for details.'
    exit 1
}
