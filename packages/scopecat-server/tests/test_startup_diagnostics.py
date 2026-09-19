"""Automatic sampling policy is independent of service import/startup timing."""

import faulthandler
from pathlib import Path
from typing import Literal, TextIO

import pytest

from scopecat_server import _startup_diagnostics


@pytest.mark.parametrize(("process", "delay"), [("daemon", 8), ("instrument", 5)])
def test_startup_schedules_and_cancels_automatic_sample(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process: Literal["daemon", "instrument"],
    delay: int,
) -> None:
    scheduled: list[tuple[int, TextIO]] = []
    cancelled: list[bool] = []

    def schedule(timeout: int, *, file: TextIO) -> None:
        scheduled.append((timeout, file))

    monkeypatch.setenv("SCOPECAT_STARTUP_DIAGNOSTICS", str(tmp_path))
    monkeypatch.setattr(faulthandler, "dump_traceback_later", schedule)
    monkeypatch.setattr(
        faulthandler,
        "cancel_dump_traceback_later",
        lambda: cancelled.append(True),
    )
    try:
        _startup_diagnostics.begin(process=process)
        [(actual_delay, stream)] = scheduled
        assert actual_delay == delay
        assert not stream.closed
        [trace] = tmp_path.glob(f"{process}-startup-*.log")
        assert "python entry" in trace.read_text()
    finally:
        _startup_diagnostics.finish()
    assert cancelled == [True]
    assert stream.closed
