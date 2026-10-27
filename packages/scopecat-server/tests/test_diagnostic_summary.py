"""Bounded projection of existing CI evidence, including an interrupted call."""

import html
import os
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts.summarize_test_diagnostics import summarize


def test_interrupted_worker_and_observed_daemon_exit(tmp_path: Path) -> None:
    (tmp_path / "gw1-123.log").write_text(
        "2026-10-27T07:00:00+00:00 setup end test_demo.py::test_exit\n"
        "2026-10-27T07:00:01+00:00 call start test_demo.py::test_exit\n"
        "Thread stack that must not be projected\n",
        encoding="utf-8",
    )
    (tmp_path / "controller-100.log").write_text(
        "2026-10-27T07:00:02+00:00 session finish 1\n", encoding="utf-8"
    )
    (tmp_path / "daemon-lifetime-123-456-1.jsonl").write_text(
        '{"pid":456,"observer_pid":123,"worker":"gw1","returncode":7,'
        '"project_root":"private-root","log_tail":"private-log"}\n'
        "{incomplete",
        encoding="utf-8",
    )
    result = summarize(tmp_path)
    assert "gw1 PID 123: pytest session status=unknown" in result
    assert "last phase=call start test_demo.py::test_exit" in html.unescape(result)
    assert "pytest session status=unknown" in result
    assert "pytest session status=1" in result
    assert "daemon PID 456, observer gw1 PID 123: returncode=7" in result
    assert "Thread stack" not in result
    assert "private-" not in result


def test_missing_malformed_and_bounded_material(tmp_path: Path) -> None:
    assert "Diagnostics unavailable (FileNotFoundError)" in summarize(
        tmp_path / "missing"
    )
    (tmp_path / "gw0-1.log").write_bytes(
        b"old phase" * 10000 + b"\n2026-10-27T07:00:01+00:00 call start <unsafe>\x1b\n"
    )
    (tmp_path / "daemon-lifetime-1-2-3.jsonl").write_text(
        "[]\n{broken", encoding="utf-8"
    )
    result = summarize(tmp_path)
    assert "old phase" not in result
    assert "&lt;unsafe&gt;" in result
    assert "\x1b" not in result
    assert "no valid daemon identity/exit record" in result
    for index in range(40):
        (tmp_path / f"gw{index}-2.log").write_text("bad data", encoding="utf-8")
    result = summarize(tmp_path)
    assert "File limit reached" in result
    assert result.count("<code>") <= 32
    assert len(result) < 40000


def test_command_text_is_inert_and_scan_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nodeid = "test_demo.py::test_exit[##[warning]injected ::error::injected]"
    path = tmp_path / "gw0-1.log"
    path.write_text(
        f"2026-10-27T07:00:00+00:00 call start {nodeid}\n", encoding="utf-8"
    )
    consumed = 0
    closed = False

    def entries() -> Iterator[SimpleNamespace]:
        nonlocal consumed
        for _ in range(300):
            consumed += 1
            yield SimpleNamespace(path=str(path))

    @contextmanager
    def scan(_directory: Path) -> Generator[Iterator[SimpleNamespace]]:
        nonlocal closed
        try:
            yield entries()
        finally:
            closed = True

    monkeypatch.setattr(os, "scandir", scan)
    result = summarize(tmp_path)
    assert consumed == 257  # 256 inspected entries plus one truncation probe.
    assert closed
    assert "File limit reached" in result
    assert "##[" not in result
    assert "::" not in result
    assert nodeid in html.unescape(result)
