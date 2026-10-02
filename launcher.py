"""
launcher.py - Entry point of the standalone .exe.

Starts the local web server on a free port and opens the browser. An invoice
dragged onto the .exe (or opened with it) arrives as a command-line argument and
is processed right away. If the app is already running, the file is handed to
that instance instead of starting a second server.
Closing the console window stops the app.
"""

import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

import app as app_module
from paths import WORK_DIR

HOST = "127.0.0.1"
PREFERRED_PORT = 8000
INSTANCE_FILE = WORK_DIR / "instance.json"


def _free_port() -> int:
    for port in (PREFERRED_PORT, 0):
        with socket.socket() as s:
            try:
                s.bind((HOST, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("Kein freier Port gefunden.")


def _running_instance():
    """The already running instance ({port, token}) or None."""
    try:
        info = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
        with urllib.request.urlopen(f"http://{HOST}:{info['port']}/api/ping", timeout=1.5) as r:
            if json.load(r).get("app") == "zugferd-studio":
                return info
    except Exception:
        pass
    return None


def _hand_over(info, path: str) -> None:
    req = urllib.request.Request(
        f"http://{HOST}:{info['port']}/api/queue-file", method="POST",
        data=json.dumps({"path": path}).encode(),
        headers={"Content-Type": "application/json", "X-Instance-Token": info["token"]})
    urllib.request.urlopen(req, timeout=5).close()


def _open_tabs(url: str, count: int, delay: float) -> None:
    time.sleep(delay)
    if count == 0:
        webbrowser.open(url)
    for _ in range(min(count, 5)):
        webbrowser.open(url + "/?pending=1")
        time.sleep(0.4)


def main() -> None:
    files = [str(Path(a).resolve()) for a in sys.argv[1:] if Path(a).is_file()]

    running = _running_instance()
    if running:
        for f in files:
            _hand_over(running, f)
        _open_tabs(f"http://{HOST}:{running['port']}", len(files), 0)
        return

    port = _free_port()
    url = f"http://{HOST}:{port}"
    for f in files:
        app_module.queue_file(f)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    INSTANCE_FILE.write_text(json.dumps({"port": port, "token": app_module.INSTANCE_TOKEN,
                                         "pid": os.getpid()}), encoding="utf-8")

    print("ZUGFeRD Studio läuft auf", url)
    print("Dieses Fenster offen lassen. Zum Beenden schließen oder Strg+C drücken.")
    threading.Thread(target=_open_tabs, args=(url, len(files), 1.5), daemon=True).start()
    try:
        uvicorn.run(app_module.app, host=HOST, port=port, log_level="warning")
    finally:
        try:
            if json.loads(INSTANCE_FILE.read_text(encoding="utf-8")).get("pid") == os.getpid():
                INSTANCE_FILE.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    main()
