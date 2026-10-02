"""
output.py - Where the finished ZUGFeRD PDF goes.

ZUGFeRD-Studio.ini (next to the .exe, created on first start):

    [Ausgabe]
    pfad =          ; empty = folder of the source invoice; if that is unknown, next to the .exe
    oeffnen = ja    ; open the created PDF with the default program

The file is named like the source invoice plus "_Zug.pdf" (Rechnung.docx ->
Rechnung_Zug.pdf). Only this one file is written to the output folder.
"""

import configparser
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from paths import DATA_DIR

CONFIG_PATH = DATA_DIR / "ZUGFeRD-Studio.ini"
SUFFIX = "_Zug.pdf"

DEFAULT_CONFIG = """\
; ZUGFeRD Studio - Einstellungen
; Änderungen gelten sofort, ein Neustart ist nicht nötig.

[Ausgabe]
; Ordner, in den die ZUGFeRD-PDF gespeichert wird (z. B. C:\\Rechnungen\\ZUGFeRD).
; Leer lassen = in den Ordner der Ursprungsrechnung speichern. Ist dieser Ordner
; nicht bekannt (Datei per Drag & Drop ins Browserfenster gezogen), wird die PDF
; direkt neben ZUGFeRD-Studio.exe gespeichert.
pfad =

; Die erstellte ZUGFeRD-PDF automatisch mit dem Standardprogramm öffnen (ja / nein).
oeffnen = ja
"""

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
    return {
        "output_dir": Path(os.path.expandvars(os.path.expanduser(raw))) if raw else None,
        "open_pdf": open_pdf not in ("nein", "no", "false", "0", "aus"),
    }


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
