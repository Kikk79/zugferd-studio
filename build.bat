@echo off
REM Baut dist\ZUGFeRD-Studio\ (PyInstaller, Ordner-Layout statt einer einzelnen
REM selbstentpackenden .exe - das senkt Fehlalarme bei Antivirus-Heuristiken)
cd /d "%~dp0"

IF NOT EXIST ".venv\Scripts\python.exe" (
    python -m venv .venv
)
.venv\Scripts\python.exe -m pip install -q -r requirements.txt -r requirements-dev.txt
IF ERRORLEVEL 1 ( echo Paketinstallation fehlgeschlagen. & pause & exit /b 1 )

echo Teste vor dem Build...
.venv\Scripts\python.exe -m pytest -q -W ignore
IF ERRORLEVEL 1 ( echo Tests fehlgeschlagen - Build abgebrochen. & pause & exit /b 1 )

.venv\Scripts\python.exe keyvault.py

.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --name ZUGFeRD-Studio ^
  --icon ../static/icon.ico --version-file ../version_info.txt --noupx ^
  --distpath dist --workpath build\pyi --specpath build ^
  --add-data "../static;static" --add-data "../tools;tools" ^
  --collect-all facturx --collect-all pypdfium2 --collect-all pypdfium2_raw --collect-data iso4217 --collect-data reportlab --collect-submodules uvicorn ^
  --hidden-import _embedded_key --hidden-import multipart --hidden-import pythoncom --hidden-import win32com.client --hidden-import pywintypes ^
  launcher.py
IF ERRORLEVEL 1 ( echo Build fehlgeschlagen. & pause & exit /b 1 )

echo.
echo Fertig: dist\ZUGFeRD-Studio\ZUGFeRD-Studio.exe
echo ^(Ordner als Ganzes weitergeben - nicht nur die .exe^)
echo Erzeuge Anleitung.pdf aus ANLEITUNG.md...
.venv\Scripts\python.exe make_manual.py "%~dp0dist\ZUGFeRD-Studio\Anleitung.pdf"
echo Erzeuge zip fuer die Weitergabe...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\ZUGFeRD-Studio' -DestinationPath 'dist\ZUGFeRD-Studio-windows.zip' -Force"
echo Fertig: dist\ZUGFeRD-Studio-windows.zip
pause
