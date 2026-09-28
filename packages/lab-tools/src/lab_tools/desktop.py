"""Native application window; development commands never import the GUI runtime."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
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
        self._operation_lock = threading.Lock()

    @contextmanager
    def _operation(self) -> Generator[None]:
        if not self._operation_lock.acquire(blocking=False):
            raise ValueError("应用正在准备更新或更改源码登记，请等待操作完成")
        try:
            yield
        finally:
            self._operation_lock.release()

    def status(self) -> dict[str, object]:
        status = self._runtime.status()
        candidate = self._runtime.prepared_update()
        return {
            "home": str(self._runtime.home),
            "state": status.state,
            "detail": status.detail,
            "installation": self._runtime.installation().model_dump(mode="json"),
            "candidate": candidate.model_dump(mode="json") if candidate else None,
        }

    def prepare_update(self, directory: str) -> dict[str, object]:
        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("请选择交付目录的完整路径")
        with self._operation():
            return self._runtime.prepare_update(path).model_dump(mode="json")

    def apply_update(self) -> None:
        with self._operation():
            candidate = self._runtime.prepared_update()
            if candidate is None:
                raise ValueError("请先准备更新，资格核验通过后再切换")
            self._runtime.stop()
            self._runtime.select(candidate)
            self.retry()

    def register_source(self, directory: str) -> str:
        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("请选择作者代码目录的完整路径")
        with self._operation():
            self._runtime.stop()
            identity = self._runtime.register_source(path)
            self.retry()
            return identity

    def restart(self) -> None:
        with self._operation():
            self._runtime.stop()
            self.retry()

    def requalify(self) -> None:
        with self._operation():
            selected = self._runtime.installation()
            self._runtime.stop()
            candidate = self._runtime.qualify(selected.python, selected.static_dir)
            self._runtime.select(candidate)
            self.retry()

    def retry(self) -> None:
        record = self._runtime.start()
        self._window().load_url(record.base_url)

    def exit(self, background: bool) -> None:
        with self._operation():
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
            resume_update = (
                '<button onclick="pywebview.api.apply_update().catch(showError)">'
                "继续完成上次更新</button>"
                if runtime.pending.exists()
                else ""
            )
            failure = (
                "<h1>启动未完成</h1>"
                f"<p>{escape(str(error))}</p>"
                "<p>数据与源码保留。可以重试，或停止此应用的后台再启动。</p>"
                '<button onclick="pywebview.api.retry().catch(showError)">'
                "重试</button> "
                '<button onclick="pywebview.api.restart().catch(showError)">'
                "停止后台并重新启动</button>"
                '<button onclick="pywebview.api.requalify().catch(showError)">'
                "停止并重新核验当前环境</button>"
                f"{resume_update}"
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
