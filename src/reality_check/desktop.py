"""Run the GUI in its own desktop window (pywebview / WebView2).

A browser never gives a page the full path of a dropped file. The desktop
window does: pywebview adds `pywebviewFullPath` to dropped files. The drop is
caught here and passed to the page as an `rc_drop` event.

The NiceGUI server runs in a background thread; the window owns the main
thread (WinForms needs it). Closing the window ends the process.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for(port: int, timeout: float = 30) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def run_desktop() -> None:
    import uvicorn
    import webview
    from fastapi import FastAPI
    from nicegui import ui
    from webview.dom import DOMEventHandler

    from reality_check import gui  # noqa: F401  (registers the page)

    port = free_port()
    fastapi_app = FastAPI()
    ui.run_with(fastapi_app, title="RealityCheck", reconnect_timeout=30, show_welcome_message=False)
    server = uvicorn.Server(uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    if not _wait_for(port):
        raise RuntimeError("The RealityCheck server did not start")

    window = webview.create_window("RealityCheck", f"http://127.0.0.1:{port}/", width=1500, height=950,
                                   min_size=(1000, 650))

    def on_drop(event) -> None:
        files = event.get("dataTransfer", {}).get("files", [])
        paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
        if paths:
            window.evaluate_js(f"emitEvent('rc_drop', {json.dumps({'paths': paths})})")

    def attach() -> None:
        doc = window.dom.document
        doc.events.dragover += DOMEventHandler(lambda e: None, prevent_default=True, stop_propagation=True)
        doc.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=True)

    window.events.loaded += attach  # fires again after every page reload
    webview.start()  # blocks until the window closes
    server.should_exit = True
    os._exit(0)
