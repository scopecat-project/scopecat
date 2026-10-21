"""Fence daemon-owned background rounds against idle shutdown."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from threading import RLock


class BackgroundRounds:
    """Count whole rounds, including discovery and final persistence.

    A long-lived subscription is not active work. Its admitted round is: shutdown
    must not interrupt a write or race the next round's first write.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._active = 0
        self._stopping = False

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    @contextmanager
    def round(self) -> Generator[bool]:
        with self._lock:
            admitted = not self._stopping
            if admitted:
                self._active += 1
        try:
            yield admitted
        finally:
            if admitted:
                with self._lock:
                    self._active -= 1

    def stop(self, accept: Callable[[], None], *, only_if_idle: bool) -> bool:
        """Commit shutdown under the same fence as round admission.

        Busy/rejected/failed requests leave admission unchanged. Explicit forced
        shutdown retains its existing cancellation semantics.
        """
        with self._lock:
            if only_if_idle and self._active:
                return False
            accept()
            self._stopping = True
            return True
