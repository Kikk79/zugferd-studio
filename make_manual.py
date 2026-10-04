"""Erzeugt Anleitung.pdf aus ANLEITUNG.md (pandoc -> HTML -> Edge/Chrome headless).

Aufruf: python make_manual.py <ziel.pdf>   (build.bat legt sie in den Release-Ordner)
Benoetigt pandoc und Microsoft Edge oder Chrome; fehlt eins davon, wird nur gewarnt.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
CSS = """
body{font-family:'Segoe UI',Arial,sans-serif;font-size:10.5pt;line-height:1.5;color:#222;max-width:none}
h1{font-size:22pt;border-bottom:2px solid #444;padding-bottom:4px}
h2{font-size:15pt;margin-top:1.4em;border-bottom:1px solid #bbb;padding-bottom:2px}
code{font-family:Consolas,monospace;background:#f1f1f1;padding:0 3px;border-radius:3px;font-size:9.5pt}
pre{background:#f6f6f6;border:1px solid #ddd;padding:8px;border-radius:4px;overflow-wrap:anywhere;white-space:pre-wrap}
pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;margin:8px 0}
th,td{border:1px solid #bbb;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#eee}
tr,pre{page-break-inside:avoid}
h1,h2{page-break-after:avoid}
@page{size:A4;margin:18mm}
"""


def _browser() -> str | None:
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ):
        if Path(p).exists():
            return p
    return None


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "Anleitung.pdf").resolve()
    pandoc, browser = shutil.which("pandoc"), _browser()
    if not pandoc or not browser:
        print("WARNUNG: pandoc oder Edge/Chrome fehlt - Anleitung.pdf wird nicht erzeugt.")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        css, html = Path(tmp, "m.css"), Path(tmp, "m.html")
        css.write_text(CSS, encoding="utf-8")
        subprocess.run(
            [pandoc, str(ROOT / "ANLEITUNG.md"), "-f", "gfm", "-s", "--embed-resources",
             "--metadata", "pagetitle=ZUGFeRD Studio - Anleitung", "--metadata", "lang=de",
             "-c", str(css), "-o", str(html)],
            check=True,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--user-data-dir={Path(tmp, 'prof')}", f"--print-to-pdf={target}", html.as_uri()],
            check=True, timeout=120,
        )
    print(f"Anleitung erzeugt: {target}")
    return 0 if target.exists() else 1


if __name__ == "__main__":
    sys.exit(main())
