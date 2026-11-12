"""Process-lifetime pending receipts, independent of control-session ownership.

Count reservations are taken before device I/O. No timeout or cache eviction can
remove successful results; only confirmed save/discard or process shutdown does.
The limits count results, not bytes, just like the frontend pending queue.
"""

from collections.abc import Generator
from contextlib import contextmanager
from threading import RLock

from scopecat.control.models import InstrumentSession
from scopecat.sdk.instruments.contracts import InstrumentDescription

from scopecat_server.errors import BackendConflict, BackendNotFound

from ._runtime_state import CollectReceiptReplay

PENDING_PER_INSTRUMENT = 20
PENDING_TOTAL = 100

type CollectionKey = tuple[str, str, str]
type RetainedCollection = tuple[
    InstrumentSession, InstrumentDescription, CollectReceiptReplay
]


class PendingCollections:
    def __init__(self) -> None:
        self._lock = RLock()
        self.disposition_lock = RLock()
        self._items: dict[CollectionKey, RetainedCollection | None] = {}

    @contextmanager
    def reserve(self, key: CollectionKey) -> Generator[None]:
        with self._lock:
            if key in self._items:
                raise BackendConflict("collection identity is already reserved")
            instrument_count = sum(item[1] == key[1] for item in self._items)
            if (
                instrument_count >= PENDING_PER_INSTRUMENT
                or len(self._items) >= PENDING_TOTAL
            ):
                raise BackendConflict(
                    "pending collection capacity reached; save or explicitly discard "
                    "existing results before collecting again"
                )
            self._items[key] = None
        try:
            yield
        finally:
            with self._lock:
                if self._items.get(key) is None:
                    self._items.pop(key, None)

    def retain(self, key: CollectionKey, collection: RetainedCollection) -> None:
        with self._lock:
            if key not in self._items:
                raise BackendConflict("collection has no pending-result reservation")
            self._items[key] = collection

    def find(self, key: CollectionKey) -> RetainedCollection | None:
        with self._lock:
            value = self._items.get(key)
            if value is None and key in self._items:
                raise BackendConflict("collection is still in progress")
            return value

    def list(
        self, instrument_id: str
    ) -> tuple[tuple[CollectionKey, RetainedCollection], ...]:
        with self._lock:
            return tuple(
                (key, value)
                for key, value in self._items.items()
                if key[1] == instrument_id and value is not None
            )

    def require(self, key: CollectionKey) -> RetainedCollection:
        value = self.find(key)
        if value is None:
            raise BackendNotFound("unsaved collection is no longer retained")
        return value

    def release(self, key: CollectionKey) -> None:
        with self._lock:
            self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
