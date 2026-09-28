"""Native application window; development commands never import the GUI runtime."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import cast
from urllib.parse import urlencode

from filelock import FileLock, Timeout

from .host_client import ensure_host, process_alive


def run(home: Path, source: Path | None = None) -> None:
    # Optional dependency: command-line/service installations stay headless.
    import webview

    home.mkdir(parents=True, exist_ok=True)
    directory = home / "host"
    directory.mkdir(exist_ok=True)
    logging.basicConfig(filename=directory / "desktop.log", level=logging.INFO)
    lock = FileLock(directory / "desktop.lock", timeout=0)
    activate = directory / "activate"
    try:
        lock.acquire()
    except Timeout:
        activate.touch()
        return
    try:
        try:
            client = ensure_host(home, source)
        except Exception:
            logging.getLogger(__name__).exception("Application startup failed")
            webview.create_window(  # pyright: ignore[reportUnknownMemberType]
                "Scopecat · 启动失败",
                html=(
                    "<h1>启动未完成</h1><p>请查看安装目录中的 host/desktop.log "
                    "和 host/host.log。数据与已有服务保留。</p>"
                ),
            )
            webview.start()
            return
        window = cast(
            "webview.Window",
            webview.create_window(  # pyright: ignore[reportUnknownMemberType]
                "Scopecat",
                f"{client.record.url}/#{urlencode({'token': client.record.token})}",
                width=1280,
                height=900,
                min_size=(800, 600),
            ),
        )
        closing = threading.Event()
        loaded = threading.Event()
        window.events.loaded += loaded.set

        def request_close() -> bool:
            if closing.is_set():
                return True
            if loaded.is_set():
                # Native close callbacks may run on the UI thread. JavaScript
                # dispatch must not block that thread waiting for itself.
                threading.Thread(
                    target=window.run_js,
                    args=("window.scopecatRequestExit()",),
                    daemon=True,
                ).start()
            else:
                client.shutdown()
                closing.set()
                return True
            return False

        window.events.closing += request_close

        def supervise() -> None:
            while not closing.wait(0.5):
                if activate.exists():
                    activate.unlink(missing_ok=True)
                    window.restore()
                    window.show()
                if not process_alive(client.record):
                    closing.set()
                    window.destroy()

        # The GUI runs on the main thread. Its supervisor never opens a browser.
        webview.start(supervise)
        closing.set()
    finally:
        lock.release()
