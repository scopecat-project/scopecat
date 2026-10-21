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
