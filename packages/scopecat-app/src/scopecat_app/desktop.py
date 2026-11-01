"""Native application window; development commands never import the GUI runtime."""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

from filelock import FileLock, Timeout

from scopecat.records.application_open import ApplicationOpenTarget
from scopecat.records.practice import NotebookPracticeClearCommand
from scopecat_tutorials.lessons import LessonTopic

from .application_runtime import ApplicationRuntime
from .bundle import managed_path
from .data_reset import UnsupportedDataSpace, reset_store
from .desktop_objects import DesktopObjectOpener, object_location
from .desktop_platform import (
    hide_window,
    install_reopen_handler,
    install_window_menu,
    show_window,
    start_tray,
)
from .desktop_session import ClosePreflightAborted, DesktopSession

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
        new_window: Callable[[str | None], None] = lambda _run_id: None,
    ):
        self._session = session
        self._runtime = session.runtime
        self._window = window
        self._prepare = prepare
        self._new_window = new_window
        self._file_lock = threading.Lock()
        self._unsupported: UnsupportedDataSpace | None = None
        self._page_thread: threading.Thread | None = None
        self.close_reply: Callable[[str, str], None] = lambda _id, _status: None

    def complete_close_preflight(self, requestId: str, status: str) -> None:
        if status not in {"saved", "blocked", "unknown"}:
            raise ValueError("Invalid close preflight status")
        self.close_reply(requestId, status)

    def new_window(self) -> None:
        with self._session.operation(allow_files=True):
            self._new_window(None)

    def open_run_window(self, run_id: object) -> None:
        """Open one retained run, never a caller-supplied URL or another service."""
        if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 512:
            raise ValueError("无效的 run 标识")
        with self._session.operation(allow_files=True):
            self._new_window(run_id)

    def set_window_title(self, title: str) -> None:
        self._window().set_title(title)

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
                    "execution_python": str(item.python),
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

    def choose_file(self) -> str | None:
        import webview

        with self._session.operation():
            selected = self._window().create_file_dialog(
                webview.FileDialog.OPEN, allow_multiple=False
            )
            return selected[0] if selected else None

    def open_capture(self) -> dict[str, object] | None:
        import webview

        from .desktop_files import import_capture

        with self._file_lock:
            _ = self._data_url()
            selected = self._window().create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=("Scopecat (*.scopecat)", "All files (*.*)"),
            )
            if not selected:
                return None
            with self._session.file_operation() as cancel:
                return import_capture(
                    self._data_url(), Path(selected[0]), cancel
                ).model_dump(mode="json")

    def save_capture(self, content_hash: str) -> str | None:
        from .desktop_files import save_capture

        return self._save_file(
            lambda base, path, cancel: save_capture(base, content_hash, path, cancel)
        )

    def export_run(self, run_id: str) -> str | None:
        from .desktop_files import export_run

        return self._save_file(
            lambda base, path, cancel: export_run(base, run_id, path, cancel)
        )

    def save_captured_artifact(
        self, content_hash: str, analysis_hash: str, artifact_id: str, filename: str
    ) -> str | None:
        from .desktop_files import save_captured_artifact

        return self._save_file(
            lambda base, path, cancel: save_captured_artifact(
                base, content_hash, analysis_hash, artifact_id, path, cancel
            ),
            filename=Path(filename.replace("\\", "/")).name,
            file_types=("All files (*.*)",),
        )

    def save_configuration(self, document: str) -> str | None:
        from .desktop_files import save_configuration

        return self._save_file(
            lambda base, path, cancel: save_configuration(base, document, path, cancel),
            filename="configuration.json",
            file_types=("JSON (*.json)",),
        )

    def save_configuration_source(self, content_hash: str) -> str | None:
        from .desktop_files import save_configuration_source

        return self._save_file(
            lambda base, path, cancel: save_configuration_source(
                base, content_hash, path, cancel
            ),
            filename="author-source.zip",
            file_types=("ZIP (*.zip)",),
        )

    def _save_file(
        self,
        download: Callable[[str, Path, threading.Event], None],
        *,
        filename: str = "capture.scopecat",
        file_types: tuple[str, ...] = ("Scopecat (*.scopecat)",),
    ) -> str | None:
        import webview

        with self._file_lock:
            _ = self._data_url()
            selected = self._window().create_file_dialog(
                webview.FileDialog.SAVE,
                save_filename=filename,
                file_types=file_types,
            )
            if not selected:
                return None
            # Cocoa SAVE returns one string; other hosts return a path tuple.
            destination = Path(selected if isinstance(selected, str) else selected[0])
            with self._session.file_operation() as cancel:
                download(self._data_url(), destination, cancel)
            return str(destination)

    def _data_url(self) -> str:
        if self._session.base_url is None:
            raise ValueError("应用尚未准备就绪，请稍后重试")
        return self._session.base_url

    def notebook_journey(self, topic: LessonTopic = "parameters") -> dict[str, object]:
        from .notebook_journey import status

        return status(self._runtime, topic)

    def prepare_notebook_journey(
        self,
        parent: str | None = None,
        topic: LessonTopic = "parameters",
        repair: bool = False,
    ) -> dict[str, object]:
        from .notebook_journey import prepare

        with self._session.operation():
            return prepare(self._runtime, parent, topic, repair=repair).view()

    def open_lesson_notebook(self, topic: LessonTopic = "parameters") -> None:
        from .notebook_journey import current, open_editor

        with self._session.operation():
            journey = current(self._runtime, topic)
            if journey is None or not journey.ready:
                raise ValueError("请先从 Help 准备所选课程")
            open_editor(journey)

    def preview_notebook_cleanup(self, topic: LessonTopic) -> dict[str, object]:
        from .notebook_cleanup import preview

        with self._session.operation():
            return preview(self._runtime, topic)

    def clear_notebook_course(
        self, topic: LessonTopic, command_json: str
    ) -> dict[str, object]:
        from .notebook_cleanup import clear

        with self._session.operation():
            return clear(
                self._runtime,
                topic,
                NotebookPracticeClearCommand.model_validate_json(command_json),
            )

    def create_source(self, parent: str, name: str) -> str:
        from scopecat_server.scaffold import write_author_scaffold

        from .author_environment import (
            create_client_environment,
            prepare_execution_environment,
        )

        directory = Path(parent)
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("请选择新代码目录的保存位置")
        if not name.strip() or name in (".", "..") or any(c in name for c in "/\\:"):
            raise ValueError("请输入单个新目录名称")
        path = directory / name
        with self._session.operation():
            write_author_scaffold(path)
            create_client_environment(self._runtime, path)
            python = prepare_execution_environment(self._runtime, path)
            self._register_source(path, python)
            return str(path)

    def _register_source(self, path: Path, python: Path | None = None) -> str:
        identity = self._runtime.register_source(path, python=python)
        self._start("?" + urlencode({"source": str(path)}) + "#settings")
        return identity

    def register_source(self, directory: str, interpreter: str) -> str:
        from scopecat.project import load_project

        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("请选择作者代码目录的完整路径")
        with self._session.operation():
            # Register the selected folder, not an ancestor discovered by walking up.
            _ = load_project(path / "scopecat.toml", resolve_adapter=False)
            python = Path(interpreter)
            if not python.is_absolute() or not python.is_file():
                raise ValueError("请选择已有执行环境的 Python 完整路径")
            return self._register_source(path, python)

    def select_source_environment(self, directory: str, interpreter: str) -> str:
        path, python = Path(directory), Path(interpreter)
        if not path.is_absolute() or not python.is_absolute() or not python.is_file():
            raise ValueError("请选择源码目录和已有 Python 的完整路径")
        with self._session.operation():
            self._runtime.select_source_environment(path, python)
        return "执行环境已选择；重新准备使用新环境，已有任务保持原环境。"

    def restart(self) -> None:
        def start() -> None:
            location = self._window().get_current_url()
            current = urlsplit(location if isinstance(location, str) else "")
            self._start(
                urlunsplit(("", "", current.path, current.query, current.fragment))
            )

        self._session.restart(start)

    def backup_application(self) -> None:
        """Capture this idle home with the existing stopped snapshot contract."""
        from uuid import uuid4

        import webview

        with ExitStack() as operation:
            operation.enter_context(self._session.operation())
            selected = self._window().create_file_dialog(webview.FileDialog.FOLDER)
            if not selected:
                return
            parent = Path(
                selected if isinstance(selected, str) else selected[0]
            ).resolve()
            if not parent.is_dir() or parent.is_relative_to(
                self._runtime.home.resolve()
            ):
                raise ValueError("请选择应用目录之外的备份目录")
            destination = parent / f"scopecat-snapshot-{uuid4().hex}"
            if not self._window().create_confirmation_dialog(
                "备份应用数据？",
                f"空闲时停止后台并备份到：{destination}\n"
                "包含科学记录、不可变源码和作者登记；"
                "不包含外部代码目录、Python 环境或安装包。\n"
                "请先保存并停止编辑练习文件。忙碌时不会停止工作。\n"
                "完成后手动重新打开应用；原数据与环境保留。继续？",
            ):
                return
            if not self._runtime.stop_if_idle():
                raise ValueError("后台仍有工作；请等待空闲后备份，运行与数据保留")
            self._after_reply(lambda: self._capture_backup(destination), operation)

    def _capture_backup(self, destination: Path) -> None:
        from scopecat_server.snapshots import create_snapshot, verify_snapshot

        try:
            self._window().load_html(_page("<h1>正在备份应用数据…</h1>"))
            _ = create_snapshot(self._runtime.instance, destination)
            _ = verify_snapshot(destination)
            result = (
                "<h1>备份完成并已校验</h1>"
                f"<p>备份位置：{escape(str(destination))}</p>"
                "<p>请保留整个目录；另行保存外部源码、Python 环境和原安装包。"
                "恢复须满足数据格式与快照兼容要求；此操作不执行格式迁移。</p>"
            )
        except Exception as error:
            result = (
                "<h1>备份未完成或未通过校验</h1>"
                f"<p>{escape(str(error))}</p>"
                f"<p>目标位置：{escape(str(destination))}</p>"
                "<p>不要将该目录作为已验证备份。原数据与环境未删除；"
                "重新打开后可再次备份。</p>"
            )
        self._window().load_html(
            _page(
                result + '<button onclick="retry()">重新打开应用</button>'
                '<button onclick="quit()">退出</button>'
            )
        )

    def prepare_author_environment(self, directory: str) -> str:
        from .author_environment import prepare_execution_environment

        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("请选择作者目录的完整路径")
        with self._session.operation():
            python = prepare_execution_environment(self._runtime, path)
            self._register_source(path, python)
            return "执行环境已准备并登记；重新预览使用新环境，已有任务保持原环境。"

    def create_author_environment(self, directory: str, rebuild: bool = False) -> str:
        from .author_environment import create_client_environment

        path = Path(directory)
        if not path.is_absolute():
            raise ValueError("请选择作者目录的完整路径")
        with self._session.operation():
            self._runtime.source(path)
            return str(create_client_environment(self._runtime, path, rebuild=rebuild))

    def _after_reply(self, action: Callable[[], None], operation: ExitStack) -> None:
        """Keep admission locked until the bridge has returned to its original page."""
        bridge = threading.current_thread()
        retained = operation.pop_all()

        def complete() -> None:
            with retained:
                # pywebview sends its JS callback after the exposed method returns.
                # Joining that caller fences page replacement without a timing delay.
                bridge.join()
                try:
                    action()
                except Exception as error:
                    logging.getLogger(__name__).exception(
                        "Desktop page operation failed"
                    )
                    self._window().load_html(
                        _recovery(
                            error, can_reset=self._session.prepare_reset is not None
                        )
                    )

        self._page_thread = threading.Thread(target=complete)
        try:
            self._page_thread.start()
        except BaseException:
            retained.close()
            raise

    def retry(self, after_reply: bool = False) -> None:
        with ExitStack() as operation:
            operation.enter_context(self._session.operation())
            if after_reply:
                self._after_reply(self._start, operation)
            else:
                self._start()

    def _start(self, location: str = "") -> None:
        if self._session.closing.is_set():
            return
        self._unsupported = None
        try:
            self._prepare()
            record = self._runtime.start()
        except UnsupportedDataSpace as error:
            self._unsupported = error
            self._window().load_html(
                _recovery(error, can_reset=self._session.prepare_reset is not None)
            )
            raise
        self._session.connected(record.base_url)
        _replace_location(
            self._window(), (self._session.page_url or record.base_url) + location
        )

    def upgrade_data(self) -> bool:
        """Upgrade only the confirmed stopped format, then retry normal startup."""
        from .data_reset import upgrade_store

        with self._session.operation():
            failure = self._unsupported
            if failure is None or self._session.base_url is not None:
                raise ValueError("只有格式升级恢复页可以执行此操作")
            if not self._window().create_confirmation_dialog(
                "备份并升级应用数据？",
                "将数据库备份到数据目录的 upgrade-backups，再原地升级。\n"
                "旧实验和分析保留；旧分析用于受管候选时需重新分析。\n"
                "失败会回滚数据库；数据目录保持不变。继续？",
            ):
                return False
            backup = upgrade_store(self._runtime, failure)
            self._unsupported = None
            notice = f"数据库恢复备份：{backup}" if backup else "此前升级已完成。"
            self._window().load_html(
                _page(
                    "<h1>数据升级完成</h1><p>" + escape(notice) + "</p>"
                    '<button onclick="retry()">重新打开应用</button>'
                    '<button onclick="quit()">退出</button>'
                )
            )
            return True

    def reset_data(self, skip_backup: bool = False) -> bool:
        """Confirmation belongs to the native host, not the unavailable backend."""
        with self._session.operation():
            failure, prepare = self._unsupported, self._session.prepare_reset
            if failure is None or prepare is None or self._session.base_url is not None:
                raise ValueError("只有不支持的数据格式启动失败时可以重新开始")
            import webview

            from .application_runtime import write_state

            settings = managed_path(
                self._runtime.home, self._runtime.home / "desktop/reset-backup.json"
            )
            marker = self._runtime.home / "data-reset.json"
            prior_backup = (
                cast("dict[str, str | None]", json.loads(marker.read_bytes())).get(
                    "backup"
                )
                if marker.exists()
                else None
            )
            destination = None
            if not skip_backup and prior_backup is None:
                remembered = (
                    cast("dict[str, str]", json.loads(settings.read_bytes())).get(
                        "directory", ""
                    )
                    if settings.exists()
                    else ""
                )
                selected = self._window().create_file_dialog(
                    webview.FileDialog.FOLDER, directory=remembered
                )
                if not selected:
                    return False
                destination = Path(
                    selected if isinstance(selected, str) else selected[0]
                )
            backup_notice = (
                "本次跳过备份。删除后无法恢复。\n"
                if skip_backup
                else f"先将原格式数据备份并校验到：{destination}\n"
                "备份不保证可由当前版本恢复；失败不会删除。\n"
            )
            if prior_backup is not None:
                backup_notice = (
                    f"验证此前已完成的备份：{prior_backup}\n不会重新备份到其他目录。\n"
                )
            if marker.exists():
                backup_notice += (
                    "此前可能已部分删除；本次若新建备份，仅包含当前剩余数据。\n"
                )
            if not self._window().create_confirmation_dialog(
                "删除应用数据并重新初始化？",
                backup_notice
                + f"永久删除：{self._runtime.instance.runtime_binding.data_root} 下的 "
                "control.sqlite3、SQLite sidecar 和 objects/。\n"
                "实验、参数、草稿、设备配置及其存储对象将不可恢复地删除。\n"
                "保留原始源码、环境、安装文件、作者目录登记及其他内容；不自动运行任务或连接设备。\n"
                "中断可能造成部分删除，无法回滚。确定永久删除并重新初始化？",
            ):
                return False
            if destination is not None:
                write_state(settings, json.dumps({"directory": str(destination)}))
            record = reset_store(self._runtime, failure, prepare, destination)
            self._unsupported = None
            self._session.connected(record.base_url)
            _replace_location(self._window(), self._session.page_url or record.base_url)
            return True

    def exit(self, background: bool) -> None:
        if background:
            self._session.keep_running(lambda: hide_window(self._window()))
        else:
            self._session.exit()

    def request_exit(self) -> dict[str, int] | None:
        return self._session.request_exit()

    def exit_status(self) -> dict[str, object]:
        return self._session.exit_status()

    def wait_for_idle(self, wait: bool) -> None:
        self._session.wait_for_idle(wait)


def _replace_location(window: webview.Window, url: str) -> None:
    # Startup/recovery and backend reconnection are not user navigation.
    # Replace the entry itself so native back gestures cannot reach these pages.
    window.run_js(f"window.location.replace({json.dumps(url)});")


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


def _recovery(error: Exception, *, can_reset: bool = False) -> str:
    reset = (
        "<p>可明确删除应用实验数据并原位初始化；不可恢复。</p>"
        f"<p>应用数据位置：{escape(str(error.home))}</p>"
        '<p><label><input id="skip-backup" type="checkbox">'
        "本次跳过备份（删除后无法恢复）</label></p>"
        '<button onclick="resetData()">删除应用数据并重新初始化…</button>'
        if isinstance(error, UnsupportedDataSpace) and can_reset
        else ""
    )
    upgrade = (
        "<p>可保留已有数据，备份数据库后原地升级。</p>"
        '<button onclick="upgradeData()">备份并升级</button>'
        if isinstance(error, UnsupportedDataSpace)
        and (error.actual, error.expected) == (115, 116)
        else ""
    )
    return _page(
        "<h1>启动未完成</h1>"
        + upgrade
        + reset
        + "<p>应用尚未准备就绪。可以重试，或停止本应用的后台后重新启动。"
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
    initial_location: str | None = None
    close_request: str | None = None
    close_status: str | None = None
    close_reply_thread: threading.Thread | None = None
    close_ready: threading.Event = field(default_factory=threading.Event)
    close_authorized: bool = False
    close_finished: threading.Event = field(default_factory=threading.Event)


class DesktopWindows:
    """Native windows share an application but own separate WebView state."""

    def __init__(self, session: DesktopSession, prepare: Callable[[], None]):
        self._session = session
        self._prepare = prepare
        self._views: list[DesktopView] = []
        self._tray_available = False
        self._tray_generation = 0
        self._lock = threading.RLock()
        session.connection_changed = self._reconnect
        self._preflight_lock = threading.Lock()
        self.preflight_timeout = 10.0
        session.close_preflight = self.preflight
        session.commit_close = self._commit_close
        session.close_aborted = self._abort_close
        self._closing_certificate: tuple[str, tuple[DesktopView, ...]] | None = None
        self._certificate: tuple[str, tuple[DesktopView, ...]] | None = None

    def _validate_certificate(self) -> None:
        certificate = self._certificate
        if certificate is None:
            raise ValueError("Saved input was not confirmed for this close request")
        request_id, views = certificate
        if any(
            view not in self._views
            or view.close_request != request_id
            or view.close_status != "saved"
            for view in views
        ):
            raise ValueError(
                "The page changed before closing. The window is retained; "
                "the background may already have stopped. Reconnect before retrying."
            )

    def _commit_close(self) -> None:
        with self._lock:
            self._validate_certificate()
            self._closing_certificate = self._certificate
            self._session.closing.set()

    def _abort_close(self, message: str) -> None:
        with self._lock:
            certificate = self._closing_certificate
            self._closing_certificate = None
            views = tuple(self._views)
        if certificate is None:
            return
        request_id, _ = certificate
        for view in views:
            view.close_authorized = False
            view.close_request = None
            try:
                self._dispatch(
                    view,
                    "scopecat:close-release",
                    {
                        "requestId": request_id,
                        "message": message,
                    },
                )
                show_window(view.window)
            except Exception:
                logging.getLogger(__name__).exception("Could not show retained view")

    def _reply(self, view: DesktopView, request_id: str, status: str) -> None:
        with self._lock:
            if (
                view in self._views
                and view.close_request == request_id
                and view.close_status is None
            ):
                view.close_status = status
                view.close_reply_thread = threading.current_thread()
                view.close_ready.set()

    @staticmethod
    def _dispatch(view: DesktopView, event: str, detail: dict[str, str]) -> None:
        view.window.run_js(
            f"window.dispatchEvent(new CustomEvent({json.dumps(event)}, "
            f"{{detail:{json.dumps(detail)}}}));"
        )

    @contextmanager
    def preflight(self, selected: DesktopView | None = None) -> Generator[None]:
        # This waits only in a bridge/background worker. Native closing callbacks
        # return immediately; JS acknowledgment uses an independent bridge call.
        if not self._preflight_lock.acquire(blocking=False):
            raise ValueError("Another close request is checking saved input")
        request_id = uuid4().hex
        message = ""
        views: tuple[DesktopView, ...] = ()
        try:
            with self._lock:
                views = (selected,) if selected is not None else tuple(self._views)
                if not views or any(view not in self._views for view in views):
                    raise ValueError("The requested view is no longer available")
                self._certificate = (request_id, views)
                for view in views:
                    view.close_request = request_id
                    view.close_status = None
                    view.close_reply_thread = None
                    view.close_ready.clear()
            for view in views:
                self._dispatch(
                    view, "scopecat:close-preflight", {"requestId": request_id}
                )
            deadline = time.monotonic() + self.preflight_timeout
            for view in views:
                if not view.close_ready.wait(max(0, deadline - time.monotonic())):
                    raise ValueError(
                        "Input saving could not be confirmed; "
                        "keep this window open and retry"
                    )
                if view.close_status != "saved":
                    raise ValueError(
                        "Input is not confirmed saved; resolve saving before closing"
                    )
            for view in views:
                reply = view.close_reply_thread
                if reply is not None and reply is not threading.current_thread():
                    # pywebview delivers its Promise result after the exposed
                    # method returns. Keep the page alive through that reply.
                    reply.join(max(0, deadline - time.monotonic()))
                    if reply.is_alive():
                        raise ValueError(
                            "Save acknowledgment is still pending; retry closing"
                        )
            with self._lock:
                if any(view.close_status != "saved" for view in views):
                    raise ValueError("The page changed while confirming saved input")
            yield
        except Exception as error:
            message = str(error)
            raise
        finally:
            for view in views:
                with self._lock:
                    retain = view.close_authorized and view.close_status == "saved"
                    if not retain and not self._session.closing.is_set():
                        view.close_authorized = False
                        if view.close_request == request_id:
                            view.close_request = None
                if not self._session.closing.is_set() and not retain:
                    try:
                        self._dispatch(
                            view,
                            "scopecat:close-release",
                            {
                                "requestId": request_id,
                                "message": message,
                            },
                        )
                        if message:
                            show_window(view.window)
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Could not release close preflight"
                        )
            self._certificate = None
            self._preflight_lock.release()

    def _close_view(self, view: DesktopView) -> None:
        try:
            with self.preflight(view), self._session.operation(allow_files=True):
                with self._lock:
                    last = sum(not item.close_authorized for item in self._views) <= 1
                    if not last:
                        self._validate_certificate()
                        view.close_authorized = True
                if last:
                    self._hide_or_retain(view.window)
                    return
                try:
                    view.window.destroy()
                except Exception:
                    view.close_authorized = False
                    raise
        except Exception:
            logging.getLogger(__name__).exception("Window close retained the view")

    def _reconnect(self, previous: str, current: str) -> None:
        old, new = (
            (urlsplit(self._session.ui_url), urlsplit(self._session.ui_url))
            if self._session.ui_url
            else (urlsplit(previous), urlsplit(current))
        )
        with self._lock:
            views = tuple(self._views)
        for view in views:
            location = view.window.get_current_url()
            if location is None:
                continue
            url = urlsplit(location)
            if (url.scheme, url.netloc) == (old.scheme, old.netloc):
                _replace_location(
                    view.window,
                    urlunsplit(
                        (new.scheme, new.netloc, url.path, url.query, url.fragment)
                    ),
                )

    @property
    def latest(self) -> DesktopView:
        with self._lock:
            return self._views[-1]

    def open_target(self, target: ApplicationOpenTarget) -> None:
        page = self._session.page_url
        if page is None:
            raise ValueError(
                "Application is not connected; reconnect it before opening"
            )
        location = object_location(page, target)
        with self._lock:
            view = next(
                (
                    view
                    for view in self._views
                    if view.window.get_current_url() == location
                    or (not view.loaded.is_set() and view.initial_location == location)
                ),
                None,
            )
            if view is None:
                view = self.create(target=target)
        if not view.loaded.wait(5):
            raise TimeoutError("The requested window has not finished loading")
        show_window(view.window)

    def create(
        self, run_id: str | None = None, *, target: ApplicationOpenTarget | None = None
    ) -> DesktopView:
        import webview

        with self._lock:
            if self._preflight_lock.locked() or self._session.closing.is_set():
                raise ValueError("Wait for input saving before opening another window")
            location = self._session.page_url
            if run_id is not None:
                if location is None:
                    raise ValueError("应用尚在准备，请稍后新建窗口")
                base = urlsplit(location)
                location = urlunsplit(
                    (base.scheme, base.netloc, "/", urlencode({"run": run_id}), "")
                )
            if target is not None:
                if location is None:
                    raise ValueError("Application is not connected")
                location = object_location(location, target)
            api = DesktopAPI(
                self._session, lambda: window, self._prepare, self.new_window
            )
            window = cast(
                "webview.Window",
                webview.create_window(  # pyright: ignore[reportUnknownMemberType]
                    "Scopecat",
                    url=location,
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

            def page_loaded() -> None:
                loaded.set()
                with self._lock:
                    # Reload/replacement invalidates the old page's certificate.
                    if view.close_request is not None:
                        view.close_status = "unknown"
                        view.close_ready.set()

            window.events.loaded += page_loaded
            _hide, request_quit = _window_close_handlers(
                window, self._session.closing, loaded
            )
            view = DesktopView(window, api, loaded, request_quit, location)

            api.close_reply = lambda request_id, status: self._reply(
                view, request_id, status
            )

            def close() -> bool:
                if self._session.closing.is_set():
                    if view.close_status == "saved":
                        return True
                    view.close_finished.set()
                    return False
                if view.close_authorized:
                    if view.close_request is not None and view.close_status == "saved":
                        return True
                    view.close_authorized = False
                    return False
                try:
                    with self._session.operation(allow_files=True), self._lock:
                        destructive = len(self._views) > 1
                    if destructive:
                        threading.Thread(
                            target=self._close_view, args=(view,), daemon=True
                        ).start()
                        return False
                except ValueError:
                    pass
                threading.Thread(
                    target=self._hide_or_retain, args=(window,), daemon=True
                ).start()
                return False

            def closed() -> None:
                with self._lock:
                    if view in self._views:
                        self._views.remove(view)
                    view.close_finished.set()

            window.events.closing += close
            window.events.closed += closed
            self._views.append(view)
            return view

    def new_window(self, run_id: str | None = None) -> None:
        if self._session.base_url is None:
            raise ValueError("应用尚在准备，请稍后新建窗口")
        if run_id is None:
            self.create()
            return
        target = ApplicationOpenTarget(kind="run", object_id=run_id)
        page = self._session.page_url
        if page is None:
            raise ValueError("Application is not connected")
        location = object_location(page, target)
        with self._lock:
            view = next(
                (
                    view
                    for view in self._views
                    if view.window.get_current_url() == location
                    or (not view.loaded.is_set() and view.initial_location == location)
                ),
                None,
            )
            if view is None:
                view = self.create(target=target)
        # A bridge request can return while a new WebView loads. The retained
        # initial location deduplicates another request during that interval.
        show_window(view.window)

    def show(self) -> None:
        with self._lock:
            views = tuple(self._views)
        for view in views:
            show_window(view.window)

    def _command_window(self) -> webview.Window:
        import webview

        active = webview.active_window()
        with self._lock:
            view = next(
                (view for view in self._views if view.window is active), self.latest
            )
        show_window(view.window)
        return view.window

    def navigate_history(self, *, backward: bool) -> None:
        window = self._command_window()
        event = "scopecat:back" if backward else "scopecat:forward"
        window.run_js(f"window.dispatchEvent(new Event('{event}'));")

    def open_file(self) -> None:
        window = self._command_window()
        if self._session.base_url is None:
            window.create_confirmation_dialog(
                "暂时无法打开文件", "应用尚未准备就绪，请稍后重试"
            )
            return
        window.run_js("window.dispatchEvent(new Event('scopecat:open-file'));")

    def zoom(self, command: Literal["in", "out", "reset"]) -> None:
        self._command_window().run_js(
            f"window.dispatchEvent(new Event('scopecat:zoom-{command}'));"
        )

    def find(self) -> None:
        self._command_window().run_js(
            "window.dispatchEvent(new Event('scopecat:find'));"
        )

    def tray_changed(self, available: bool) -> None:
        with self._lock:
            previous = self._tray_available
            self._tray_available = available
            if previous != available:
                self._tray_generation += 1
        if previous and not available and not self._session.closing.is_set():
            self.show()

    def tray_status(self) -> None:
        with self._lock:
            available = self._tray_available
        self._command_window().create_confirmation_dialog(
            "后台运行",
            "系统托盘可用。关闭最后一个窗口后，可从托盘重新打开或退出。"
            if available
            else "系统托盘不可用或尚未确认。最后一个窗口将保持打开，"
            "以便返回正在进行的工作。可最小化窗口；"
            "要退出，请选择“文件 → 退出 Scopecat”。"
            "关闭窗口不会停止测量。",
        )

    def _try_hide(self, window: webview.Window) -> bool:
        with self._lock:
            available = self._tray_available
            generation = self._tray_generation
        if not available:
            return False
        # Native Invoke can synchronously wait on a closing callback which
        # takes our lock. Never hold that lock across native UI operations.
        hide_window(window)
        with self._lock:
            retained = self._tray_available and generation == self._tray_generation
        if not retained:
            # Tray loss may have restored this view before the native hide
            # completed. Undo that late hide, even if the tray has reappeared.
            show_window(window)
        return retained

    def _hide_or_retain(self, window: webview.Window) -> None:
        if self._try_hide(window):
            return
        show_window(window)
        self.tray_status()

    def hide(self) -> None:
        with self._lock:
            views = tuple(self._views)
        hidden = [self._try_hide(view.window) for view in views]
        if not all(hidden):
            self.tray_status()

    def request_quit(self) -> None:
        _ = self.latest.request_quit()

    def destroy(self) -> None:
        with self._lock:
            views = tuple(self._views)
            certificate = self._closing_certificate
            if certificate is None or any(
                view.close_request != certificate[0] or view.close_status != "saved"
                for view in views
            ):
                raise ClosePreflightAborted(
                    "A page changed before closing. Views are retained; "
                    "the background may have stopped. Reconnect before retrying."
                )
        deadline = time.monotonic() + self.preflight_timeout
        for view in views:
            view.close_finished.clear()
            view.window.destroy()
            if not view.close_finished.wait(max(0, deadline - time.monotonic())):
                raise ClosePreflightAborted(
                    "Window closure was not confirmed; retry closing"
                )
            with self._lock:
                if view in self._views:
                    raise ClosePreflightAborted(
                        "A changed page refused closure. "
                        "Reconnect and retry when input is saved."
                    )


def run(
    home: Path,
    source: Path | None = None,
    *,
    prepare: Callable[[], None] | None = None,
    package_identity: str = "source-development",
    prepare_reset: Callable[[], None] | None = None,
    session: DesktopSession | None = None,
) -> None:
    # Optional dependency: command-line/service installations stay headless.
    import webview
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
        runtime = session.runtime if session is not None else ApplicationRuntime(home)
        closing = session.closing if session is not None else threading.Event()

        def configure() -> None:
            selected = runtime.configure(
                static_dir=source / "apps/scopecat-ui/dist" if source else None
            )
            checked = runtime.qualify(
                selected.python,
                selected.static_dir,
                delivery_root=selected.delivery_root,
            )
            if checked != selected or runtime.pending.exists():
                runtime.select(checked)

        session = session or DesktopSession(runtime, closing)
        session.prepare_reset = prepare_reset
        windows = DesktopWindows(session, prepare or configure)
        opener = DesktopObjectOpener(session, windows.open_target)
        first = windows.create()
        window, api, loaded = first.window, first.api, first.loaded

        def new_window_from_menu() -> None:
            try:
                with session.operation(allow_files=True):
                    windows.new_window()
            except ValueError as error:
                windows.show()
                windows.latest.window.create_confirmation_dialog(
                    "暂时无法新建窗口", str(error)
                )

        def create_tray() -> Icon:
            import pystray
            from PIL import Image

            tray_name = "tray-template.png" if sys.platform == "darwin" else "tray.png"
            with Image.open(Path(__file__).with_name("icons") / tray_name) as image:
                icon_image = image.convert("RGBA")
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
            if closing.is_set() and session.finish_exit(windows.destroy):
                return
            try:
                # Cocoa status items need the application to have finished
                # launching; a queued callback before webview.start is too early.
                stop_tray = start_tray(create_tray, windows.tray_changed)
                install_window_menu()
                install_reopen_handler(
                    windows.show, windows.request_quit, closing.is_set
                )
                api.retry()
            except Exception as error:
                logging.getLogger(__name__).exception("Application startup failed")
                window.load_html(_recovery(error, can_reset=prepare_reset is not None))
            while True:
                while not closing.wait(0.5):
                    opener.pump()
                    try:
                        session.poll_exit()
                    except Exception:
                        session.wait_for_idle(False)
                        windows.show()
                        logging.getLogger(__name__).exception(
                            "Deferred quit retained open views"
                        )
                    if activate.exists():
                        requested_package = activate.read_text(encoding="utf-8")
                        activate.unlink(missing_ok=True)
                        windows.show()
                        if requested_package != package_identity:
                            quit_current = (
                                windows.latest.window.create_confirmation_dialog(
                                    "Scopecat 已安装其他版本",
                                    "当前窗口仍由之前打开的版本运行。"
                                    "请退出当前应用，再打开已安装的版本。现在退出？",
                                )
                            )
                            if quit_current:
                                windows.request_quit()
                # Keep the native completion hook out of the exposed JavaScript API.
                if session.finish_exit(windows.destroy):
                    return

        # The GUI runs on the main thread. Its supervisor never opens a browser.
        modifier = "⌘" if sys.platform == "darwin" else "Ctrl+"
        try:
            # WinForms otherwise extracts pythonw.exe's icon, not the native
            # launcher's icon. Tray artwork is configured independently above.
            webview.start(
                supervise,
                menu=[
                    Menu(
                        "文件",
                        [
                            MenuAction(f"打开文件…（{modifier}O）", windows.open_file),
                            MenuAction("新建窗口", new_window_from_menu),
                            MenuAction("后台运行状态…", windows.tray_status),
                            MenuAction(
                                f"退出 Scopecat（{modifier}Q）", windows.request_quit
                            ),
                        ],
                    ),
                    Menu(
                        "视图",
                        [
                            MenuAction(f"查找…（{modifier}F）", windows.find),
                            MenuAction(
                                f"放大（{modifier}+）", lambda: windows.zoom("in")
                            ),
                            MenuAction(
                                f"缩小（{modifier}-）", lambda: windows.zoom("out")
                            ),
                            MenuAction(
                                f"实际大小 100%（{modifier}0）",
                                lambda: windows.zoom("reset"),
                            ),
                        ],
                    ),
                    Menu(
                        "导航",
                        [
                            MenuAction(
                                "后退", lambda: windows.navigate_history(backward=True)
                            ),
                            MenuAction(
                                "前进", lambda: windows.navigate_history(backward=False)
                            ),
                        ],
                    ),
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
            opener.close()
    finally:
        lock.release()
