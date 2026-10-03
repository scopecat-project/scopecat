"""Application-wide operations and shutdown, independent of any one window."""

from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager

from .application_runtime import ApplicationRuntime


class DesktopSession:
    """Share lifecycle decisions; file transfers remain independent window work."""

    def __init__(self, runtime: ApplicationRuntime, closing: threading.Event):
        self.runtime = runtime
        self.closing = closing
        self.base_url: str | None = None
        self.connection_changed: Callable[[str, str], None] = lambda _old, _new: None
        self._operation_lock = threading.Lock()
        self._exit_thread: threading.Thread | None = None
        self._waiting = threading.Event()
        self._files = threading.Condition()
        self._file_operations: dict[threading.Thread, threading.Event] = {}
        self._file_threads: set[threading.Thread] = set()

    @contextmanager
    def file_operation(self) -> Generator[threading.Event]:
        thread = threading.current_thread()
        cancel = threading.Event()
        with self.operation(allow_files=True), self._files:
            self._file_operations[thread] = cancel
            self._file_threads = {
                item for item in self._file_threads if item.is_alive()
            }
            self._file_threads.add(thread)
        try:
            yield cancel
        finally:
            with self._files:
                del self._file_operations[thread]
                self._files.notify_all()

    def connected(self, base_url: str) -> None:
        previous = self.base_url
        self.base_url = base_url
        if previous is not None and previous != base_url:
            self.connection_changed(previous, base_url)

    @contextmanager
    def operation(self, *, allow_files: bool = False) -> Generator[None]:
        if not self._operation_lock.acquire(blocking=False):
            raise ValueError("应用正在执行另一项操作，请稍候再试")
        try:
            if self.closing.is_set():
                raise ValueError("应用正在关闭，无法执行新操作")
            with self._files:
                if self._file_operations and not allow_files:
                    raise ValueError("请等待文件操作完成后再修改应用环境")
            yield
        finally:
            self._operation_lock.release()

    def exit(self) -> None:
        with self.operation(allow_files=True):
            with self._files:
                for cancel in self._file_operations.values():
                    cancel.set()
                if not self._files.wait_for(
                    lambda: not self._file_operations, timeout=35
                ):
                    raise ValueError("文件操作尚未结束，请稍后重试退出")
            if self.runtime.selection.exists():
                self.runtime.stop()
            self._exit_thread = threading.current_thread()
            self.closing.set()

    def request_exit(self) -> dict[str, int] | None:
        with self.operation(allow_files=True):
            with self._files:
                if self._file_operations:
                    return {
                        "file_operations": len(self._file_operations),
                        **self.runtime.activity().model_dump(),
                    }
            if not self.runtime.selection.exists() or self.runtime.stop_if_idle():
                self._exit_thread = threading.current_thread()
                self.closing.set()
                return None
            return self.runtime.activity().model_dump()

    def wait_for_idle(self, wait: bool) -> None:
        with self.operation(allow_files=True):
            if wait:
                self._exit_thread = threading.current_thread()
                self._waiting.set()
            else:
                self._waiting.clear()

    def keep_running(self, hide: Callable[[], None]) -> None:
        with self.operation(allow_files=True):
            self._waiting.clear()
            hide()

    def poll_exit(self) -> None:
        if self._waiting.is_set() and self._operation_lock.acquire(blocking=False):
            try:
                with self._files:
                    if self._file_operations:
                        return
                if self.runtime.stop_if_idle():
                    self.closing.set()
            finally:
                self._operation_lock.release()

    def finish_exit(self, destroy_windows: Callable[[], None]) -> None:
        for thread in self._file_threads:
            thread.join()
        if self._exit_thread is not None:
            # Keep every page alive until the requesting bridge has delivered
            # its result; WebKit may otherwise wait forever on that callback.
            self._exit_thread.join()
        destroy_windows()
