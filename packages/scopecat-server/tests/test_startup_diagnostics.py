"""Automatic sampling policy is independent of service import/startup timing."""

import faulthandler
import io
from pathlib import Path
from typing import Literal, TextIO, override

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
        if process == "daemon":
            _startup_diagnostics.observe_shutdown()
            assert scheduled[-1] == (5, stream)
            assert "shutdown accepted" in trace.read_text()
    finally:
        _startup_diagnostics.finish()
    assert cancelled == [True]
    assert stream.closed


def test_shutdown_does_not_schedule_without_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("shutdown sampling must remain opt-in")

    monkeypatch.setattr(faulthandler, "dump_traceback_later", unexpected)
    _startup_diagnostics.observe_shutdown()


def test_shutdown_write_and_close_failures_preserve_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenStream(io.StringIO):
        @override
        def write(self, _text: str, /) -> int:
            raise OSError("diagnostic disk unavailable")

        @override
        def close(self) -> None:
            super().close()
            raise OSError("diagnostic flush unavailable")

    cancelled: list[bool] = []
    monkeypatch.setattr(_startup_diagnostics, "_stream", BrokenStream())
    monkeypatch.setattr(
        faulthandler, "cancel_dump_traceback_later", lambda: cancelled.append(True)
    )
    original = RuntimeError("original shutdown failure")
    with pytest.raises(RuntimeError) as caught:
        try:
            _startup_diagnostics.observe_shutdown()
            _startup_diagnostics.shutdown_stage("closing runtime")
            raise original
        finally:
            _startup_diagnostics.finish()
    assert caught.value is original
    assert cancelled == [True]
    # A failed close must not leave a stream for later shutdown observations.
    _startup_diagnostics.observe_shutdown()
