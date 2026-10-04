"""
output.py - Where the finished ZUGFeRD PDF goes.

ZUGFeRD-Studio.ini (next to the .exe, created on first start):

    [Ausgabe]
    pfad =          ; empty = folder of the source invoice; if that is unknown, next to the .exe
    oeffnen = ja    ; open the created PDF with the default program
    [KI]
    aktiv = ja      ; AI fallback for uncertain extractions (ja / nein)
    modell =        ; model id; empty = built-in default
    denken = xhigh      ; thinking effort: standard / aus / niedrig / mittel / hoch / xhigh
    kontext = 32768 ; max. context in tokens that is sent to the model

The file is named like the source invoice plus "_Zug.pdf" (Rechnung.docx ->
Rechnung_Zug.pdf). Only this one file is written to the output folder.
"""

import configparser
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from paths import DATA_DIR

CONFIG_PATH = DATA_DIR / "ZUGFeRD-Studio.ini"
SUFFIX = "_Zug.pdf"
_FALSE_WORDS = ("nein", "no", "false", "0", "aus", "off")

_CONFIG_TEMPLATE = """; ZUGFeRD Studio - Einstellungen
; Diese Datei kann auch im Programm unter "Einstellungen" bearbeitet werden.
; Änderungen gelten sofort, ein Neustart ist nicht nötig.

[Ausgabe]
; Ordner, in den die ZUGFeRD-PDF gespeichert wird (z. B. C:\\Rechnungen\\ZUGFeRD).
; Leer lassen = in den Ordner der Ursprungsrechnung speichern. Ist dieser Ordner
; nicht bekannt (Datei per Drag & Drop ins Browserfenster gezogen), wird die PDF
; direkt neben ZUGFeRD-Studio.exe gespeichert.
pfad = {pfad}

; Die erstellte ZUGFeRD-PDF automatisch mit dem Standardprogramm öffnen (ja / nein).
oeffnen = {oeffnen}

[KI]
; KI-Prüfung als Fallback bei unsicherer Erkennung (ja / nein). Bei "ja" werden
; Rechnungstext und Seitenbilder an den KI-Server gesendet, bei "nein" bleibt alles lokal.
aktiv = {aktiv}

; KI-Modell (ID wie vom Server gemeldet, z. B. prism-ml/Ternary-Bonsai-2-27B-gguf:PQ2_0).
; Leer lassen = eingebautes Standardmodell. Die Umgebungsvariable UNSLOTH_MODEL hat Vorrang.
modell = {modell}

; Denkaufwand (Reasoning) des Modells: standard (Servervorgabe), aus, niedrig, mittel, hoch,
; xhigh (höchste Stufe, Vorgabe).
denken = {denken}

; Maximaler Kontext in Tokens, der an das Modell gesendet wird (Rechnungstext und
; Seitenbilder). Längere Rechnungstexte werden entsprechend gekürzt.
kontext = {kontext}
"""

DEFAULT_AI_CONTEXT = 32768
MIN_AI_CONTEXT = 4096
MAX_AI_CONTEXT = 262144
DEFAULT_AI_THINKING = "xhigh"
THINKING_LEVELS = ("standard", "aus", "niedrig", "mittel", "hoch", "xhigh")


def render_config(
    output_dir: str = "",
    open_pdf: bool = True,
    ai_enabled: bool = True,
    ai_model: str = "",
    ai_thinking: str = DEFAULT_AI_THINKING,
    ai_context: int = DEFAULT_AI_CONTEXT,
) -> str:
    return _CONFIG_TEMPLATE.format(
        pfad=" ".join(output_dir.split()),
        oeffnen="ja" if open_pdf else "nein",
        aktiv="ja" if ai_enabled else "nein",
        modell=" ".join(ai_model.split()),
        denken=ai_thinking,
        kontext=ai_context,
    )


DEFAULT_CONFIG = render_config()

_dialog_lock = threading.Lock()
_last_dialog_dir: Optional[str] = None


def ensure_config() -> None:
    """Write the commented default config if there is none yet."""
    if not CONFIG_PATH.exists():
        try:
            CONFIG_PATH.write_text(DEFAULT_CONFIG, encoding="utf-8")
        except OSError:
            pass  # read-only location: defaults still apply


def load_config() -> Dict[str, Any]:
    parser = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    try:
        parser.read(CONFIG_PATH, encoding="utf-8-sig")  # Notepad may add a BOM
    except (configparser.Error, OSError):
        pass
    raw = parser.get("Ausgabe", "pfad", fallback="").strip().strip('"')
    open_pdf = parser.get("Ausgabe", "oeffnen", fallback="ja").strip().lower()
    ai = parser.get("KI", "aktiv", fallback="ja").strip().lower()
    ai_model = parser.get("KI", "modell", fallback="").strip().strip('"')
    thinking = parser.get("KI", "denken", fallback=DEFAULT_AI_THINKING).strip().lower()
    try:
        context = int(parser.get("KI", "kontext", fallback=str(DEFAULT_AI_CONTEXT)).strip())
    except ValueError:
        context = DEFAULT_AI_CONTEXT
    return {
        "output_dir": Path(os.path.expandvars(os.path.expanduser(raw))) if raw else None,
        "output_dir_raw": raw,
        "open_pdf": open_pdf not in _FALSE_WORDS,
        "ai_enabled": ai not in _FALSE_WORDS,
        "ai_model": ai_model,
        "ai_thinking": thinking if thinking in THINKING_LEVELS else DEFAULT_AI_THINKING,
        "ai_context": min(max(context, MIN_AI_CONTEXT), MAX_AI_CONTEXT),
    }


def save_config(
    output_dir: str,
    open_pdf: bool,
    ai_enabled: bool,
    ai_model: str = "",
    ai_thinking: str = DEFAULT_AI_THINKING,
    ai_context: int = DEFAULT_AI_CONTEXT,
) -> Dict[str, Any]:
    """Validate and write the settings. Raises ValueError with a user-facing message."""
    ai_model = (ai_model or "").strip().strip('"')
    if re.search(r"[\s;#]", ai_model):
        raise ValueError("Die Modell-ID darf keine Leerzeichen, Semikolons oder # enthalten.")
    if ai_thinking not in THINKING_LEVELS:
        raise ValueError("Unbekannter Denkaufwand: " + ", ".join(THINKING_LEVELS) + " sind erlaubt.")
    if not MIN_AI_CONTEXT <= ai_context <= MAX_AI_CONTEXT:
        raise ValueError(f"Der maximale Kontext muss zwischen {MIN_AI_CONTEXT} und {MAX_AI_CONTEXT} Tokens liegen.")
    raw = (output_dir or "").strip().strip('"')
    if raw:
        folder = Path(os.path.expandvars(os.path.expanduser(raw)))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"Ausgabeordner nicht verfügbar: {exc}") from exc
        if not folder.is_dir() or not os.access(folder, os.W_OK):
            raise ValueError("Der Ausgabeordner ist nicht beschreibbar.")
    try:
        tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
        tmp.write_text(render_config(raw, open_pdf, ai_enabled, ai_model, ai_thinking, ai_context), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)
    except OSError as exc:
        raise ValueError(f"Einstellungen konnten nicht gespeichert werden ({CONFIG_PATH}): {exc}") from exc
    return load_config()


def output_name(source_name: str) -> str:
    return Path(source_name).stem + SUFFIX


def target_path(source_name: str, source_dir: Optional[str]) -> Path:
    cfg = load_config()
    folder = cfg["output_dir"] or (Path(source_dir) if source_dir else None) or DATA_DIR
    return folder / output_name(source_name)


def save_output(pdf_bytes: bytes, source_name: str, source_path: Optional[str]) -> Dict[str, Any]:
    """Write <source>_Zug.pdf to the configured folder and optionally open it."""
    source_dir = str(Path(source_path).parent) if source_path else None
    target = target_path(source_name, source_dir)
    if source_path and target.resolve() == Path(source_path).resolve():
        return {"saved": False, "path": str(target), "error": "Ausgabedatei wäre identisch mit der Quelldatei."}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"saved": False, "path": str(target), "error": f"Ausgabeordner nicht verfügbar: {exc}"}

    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_bytes(pdf_bytes)
        os.replace(tmp, target)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        return {"saved": False, "path": str(target),
                "error": f"{target.name} konnte nicht überschrieben werden – ist die Datei noch in einem "
                         "PDF-Programm geöffnet? Bitte schließen und erneut erstellen."}
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return {"saved": False, "path": str(target), "error": f"Speichern fehlgeschlagen: {exc}"}

    opened = False
    if load_config()["open_pdf"] and not os.environ.get("ZUGFERD_NO_OPEN") and hasattr(os, "startfile"):
        try:
            os.startfile(str(target))  # default PDF program
            opened = True
        except OSError:
            pass
    return {"saved": True, "path": str(target), "opened": opened, "error": None}


def pick_folder_dialog(initial: Optional[str] = None) -> Optional[str]:
    """Native Windows folder chooser, shown in front of the browser. None = cancelled."""
    import tkinter as tk
    from tkinter import filedialog

    with _dialog_lock:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        try:
            path = filedialog.askdirectory(
                parent=root, title="Ausgabeordner auswählen", mustexist=False,
                initialdir=initial if initial and Path(initial).is_dir() else None)
        finally:
            root.destroy()
    return str(Path(path)) if path else None


def pick_file_dialog() -> Optional[str]:
    """Native Windows 'Open' dialog, shown in front of the browser. None = cancelled."""
    global _last_dialog_dir
    import tkinter as tk
    from tkinter import filedialog

    with _dialog_lock:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        try:
            path = filedialog.askopenfilename(
                parent=root, title="Rechnung auswählen", initialdir=_last_dialog_dir or None,
                filetypes=[("Rechnungen", "*.pdf *.docx *.doc"), ("PDF", "*.pdf"),
                           ("Word", "*.docx *.doc"), ("Alle Dateien", "*.*")])
        finally:
            root.destroy()
    if path:
        _last_dialog_dir = str(Path(path).parent)
        return str(Path(path))
    return None
