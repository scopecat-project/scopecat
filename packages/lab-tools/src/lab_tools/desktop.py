"""Native application window; development commands never import the GUI runtime."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib.parse import urlencode, urlsplit, urlunsplit

from filelock import FileLock, Timeout

from .application_runtime import ApplicationRuntime
from .desktop_platform import (
    hide_window,
    install_reopen_handler,
    show_window,
    start_tray,
)
from .desktop_session import DesktopSession

if TYPE_CHECKING:
    import webview
    from pystray._base import Icon


class DesktopAPI:
    """Native operations act on this home only, without another HTTP service."""

    def __init__(
        self,
        session: DesktopSession,
        window: Callable[[], webview.Window],
        prepare: Callable[[], None] = lambda: None,
        new_window: Callable[[], None] = lambda: None,
    ):
        self._session = session
        self._runtime = session.runtime
        self._window = window
        self._prepare = prepare
        self._new_window = new_window

    def new_window(self) -> None:
        with self._session.operation():
            self._new_window()

    def status(self) -> dict[str, object]:
        from scopecat.author_workspaces import local_author_workspaces

        from .author_environment import environment_python

        status = self._runtime.status()
        return {
            "home": str(self._runtime.home),
            "state": status.state,
            "detail": status.detail,
            "installation": self._runtime.installation().model_dump(mode="json"),
            "sources": [
                {
                    "directory": str(item.root),
                    "python": str(python) if python.is_file() else None,
                }
                for item in local_author_workspaces(self._runtime.root)
                for python in (environment_python(item.root / ".venv"),)
            ],
        }

    def choose_directory(self) -> str | None:
        import webview

        with self._session.operation():
            selected = self._window().create_file_dialog(webview.FileDialog.FOLDER)
            return selected[0] if selected else None

    def open_capture(self) -> dict[str, object] | None:
        import webview

        from .desktop_files import import_capture

        with self._session.operation():
            base_url = self._data_url()
            selected = self._window().create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=("Scopecat (*.scopecat)", "All files (*.*)"),
            )
            if not selected:
                return None
            return import_capture(base_url, Path(selected[0])).model_dump(mode="json")

    def save_capture(self, content_hash: str) -> str | None:
        import webview

        from .desktop_files import save_capture

        with self._session.operation():
            base_url = self._data_url()
            selected = self._window().create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename="capture.scopecat",
                file_types=("Scopecat (*.scopecat)",),
            )
            if not selected:
                return None
            destination = Path(selected[0])
            save_capture(base_url, content_hash, destination)
            return str(destination)

    def _data_url(self) -> str:
        if self._session.base_url is None:
            raise ValueError("应用尚未准备就绪，请稍后重试")
        return self._session.base_url

    def create_source(self, parent: str, name: str) -> str:
        from scopecat_server.scaffold import write_author_scaffold

        from .author_environment import create_client_environment

        directory = Path(parent)
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("请选择新代码目录的保存位置")
        if not name.strip() or name in (".", "..") or any(c in name for c in "/\\:"):
            raise ValueError("请输入单个新目录名称")
        path = directory / name
        with self._session.operation():
            write_author_scaffold(path)
            create_client_environment(self._runtime, path)
            self._register_source(path)
            return str(path)

    def _register_source(self, path: Path, python: Path | None = None) -> str:
        if not self._runtime.stop_if_idle():
            raise ValueError(
                "请先完成或停止当前工作，再添加代码目录；已创建的文件和环境保留"
            )
        identity = self._runtime.register_source(path, python=python)
        self._start("?" + urlencode({"source": str(path)}) + "#settings")
        return identity

    def register_source(self, directory: str) -> str:
        from scopecat.project import load_project

        from .author_environment import prepare_execution_environment

        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("请选择作者代码目录的完整路径")
        with self._session.operation():
            # Register the selected folder, not an ancestor discovered by walking up.
            _ = load_project(path / "scopecat.toml", resolve_adapter=False)
            python = (
                prepare_execution_environment(self._runtime, path)
                if (path / "pyproject.toml").is_file()
                else None
            )
            return self._register_source(path, python)

    def restart(self) -> None:
        with self._session.operation():
            if self._runtime.selection.exists():
                self._runtime.stop()
            self._start()

    def prepare_author_environment(self, directory: str) -> str:
        from .author_environment import prepare_execution_environment

        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("请选择已登记作者目录的完整路径")
        with self._session.operation():
            self._runtime.source(path)
            python = prepare_execution_environment(self._runtime, path)
            self._runtime.select_source_environment(path, python)
            return "后台依赖已准备；重新预览使用新环境，已有任务保持原环境。"

    def create_author_environment(self, directory: str, rebuild: bool = False) -> str:
        from .author_environment import create_client_environment

        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("请选择作者目录的完整路径")
        with self._session.operation():
            self._runtime.source(path)
            return str(create_client_environment(self._runtime, path, rebuild=rebuild))

    def retry(self) -> None:
        with self._session.operation():
            self._start()

    def _start(self, location: str = "") -> None:
        if self._session.closing.is_set():
            return
        self._prepare()
        record = self._runtime.start()
        self._session.connected(record.base_url)
        self._window().load_url(record.base_url + location)

    def exit(self, background: bool) -> None:
        if background:
            self._session.keep_running(lambda: hide_window(self._window()))
        else:
            self._session.exit()

    def request_exit(self) -> dict[str, int] | None:
        return self._session.request_exit()

    def wait_for_idle(self, wait: bool) -> None:
        self._session.wait_for_idle(wait)


def _page(content: str) -> str:
    return (
        '<!doctype html><html lang="zh"><meta charset="utf-8">'
        "<style>body{font:16px system-ui;background:#f8fafc;color:#172033;"
        "margin:0}main{max-width:720px;margin:12vh auto;padding:32px;"
        "background:white;border:1px solid #dbe2ea;border-radius:12px}"
        "p{line-height:1.7;overflow-wrap:anywhere}button{font:inherit;"
        "padding:9px 14px;margin:6px 6px 6px 0;cursor:pointer}"
        "[role=alert]{color:#b42318}</style><main>"
        + content
        + '<div id="quit-options" hidden>'
        '<button onclick="exit(false)">停止工作并退出</button> '
        '<button onclick="exit(true)">保留后台并隐藏窗口</button></div>'
        '<p id="progress" role="status"></p><p id="error" role="alert"></p>'
        "</main><script>"
        + Path(__file__).with_name("desktop_page.js").read_text(encoding="utf-8")
        + "</script></html>"
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
        '<button onclick="retry()">重试</button> '
        '<button onclick="restart()">'
        "停止后台并重新启动</button> "
        '<button onclick="quit()">'
        "退出 Scopecat</button>"
    )


def _window_close_handlers(
    window: webview.Window, closing: threading.Event, loaded: threading.Event
) -> tuple[Callable[[], bool], Callable[[], bool]]:
    def request_quit() -> bool:
        if closing.is_set():
            return True
        if loaded.is_set():
            # Dispatch off the UI thread so the JavaScript bridge cannot deadlock.
            def dispatch() -> None:
                show_window(window)
                window.run_js(
                    "if (typeof window.scopecatRequestExit === 'function') "
                    "{ window.scopecatRequestExit(); } "
                    "else { pywebview.api.request_exit(); }"
                )

            threading.Thread(target=dispatch, daemon=True).start()
        return False

    def request_close() -> bool:
        if closing.is_set():
            return True
        threading.Thread(target=lambda: hide_window(window), daemon=True).start()
        return False

    return request_close, request_quit


@dataclass
class DesktopView:
    window: webview.Window
    api: DesktopAPI
    loaded: threading.Event
    request_quit: Callable[[], bool]


class DesktopWindows:
    """Native windows share an application but own separate WebView state."""

    def __init__(self, session: DesktopSession, prepare: Callable[[], None]):
        self._session = session
        self._prepare = prepare
        self._views: list[DesktopView] = []
        self._lock = threading.RLock()
        session.connection_changed = self._reconnect

    def _reconnect(self, previous: str, current: str) -> None:
        old, new = urlsplit(previous), urlsplit(current)
        with self._lock:
            views = tuple(self._views)
        for view in views:
            location = view.window.get_current_url()
            if location is None:
                continue
            url = urlsplit(location)
            if (url.scheme, url.netloc) == (old.scheme, old.netloc):
                view.window.load_url(
                    urlunsplit(
                        (new.scheme, new.netloc, url.path, url.query, url.fragment)
                    )
                )

    @property
    def latest(self) -> DesktopView:
        with self._lock:
            return self._views[-1]

    def create(self) -> DesktopView:
        import webview

        with self._lock:
            api = DesktopAPI(
                self._session, lambda: window, self._prepare, self.new_window
            )
            window = cast(
                "webview.Window",
                webview.create_window(  # pyright: ignore[reportUnknownMemberType]
                    "Scopecat",
                    url=self._session.base_url,
                    html=(
                        None
                        if self._session.base_url
                        else _page("<h1>Scopecat</h1><p>正在准备应用，请稍候…</p>")
                    ),
                    js_api=api,
                    width=1280,
                    height=900,
                    min_size=(800, 600),
                    text_select=True,
                ),
            )
            loaded = threading.Event()
            window.events.loaded += loaded.set
            hide, request_quit = _window_close_handlers(
                window, self._session.closing, loaded
            )
            view = DesktopView(window, api, loaded, request_quit)

            def close() -> bool:
                with self._lock:
                    if self._session.closing.is_set():
                        return True
                    if len(self._views) > 1:
                        # Reserve the close before another window checks whether
                        # it is the last view keeping the native host alive.
                        self._views.remove(view)
                        return True
                return hide()

            def closed() -> None:
                with self._lock:
                    if view in self._views:
                        self._views.remove(view)

            window.events.closing += close
            window.events.closed += closed
            self._views.append(view)
            return view

    def new_window(self) -> None:
        if self._session.base_url is None:
            raise ValueError("应用尚在准备，请稍后新建窗口")
        self.create()

    def show(self) -> None:
        show_window(self.latest.window)

    def open_file(self) -> None:
        import webview

        active = webview.active_window()
        with self._lock:
            view = next(
                (view for view in self._views if view.window is active), self.latest
            )
        show_window(view.window)
        if self._session.base_url is None:
            view.window.create_confirmation_dialog(
                "暂时无法打开文件", "应用尚未准备就绪，请稍后重试"
            )
            return
        view.window.run_js("window.dispatchEvent(new Event('scopecat:open-file'));")

    def hide(self) -> None:
        with self._lock:
            views = tuple(self._views)
        for view in views:
            hide_window(view.window)

    def request_quit(self) -> None:
        _ = self.latest.request_quit()

    def destroy(self) -> None:
        with self._lock:
            views = tuple(self._views)
        for view in views:
            view.window.destroy()


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
    from PIL import Image
    from webview.menu import Menu, MenuAction

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

        session = DesktopSession(runtime, closing)
        windows = DesktopWindows(session, prepare or configure)
        first = windows.create()
        window, api, loaded = first.window, first.api, first.loaded

        def new_window_from_menu() -> None:
            try:
                with session.operation():
                    windows.new_window()
            except ValueError as error:
                windows.show()
                windows.latest.window.create_confirmation_dialog(
                    "暂时无法新建窗口", str(error)
                )

        tray_name = "tray-template.png" if sys.platform == "darwin" else "tray.png"
        with Image.open(Path(__file__).with_name("icons") / tray_name) as image:
            icon_image = image.convert("RGBA")

        def create_tray() -> Icon:
            return pystray.Icon(
                "Scopecat",
                icon_image,
                "Scopecat",
                menu=pystray.Menu(
                    pystray.MenuItem("打开 Scopecat", windows.show, default=True),
                    pystray.MenuItem("隐藏窗口（后台运行）", windows.hide),
                    pystray.MenuItem("新建窗口", new_window_from_menu),
                    pystray.MenuItem("退出 Scopecat", windows.request_quit),
                ),
            )

        stop_tray: Callable[[], None] | None = None

        def supervise() -> None:
            nonlocal stop_tray
            while not loaded.wait(0.5):
                if closing.is_set():
                    return
            if closing.is_set():
                session.finish_exit(windows.destroy)
                return
            try:
                # Cocoa status items need the application to have finished
                # launching; a queued callback before webview.start is too early.
                stop_tray = start_tray(create_tray)
                install_reopen_handler(
                    windows.show, windows.request_quit, closing.is_set
                )
                api.retry()
            except Exception as error:
                logging.getLogger(__name__).exception("Application startup failed")
                window.load_html(_recovery(error))
            while not closing.wait(0.5):
                try:
                    session.poll_exit()
                except Exception as error:
                    session.wait_for_idle(False)
                    windows.show()
                    windows.latest.window.load_html(_recovery(error))
                if activate.exists():
                    requested_package = activate.read_text(encoding="utf-8")
                    activate.unlink(missing_ok=True)
                    windows.show()
                    if requested_package != package_identity:
                        quit_current = windows.latest.window.create_confirmation_dialog(
                            "Scopecat 已安装其他版本",
                            "当前窗口仍由之前打开的版本运行。"
                            "请退出当前应用，再打开已安装的版本。现在退出？",
                        )
                        if quit_current:
                            windows.request_quit()
            # Keep the native completion hook out of the exposed JavaScript API.
            session.finish_exit(windows.destroy)

        # The GUI runs on the main thread. Its supervisor never opens a browser.
        try:
            # WinForms otherwise extracts pythonw.exe's icon, not the native
            # launcher's icon. Tray artwork is configured independently above.
            webview.start(
                supervise,
                menu=[
                    Menu(
                        "文件",
                        [
                            MenuAction("打开文件…", windows.open_file),
                            MenuAction("新建窗口", new_window_from_menu),
                        ],
                    )
                ],
                icon=(
                    str(Path(__file__).with_name("icons") / "Scopecat.ico")
                    if sys.platform == "win32"
                    else None
                ),
            )
        finally:
            if stop_tray is not None:
                stop_tray()
            closing.set()
    finally:
        lock.release()
