from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from typing import Literal
from unittest.mock import patch

import numpy as np
import pytest
from scopecat.control.models import InstrumentSession
from scopecat.records.instrument import InstrumentReadback
from scopecat.records.measurement import MeasurementArray
from scopecat.sdk.instruments.commands import (
    CollectCommand,
    CollectReceipt,
    InteractiveCollectIntent,
)
from scopecat.sdk.instruments.contracts import InstrumentDescription

from scopecat_server.errors import BackendConflict
from scopecat_server.instruments._runtime_state import CollectReceiptReplay
from scopecat_server.instruments.pending_collections import (
    PendingCollections,
    RetainedCollection,
)
from scopecat_server.instruments.retained_receipt import RawCollectReceipt

KEY = ("session", "instrument", "command")


@dataclass
class Snapshot:
    storage_policy: Literal["memory_only", "allow_temporary_disk"]
    revision: int


class Settings:
    def __init__(self) -> None:
        self.coordination_lock = RLock()
        self.storage_policy: Literal["memory_only", "allow_temporary_disk"] = (
            "memory_only"
        )
        self.revision = 0

    def read(self) -> Snapshot:
        return Snapshot(storage_policy=self.storage_policy, revision=self.revision)


def collection() -> RetainedCollection:
    raw = RawCollectReceipt.from_receipt(
        CollectReceipt(
            readback=InstrumentReadback(
                values={
                    "a": MeasurementArray.create(
                        dtype="float64",
                        unit=None,
                        values=np.arange(8, dtype=np.float64),
                    )
                }
            )
        )
    )
    replay = CollectReceiptReplay(
        intent=InteractiveCollectIntent.model_construct(retain_result=True),
        command=CollectCommand.model_construct(),
        receipt=raw,
        collected_at=datetime.now(UTC),
    )
    return (
        InstrumentSession.model_construct(),
        InstrumentDescription.model_construct(),
        replay,
    )


def store(tmp_path: Path, settings: Settings | None = None) -> PendingCollections:
    return PendingCollections(
        settings=settings,
        spool_root=tmp_path,
        memory_bytes=8192,
        work_bytes=32768,
        parser_bytes=0,
        disk_bytes=32768,
    )


def test_concurrent_reservations_prevent_overcommit(tmp_path: Path) -> None:
    pending = store(tmp_path)
    with (
        pending.reserve(KEY, expected_bytes=5000),
        pytest.raises(BackendConflict, match="memory capacity"),
        pending.reserve(("s", "i", "second"), expected_bytes=5000),
    ):
        pytest.fail("must reject before device IO")
    assert pending.capacity()["memory_bytes"] == 0


def test_policy_captured_at_admission_and_shared_budget(tmp_path: Path) -> None:
    settings = Settings()
    pending = PendingCollections(
        settings=settings,
        spool_root=tmp_path,
        memory_bytes=65536,
        work_bytes=65536,
        disk_bytes=32768,
        parser_bytes=0,
    )
    with pending.reserve(KEY, expected_bytes=4096):
        settings.storage_policy = "allow_temporary_disk"
        settings.revision = 1
        pending.retain(KEY, collection())
    assert pending.capacity()["memory_bytes"] > 0
    assert not list(tmp_path.glob("owner-*/*.pending"))
    with pending.reserve(("s", "i", "disk"), expected_bytes=4096):
        pending.retain(("s", "i", "disk"), collection())
    assert pending.capacity()["disk_bytes"] > 0
    assert list(tmp_path.glob("owner-*/*.pending"))


def test_disk_failure_keeps_original_and_blocks_new_admission(tmp_path: Path) -> None:
    settings = Settings()
    settings.storage_policy = "allow_temporary_disk"
    pending = store(tmp_path, settings)
    with (
        pending.reserve(KEY, expected_bytes=4096),
        patch.object(pending, "_write", side_effect=OSError("injected full disk")),
    ):
        pending.retain(KEY, collection())
    assert pending.capacity()["storage_degraded"]
    with pending.lease(KEY) as retained:
        assert isinstance(retained[2].receipt, RawCollectReceipt)
        assert retained[2].receipt.array_values("a", 0, 8) == tuple(
            float(i) for i in range(8)
        )
    with (
        pytest.raises(BackendConflict, match="storage failure"),
        pending.reserve(("s", "i", "next")),
    ):
        pytest.fail("must block until emergency ownership is resolved")
    pending.release(KEY)
    assert pending.capacity()["work_bytes"] == 0


def test_read_claim_keeps_disposed_memory_charged_until_reader_exits(
    tmp_path: Path,
) -> None:
    pending = store(tmp_path)
    with pending.read_claim(KEY):
        with pending.reserve(KEY, expected_bytes=4096):
            pending.retain(KEY, collection())
        charged = pending.capacity()["memory_bytes"]
        pending.release(KEY)
        assert pending.capacity()["memory_bytes"] == charged
        assert charged > 0
    assert pending.capacity()["memory_bytes"] == 0


def test_disk_lease_survives_discard_and_cleanup_retries(tmp_path: Path) -> None:
    settings = Settings()
    settings.storage_policy = "allow_temporary_disk"
    pending = store(tmp_path, settings)
    with pending.reserve(KEY, expected_bytes=4096):
        pending.retain(KEY, collection())
    with pending.lease(KEY) as retained:
        raw = retained[2].receipt
        pending.release(KEY)
        assert isinstance(raw, RawCollectReceipt)
        assert raw.array_values("a", 1, 2) == (1.0, 2.0)
        assert pending.capacity()["disk_bytes"] > 0
    del raw, retained
    # Cleanup may have been deferred while caller-held exported views existed.
    with pending.reserve(("s", "i", "next")):
        pass
    assert pending.capacity()["disk_bytes"] == 0
    assert not list(tmp_path.glob("owner-*/*.pending"))


def test_prepare_rejects_insufficient_free_disk_before_io(tmp_path: Path) -> None:
    settings = Settings()
    settings.storage_policy = "allow_temporary_disk"
    pending = store(tmp_path, settings)
    with (
        pending.reserve(KEY),
        patch(
            "scopecat_server.instruments.pending_collections.shutil.disk_usage",
            return_value=SimpleNamespace(free=10),
        ),
        pytest.raises(BackendConflict, match="disk space"),
    ):
        pending.prepare(KEY, 4096)
    assert pending.capacity()["work_bytes"] == 0


def test_unknown_envelope_rejected_by_memory_default_without_allocating(
    tmp_path: Path,
) -> None:
    from scopecat_server.instruments.pending_collections import MAX_RECEIPT_BYTES

    pending = PendingCollections(spool_root=tmp_path)
    with (
        pending.reserve(KEY),
        pytest.raises(BackendConflict, match="allow temporary disk"),
    ):
        pending.prepare(KEY, MAX_RECEIPT_BYTES)
    assert pending.capacity()["memory_bytes"] == 0


def test_unknown_envelope_can_be_reserved_on_disk_without_allocating(
    tmp_path: Path,
) -> None:
    from scopecat_server.instruments.pending_collections import MAX_RECEIPT_BYTES

    settings = Settings()
    settings.storage_policy = "allow_temporary_disk"
    pending = PendingCollections(settings=settings, spool_root=tmp_path)
    with pending.reserve(KEY):
        pending.prepare(KEY, MAX_RECEIPT_BYTES)
        assert pending.capacity()["disk_bytes"] == MAX_RECEIPT_BYTES
    assert pending.capacity()["disk_bytes"] == 0
    pending.clear()


def test_stale_cleanup_never_removes_live_runtime_original(tmp_path: Path) -> None:
    settings = Settings()
    settings.storage_policy = "allow_temporary_disk"
    first = store(tmp_path, settings)
    with first.reserve(KEY, expected_bytes=4096):
        first.retain(KEY, collection())
    files = list(tmp_path.glob("owner-*/*.pending"))
    assert len(files) == 1
    second = store(tmp_path, settings)
    with second.reserve(("s", "i", "second")):
        assert files[0].exists()
    first.clear()
    second.clear()


def test_stale_owner_cleanup_is_bounded_and_ignores_unknown_files(
    tmp_path: Path,
) -> None:
    old = tmp_path / "owner-dead"
    old.mkdir()
    (old / "owner.lock").touch()
    (old / "receipt-old.pending").write_bytes(b"old")
    (old / "user-file").write_bytes(b"keep")
    settings = Settings()
    settings.storage_policy = "allow_temporary_disk"
    pending = store(tmp_path, settings)
    with pending.reserve(KEY):
        assert not (old / "receipt-old.pending").exists()
        assert (old / "user-file").read_bytes() == b"keep"
    pending.clear()


def test_disk_reservation_stays_charged_during_spool_write(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    settings = Settings()
    settings.storage_policy = "allow_temporary_disk"
    pending = PendingCollections(
        settings=settings,
        spool_root=tmp_path,
        memory_bytes=65536,
        work_bytes=65536,
        disk_bytes=35000,
        parser_bytes=0,
    )
    entered, resume = Event(), Event()
    write = pending._write

    def blocked(raw: RawCollectReceipt):
        entered.set()
        assert resume.wait(5)
        return write(raw)

    def collect() -> None:
        with pending.reserve(KEY, expected_bytes=10000):
            pending.retain(KEY, collection())

    with (
        patch.object(pending, "_write", blocked),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        future = pool.submit(collect)
        try:
            assert entered.wait(5)
            assert pending.capacity()["disk_bytes"] == 10000
            with (
                pytest.raises(BackendConflict, match="disk capacity"),
                pending.reserve(("s", "i", "second"), expected_bytes=10000),
            ):
                pytest.fail("reservation must not be reused during disk write")
        finally:
            resume.set()
        future.result(timeout=5)
    pending.clear()


def test_staging_is_outside_objects_and_cleanup_failure_stays_charged(
    tmp_path: Path,
) -> None:
    pending = store(tmp_path)
    with pending.staging_file(1024) as staged:
        assert staged.parent.parent == tmp_path
        staged.write_bytes(b"original")
        assert pending.capacity()["disk_bytes"] == 2048
    assert pending.capacity()["disk_bytes"] == 0
    assert not staged.exists()
    pending.clear()


def test_spool_owner_lock_releases_when_shutdown_uses_another_thread(
    tmp_path: Path,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from filelock import FileLock

    settings = Settings()
    settings.storage_policy = "allow_temporary_disk"
    pending = store(tmp_path, settings)

    def acquire() -> None:
        with pending.reserve(KEY):
            pass

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(acquire).result(timeout=5)
    marker = next(tmp_path.glob("owner-*/owner.lock"))
    pending.clear()
    with FileLock(marker).acquire(timeout=0):
        pass


def test_disk_descriptors_and_headers_share_memory_budget_across_policies(
    tmp_path: Path,
) -> None:
    settings = Settings()
    settings.storage_policy = "allow_temporary_disk"
    raw = collection()[2].receipt
    assert isinstance(raw, RawCollectReceipt)
    resident = raw.descriptor_bytes + len(raw.header)
    pending = PendingCollections(
        settings=settings,
        spool_root=tmp_path,
        memory_bytes=resident * 2,
        parser_bytes=resident,
        work_bytes=1024 * 1024,
        disk_bytes=1024 * 1024,
    )
    for index in range(2):
        key = ("s", "i", str(index))
        with pending.reserve(key):
            pending.prepare(key, 4096)
            pending.retain(key, collection())
    assert pending.capacity()["memory_bytes"] == resident * 2
    with (
        pending.reserve(KEY),
        pytest.raises(BackendConflict, match="descriptor capacity"),
    ):
        pending.prepare(KEY, 4096)
    settings.storage_policy = "memory_only"
    with pending.reserve(KEY), pytest.raises(BackendConflict, match="memory capacity"):
        pending.prepare(KEY, 4096)
    pending.clear()


@pytest.mark.parametrize("policy", ["memory_only", "allow_temporary_disk"])
def test_view_construction_keeps_reserved_descriptor_memory_charged(
    tmp_path: Path, policy: Literal["memory_only", "allow_temporary_disk"]
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    settings = Settings()
    settings.storage_policy = policy
    pending = PendingCollections(
        settings=settings,
        spool_root=tmp_path,
        memory_bytes=100000,
        parser_bytes=64000,
        work_bytes=256000,
        disk_bytes=256000,
    )
    entered, resume = Event(), Event()
    value = collection()
    original = RawCollectReceipt.with_view

    def blocked(
        raw: RawCollectReceipt, *, legacy_full_view: bool = False
    ) -> RawCollectReceipt:
        entered.set()
        assert resume.wait(5)
        return original(raw, legacy_full_view=legacy_full_view)

    def collect() -> None:
        with pending.reserve(KEY, expected_bytes=4096):
            pending.retain(KEY, value)

    with (
        patch.object(RawCollectReceipt, "with_view", blocked),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        future = pool.submit(collect)
        try:
            assert entered.wait(5)
            assert pending.capacity()["memory_bytes"] >= 64000
            with (
                pytest.raises(BackendConflict, match="memory capacity"),
                pending.reserve(("s", "i", "second"), expected_bytes=4096),
            ):
                pytest.fail("inflight view reservation must not be reused")
        finally:
            resume.set()
        future.result(timeout=5)
    pending.clear()


def test_pending_disk_quota_reserves_room_to_save_without_forced_discard(
    tmp_path: Path,
) -> None:
    settings = Settings()
    settings.storage_policy = "allow_temporary_disk"
    value = collection()
    raw = value[2].receipt
    assert isinstance(raw, RawCollectReceipt)
    pending = PendingCollections(
        settings=settings,
        spool_root=tmp_path,
        memory_bytes=1024 * 1024,
        work_bytes=1024 * 1024,
        disk_bytes=raw.size_bytes * 4,
        parser_bytes=0,
    )
    for index in range(2):
        with pending.reserve(("s", "i", str(index)), expected_bytes=raw.size_bytes):
            pending.retain(("s", "i", str(index)), value)
    with (
        pytest.raises(BackendConflict, match="disk capacity"),
        pending.reserve(KEY, expected_bytes=raw.size_bytes),
    ):
        pytest.fail("must preserve save working room before new acquisition")
    # Both accepted originals can make progress through explicit save staging;
    # no result needs to be thrown away to escape our own quota.
    for index in range(2):
        with pending.staging_file(raw.size_bytes) as staged:
            staged.write_bytes(raw.to_bytes())
        pending.release(("s", "i", str(index)))
    pending.clear()
