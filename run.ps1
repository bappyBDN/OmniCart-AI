param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Python = Join-Path $Root 'venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw 'Python environment missing. Create venv and install backend\requirements.txt first.'
}
if (-not (Test-Path (Join-Path $Root 'frontend\dist\index.html'))) {
    throw 'React build missing. Run npm --prefix frontend install and npm --prefix frontend run build first.'
}
Push-Location $Root
try {
    Write-Host "OmniCart: http://localhost:$Port/ (Ctrl+C to stop)"
    & $Python -m uvicorn backend.main:app --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
