"""Event-controlled scheduling; real pipes/lifecycle live in test_launch_workers."""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from scopecat.records.author_revision import AuthorRevisionRef, AuthorRevisionState
from scopecat.records.launch_request import LaunchRequest

from scopecat_server.services.revision_workers import (
    AuthorWorkerBinding,
    RevisionWorkers,
)


def request(key: str, *, slow: bool = False) -> LaunchRequest:
    return LaunchRequest(
        action="list",
        experiment="slow" if slow else "",
        code_revision=AuthorRevisionRef(content_hash="sha256:" + key * 64),
    )


class Worker:
    def __init__(
        self, binding: AuthorWorkerBinding, revision: str, _module: str, **_: object
    ) -> None:
        self.binding = binding
        self.revision = revision
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = False

    def call(
        self, command: LaunchRequest, _timeout: float
    ) -> subprocess.CompletedProcess[str]:
        assert not self.closed
        if command.experiment == "slow":
            self.entered.set()
            assert self.release.wait(5), "test did not release active worker"
        return subprocess.CompletedProcess([], 0, self.revision, "")

    def receive(self, _timeout: float) -> subprocess.CompletedProcess[str]:
        ref = AuthorRevisionRef(content_hash=self.revision)
        return subprocess.CompletedProcess([], 0, ref.model_dump_json(), "")

    def diagnostics(self) -> str:
        return ""

    def close(self) -> None:
        assert not self.entered.is_set() or self.release.is_set()
        self.closed = True


@pytest.fixture
def workers() -> Iterator[list[Worker]]:
    created: list[Worker] = []

    def spawn(
        binding: AuthorWorkerBinding, revision: str, module: str, **kwargs: object
    ) -> Worker:
        worker = Worker(binding, revision, module, **kwargs)
        created.append(worker)
        return worker

    with patch("scopecat_server.services.revision_workers._Worker", side_effect=spawn):
        yield created


def test_warm_revision_progresses_while_same_revision_waits(
    tmp_path: Path, workers: list[Worker]
) -> None:
    binding = AuthorWorkerBinding(tmp_path, Path(sys.executable).absolute())
    pool = RevisionWorkers()
    pool.call(binding, request("a"))
    pool.call(binding, request("b"))
    a, b = workers
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            active = executor.submit(pool.call, binding, request("a", slow=True))
            try:
                assert a.entered.wait(2)
                independent = executor.submit(pool.call, binding, request("b"))
                assert independent.result(timeout=2).stdout == b.revision
                with pytest.raises(
                    subprocess.TimeoutExpired, match="author worker queue"
                ):
                    pool.call(binding, request("a"), timeout=0.01)
                assert len(workers) == 2
                assert not a.closed
            finally:
                a.release.set()
            assert active.result(timeout=2).stdout == a.revision
        assert pool.call(binding, request("a")).stdout == a.revision
    finally:
        pool.close()


def test_new_revision_waits_for_idle_slot_and_never_evicts_active(
    tmp_path: Path, workers: list[Worker]
) -> None:
    binding = AuthorWorkerBinding(tmp_path, Path(sys.executable).absolute())
    pool = RevisionWorkers()
    pool.call(binding, request("a"))
    pool.call(binding, request("b"))
    a, b = workers
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            first = executor.submit(pool.call, binding, request("a", slow=True))
            second = executor.submit(pool.call, binding, request("b", slow=True))
            try:
                assert a.entered.wait(2) and b.entered.wait(2)
                with pytest.raises(
                    subprocess.TimeoutExpired, match="author worker queue"
                ):
                    pool.call(binding, request("c"), timeout=0.01)
                assert len(workers) == 2
                third = executor.submit(pool.call, binding, request("c"))
                b.release.set()
                second.result(timeout=2)
                assert third.result(timeout=2).stdout == workers[-1].revision
                assert b.closed and not a.closed
                assert len(workers) == 3
            finally:
                a.release.set()
                b.release.set()
            first.result(timeout=2)
    finally:
        pool.close()


def test_publication_waits_for_capacity_and_cleans_timed_out_candidate(
    tmp_path: Path, workers: list[Worker]
) -> None:
    binding = AuthorWorkerBinding(tmp_path, Path(sys.executable).absolute())
    pool = RevisionWorkers()
    pool.call(binding, request("a"))
    pool.call(binding, request("b"))
    a, b = workers
    ref = request("c").code_revision
    assert ref is not None
    published = threading.Event()

    def publish() -> AuthorRevisionState:
        published.set()
        return AuthorRevisionState()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(pool.call, binding, request("a", slow=True))
            second = executor.submit(pool.call, binding, request("b", slow=True))
            try:
                assert a.entered.wait(2) and b.entered.wait(2)
                with pytest.raises(
                    subprocess.TimeoutExpired, match="publication queue"
                ):
                    pool.publish_validated(
                        binding, tmp_path, ref, publish, timeout=0.01
                    )
                assert workers[-1].closed and not published.is_set()
                b.release.set()
                second.result(timeout=2)
                pool.publish_validated(binding, tmp_path, ref, publish)
                assert published.is_set() and b.closed and not a.closed
                assert pool.call(binding, request("c")).stdout == ref.content_hash
            finally:
                a.release.set()
                b.release.set()
            first.result(timeout=2)
    finally:
        pool.close()


def test_publication_preserves_equivalent_active_worker(
    tmp_path: Path, workers: list[Worker]
) -> None:
    binding = AuthorWorkerBinding(tmp_path, Path(sys.executable).absolute())
    pool = RevisionWorkers()
    command = request("a", slow=True)
    assert command.code_revision is not None
    pool.call(binding, request("a"))
    a = workers[0]
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            active = executor.submit(pool.call, binding, command)
            try:
                assert a.entered.wait(2)
                pool.publish_validated(
                    binding, tmp_path, command.code_revision, AuthorRevisionState
                )
                assert len(workers) == 2 and workers[1].closed
                assert not a.closed
            finally:
                a.release.set()
            active.result(timeout=2)
        assert pool.call(binding, request("a")).stdout == a.revision
        assert len(workers) == 2
    finally:
        pool.close()


def test_shutdown_drains_active_calls(tmp_path: Path, workers: list[Worker]) -> None:
    binding = AuthorWorkerBinding(tmp_path, Path(sys.executable).absolute())
    pool = RevisionWorkers()
    pool.call(binding, request("a"))
    a = workers[0]
    with ThreadPoolExecutor(max_workers=2) as executor:
        active = executor.submit(pool.call, binding, request("a", slow=True))
        try:
            assert a.entered.wait(2)
            closing = executor.submit(pool.close)
            with pytest.raises(TimeoutError):
                closing.result(timeout=0.01)
            assert not a.closed
        finally:
            a.release.set()
        active.result(timeout=2)
        closing.result(timeout=2)
    assert a.closed


@pytest.mark.parametrize("different_runtime", [False, True])
def test_same_revision_keeps_binding_ownership_during_validation_adoption(
    tmp_path: Path, workers: list[Worker], *, different_runtime: bool
) -> None:
    first = AuthorWorkerBinding(tmp_path / "first", tmp_path / "python-a")
    second = AuthorWorkerBinding(
        first.workspace if different_runtime else tmp_path / "second",
        tmp_path / "python-b" if different_runtime else first.python,
    )
    command = request("a", slow=True)
    assert command.code_revision is not None
    pool = RevisionWorkers()
    pool.call(first, request("a"))
    active_worker = workers[0]
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            active = executor.submit(pool.call, first, command)
            try:
                assert active_worker.entered.wait(2)
                pool.publish_validated(
                    second, tmp_path, command.code_revision, AuthorRevisionState
                )
                assert len(workers) == 2
                assert workers[1].binding == second and not workers[1].closed
                pool.call(second, request("a"), timeout=0.1)
                assert len(workers) == 2  # adopts only this binding's validation worker
                assert not active_worker.closed
            finally:
                active_worker.release.set()
            active.result(timeout=2)
        pool.call(first, request("a"))
        assert len(workers) == 2
    finally:
        pool.close()
