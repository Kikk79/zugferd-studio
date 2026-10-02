# ZUGFeRD Studio

Wandelt PDF- und Word-Rechnungen (DOCX/DOC) in ZUGFeRD-/Factur-X-E-Rechnungen um
(Hybrid-PDF mit eingebettetem `factur-x.xml`, Profile **EN 16931** und **BASIC**).
Alles läuft lokal; es wird nichts ins Internet gesendet.

## Start

Doppelklick auf `start.bat` (oder `start.ps1`). Beim ersten Start wird `.venv` angelegt und
`requirements.txt` installiert. Die Oberfläche öffnet sich unter <http://localhost:8000>.

1. PDF/Word-Datei auf die Fläche ziehen.
2. Die erkannten Daten prüfen (Reiter *Übersicht*, *Positionen*). Rote Hinweise ("Zu beheben")
   müssen korrigiert werden, gelbe ("Bitte prüfen") sind Hinweise.
3. **ZUGFeRD erstellen** → PDF und XML herunterladen.

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
- Gescannte PDFs (nur Bild) werden nicht erkannt (keine OCR).
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
Hochgeladene Dateien liegen dann in `storage\` neben `ZUGFeRD-Studio.exe`.
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
`zugferd_generator.py` (XML + Einbettung) · `schematron.py` (Geschäftsregeln) · `pdf_renderer.py` (Muster-PDF, Word-Konvertierung).

Hochgeladene Dateien liegen unter `storage/<uuid>/` und werden nach 7 Tagen beim Start gelöscht.
