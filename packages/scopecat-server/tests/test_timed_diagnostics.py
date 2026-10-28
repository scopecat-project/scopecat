"""Sampler lifetime safety and real synchronous stack capture."""

from __future__ import annotations

import ast
import faulthandler
import threading
from collections.abc import Generator
from pathlib import Path
from typing import TextIO

import pytest

from scopecat_server import _startup_diagnostics, _timed_diagnostics


@pytest.fixture(autouse=True)
def stop_sampler() -> Generator[None]:
    _timed_diagnostics.cancel()
    try:
        yield
    finally:
        _startup_diagnostics.finish()
        _timed_diagnostics.cancel()


@pytest.mark.parametrize("repeat", [False, True])
def test_real_stack_sample_and_thread_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repeat: bool
) -> None:
    sampled = threading.Event()
    calls: list[threading.Thread] = []
    dump = faulthandler.dump_traceback

    def capture(*, file: TextIO, all_threads: bool) -> None:
        dump(file=file, all_threads=all_threads)
        calls.append(threading.current_thread())
        if len(calls) >= (2 if repeat else 1):
            sampled.set()

    monkeypatch.setattr(faulthandler, "dump_traceback", capture)
    path = tmp_path / "trace.log"
    with path.open("w") as stream:
        _timed_diagnostics.schedule(0.001, file=stream, repeat=repeat)
        assert sampled.wait(5)
        _timed_diagnostics.cancel()
        assert not stream.closed  # The sampler borrows, never owns, this file.
    assert calls
    assert all(not thread.is_alive() for thread in calls)
    if not repeat:
        assert len(calls) == 1
    text = path.read_text()
    assert "Timeout (0:00:00.001000)!" in text
    assert "test_timed_diagnostics.py" in text
    assert "<invalid frame>" not in text


def test_cancel_waits_for_active_sample_before_file_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    cancelling = threading.Event()
    closed = threading.Event()
    sampled: list[bool] = []
    stream = (tmp_path / "trace.log").open("w")

    def capture(*, file: TextIO, all_threads: bool) -> None:
        assert all_threads
        entered.set()
        assert release.wait(5)
        file.write("sample finished\n")
        sampled.append(not file.closed)

    def close() -> None:
        cancelling.set()
        _timed_diagnostics.cancel()
        stream.close()
        closed.set()

    monkeypatch.setattr(faulthandler, "dump_traceback", capture)
    closer = threading.Thread(target=close)
    try:
        _timed_diagnostics.schedule(0.001, file=stream)
        assert entered.wait(5)
        closer.start()
        assert cancelling.wait(5)
        assert not closed.wait(0.05)
        assert not stream.closed
    finally:
        release.set()
        if closer.ident is not None:
            closer.join(5)
        _timed_diagnostics.cancel()
        stream.close()
    assert not closer.is_alive()
    assert closed.is_set()
    assert sampled == [True]


def test_replacement_cancels_pending_sample_and_preserves_new_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sampled = threading.Event()
    files: list[TextIO] = []

    def capture(*, file: TextIO, all_threads: bool) -> None:
        assert all_threads
        files.append(file)
        sampled.set()

    monkeypatch.setattr(faulthandler, "dump_traceback", capture)
    with (
        (tmp_path / "first").open("w") as first,
        (tmp_path / "second").open("w") as second,
    ):
        _timed_diagnostics.schedule(60, file=first)
        _timed_diagnostics.schedule(0.001, file=second)
        first.close()
        assert sampled.wait(5)
        _timed_diagnostics.cancel()
    assert files == [second]
    assert (tmp_path / "first").read_text() == ""


def test_repeated_startup_begin_finish_closes_files_and_joins_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPECAT_STARTUP_DIAGNOSTICS", str(tmp_path))
    streams: list[TextIO] = []
    for _ in range(3):
        _startup_diagnostics.begin()
        stream = _startup_diagnostics._stream
        assert stream is not None
        assert all(previous.closed for previous in streams)
        streams.append(stream)
    _startup_diagnostics.finish()
    _startup_diagnostics.finish()
    assert all(stream.closed for stream in streams)
    assert not any(
        thread.name == "scopecat-traceback-sampler" for thread in threading.enumerate()
    )


def test_cancel_pending_sample_without_touching_closed_file(tmp_path: Path) -> None:
    with (tmp_path / "trace.log").open("w") as stream:
        _timed_diagnostics.schedule(60, file=stream)
        _timed_diagnostics.cancel()
    _timed_diagnostics.cancel()
    assert (tmp_path / "trace.log").read_text() == ""


def test_no_native_watchdog_calls_or_imports() -> None:
    root = Path(__file__).resolve().parents[3]
    forbidden = {"dump_traceback_later", "cancel_dump_traceback_later"}
    offenders: list[str] = []
    for directory in ("packages", "scripts", "testing"):
        for path in (root / directory).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Attribute) and node.attr in forbidden) or (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "faulthandler"
                    and any(alias.name in forbidden for alias in node.names)
                ):
                    offenders.append(str(path.relative_to(root)))
    assert offenders == []
