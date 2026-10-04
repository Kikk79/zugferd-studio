# ZUGFeRD Studio

Wandelt PDF- und Word-Rechnungen (DOCX/DOC) in ZUGFeRD-/Factur-X-E-Rechnungen um
(Hybrid-PDF mit eingebettetem `factur-x.xml`, Profile **EN 16931** und **BASIC**).
Die normale Texterkennung läuft lokal. Nur wenn Angaben fehlen, Extraktionshinweise entstehen
oder die Validierung Auffälligkeiten findet, wird die Rechnung zur Prüfung an den
OpenAI-kompatiblen Endpunkt `https://unsloth.aicolab.de/v1` gesendet. Dabei werden
Rechnungstext und bis zu zehn PDF-Seitenbilder übertragen. KI-Ergebnisse bleiben
prüfpflichtige Vorschläge; danach wird die ZUGFeRD-Datei nicht automatisch erzeugt.

## Start

Doppelklick auf `start.bat` (oder `start.ps1`). Beim ersten Start wird `.venv` angelegt und
`requirements.txt` installiert. Die Oberfläche öffnet sich unter <http://localhost:8000>.

1. Rechnung öffnen – auf einem dieser Wege:
   - **Datei öffnen…** (Windows-Dialog),
   - die Rechnung im Explorer **auf `ZUGFeRD-Studio.exe` ziehen** (läuft das Programm schon,
     wird die Datei an das offene Fenster übergeben),
   - oder per Drag & Drop ins Browserfenster.
2. Die erkannten Daten prüfen (Reiter *Übersicht*, *Positionen*). Rote Hinweise ("Zu beheben")
   müssen korrigiert werden, gelbe ("Bitte prüfen") sind Hinweise.
3. **ZUGFeRD erstellen** – die PDF wird automatisch gespeichert und mit dem Standardprogramm geöffnet.

## Ausgabe und Einstellungen

Die ZUGFeRD-PDF heißt wie die Ursprungsrechnung plus `_Zug.pdf`
(`Rechnung 393.pdf` → `Rechnung 393_Zug.pdf`, auch bei Word-Dateien). Es wird **nur diese eine
Datei** geschrieben; Arbeitsdateien liegen im Windows-Temp-Ordner und werden nach 24 Stunden gelöscht.

Die Einstellungen lassen sich im Reiter **Einstellungen** der Oberfläche ändern (Speichern gilt sofort).
Sie liegen in `ZUGFeRD-Studio.ini` neben `ZUGFeRD-Studio.exe` (wird beim ersten Start angelegt):

```ini
[Ausgabe]
pfad =          ; leer = Ordner der Ursprungsrechnung
oeffnen = ja    ; erstellte PDF mit dem Standardprogramm öffnen (ja / nein)

[KI]
aktiv = ja      ; KI-Fallback verwenden (ja / nein)
```

Der Token für den KI-Fallback wird im Reiter *Einstellungen* eingetragen und in einer nicht versionierten
`.env` neben der Anwendung gespeichert (die Seite zeigt ihn nie wieder an, nur ob einer gesetzt ist).
Reihenfolge: Umgebungsvariable, dann `.env`, dann ein beim Bau eingebetteter Token. Bevorzugtes Format: `UNSLOTH_API_KEY=<Token>`. Ein einzelner Token ohne
Variablennamen wird aus Kompatibilitätsgründen ebenfalls erkannt. Das Modell ist
`unsloth/Qwen3.8-27B-GGUF`; Endpunkt und Modell lassen sich mit `UNSLOTH_BASE_URL` und
`UNSLOTH_MODEL` überschreiben. Mit `aktiv = nein` im Abschnitt `[KI]` der INI (oder im Reiter *Einstellungen*) bleibt die Verarbeitung lokal;
die Umgebungsvariable `ZUGFERD_AI_ENABLED` hat Vorrang vor der INI.

Das Release enthält **keinen** Token. Für einen privaten Build kann `set EMBED_KEY=1` vor `build.bat` den
Token aus der `.env` verschlüsselt in die exe einbetten (`keyvault.py`; nur Verschleierung, kein echter Schutz –
solche Builds nicht veröffentlichen).

Speicherort, wenn `pfad` leer ist: der Ordner der Ursprungsrechnung. Bei Drag & Drop ins
Browserfenster kennt der Browser diesen Ordner nicht – dann landet die PDF direkt neben der exe.
Ist die `_Zug.pdf` beim erneuten Erstellen noch in einem PDF-Programm geöffnet, meldet das
Programm das; nach dem Schließen einfach erneut erstellen.

## Was geprüft wird

| Prüfung | Wie |
|---|---|
| XML-Struktur | offizielles Factur-X-XSD (immer) |
| EN-16931-Geschäftsregeln (BR-CO-*, BR-S-* …) | offizielles Schematron über Saxon + Java (`tools/saxon-he.jar`); ohne Java wird "nicht geprüft" angezeigt |
| Summen | alle Beträge werden aus Positionen berechnet (Menge × Preis, USt je Satz, Anzahlung abgezogen) und mit den Summen des Quelldokuments verglichen; Abweichungen werden gemeldet |
| IBAN / Pflichtfelder | Prüfsumme, USt-IdNr./Steuernummer des Verkäufers, Käufername, Länder |

Es werden **keine Daten erfunden**: fehlt eine Pflichtangabe (z. B. die USt-IdNr.), wird sie
nicht durch einen Platzhalter ersetzt, sondern die Erstellung blockiert.

## Unterstützt

- Teil-/Schluss-Rechnungen mit **Anzahlung** (`TotalPrepaidAmount`, Verweis auf die Anzahlungsrechnung)
- **Skonto** (strukturiert als `#SKONTO#TAGE=..#PROZENT=..#`), mehrere Steuersätze, Fremdwährung
- Nachlässe als negative Position
- **Steuerfreie Lieferungen** (Ausfuhr § 4 Nr. 1a, innergemeinschaftlich, Reverse Charge, sonstige Befreiung): wird im Text erkannt, Kategorie und Befreiungsgrund stehen im Formular
- Bereits vorhandene ZUGFeRD-PDFs werden erkannt und aus dem XML gelesen
- Word: Konvertierung per Microsoft Word (pywin32) mit PDF/A-1-Export; ohne Word nur DOCX in vereinfachtem Layout

## Grenzen (bitte beachten)

- Die Texterkennung ist heuristisch, optimiert für deutsche Rechnungen mit Absenderzeile und
  Positionstabelle. Immer die Felder und Summen kontrollieren – die Oberfläche zeigt Abweichungen an.
- Gescannte PDFs werden lokal nicht per OCR verarbeitet; wenn der Fallback aktiviert ist,
  kann das Vision-Modell die PDF-Seiten prüfen. Unsichere Werte bleiben manuell prüfpflichtig.
- Keine Gutschriften (Typ 381), keine Rabatte/Zuschläge auf Dokumentebene (als negative Position erfassen),
  kein XRechnung-Profil.
- Die strikte **PDF/A-3-Konformität** wurde nicht mit veraPDF geprüft. Das XML ist XSD- und
  Schematron-geprüft; das PDF übernimmt die Eigenschaften des Ausgangs-PDFs (Word-Export wird als PDF/A-1 erzeugt).
- Bei Auslandsrechnungen (z. B. CH mit 8,1 % MwSt.) werden die Zahlen korrekt übernommen, die steuerliche
  Eignung für EN 16931 muss selbst beurteilt werden.

## Entwicklung

```
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

## Als .exe bauen

`build.bat` ausführen (führt zuerst die Tests aus, dann PyInstaller) → `dist\ZUGFeRD-Studio\` (Ordner,
nicht nur eine Datei) plus `dist\ZUGFeRD-Studio-windows.zip` zur Weitergabe. Java (Regelprüfung) und
Microsoft Word (DOC/DOCX) müssen auf dem Zielrechner vorhanden sein, falls diese Funktionen genutzt werden.
Die Tests sind vollständig synthetisch (keine echten Rechnungen im Repo) und laufen in jedem Klon ohne Zusatzdateien durch.

**Warum ein Ordner statt einer einzelnen .exe:** Eine einzelne selbstentpackende `--onefile`-Datei
entspricht genau dem Muster, das Antivirus-Heuristiken (v. a. Microsofts ML-Erkennung) häufig als
Dropper fehlklassifizieren – unabhängig vom tatsächlichen Code. Das Ordner-Layout (`--onedir`) mit
eingebetteter Versionsinfo (`version_info.txt`) senkt das Risiko deutlich: Beim mit VirusTotal
geprüften Build meldeten 2 von 75 Scannern einen heuristischen Treffer (Microsofts generisches
`Trojan:Win32/Wacatac.B!ml`, ein bekannter Fehlalarm-Sammelbegriff bei unsignierten PyInstaller-Programmen,
und ein ebenso unspezifischer Treffer von APEX) – keiner nannte eine konkrete Malware-Familie.
Der zuverlässige, vollständige Fix ist eine **Codesignatur** mit einem echten Zertifikat (kostenpflichtig,
mit Identitätsprüfung); ohne Signatur bleibt bei PyInstaller-Programmen ein Restrisiko für
Heuristik-Fehlalarme bestehen, egal wie der Code aussieht.

Dateien: `app.py` (API) · `extractor.py` (Texterkennung) · `invoice_logic.py` (Berechnung/Validierung) ·
`zugferd_generator.py` (XML + Einbettung) · `schematron.py` (Geschäftsregeln) · `pdf_renderer.py` (Muster-PDF, Word-Konvertierung) ·
`output.py` (Config, Speichern, Windows-Dialog) · `launcher.py` (exe-Start, Datei-Übergabe).

Arbeitsdateien liegen unter `%TEMP%\ZUGFeRD-Studio\storage\<uuid>\` und werden nach 24 Stunden beim Start gelöscht.
