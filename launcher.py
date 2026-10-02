"""
launcher.py - Entry point of the standalone .exe.

Starts the local web server on a free port and opens the browser.
Closing the console window stops the app.
"""

import socket
import threading
import time
import webbrowser

import uvicorn

from app import app

HOST = "127.0.0.1"
PREFERRED_PORT = 8000


def _free_port() -> int:
    for port in (PREFERRED_PORT, 0):
        with socket.socket() as s:
            try:
                s.bind((HOST, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("Kein freier Port gefunden.")


def _open_browser(url: str) -> None:
    time.sleep(1.5)
    webbrowser.open(url)


def main() -> None:
    port = _free_port()
    url = f"http://{HOST}:{port}"
    print("ZUGFeRD Studio läuft auf", url)
    print("Dieses Fenster offen lassen. Zum Beenden schließen oder Strg+C drücken.")
    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    uvicorn.run(app, host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
