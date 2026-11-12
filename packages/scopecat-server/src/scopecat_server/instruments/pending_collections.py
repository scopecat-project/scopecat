"""Admission and process-lifetime ownership of direct collection originals.

The original framed receipt is owned before optional disk IO. Disk failures do
not discard it. Storage policy is captured at admission, not at completion.
"""

from __future__ import annotations

import mmap
import os
import shutil
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from hashlib import file_digest, sha256
from pathlib import Path
from threading import RLock
from typing import Literal, Protocol
from uuid import uuid4

from filelock import FileLock, Timeout
from scopecat.control.models import InstrumentSession
from scopecat.sdk.instruments.commands import CollectCommand
from scopecat.sdk.instruments.contracts import InstrumentDescription

from scopecat_server.errors import BackendConflict, BackendNotFound

from ._runtime_state import CollectReceiptReplay
from .retained_receipt import RawCollectReceipt

PENDING_PER_INSTRUMENT = 20
PENDING_TOTAL = 100
# These are application payload budgets, not claims about total OS process RSS.
DEFAULT_MEMORY_BYTES = 256 * 1024 * 1024
DEFAULT_WORK_BYTES = 768 * 1024 * 1024
DEFAULT_DISK_BYTES = 2 * 1024 * 1024 * 1024
MAX_RECEIPT_BYTES = 520 * 1024 * 1024 + 64 * 1024
MAX_ATTACHMENT_BYTES = 128 * 1024 * 1024
HEADER_ALLOWANCE = 8 * 1024 * 1024 + 64 * 1024

type CollectionKey = tuple[str, str, str]
type RetainedCollection = tuple[
    InstrumentSession, InstrumentDescription, CollectReceiptReplay
]
type StoragePolicy = Literal["memory_only", "allow_temporary_disk"]


class SettingsSnapshot(Protocol):
    revision: int
    storage_policy: StoragePolicy


class PendingResultSettingsProvider(Protocol):
    coordination_lock: RLock

    def read(self) -> SettingsSnapshot: ...


@dataclass(frozen=True)
class _DefaultSnapshot:
    revision: int = 0
    storage_policy: StoragePolicy = "memory_only"


class _DefaultSettings:
    def __init__(self) -> None:
        self.coordination_lock = RLock()

    def read(self) -> _DefaultSnapshot:
        return _DefaultSnapshot()


@dataclass
class _Reservation:
    policy: StoragePolicy
    revision: int
    expected_bytes: int
    work_bytes: int
    visible: bool


@dataclass
class _Backing:
    path: Path
    size_bytes: int
    mapping: mmap.mmap | None = None


@dataclass
class _Entry:
    reservation: _Reservation
    collection: RetainedCollection | None = None
    resident_bytes: int = 0
    backing: _Backing | None = None
    emergency: bool = False
    pins: int = 0
    disposed: bool = False
    ready: bool = True


def collection_reservation_bytes(command: CollectCommand) -> int:
    """Bound canonical numeric bytes; unknown/text use the wire maximum.

    No strings are expanded to padded Unicode arrays in the retained path.
    Driver-side allocations before producing a valid frame are not controlled
    by this application payload budget.
    """
    widths = {"float64": 8, "int64": 8, "complex128": 16, "bool": 1}
    total = HEADER_ALLOWANCE
    for request in command.requests:
        if request.dtype not in widths:
            return MAX_RECEIPT_BYTES
        count = 1
        for axis in request.dimensions:
            if axis.size is None:
                return MAX_RECEIPT_BYTES
            count *= axis.size
            if count * widths[request.dtype] > MAX_ATTACHMENT_BYTES:
                raise BackendConflict(
                    "collection array exceeds the supported wire size; acquisition "
                    "has not started"
                )
        total += count * widths[request.dtype]
        if total > MAX_RECEIPT_BYTES:
            raise BackendConflict(
                "collection exceeds the supported wire size; acquisition has not "
                "started"
            )
    return total


class PendingCollections:
    def __init__(
        self,
        *,
        settings: PendingResultSettingsProvider | None = None,
        spool_root: Path | None = None,
        memory_bytes: int = DEFAULT_MEMORY_BYTES,
        work_bytes: int = DEFAULT_WORK_BYTES,
        disk_bytes: int = DEFAULT_DISK_BYTES,
        parser_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if min(memory_bytes, work_bytes, disk_bytes) < 1:
            raise ValueError("pending capacity budgets must be positive")
        self._parser_bytes = parser_bytes
        self._disk_work = 0
        self._settings = settings or _DefaultSettings()
        self._lock = self._settings.coordination_lock
        self.disposition_lock = RLock()
        self._items: dict[CollectionKey, _Entry] = {}
        self._garbage: list[_Backing] = []
        self._retired: list[_Entry] = []
        self._readers: dict[CollectionKey, int] = {}
        self._claimed_entries: dict[CollectionKey, _Entry] = {}
        self._spool_base = spool_root
        self._spool_root: Path | None = None
        self._spool_owner: FileLock | None = None
        self._memory_limit, self._work_limit, self._disk_limit = (
            memory_bytes,
            work_bytes,
            disk_bytes,
        )
        self._stopped = False

    def _ensure_spool(self) -> None:
        if self._spool_root is not None:
            return
        base = self._spool_base
        if base is None or base.is_symlink():
            raise OSError("temporary result storage is unavailable")
        base.mkdir(parents=True, exist_ok=True)
        # Only module-created directories, never arbitrary tree deletion.
        for old in base.glob("owner-*"):
            if old.is_symlink() or not old.is_dir():
                continue
            marker = old / "owner.lock"
            if marker.is_symlink() or not marker.is_file():
                continue
            guard = FileLock(marker)
            try:
                guard.acquire(timeout=0)
            except Timeout:
                continue
            try:
                for path in old.glob("receipt-*.pending"):
                    if path.is_symlink() or not path.is_file():
                        continue
                    self._garbage.append(_Backing(path, path.stat().st_size))
                self._retry_cleanup()
            finally:
                guard.release()
        root = base / ("owner-" + uuid4().hex)
        root.mkdir()
        owner = FileLock(root / "owner.lock", thread_local=False)
        owner.acquire(timeout=0)
        self._spool_root, self._spool_owner = root, owner

    def _usage(self) -> tuple[int, int, int]:
        memory = sum(
            max(
                e.resident_bytes,
                (
                    (
                        e.reservation.expected_bytes
                        if e.reservation.policy == "memory_only"
                        else 0
                    )
                    + (self._parser_bytes if e.reservation.expected_bytes else 0)
                )
                if e.collection is None or e.reservation.work_bytes
                else 0,
            )
            for e in (*self._items.values(), *self._retired)
        )
        work = sum(
            e.reservation.work_bytes for e in (*self._items.values(), *self._retired)
        )
        disk = (
            sum(
                e.backing.size_bytes
                if e.backing
                else (
                    e.reservation.expected_bytes
                    if e.reservation.policy == "allow_temporary_disk"
                    else 0
                )
                for e in (*self._items.values(), *self._retired)
            )
            + sum(g.size_bytes for g in self._garbage)
            + self._disk_work
        )
        return memory, work, disk

    def _disk_admission_fits(self, key: CollectionKey, expected_bytes: int) -> bool:
        """Leave enough staging room to save at least the largest pending original.

        Otherwise a full pending quota can prevent every save, forcing discard
        even when the user wants to preserve all results. Saves serialize under
        disposition_lock, so one largest-original work allowance is sufficient.
        """
        _, _, disk = self._usage()
        entry = self._items.get(key)
        previous = 0 if entry is None else entry.reservation.expected_bytes
        largest = max(
            (
                e.collection[2].receipt.size_bytes
                if e.collection is not None
                and isinstance(e.collection[2].receipt, RawCollectReceipt)
                else e.reservation.expected_bytes
                for item_key, e in self._items.items()
                if item_key != key
            ),
            default=0,
        )
        largest = max(largest, expected_bytes)
        return disk - previous + expected_bytes + 2 * largest <= self._disk_limit

    def capacity(self) -> dict[str, int | bool]:
        with self._lock:
            memory, work, disk = self._usage()
            return {
                "memory_bytes": memory,
                "work_bytes": work,
                "disk_bytes": disk,
                "memory_limit_bytes": self._memory_limit,
                "work_limit_bytes": self._work_limit,
                "disk_limit_bytes": self._disk_limit,
                "storage_degraded": any(
                    e.emergency for e in (*self._items.values(), *self._retired)
                )
                or bool(self._garbage),
            }

    @contextmanager
    def reserve(
        self, key: CollectionKey, *, expected_bytes: int = 0, visible: bool = True
    ) -> Generator[None]:
        with self._lock:
            self._retry_cleanup()
            if self._stopped:
                raise BackendConflict("pending result admission is stopped")
            if key in self._items:
                raise BackendConflict("collection identity is already reserved")
            if any(e.emergency for e in (*self._items.values(), *self._retired)):
                raise BackendConflict(
                    "a result is retained after temporary storage failure; save or "
                    "explicitly discard it before collecting again"
                )
            if visible and (
                sum(
                    k[1] == key[1] and e.reservation.visible
                    for k, e in self._items.items()
                )
                >= PENDING_PER_INSTRUMENT
                or sum(
                    e.reservation.visible
                    for e in (*self._items.values(), *self._retired)
                )
                >= PENDING_TOTAL
            ):
                raise BackendConflict(
                    "pending collection capacity reached; save or explicitly discard "
                    "existing results before collecting again"
                )
            snapshot = self._settings.read()
            memory, work, _ = self._usage()
            working = (
                expected_bytes
                + min(expected_bytes, MAX_ATTACHMENT_BYTES)
                + self._parser_bytes
            )
            if work + working > self._work_limit:
                raise BackendConflict(
                    "collection working capacity unavailable; acquisition has not "
                    "started"
                )
            if (
                memory
                + (expected_bytes if snapshot.storage_policy == "memory_only" else 0)
                + (self._parser_bytes if expected_bytes else 0)
                > self._memory_limit
            ):
                raise BackendConflict(
                    "pending memory capacity unavailable; acquisition has not "
                    "started; save results or allow temporary disk "
                    "in application settings"
                )
            if snapshot.storage_policy == "allow_temporary_disk":
                if self._spool_base is None:
                    raise BackendConflict(
                        "temporary result storage is unavailable; acquisition has not "
                        "started"
                    )
                if not self._disk_admission_fits(key, expected_bytes):
                    raise BackendConflict(
                        "pending disk capacity or reserved save working space "
                        "unavailable; "
                        "save existing results before collecting again"
                    )
                # Create the directory before irreversible device IO. Free-space
                # checks are advisory; the RAM reservation remains the fallback.
                try:
                    self._ensure_spool()
                    assert self._spool_root is not None
                    with tempfile.TemporaryFile(dir=self._spool_root):
                        pass
                    if not self._disk_admission_fits(key, expected_bytes):
                        raise BackendConflict(
                            "stale temporary files consume pending disk capacity"
                        )
                except OSError as error:
                    raise BackendConflict(
                        "temporary result storage is not writable; acquisition has not "
                        "started"
                    ) from error
            self._items[key] = _Entry(
                _Reservation(
                    snapshot.storage_policy,
                    snapshot.revision,
                    expected_bytes,
                    working,
                    visible,
                )
            )
            entry = self._items[key]
            entry.pins = self._readers.get(key, 0)
            if entry.pins:
                self._claimed_entries[key] = entry
        try:
            yield
        finally:
            with self._lock:
                entry = self._items.get(key)
                if entry is not None:
                    if entry.collection is None:
                        self._items.pop(key)
                    elif not entry.emergency:
                        entry.reservation.work_bytes = 0

    def prepare(self, key: CollectionKey, expected_bytes: int) -> None:
        """Upgrade a count reservation after resolving the frozen command."""
        with self._lock:
            entry = self._items[key]
            if entry.collection is not None:
                raise BackendConflict("collection already completed")
            # Preserve retry compatibility while there is room. Eviction is
            # restricted to unpinned Python-owned results, never pending UI data.
            for old_key, old in tuple(self._items.items()):
                memory, _, disk = self._usage()
                needed_memory = self._parser_bytes + (
                    expected_bytes if entry.reservation.policy == "memory_only" else 0
                )
                needed_disk = (
                    expected_bytes
                    if entry.reservation.policy == "allow_temporary_disk"
                    else 0
                )
                hidden_count = sum(
                    not e.reservation.visible and e.collection is not None
                    for e in self._items.values()
                )
                if (
                    memory + needed_memory <= self._memory_limit
                    and disk + needed_disk <= self._disk_limit
                    and hidden_count < 256
                ):
                    break
                if (
                    old_key != key
                    and not old.reservation.visible
                    and old.collection is not None
                    and not old.pins
                ):
                    self.release(old_key)
            memory, work, disk = self._usage()
            delta = expected_bytes - entry.reservation.expected_bytes
            working = (
                expected_bytes
                + min(expected_bytes, MAX_ATTACHMENT_BYTES)
                + self._parser_bytes
            )
            if work - entry.reservation.work_bytes + working > self._work_limit:
                raise BackendConflict(
                    "collection working capacity unavailable; acquisition has not "
                    "started"
                )
            if (
                entry.reservation.policy == "memory_only"
                and memory
                + delta
                + (self._parser_bytes if not entry.reservation.expected_bytes else 0)
                > self._memory_limit
            ):
                raise BackendConflict(
                    "pending memory capacity unavailable; acquisition has not "
                    "started; save results or allow temporary disk "
                    "in application settings"
                )
            if (
                entry.reservation.policy == "allow_temporary_disk"
                and not self._disk_admission_fits(key, expected_bytes)
            ):
                raise BackendConflict(
                    "pending disk capacity or reserved save working space unavailable; "
                    "save existing results before collecting again"
                )
            if entry.reservation.policy == "allow_temporary_disk":
                if (
                    memory
                    + (
                        self._parser_bytes
                        if not entry.reservation.expected_bytes
                        else 0
                    )
                    > self._memory_limit
                ):
                    raise BackendConflict(
                        "pending descriptor capacity unavailable; "
                        "acquisition has not started"
                    )
                assert self._spool_root is not None
                try:
                    free = shutil.disk_usage(self._spool_root).free
                except OSError as error:
                    raise BackendConflict(
                        "cannot inspect temporary storage; acquisition has not started"
                    ) from error
                # Concurrent reservations are included, even though their bytes
                # are not on disk yet. This is advisory, not a write guarantee.
                if free < disk + delta:
                    raise BackendConflict(
                        "insufficient temporary disk space; acquisition has not started"
                    )
            entry.reservation.expected_bytes = expected_bytes
            entry.reservation.work_bytes = working

    def retain(
        self, key: CollectionKey, collection: RetainedCollection, *, ready: bool = True
    ) -> RawCollectReceipt:
        with self.disposition_lock:
            return self._retain(key, collection, ready=ready)

    def _retain(
        self, key: CollectionKey, collection: RetainedCollection, *, ready: bool
    ) -> RawCollectReceipt:
        raw = collection[2].receipt
        if not isinstance(raw, RawCollectReceipt):
            raw = RawCollectReceipt.from_receipt(raw)
            collection = (
                collection[0],
                collection[1],
                replace(collection[2], receipt=raw),
            )
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                raise BackendConflict("collection has no pending-result reservation")
            # Establish ownership before any fallible storage operation.
            entry.collection = collection
            entry.ready = ready
            entry.reservation.visible = bool(
                collection[2].intent.retain_result and raw.status == "collected"
            )
            entry.resident_bytes = raw.resident_bytes
            if (
                raw.size_bytes > entry.reservation.expected_bytes
                and entry.reservation.expected_bytes
            ):
                entry.emergency = True
            policy = entry.reservation.policy
        # Optional typed views are fallible, after immutable original ownership.
        try:
            viewed = raw.with_view()
        except ValueError as error:
            viewed = raw.with_contract_error(str(error))
        except MemoryError:
            viewed = raw
        collection = (
            collection[0],
            collection[1],
            replace(collection[2], receipt=viewed),
        )
        with self._lock:
            entry.collection = collection
            entry.resident_bytes = viewed.resident_bytes
        if policy == "memory_only":
            return viewed
        try:
            backing, stored = self._write(viewed)
        except OSError, ValueError, MemoryError:
            with self._lock:
                entry.emergency = True
            return viewed
        # Mapping is already backed by the fsynced, verified original. Retain
        # known invalidity rather than accidentally upgrading a diagnostic file.
        try:
            stored = (
                stored.with_contract_error(viewed.contract_error)
                if viewed.contract_error
                else stored.with_view()
            )
        except ValueError as error:
            stored = stored.with_contract_error(str(error))
        except MemoryError:
            pass
        with self._lock:
            entry.backing = backing
            entry.collection = (
                collection[0],
                collection[1],
                replace(collection[2], receipt=stored),
            )
            entry.resident_bytes = len(stored.header) + stored.descriptor_bytes
            entry.emergency = raw.size_bytes > entry.reservation.expected_bytes
        return stored

    def _write(self, raw: RawCollectReceipt) -> tuple[_Backing, RawCollectReceipt]:
        assert self._spool_root is not None
        fd, name = tempfile.mkstemp(
            prefix="receipt-", suffix=".pending", dir=self._spool_root
        )
        path = Path(name)
        mapped: mmap.mmap | None = None
        try:
            digest = sha256()
            with os.fdopen(fd, "wb") as stream:
                for segment in raw.segments():
                    view = memoryview(segment)
                    for offset in range(0, len(view), 64 * 1024):
                        chunk = view[offset : offset + 64 * 1024]
                        stream.write(chunk)
                        digest.update(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if path.stat().st_size != raw.size_bytes:
                raise OSError("temporary receipt size mismatch")
            with path.open("rb") as source:
                if file_digest(source, "sha256").digest() != digest.digest():
                    raise OSError("temporary receipt checksum mismatch")
                mapped = mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ)
            stored = RawCollectReceipt.from_http_buffer(
                memoryview(mapped), defer_view=True
            )
            return _Backing(path, raw.size_bytes, mapped), stored
        except BaseException:
            # Validation can fail after mapping. Queue cleanup rather than
            # losing accounting when an exported view delays Windows unlink.
            with self._lock:
                self._garbage.append(_Backing(path, raw.size_bytes, mapped))
                self._retry_cleanup()
            raise

    def set_validation(
        self,
        key: CollectionKey,
        status: Literal["verified", "unverified", "invalid"],
        reason: str | None = None,
        code: str | None = None,
    ) -> None:
        with self._lock:
            entry = self._items[key]
            assert entry.collection is not None
            session, description, replay = entry.collection
            entry.collection = (
                session,
                description,
                replace(
                    replay,
                    receipt_validation=status,
                    validation_reason=None if reason is None else reason[:512],
                    validation_code=None if code is None else code[:100],
                ),
            )
            entry.ready = True

    def find(self, key: CollectionKey) -> RetainedCollection | None:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            if entry.collection is None or not entry.ready:
                raise BackendConflict("collection is still in progress")
            return entry.collection

    def list(
        self, instrument_id: str
    ) -> tuple[tuple[CollectionKey, RetainedCollection], ...]:
        with self._lock:
            return tuple(
                (key, e.collection)
                for key, e in self._items.items()
                if key[1] == instrument_id
                and e.collection is not None
                and e.reservation.visible
                and e.ready
            )

    def require(self, key: CollectionKey) -> RetainedCollection:
        value = self.find(key)
        if value is None:
            raise BackendNotFound("unsaved collection is no longer retained")
        return value

    @contextmanager
    def lease(self, key: CollectionKey) -> Generator[RetainedCollection]:
        # Pins can be released on a different HTTP streaming thread. No RLock is
        # held across yield; committed disposal retires rather than invalidates.
        with self._lock:
            collection = self.require(key)
            entry = self._items[key]
            entry.pins += 1
        try:
            yield collection
        finally:
            collection = None
            with self._lock:
                entry.pins -= 1
                if entry.disposed and not entry.pins:
                    self._retired.remove(entry)
                    self._finish_release(entry)
                self._retry_cleanup()

    @contextmanager
    def read_claim(self, key: CollectionKey) -> Generator[None]:
        """Pin an existing or forthcoming original without serializing device IO."""
        with self._lock:
            self._readers[key] = self._readers.get(key, 0) + 1
            entry = self._items.get(key)
            if entry is not None:
                entry.pins += 1
                self._claimed_entries[key] = entry
        try:
            yield
        finally:
            with self._lock:
                self._readers[key] -= 1
                entry = self._claimed_entries.get(key)
                if entry is not None:
                    entry.pins -= 1
                    if entry.disposed and not entry.pins:
                        self._retired.remove(entry)
                        self._finish_release(entry)
                if not self._readers[key]:
                    self._readers.pop(key)
                    self._claimed_entries.pop(key, None)
                self._retry_cleanup()

    @property
    def work_limit(self) -> int:
        return self._work_limit

    @contextmanager
    def materialize(self, raw: RawCollectReceipt) -> Generator[None]:
        with self.working_bytes(raw.materialization_bytes):
            yield

    @contextmanager
    def working_bytes(self, needed: int) -> Generator[None]:
        if needed < 0:
            raise ValueError("work reservation must not be negative")
        with self._lock:
            _, work, _ = self._usage()
            if needed + work > self._work_limit:
                raise BackendConflict(
                    "result read exceeds available working capacity; "
                    "original result remains retained"
                )
            key = ("materialize", "work", uuid4().hex)
            self._items[key] = _Entry(_Reservation("memory_only", 0, 0, needed, False))
        try:
            yield
        finally:
            with self._lock:
                self._items.pop(key, None)

    @contextmanager
    def disk_work_bytes(self, needed: int) -> Generator[None]:
        if needed < 0:
            raise ValueError("disk reservation must not be negative")
        with self._lock:
            _, _, disk = self._usage()
            if needed + disk > self._disk_limit:
                raise BackendConflict(
                    "temporary save capacity unavailable; original remains retained"
                )
            self._disk_work += needed
        try:
            yield
        finally:
            with self._lock:
                self._disk_work -= needed

    @contextmanager
    def staging_file(self, expected_bytes: int) -> Generator[Path]:
        """Save staging outside objects and snapshots, owned by this process."""
        with self.disk_work_bytes(expected_bytes * 2):
            with self._lock:
                self._ensure_spool()
                assert self._spool_root is not None
                fd, name = tempfile.mkstemp(
                    prefix="receipt-", suffix=".pending", dir=self._spool_root
                )
                os.close(fd)
                path = Path(name)
            try:
                yield path
            finally:
                with self._lock:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        # Reservation becomes cleanup charge before it expires.
                        self._garbage.append(_Backing(path, expected_bytes))

    def release(self, key: CollectionKey) -> None:
        with self._lock:
            entry = self._items.pop(key, None)
            if entry is not None:
                entry.disposed = True
                if entry.pins:
                    self._retired.append(entry)
                else:
                    self._finish_release(entry)
            self._retry_cleanup()

    def _finish_release(self, entry: _Entry) -> None:
        entry.collection = None
        if entry.backing is not None:
            self._garbage.append(entry.backing)
            entry.backing = None

    def release_python_owned(self, session_id: str) -> None:
        with self._lock:
            for key, entry in tuple(self._items.items()):
                if key[0] == session_id and not entry.reservation.visible:
                    self.release(key)

    def _retry_cleanup(self) -> None:
        remaining: list[_Backing] = []
        for backing in self._garbage:
            try:
                if backing.mapping is not None:
                    backing.mapping.close()
                    backing.mapping = None
                backing.path.unlink(missing_ok=True)
            except OSError, BufferError:
                remaining.append(backing)
        self._garbage = remaining
        if (
            self._stopped
            and not self._items
            and not self._retired
            and self._spool_owner is not None
        ):
            self._spool_owner.release()
            self._spool_owner = None

    def clear(self) -> None:
        with self.disposition_lock, self._lock:
            self._stopped = True
            for key in tuple(self._items):
                self.release(key)
            self._retry_cleanup()
