# ZUGFeRD Studio - PowerShell Starter
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  ZUGFeRD & Factur-X E-Rechnungs Generator" -ForegroundColor Green
Write-Host "  PDF / Word Drag & Drop Konverter (EN 16931)" -ForegroundColor Yellow
Write-Host "========================================================" -ForegroundColor Cyan

Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Erstelle Python Virtual Environment (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
}

Write-Host "Pruefe erforderliche Pakete..." -ForegroundColor Yellow
& ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "Paketinstallation fehlgeschlagen." -ForegroundColor Red; exit 1 }

Write-Host "Starte Web-Server auf http://localhost:8000 ..." -ForegroundColor Green
Start-Job { Start-Sleep 3; Start-Process "http://localhost:8000" } | Out-Null
& ".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
