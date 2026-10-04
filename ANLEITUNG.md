# ZUGFeRD Studio – Anleitung

ZUGFeRD Studio macht aus einer PDF- oder Word-Rechnung eine **E-Rechnung im ZUGFeRD-/Factur-X-Format**:
ein normal lesbares PDF, in das die maschinenlesbaren Rechnungsdaten (`factur-x.xml`) eingebettet sind.
Unterstützt werden die Profile **EN 16931** und **BASIC**.

## Installation

Den entpackten Ordner `ZUGFeRD-Studio` an einen beliebigen Ort kopieren (z. B. auf den Desktop oder nach
`C:\Programme`). **Der Ordner muss als Ganzes zusammenbleiben** – die `ZUGFeRD-Studio.exe` allein
funktioniert nicht. Eine Installation ist nicht nötig, gestartet wird per Doppelklick auf `ZUGFeRD-Studio.exe`.

Windows SmartScreen oder Virenscanner können bei einem unsignierten Programm eine Warnung zeigen
(„Weitere Informationen“ → „Trotzdem ausführen“).

## Rechnung umwandeln

1. **Rechnung öffnen** – auf einem dieser Wege:
   - Im Programmfenster auf **Datei öffnen…** klicken,
   - die Rechnung im Explorer **auf `ZUGFeRD-Studio.exe` ziehen** (ist das Programm schon offen,
     wird sie in das offene Fenster übernommen) – **empfohlen**, weil das Programm dann den Ordner der
     Rechnung kennt und die fertige Datei dort ablegt,
   - oder die Datei per Drag & Drop in das Programmfenster (Browserfenster) ziehen – hier ist der Ordner
     unbekannt, die fertige Datei landet neben der `ZUGFeRD-Studio.exe` (siehe unten).
2. **Erkannte Daten prüfen** (Reiter *Übersicht* und *Positionen*). Das Programm liest die Rechnung
   automatisch aus; Fehler sind möglich, kontrollieren Sie deshalb Felder und Summen.
   - **Rote Hinweise („Zu beheben“)** müssen korrigiert werden, sonst lässt sich die Datei nicht erstellen.
   - **Gelbe Hinweise („Bitte prüfen“)** sind Hinweise zur Kontrolle.
3. **ZUGFeRD erstellen** klicken. Die fertige PDF wird gespeichert und mit Ihrem Standard-PDF-Programm geöffnet.

## Wo liegt die fertige Datei?

Die neue Datei heißt wie die Ursprungsrechnung plus `_Zug.pdf`:
`Rechnung 393.pdf` → `Rechnung 393_Zug.pdf` (auch bei Word-Rechnungen). Nur diese eine Datei wird
geschrieben, die Originalrechnung bleibt unverändert.

Wo sie liegt, hängt davon ab, wie die Rechnung geöffnet wurde (sofern in den Einstellungen kein fester
Speicherordner eingetragen ist):

| Rechnung geöffnet über | Die `_Zug.pdf` liegt … |
|---|---|
| auf die `ZUGFeRD-Studio.exe` gezogen | im **Ordner der Rechnung** (Quellordner) |
| **Datei öffnen…** (Windows-Dialog) | im **Ordner der Rechnung** (Quellordner) |
| ins Programmfenster (Browser) gezogen | neben der `ZUGFeRD-Studio.exe` |

Ist in den Einstellungen ein Speicherordner eingetragen, gilt immer dieser.

Ist die `_Zug.pdf` beim erneuten Erstellen noch in einem PDF-Programm geöffnet, meldet das Programm das –
nach dem Schließen einfach noch einmal erstellen.

## Einstellungen

Die Einstellungen ändern Sie im Programm über den Reiter **Einstellungen** (neben „Rechnung umwandeln“).
Mit **Speichern** gelten sie sofort, ein Neustart ist nicht nötig.

- **Speicherordner für die ZUGFeRD-PDF:** Leer lassen, dann wird im Ordner der Ursprungsrechnung gespeichert.
  Mit **Ordner wählen…** oder per Eintippen (z. B. `D:\E-Rechnungen`) legen Sie einen festen Ordner für alle
  erstellten Dateien fest. Der Ordner wird bei Bedarf angelegt.
- **Erstellte PDF automatisch öffnen:** abwählen, wenn die PDF nach dem Erstellen nicht aufgehen soll.
- **KI-Prüfung als Fallback verwenden:** siehe nächster Abschnitt.

Die Werte stehen in der Datei `ZUGFeRD-Studio.ini` neben der `ZUGFeRD-Studio.exe` (wird beim ersten Start angelegt).
Sie lässt sich bei Bedarf auch mit einem Texteditor ändern.

## KI-Prüfung (automatischer Fallback)

Die normale Texterkennung läuft **lokal auf Ihrem Rechner**. Nur wenn Angaben fehlen, die Erkennung
Auffälligkeiten meldet oder die Prüfung Fehler findet, wird die Rechnung zusätzlich von einer KI
geprüft. Dafür werden der **Rechnungstext und bis zu zehn Seitenbilder** an den Server
`https://unsloth.aicolab.de/v1` übertragen. Der erforderliche Zugangsschlüssel ist im Programm bereits
hinterlegt, Sie müssen nichts einrichten.

- KI-Ergebnisse sind **Vorschläge**: Sie müssen sie prüfen, bevor Sie die Datei erstellen.
- **Datenschutz:** Enthält eine Rechnung Daten, die den Rechner nicht verlassen dürfen, schalten Sie die
  KI ab: Reiter **Einstellungen** → Haken bei „KI-Prüfung als Fallback verwenden“ entfernen → **Speichern**.
  Dann bleibt alles lokal. Ohne KI müssen Sie unvollständig erkannte Felder selbst ergänzen.
- Mit einer eigenen `.env` neben der `ZUGFeRD-Studio.exe` (Zeile `UNSLOTH_API_KEY=<Token>`) lässt sich ein anderer
  Zugangsschlüssel verwenden; er hat Vorrang vor dem eingebauten.

## Was wird geprüft?

| Prüfung | Wie |
|---|---|
| XML-Aufbau | offizielles Factur-X-Schema (immer) |
| Geschäftsregeln nach EN 16931 | offizielles Schematron-Regelwerk; **benötigt Java** auf dem Rechner, ohne Java zeigt das Programm „nicht geprüft“ |
| Summen | Alle Beträge werden aus den Positionen nachgerechnet und mit den Summen der Rechnung verglichen; Abweichungen werden gemeldet |
| IBAN und Pflichtfelder | IBAN-Prüfsumme, USt-IdNr./Steuernummer des Verkäufers, Käufername, Länder |

Es werden **keine Daten erfunden**: Fehlt eine Pflichtangabe (z. B. die USt-IdNr.), setzt das Programm keinen
Platzhalter ein, sondern verhindert die Erstellung, bis Sie die Angabe ergänzt haben.

## Was wird unterstützt?

- Teil- und Schlussrechnungen mit **Anzahlung** (mit Verweis auf die Anzahlungsrechnung)
- **Skonto**, mehrere Steuersätze, Fremdwährungen
- Nachlässe als negative Position
- **Steuerfreie Lieferungen** (Ausfuhr, innergemeinschaftlich, Reverse Charge, sonstige Befreiung) – Kategorie und
  Befreiungsgrund stehen im Formular
- Bereits vorhandene ZUGFeRD-PDFs werden erkannt und aus dem enthaltenen XML gelesen
- **Word-Rechnungen (DOC/DOCX):** Die Umwandlung erfolgt mit Microsoft Word, das dafür installiert sein muss.
  Ohne Word funktioniert nur DOCX, mit vereinfachtem Layout.

## Grenzen – bitte beachten

- Die Texterkennung ist auf **deutsche Rechnungen** mit Absenderzeile und Positionstabelle ausgelegt.
  **Kontrollieren Sie immer Felder und Summen.**
- Gescannte PDFs werden lokal nicht per Texterkennung (OCR) gelesen. Ist die KI-Prüfung aktiv, kann sie die
  Seitenbilder auswerten; unsichere Werte müssen Sie trotzdem selbst prüfen.
- **Nicht unterstützt:** Gutschriften, Rabatte oder Zuschläge auf Rechnungsebene (bitte als negative Position
  erfassen) und das XRechnung-Profil.
- Die strikte **PDF/A-3-Konformität** wurde nicht mit einem Prüfprogramm (veraPDF) getestet. Das eingebettete XML ist
  schema- und regelgeprüft; das PDF übernimmt die Eigenschaften der Ausgangs-PDF.
- Bei Auslandsrechnungen (z. B. Schweiz mit 8,1 % MwSt.) werden die Zahlen korrekt übernommen, ob die Rechnung
  steuerlich für EN 16931 geeignet ist, müssen Sie selbst beurteilen.

## Häufige Fragen

**Windows warnt beim Start.** Das Programm ist nicht digital signiert; solche Warnungen sind bei unsignierten
Programmen üblich. Mit „Weitere Informationen“ → „Trotzdem ausführen“ lässt es sich starten.

**Die Regelprüfung zeigt „nicht geprüft“.** Auf dem Rechner ist kein Java installiert. Die Datei lässt sich trotzdem
erstellen, die Geschäftsregeln werden dann aber nicht geprüft.

**Eine Word-Datei lässt sich nicht umwandeln.** Microsoft Word ist nicht installiert oder nicht startbar.
Alternativ die Rechnung in Word als PDF speichern und diese PDF öffnen.

**Es erscheint ein roter Hinweis.** Die Angabe fehlt oder ist unplausibel (z. B. falsche IBAN-Prüfsumme,
abweichende Summen). Im Formular korrigieren oder ergänzen; danach ist die Erstellung wieder möglich.
