param([int]$Port=8765)
$ErrorActionPreference='Stop'
Push-Location $PSScriptRoot
try {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv sync --locked
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        & uv run --locked python launcher.py --port $Port
    } else {
        if (-not (Test-Path '.venv\Scripts\python.exe')) {
            & py -3 -m venv .venv
            if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11+ or uv first.' }
            & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
            if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
        }
        & .\.venv\Scripts\python.exe launcher.py --port $Port
    }
} finally { Pop-Location }
