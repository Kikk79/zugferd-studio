@echo off
title ZUGFeRD & Factur-X Studio
echo ========================================================
echo   ZUGFeRD ^& Factur-X E-Rechnungs Generator
echo   PDF / Word Drag ^& Drop Konverter (EN 16931)
echo ========================================================
echo.

cd /d "%~dp0"

IF NOT EXIST ".venv\Scripts\python.exe" (
    echo [1/2] Erstelle Python Virtual Environment ^(.venv^)...
    python -m venv .venv
    IF ERRORLEVEL 1 (
        echo Python wurde nicht gefunden. Bitte Python 3.11+ installieren.
        pause
        exit /b 1
    )
)

echo Pruefe erforderliche Pakete...
.venv\Scripts\python.exe -m pip install -q -r requirements.txt
IF ERRORLEVEL 1 (
    echo Paketinstallation fehlgeschlagen.
    pause
    exit /b 1
)

echo.
echo Starte Web-Server auf http://localhost:8000 ...
echo Zum Beenden dieses Fenster schliessen oder Strg+C druecken.
echo.

start "" cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8000"
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000

pause
