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
from .desktop_platform import install_reopen_handler

if TYPE_CHECKING:
    import webview


class DesktopAPI:
    """Native operations act on this home only, without another HTTP service."""

    def __init__(
        self,
        runtime: ApplicationRuntime,
        window: Callable[[], webview.Window],
        closing: threading.Event,
        prepare: Callable[[], None] = lambda: None,
    ):
        self._runtime = runtime
        self._window = window
        self._closing = closing
        self._operation_lock = threading.Lock()
        self._exit_thread: threading.Thread | None = None
        self._prepare = prepare
        self._waiting = threading.Event()

    @contextmanager
    def _operation(self) -> Generator[None]:
        if not self._operation_lock.acquire(blocking=False):
            raise ValueError("应用正在启动或更改源码登记，请等待操作完成")
        try:
            yield
        finally:
            self._operation_lock.release()

    def status(self) -> dict[str, object]:
        status = self._runtime.status()
        return {
            "home": str(self._runtime.home),
            "state": status.state,
            "detail": status.detail,
            "installation": self._runtime.installation().model_dump(mode="json"),
        }

    def register_source(self, directory: str) -> str:
        from .author_environment import prepare_execution_environment

        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("请选择作者代码目录的完整路径")
        with self._operation():
            python = (
                prepare_execution_environment(self._runtime, path)
                if (path / "pyproject.toml").is_file()
                else None
            )
            self._runtime.stop()
            identity = self._runtime.register_source(path, python=python)
            self._start()
            return identity

    def restart(self) -> None:
        with self._operation():
            if self._runtime.selection.exists():
                self._runtime.stop()
            self._start()

    def prepare_author_environment(self, directory: str) -> str:
        from .author_environment import prepare_execution_environment

        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("请选择已登记作者目录的完整路径")
        with self._operation():
            self._runtime.source(path)
            python = prepare_execution_environment(self._runtime, path)
            self._runtime.select_source_environment(path, python)
            return "后台依赖已准备；重新预览使用新环境，已有任务保持原环境。"

    def create_author_environment(self, directory: str, rebuild: bool = False) -> str:
        from .author_environment import create_client_environment

        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("请选择作者目录的完整路径")
        with self._operation():
            self._runtime.source(path)
            return str(create_client_environment(self._runtime, path, rebuild=rebuild))

    def retry(self) -> None:
        with self._operation():
            self._start()

    def _start(self) -> None:
        if self._closing.is_set():
            return
        self._prepare()
        record = self._runtime.start()
        self._window().load_url(record.base_url)

    def exit(self, background: bool) -> None:
        with self._operation():
            if background:
                self._waiting.clear()
                self._window().hide()
                return
            if self._runtime.selection.exists():
                self._runtime.stop()
            self._exit_thread = threading.current_thread()
            self._closing.set()

    def request_exit(self) -> dict[str, int] | None:
        with self._operation():
            if not self._runtime.selection.exists() or self._runtime.stop_if_idle():
                self._exit_thread = threading.current_thread()
                self._closing.set()
                return None
            return self._runtime.activity().model_dump()

    def wait_for_idle(self, wait: bool) -> None:
        if wait:
            self._exit_thread = threading.current_thread()
            self._waiting.set()
        else:
            self._waiting.clear()

    def _poll_exit(self) -> None:
        if self._waiting.is_set() and self._operation_lock.acquire(blocking=False):
            try:
                if self._runtime.stop_if_idle():
                    self._closing.set()
            finally:
                self._operation_lock.release()

    def _finish_exit(self) -> None:
        if self._exit_thread is not None:
            # pywebview sends the API result back to JavaScript after exit()
            # returns. Destroying the page before that bridge thread finishes
            # can leave it waiting forever for a WebKit evaluation callback.
            self._exit_thread.join()
        self._window().destroy()


def _page(content: str) -> str:
    return (
        '<!doctype html><html lang="zh"><meta charset="utf-8">'
        "<style>body{font:16px system-ui;background:#f8fafc;color:#172033;"
        "margin:0}main{max-width:720px;margin:12vh auto;padding:32px;"
        "background:white;border:1px solid #dbe2ea;border-radius:12px}"
        "p{line-height:1.7;overflow-wrap:anywhere}button{font:inherit;"
        "padding:9px 14px;margin:6px 6px 6px 0;cursor:pointer}"
        "[role=alert]{color:#b42318}</style><main>" + content + "</main></html>"
    )


def _recovery(error: Exception) -> str:
    return _page(
        "<h1>启动未完成</h1>"
        "<p>应用尚未准备就绪。可以重试，或停止本应用的后台后重新启动。"
        "如果仍无法完成，请将错误详情交给维护者。</p>"
        "<details><summary>查看错误详情</summary>"
        f"<p>{escape(str(error))}</p>"
        "<p>日志位于应用数据目录的 native-start.log 和 desktop/desktop.log。</p>"
        "</details>"
        '<button onclick="pywebview.api.retry().catch(showError)">重试</button> '
        '<button onclick="pywebview.api.restart().catch(showError)">'
        "停止后台并重新启动</button> "
        '<button onclick="quit()">'
        "退出 Scopecat</button>"
        '<div id="quit-options" hidden>'
        '<button onclick="pywebview.api.exit(false).catch(showError)">'
        "停止工作并退出</button> "
        '<button onclick="pywebview.api.exit(true).catch(showError)">'
        "保留后台并隐藏窗口</button></div>"
        '<p id="error" role="alert"></p><script>function showError(e) {'
        "document.getElementById('error').textContent = e.message; }"
        "async function quit() { try { const work = await pywebview.api.request_exit();"
        "if (work) { showError({message: '后台仍有未完成工作，请明确选择是否停止。'});"
        "document.getElementById('quit-options').hidden = false; }"
        "} catch(e) { showError(e); "
        "document.getElementById('quit-options').hidden = false; }}"
        "window.scopecatRequestExit = quit;</script>"
    )


def run(
    home: Path,
    source: Path | None = None,
    *,
    prepare: Callable[[], None] | None = None,
    package_identity: str = "source-development",
) -> None:
    # Optional dependency: command-line/service installations stay headless.
    import pystray
    import webview
    from PIL import Image, ImageDraw

    home.mkdir(parents=True, exist_ok=True)
    directory = home / "desktop"
    directory.mkdir(exist_ok=True)
    logging.basicConfig(filename=directory / "desktop.log", level=logging.INFO)
    lock = FileLock(directory / "desktop.lock", timeout=0)
    activate = directory / "activate"
    try:
        lock.acquire()
    except Timeout:
        activate.write_text(package_identity, encoding="utf-8")
        return
    try:
        runtime = ApplicationRuntime(home)
        closing = threading.Event()

        def configure() -> None:
            selected = runtime.configure(
                static_dir=source / "apps/scopecat-ui/dist" if source else None
            )
            checked = runtime.qualify(selected.python, selected.static_dir)
            if checked != selected or runtime.pending.exists():
                runtime.select(checked)

        api = DesktopAPI(runtime, lambda: window, closing, prepare or configure)
        window = cast(
            "webview.Window",
            webview.create_window(  # pyright: ignore[reportUnknownMemberType]
                "Scopecat",
                html=_page(
                    '<h1>Scopecat</h1><p id="status">正在准备应用，请稍候…</p>'
                    "<script>window.scopecatRequestExit = () => "
                    "pywebview.api.request_exit().catch(e => {"
                    "document.getElementById('status').textContent = e.message;"
                    "});</script>"
                ),
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
                def dispatch() -> None:
                    window.show()
                    window.run_js(
                        "if (typeof window.scopecatRequestExit === 'function') "
                        "{ window.scopecatRequestExit(); } "
                        "else { pywebview.api.request_exit(); }"
                    )

                threading.Thread(target=dispatch, daemon=True).start()
            else:
                # Startup owns an operation and may still create a service.
                # Do not abandon it by destroying the window underneath it.
                return False
            return False

        window.events.closing += request_close

        def show() -> None:
            window.restore()
            window.show()

        def quit_from_menu() -> None:
            show()
            _ = request_close()

        icon_image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        drawing = ImageDraw.Draw(icon_image)
        drawing.rounded_rectangle((4, 4, 60, 60), radius=12, fill="#2563eb")
        drawing.line(
            (12, 34, 23, 34, 29, 17, 37, 47, 44, 30, 53, 30), fill="white", width=4
        )
        tray = pystray.Icon(
            "Scopecat",
            icon_image,
            "Scopecat",
            menu=pystray.Menu(
                pystray.MenuItem("打开 Scopecat", show, default=True),
                pystray.MenuItem("隐藏窗口（后台运行）", window.hide),
                pystray.MenuItem("退出 Scopecat", quit_from_menu),
            ),
        )
        tray.run_detached()  # pyright: ignore[reportUnknownMemberType]

        def supervise() -> None:
            while not loaded.wait(0.5):
                if closing.is_set():
                    return
            install_reopen_handler(show)
            if closing.is_set():
                api._finish_exit()  # pyright: ignore[reportPrivateUsage]
                return
            try:
                api.retry()
            except Exception as error:
                logging.getLogger(__name__).exception("Application startup failed")
                window.load_html(_recovery(error))
            while not closing.wait(0.5):
                try:
                    api._poll_exit()  # pyright: ignore[reportPrivateUsage]
                except Exception as error:
                    api.wait_for_idle(False)
                    show()
                    window.load_html(_recovery(error))
                if activate.exists():
                    requested_package = activate.read_text(encoding="utf-8")
                    activate.unlink(missing_ok=True)
                    show()
                    if requested_package != package_identity:
                        quit_current = window.create_confirmation_dialog(
                            "Scopecat 已安装其他版本",
                            "当前窗口仍由之前打开的版本运行。"
                            "请退出当前应用，再打开已安装的版本。现在退出？",
                        )
                        if quit_current:
                            _ = request_close()
            # Keep the native completion hook out of the exposed JavaScript API.
            api._finish_exit()  # pyright: ignore[reportPrivateUsage]

        # The GUI runs on the main thread. Its supervisor never opens a browser.
        try:
            webview.start(supervise)
        finally:
            tray.visible = False
            tray.stop()
            closing.set()
    finally:
        lock.release()
