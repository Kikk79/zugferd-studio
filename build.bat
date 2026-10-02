@echo off
REM Baut dist\ZUGFeRD-Studio.exe (PyInstaller, eine einzelne Datei)
cd /d "%~dp0"

IF NOT EXIST ".venv\Scripts\python.exe" (
    python -m venv .venv
)
.venv\Scripts\python.exe -m pip install -q -r requirements.txt -r requirements-dev.txt
IF ERRORLEVEL 1 ( echo Paketinstallation fehlgeschlagen. & pause & exit /b 1 )

echo Teste vor dem Build...
.venv\Scripts\python.exe -m pytest -q -W ignore
IF ERRORLEVEL 1 ( echo Tests fehlgeschlagen - Build abgebrochen. & pause & exit /b 1 )

.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --name ZUGFeRD-Studio ^
  --distpath dist --workpath build\pyi --specpath build ^
  --add-data "../static;static" --add-data "../tools;tools" ^
  --collect-all facturx --collect-data iso4217 --collect-data reportlab --collect-submodules uvicorn ^
  --hidden-import multipart --hidden-import pythoncom --hidden-import win32com.client --hidden-import pywintypes ^
  launcher.py
IF ERRORLEVEL 1 ( echo Build fehlgeschlagen. & pause & exit /b 1 )

echo.
echo Fertig: dist\ZUGFeRD-Studio.exe
pause
