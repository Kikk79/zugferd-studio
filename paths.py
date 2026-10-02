"""
paths.py - Where bundled resources and user data live.

From source both are the project folder. In a PyInstaller build, bundled files
(static/, tools/) are unpacked to a temp dir (RESOURCE_DIR) while uploads must
persist next to the .exe (DATA_DIR).
"""

import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    DATA_DIR = Path(sys.executable).resolve().parent
else:
    RESOURCE_DIR = DATA_DIR = Path(__file__).resolve().parent
