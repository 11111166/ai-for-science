$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$api = Start-Process -FilePath "python" -ArgumentList "-m","uvicorn","app.main:app","--reload","--port","8000" -WorkingDirectory (Join-Path $root "services/api") -PassThru -WindowStyle Hidden
try {
  Push-Location $root
  npm run dev:web
} finally {
  Pop-Location
  Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
}
