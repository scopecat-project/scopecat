"""Native application window; development commands never import the GUI runtime."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, cast

from filelock import FileLock, Timeout

from .application_runtime import ApplicationRuntime

if TYPE_CHECKING:
    import webview


class DesktopAPI:
    """Native operations act on this home only, without another HTTP service."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        window: Callable[[], webview.Window],
        closing: threading.Event,
    ):
        self._runtime = runtime
        self._window = window
        self._closing = closing

    def status(self) -> dict[str, object]:
        status = self._runtime.status()
        return {
            "home": str(self._runtime.home),
            "state": status.state,
            "detail": status.detail,
            "installation": self._runtime.installation().model_dump(mode="json"),
        }

    def restart(self) -> None:
        self._runtime.stop()
        self.retry()

    def retry(self) -> None:
        record = self._runtime.start()
        self._window().load_url(record.base_url)

    def exit(self, background: bool) -> None:
        if not background:
            self._runtime.stop()
        self._closing.set()
        self._window().destroy()


def run(home: Path, source: Path | None = None) -> None:
    # Optional dependency: command-line/service installations stay headless.
    import webview

    home.mkdir(parents=True, exist_ok=True)
    directory = home / "desktop"
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
        runtime = ApplicationRuntime(home)
        closing = threading.Event()
        api = DesktopAPI(runtime, lambda: window, closing)
        url = None
        failure = None
        try:
            runtime.configure(
                static_dir=source / "apps/scopecat-ui/dist" if source else None
            )
            url = runtime.start().base_url
        except Exception as error:
            logging.getLogger(__name__).exception("Application startup failed")
            failure = (
                "<h1>启动未完成</h1>"
                f"<p>{escape(str(error))}</p>"
                "<p>数据与源码保留。可以重试，或停止此应用的后台再启动。</p>"
                '<button onclick="pywebview.api.retry().catch(showError)">'
                "重试</button> "
                '<button onclick="pywebview.api.restart().catch(showError)">'
                "停止后台并重新启动</button>"
                '<button onclick="pywebview.api.exit(true)">关闭窗口</button>'
                '<p id="error"></p><script>function showError(e) {'
                "document.getElementById('error').textContent = e.message; }"
                "window.scopecatRequestExit = () => pywebview.api.exit(true);</script>"
            )
        window = cast(
            "webview.Window",
            webview.create_window(  # pyright: ignore[reportUnknownMemberType]
                "Scopecat",
                url=url,
                html=failure,
                js_api=api,
                width=1280,
                height=900,
                min_size=(800, 600),
            ),
        )
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

        # The GUI runs on the main thread. Its supervisor never opens a browser.
        webview.start(supervise)
        closing.set()
    finally:
        lock.release()
