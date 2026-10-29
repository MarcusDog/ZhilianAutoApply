param([switch]$UseExistingEnvironment)
$ErrorActionPreference='Stop'
$rootDir=Split-Path $PSScriptRoot -Parent
Push-Location $rootDir
try {
    if (-not $UseExistingEnvironment) {
        & uv sync --locked --group build
        if ($LASTEXITCODE -ne 0) { throw 'Build dependencies unavailable.' }
        $pythonExe=Join-Path $rootDir '.venv\Scripts\python.exe'
    } else { $pythonExe=$env:BUILD_PYTHON }
    & $pythonExe -m PyInstaller --noconfirm --onedir --windowed --name ZhilianApplyDesk --add-data 'web;web' --collect-all playwright --collect-submodules sqlalchemy.dialects.sqlite --hidden-import uvicorn.logging --hidden-import uvicorn.loops.asyncio --hidden-import uvicorn.protocols.http.h11_impl --hidden-import uvicorn.lifespan.on desktop_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    Copy-Item -LiteralPath README.md,NOTICE.md -Destination 'dist\ZhilianApplyDesk'
    & $pythonExe scripts\package_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Release packaging failed.' }
} finally { Pop-Location }
