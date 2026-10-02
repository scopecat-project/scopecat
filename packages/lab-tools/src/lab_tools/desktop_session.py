"""Application-wide operations and shutdown, independent of any one window."""

from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager

from .application_runtime import ApplicationRuntime


class DesktopSession:
    """All window bridges share one operation lock and one quit decision."""

    def __init__(self, runtime: ApplicationRuntime, closing: threading.Event):
        self.runtime = runtime
        self.closing = closing
        self.base_url: str | None = None
        self.connection_changed: Callable[[str, str], None] = lambda _old, _new: None
        self._operation_lock = threading.Lock()
        self._exit_thread: threading.Thread | None = None
        self._waiting = threading.Event()

    def connected(self, base_url: str) -> None:
        previous = self.base_url
        self.base_url = base_url
        if previous is not None and previous != base_url:
            self.connection_changed(previous, base_url)

    @contextmanager
    def operation(self) -> Generator[None]:
        if not self._operation_lock.acquire(blocking=False):
            raise ValueError("应用正在执行另一项操作，请稍候再试")
        try:
            if self.closing.is_set():
                raise ValueError("应用正在关闭，无法执行新操作")
            yield
        finally:
            self._operation_lock.release()

    def exit(self) -> None:
        with self.operation():
            if self.runtime.selection.exists():
                self.runtime.stop()
            self._exit_thread = threading.current_thread()
            self.closing.set()

    def request_exit(self) -> dict[str, int] | None:
        with self.operation():
            if not self.runtime.selection.exists() or self.runtime.stop_if_idle():
                self._exit_thread = threading.current_thread()
                self.closing.set()
                return None
            return self.runtime.activity().model_dump()

    def wait_for_idle(self, wait: bool) -> None:
        with self.operation():
            if wait:
                self._exit_thread = threading.current_thread()
                self._waiting.set()
            else:
                self._waiting.clear()

    def keep_running(self, hide: Callable[[], None]) -> None:
        with self.operation():
            self._waiting.clear()
            hide()

    def poll_exit(self) -> None:
        if self._waiting.is_set() and self._operation_lock.acquire(blocking=False):
            try:
                if self.runtime.stop_if_idle():
                    self.closing.set()
            finally:
                self._operation_lock.release()

    def finish_exit(self, destroy_windows: Callable[[], None]) -> None:
        if self._exit_thread is not None:
            # Keep every page alive until the requesting bridge has delivered
            # its result; WebKit may otherwise wait forever on that callback.
            self._exit_thread.join()
        destroy_windows()
