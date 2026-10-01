"""
run.py
------
Starts the dashboard and opens it in your default browser automatically.
Meant for LOCAL use. On Render (or similar), the platform starts the app
directly with `uvicorn main:app --host 0.0.0.0 --port $PORT` (see render.yaml) —
but if this script ever gets used as the start command there instead, it
detects the cloud environment and behaves correctly (binds 0.0.0.0, skips
opening a browser) rather than failing.

Usage:
    python run.py

Environment variables (all optional):
    PORT           port to listen on (default 8000; the next free port is used if busy)
    HOST           host to bind to (default 127.0.0.1; use 0.0.0.0 to share on your LAN)
    OPEN_BROWSER   set to 0 to skip auto-opening the browser
"""

import os
import socket
import threading
import time
import webbrowser

import uvicorn


def find_free_port(preferred: int, host: str) -> int:
    port = preferred
    for _ in range(20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host if host != "0.0.0.0" else "127.0.0.1", port))
                return port
            except OSError:
                port += 1
    return preferred


def _running_on_a_cloud_platform() -> bool:
    """Render, Railway, Heroku-likes, etc. all set one of these."""
    return any(os.environ.get(k) for k in ("RENDER", "DYNO", "RAILWAY_ENVIRONMENT", "FLY_APP_NAME"))


def main():
    on_cloud = _running_on_a_cloud_platform()

    host = os.environ.get("HOST", "0.0.0.0" if on_cloud else "127.0.0.1")
    preferred_port = int(os.environ.get("PORT", "8000"))
    open_browser = (os.environ.get("OPEN_BROWSER", "1") != "0") and not on_cloud

    port = preferred_port if on_cloud else find_free_port(preferred_port, host)
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"

    if open_browser:
        def _open():
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    print("=" * 60)
    print("  Unnati Hyundai — Dealership Performance Dashboard")
    print(f"  Serving at: {url}")
    print(f"  Running from folder: {os.path.dirname(os.path.abspath(__file__))}")
    print("  Build: V4 (model / consultant / source window)")
    if not on_cloud:
        print("  Press CTRL+C to stop")
    print("=" * 60)

    uvicorn.run("main:app", host=host, port=port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
