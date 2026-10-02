"""
paths.py - Where bundled resources, the config and working files live.

RESOURCE_DIR  bundled files (static/, tools/); a temp dir inside a PyInstaller build.
DATA_DIR      the folder of the .exe (project folder when run from source): holds
              ZUGFeRD-Studio.ini and is the fallback output folder.
WORK_DIR      scratch space for sessions, in the user's temp folder, so nothing but
              the finished ZUGFeRD PDF ever appears next to the .exe or the invoice.

ZUGFERD_DATA_DIR / ZUGFERD_WORK_DIR override the last two (used by the tests).
"""

import os
import sys
import tempfile
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    _default_data = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = Path(__file__).resolve().parent
    _default_data = RESOURCE_DIR

DATA_DIR = Path(os.environ.get("ZUGFERD_DATA_DIR") or _default_data)
WORK_DIR = Path(os.environ.get("ZUGFERD_WORK_DIR") or Path(tempfile.gettempdir()) / "ZUGFeRD-Studio")
