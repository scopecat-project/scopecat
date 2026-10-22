from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from scopecat_server.services.background_rounds import BackgroundRounds


def test_idle_shutdown_waits_for_round_and_fences_next_round() -> None:
    rounds = BackgroundRounds()
    entered, finish = Event(), Event()
    accepted: list[bool] = []

    def work() -> None:
        with rounds.round() as admitted:
            assert admitted
            entered.set()
            assert finish.wait(5)

    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(work)
        assert entered.wait(5)
        try:
            assert rounds.active == 1
            assert not rounds.stop(lambda: accepted.append(True), only_if_idle=True)
            assert not accepted
        finally:
            finish.set()
        pending.result()
    assert rounds.active == 0
    assert rounds.stop(lambda: accepted.append(True), only_if_idle=True)
    with rounds.round() as admitted:
        assert not admitted
        assert rounds.active == 0
    assert accepted == [True]


def test_failed_shutdown_and_failed_round_restore_admission() -> None:
    rounds = BackgroundRounds()

    def reject() -> None:
        raise ValueError("shutdown failed")

    with pytest.raises(ValueError, match="shutdown failed"):
        rounds.stop(reject, only_if_idle=True)
    with pytest.raises(ValueError, match="round failed"), rounds.round() as admitted:
        assert admitted
        raise ValueError("round failed")
    assert rounds.active == 0
    with rounds.round() as admitted:
        assert admitted


def test_shutdown_decision_serializes_with_concurrent_round_admission() -> None:
    rounds = BackgroundRounds()
    attempted, completed = Event(), Event()

    def work() -> bool:
        attempted.set()
        with rounds.round() as admitted:
            completed.set()
            return admitted

    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = None

        def accept() -> None:
            nonlocal pending
            pending = workers.submit(work)
            assert attempted.wait(5)
            assert not completed.is_set()

        assert rounds.stop(accept, only_if_idle=True)
        assert pending is not None
        assert not pending.result(timeout=5)


def test_drain_pauses_next_round_but_current_work_and_cancel_remain_reversible() -> (
    None
):
    rounds = BackgroundRounds()
    with rounds.round() as started:
        assert started
        rounds.drain(True)
        assert rounds.active == 1
        with rounds.round() as maintenance:
            assert not maintenance
        with rounds.round(current=True) as bounded:
            assert bounded
        assert not rounds.stop(lambda: None, only_if_idle=True)
    rounds.drain(False)
    with rounds.round() as resumed:
        assert resumed


def test_analysis_follow_thread_resumes_after_cancelled_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import Mock

    from scopecat_server.services.analysis_follow import AnalysisFollowRunner

    rounds = BackgroundRounds()
    rounds.drain(True)
    repository = Mock()
    repository.running.return_value = ("follow",)
    repository.states.return_value = {}
    invoked = Event()
    runner = AnalysisFollowRunner(repository, Mock(), Mock(), rounds=rounds)

    def advance(identity: str) -> None:
        invoked.set()

    monkeypatch.setattr(runner, "_advance", advance)
    runner.start()
    try:
        assert not invoked.wait(0.7)
        assert runner._thread is not None and runner._thread.is_alive()
        rounds.drain(False)
        assert invoked.wait(2)
        repository.set_state.assert_not_called()
    finally:
        runner.stop()
