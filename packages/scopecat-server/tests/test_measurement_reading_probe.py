from __future__ import annotations

import multiprocessing
import os
import tempfile
import time
from pathlib import Path

import psutil
import pytest
from scopecat.measurements.recording_arrow import encode_measurement_append
from scopecat.records.measurement_recording import MeasurementDatasetAppend

from .measurement_reading_probe import (
    MAX_APPEND_BYTES,
    Scene,
    _cancel,
    _isolate_temp,
    _wait_descendants,
    directory_bytes,
    run_session,
    workload,
)


@pytest.mark.parametrize("scene,count", [("scalar", 4096), ("arrays", 256)])
def test_fixed_synthetic_workloads_preserve_identity_and_bound_append(
    scene: Scene, count: int
) -> None:
    header, records = workload(scene, "synthetic-run")
    assert len(records) == header.record_count_limit == count
    assert [record.point_index for record in records] == list(range(count))
    assert all(record.run_id == "synthetic-run" for record in records)
    if scene == "arrays":
        assert header.dataset_schema.dimensions[1].size == 128
        assert set(records[0].observables) == {"I", "Q"}
    batch = records[: count // 32]
    content = encode_measurement_append(
        MeasurementDatasetAppend(
            run_id="synthetic-run",
            header_content_hash=header.content_hash,
            acquisition_start=0,
            records=batch,
        ),
        header.dataset_schema,
    )
    assert len(content) < MAX_APPEND_BYTES


def test_rejects_existing_session_without_touching_its_files(tmp_path: Path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("test", encoding="utf-8")
    session = tmp_path / "existing"
    session.mkdir()
    original = session / "keep"
    original.write_bytes(b"untouched")
    with pytest.raises(FileExistsError):
        run_session(session, static, 15)
    assert list(session.iterdir()) == [original]
    assert original.read_bytes() == b"untouched"


@pytest.mark.parametrize("seconds", [0, 14, 901, 1800])
def test_rejects_invalid_time_budget_before_creating_session(
    tmp_path: Path, seconds: int
):
    session = tmp_path / "new"
    with pytest.raises(ValueError, match="15 and 900"):
        run_session(session, tmp_path, seconds)
    assert not session.exists()


def test_directory_budget_counts_both_retained_scenes(tmp_path: Path):
    for scene in ("scalar", "arrays"):
        (tmp_path / scene).mkdir()
        (tmp_path / scene / "data").write_bytes(b"12345")
    assert directory_bytes(tmp_path) == 10


def test_cancellation_stops_owned_process_without_stopping_another(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    owned = context.Process(target=time.sleep, args=(30,))
    other = context.Process(target=time.sleep, args=(30,))
    owned.start()
    other.start()
    try:
        _cancel(owned, tmp_path, time.monotonic())
        assert not owned.is_alive()
        assert owned.exitcode != 0
        assert (tmp_path / "cancel").exists()
        assert other.is_alive()
    finally:
        for process in (owned, other):
            if process.is_alive():
                process.kill()
            process.join(timeout=5)
            process.close()


def test_temp_files_are_included_in_the_session_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(name, "prior-value")
    monkeypatch.setattr(tempfile, "tempdir", None)
    temporary = _isolate_temp(tmp_path)
    assert all(os.environ[name] == str(temporary) for name in ("TEMP", "TMP", "TMPDIR"))
    with tempfile.NamedTemporaryFile() as file:
        assert Path(file.name).parent == temporary
        assert file.write(b"synthetic") == 9
        file.flush()
        assert directory_bytes(tmp_path) == 9


def test_unexited_captured_descendants_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only the waiting result is simulated; the helper must never kill this process.
    retained = psutil.Process()

    def unexited(
        _procs: list[psutil.Process], *, timeout: float
    ) -> tuple[list[psutil.Process], list[psutil.Process]]:
        return [], [retained]

    monkeypatch.setattr(psutil, "wait_procs", unexited)
    with pytest.raises(
        RuntimeError, match="captured owned descendants have not exited"
    ):
        _wait_descendants([retained], 0)
