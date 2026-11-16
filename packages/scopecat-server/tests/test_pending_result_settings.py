import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from pydantic import ValidationError

from scopecat_server.errors import BackendConflict
from scopecat_server.services.pending_result_settings import (
    PENDING_RESULT_SETTINGS_FILE,
    PendingResultSettings,
    PendingResultSettingsService,
)


def test_default_is_memory_only_without_creating_a_file(tmp_path: Path) -> None:
    service = PendingResultSettingsService(tmp_path)
    assert service.read() == PendingResultSettings()
    assert not service.path.exists()


def test_saved_policy_is_data_space_shared_revision_checked_and_immutable(
    tmp_path: Path,
) -> None:
    first = PendingResultSettingsService(tmp_path)
    second = PendingResultSettingsService(tmp_path)
    old_reservation = first.read()
    disk = first.save(PendingResultSettings(storage_policy="allow_temporary_disk"))
    assert second.read() == disk
    assert disk.revision == 1
    assert old_reservation.storage_policy == "memory_only"
    memory = second.save(
        PendingResultSettings(revision=disk.revision, storage_policy="memory_only")
    )
    assert memory.revision == 2
    assert disk.storage_policy == "allow_temporary_disk"
    with pytest.raises(BackendConflict, match="reload"):
        first.save(disk)
    assert first.read() == memory
    assert PendingResultSettingsService(tmp_path / "another").read().revision == 0


@pytest.mark.parametrize(
    "content", ["broken", '{"storage_policy":"unknown"}', '{"revision":true}']
)
def test_invalid_settings_never_fall_back_to_disk(tmp_path: Path, content: str) -> None:
    (tmp_path / PENDING_RESULT_SETTINGS_FILE).write_text(content)
    with pytest.raises(BackendConflict, match="invalid"):
        PendingResultSettingsService(tmp_path).read()


@pytest.mark.parametrize("kind", ["directory", "symlink", "fifo"])
def test_non_regular_settings_are_rejected_without_reading(
    tmp_path: Path, kind: str
) -> None:
    service = PendingResultSettingsService(tmp_path)
    if kind == "directory":
        service.path.mkdir()
    elif kind == "symlink":
        target = tmp_path / "actual.json"
        target.write_text(PendingResultSettings().model_dump_json())
        try:
            service.path.symlink_to(target)
        except OSError:
            pytest.skip("Symlinks are unavailable on this platform")
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("Named pipes are unavailable on this platform")
        os.mkfifo(service.path)
    with pytest.raises(BackendConflict, match="regular file"):
        service.read()


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_failed_settings_write_keeps_committed_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    service = PendingResultSettingsService(tmp_path)
    previous = service.save(PendingResultSettings())

    def fail(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected settings write failure")

    if failure == "fsync":
        monkeypatch.setattr(
            "scopecat_server.services.pending_result_settings.os.fsync", fail
        )
    else:
        monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="injected"):
        service.save(
            PendingResultSettings(
                revision=previous.revision, storage_policy="allow_temporary_disk"
            )
        )
    assert service.read() == previous
    assert not list(tmp_path.glob(".pending-result-settings-*.tmp"))


def test_policy_publication_waits_for_admission_snapshot_lock(tmp_path: Path) -> None:
    service = PendingResultSettingsService(tmp_path)
    entered = Event()

    def save() -> PendingResultSettings:
        entered.set()
        return service.save(
            PendingResultSettings(storage_policy="allow_temporary_disk")
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with service.coordination_lock:
            admission_snapshot = service.read()
            future = pool.submit(save)
            assert entered.wait(5)
            assert not future.done()
        updated = future.result(timeout=5)
    assert admission_snapshot.storage_policy == "memory_only"
    assert service.read() == updated


def test_direct_invalid_model_copy_cannot_publish_an_unknown_policy(
    tmp_path: Path,
) -> None:
    service = PendingResultSettingsService(tmp_path)
    with pytest.raises(ValidationError):
        service.save(
            PendingResultSettings().model_copy(update={"storage_policy": "unknown"})
        )
    assert not service.path.exists()
