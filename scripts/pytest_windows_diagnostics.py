"""Opt-in CI test-phase and thread-stack diagnostics; no environment or locals."""

from __future__ import annotations

import faulthandler
import os
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import pytest

_stream: TextIO | None = None


def _record(message: str) -> None:
    if _stream is not None:
        _stream.write(f"{datetime.now(UTC).isoformat()} {message[:1024]}\n")
        _stream.flush()


def pytest_sessionstart() -> None:
    global _stream
    directory = Path(os.environ["SCOPECAT_TEST_DIAGNOSTICS"])
    directory.mkdir(parents=True, exist_ok=True)
    worker = os.environ.get("PYTEST_XDIST_WORKER", "controller")
    _stream = (directory / f"{worker}-{os.getpid()}.log").open(
        "w", encoding="utf-8", buffering=1
    )
    _record("session start")
    faulthandler.dump_traceback_later(120, repeat=True, file=_stream)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None]:
    _record(f"setup start {item.nodeid}")
    try:
        return (yield)
    finally:
        _record(f"setup end {item.nodeid}")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None]:
    _record(f"call start {item.nodeid}")
    try:
        return (yield)
    finally:
        _record(f"call end {item.nodeid}")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Generator[None]:
    _record(f"teardown start {item.nodeid}")
    try:
        return (yield)
    finally:
        _record(f"teardown end {item.nodeid}")


def pytest_sessionfinish(exitstatus: int) -> None:
    global _stream
    faulthandler.cancel_dump_traceback_later()
    _record(f"session finish {exitstatus}")
    if _stream is not None:
        _stream.close()
        _stream = None
