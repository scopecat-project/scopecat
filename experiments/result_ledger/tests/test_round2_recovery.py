"""Independent atomic-completion and durable adaptive-proposal regression tests."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from experiments.result_ledger.core import Conflict, Store

ROOT = Path(__file__).resolve().parents[3]


def point(identity):
    return {"id": identity, "coordinates": {"x": {"value": 1.0, "unit": "Hz"}}}


def unit(identity, member):
    return {"id": identity, "point_ids": [member], "required_outputs": ["signal"]}


def output(member="p0", value=1.0):
    return [
        {
            "id": "signal",
            "point_ids": [member],
            "dims": ["point"],
            "shape": [1],
            "dtype": "float64",
            "unit": "V",
            "values": [value],
            "availability": [True],
            "coordinates": {},
            "provenance": {"provider": "synthetic-only"},
        }
    ]


def setup(path):
    store = Store(path)
    store.initialize(
        "run",
        [point("p0")],
        [unit("u0", "p0")],
        [
            {"id": "fixed", "unit_ids": ["u0"], "closed": True},
            {"id": "adaptive", "unit_ids": [], "closed": False},
        ],
    )
    return store, store.acquire("writer")


def complete(store, fence, attempt, **changes):
    args = {"selection_policy": "sole_candidate", "publish_groups": ("fixed",)}
    args.update(changes)
    return store.complete_attempt(
        fence, attempt, output(), {"source": "simulation"}, **args
    )


def test_atomic_completion_event_failure_rolls_back_result_selection_and_snapshot(
    tmp_path,
):
    path = tmp_path / "ledger.db"
    store, fence = setup(path)
    with store:
        attempt = store.begin_attempt(fence, "u0")
        before = store.state()
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TRIGGER fail_event BEFORE INSERT ON outbox "
                "BEGIN SELECT RAISE(ABORT, 'atomic completion fault'); END"
            )
        with pytest.raises(sqlite3.DatabaseError, match="atomic completion fault"):
            complete(store, fence, attempt)
        assert store.state() == before
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TRIGGER fail_event")
        receipt = complete(store, fence, attempt)
        after = store.state()
        assert (
            len(after["results"])
            == len(after["selections"])
            == len(after["snapshots"])
            == len(after["events"])
            == 1
        )
        assert receipt["snapshot_ids"]["fixed"] == after["snapshots"][0]["id"]
        # A lost response is replayed against committed authority, not reexecuted.
        assert complete(store, fence, attempt) == receipt
        assert store.state() == after


@pytest.mark.parametrize("hook_boundary", ["result-save", "selection"])
def test_in_transaction_hook_failure_exposes_no_partial_completion(
    tmp_path, hook_boundary
):
    store, fence = setup(tmp_path / "ledger.db")
    with store:
        attempt = store.begin_attempt(fence, "u0")
        before = store.state()

        def fail(boundary):
            if boundary == hook_boundary:
                raise RuntimeError("injected completion boundary")

        with pytest.raises(RuntimeError, match="injected completion boundary"):
            complete(store, fence, attempt, fault_hook=fail)
        assert store.state() == before


def test_ambiguous_or_unknown_selection_policy_never_saves_a_partial_candidate(
    tmp_path,
):
    store, fence = setup(tmp_path / "ledger.db")
    with store:
        first = store.begin_attempt(fence, "u0")
        store.save_result(fence, first, output(value=2.0), {"source": "simulation"})
        second = store.begin_attempt(fence, "u0")
        before = store.state()
        with pytest.raises((Conflict, ValueError)):
            complete(store, fence, second)
        assert store.state() == before
        with pytest.raises((Conflict, ValueError)):
            complete(store, fence, second, selection_policy="take-whatever-is-newest")
        assert store.state() == before


def test_proposal_append_survives_reopen_with_exact_source_and_no_inferred_close(
    tmp_path,
):
    path = tmp_path / "ledger.db"
    store, fence = setup(path)
    attempt = store.begin_attempt(fence, "u0")
    source = complete(store, fence, attempt)["snapshot_ids"]["fixed"]
    proposal = {
        "id": "proposal-1",
        "source_snapshot": source,
        "rule_id": "available-mean/v1",
        "axis_values": {"x": [1.0]},
        "author_intent_fingerprint": "test-intent",
    }
    points, units = [point("p1")], [unit("u1", "p1")]
    store.append_work(fence, points, units, "adaptive", proposal=proposal)
    store.close()
    with Store(path) as recovered:
        current = recovered.acquire("replacement")
        recovered.reconcile_unknowns(current)
        assert not recovered.readiness("adaptive")["closed"]
        before = recovered.state()
        recovered.append_work(current, points, units, "adaptive", proposal=proposal)
        assert recovered.state() == before
        accepted_proposal = before["proposals"][0]
        assert accepted_proposal["proposal"] == proposal
        assert accepted_proposal["points"] == points
        assert accepted_proposal["units"] == units
        assert accepted_proposal["group_id"] == "adaptive"
        attempt = recovered.begin_attempt(current, "u1")
        recovered.complete_attempt(
            current,
            attempt,
            output("p1"),
            {"source": "simulation"},
            selection_policy="sole_candidate",
            publish_groups=("adaptive",),
        )
        assert not recovered.readiness("adaptive")["closed"]
        assert len(recovered.state()["snapshots"]) == 1
        recovered.close_group(current, "adaptive")
        recovered.publish_group(current, "adaptive")
        # Lost append acknowledgements remain replayable even after explicit closure.
        recovered.append_work(current, points, units, "adaptive", proposal=proposal)
        assert len(recovered.state()["points"]) == 2
        with pytest.raises(Conflict):
            recovered.append_work(
                current,
                points,
                units,
                "adaptive",
                proposal={**proposal, "rule_id": "changed/v2"},
            )


def cli(*arguments, expected=0):
    result = subprocess.run(  # noqa: S603 - fixed local module and test arguments
        [sys.executable, "-m", "experiments.result_ledger", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == expected, (result.stdout, result.stderr)
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode", ["atomic", "staged"])
@pytest.mark.parametrize(
    "boundary", ["provider-return", "result-save", "group-publish", "notification"]
)
def test_both_commit_modes_survive_hard_process_death_at_each_boundary(
    tmp_path, mode, boundary
):
    directory = str(tmp_path / "experiment")
    cli(
        "demo",
        "--directory",
        directory,
        "--commit-mode",
        mode,
        "--crash-at",
        boundary,
        "--hard-crash",
        expected=75,
    )
    interrupted = cli("inspect", "--directory", directory)
    unknown = boundary == "provider-return" or (
        mode == "atomic" and boundary == "result-save"
    )
    assert len(interrupted["results"]) == (0 if unknown else 1)
    assert len(interrupted["provider_calls"]) == 1
    if unknown:
        cli("recover", "--directory", directory, expected=3)
        blocked = cli("inspect", "--directory", directory)
        assert blocked["attempts"][0]["status"] == "unknown"
        finished = cli("recover", "--directory", directory, "--authorize-retry")
        assert any(
            attempt["status"] == "retry_authorized" for attempt in finished["attempts"]
        )
    else:
        finished = cli("recover", "--directory", directory)
    assert (
        len(finished["results"])
        == len(finished["snapshots"])
        == len(finished["events"])
        == 3
    )
    assert (
        len(finished["attempts"])
        == len(finished["provider_calls"])
        == (4 if unknown else 3)
    )
    before = {
        key: finished[key]
        for key in ("results", "snapshots", "events", "provider_calls")
    }
    again = cli("recover", "--directory", directory)
    assert {key: again[key] for key in before} == before


@pytest.mark.parametrize("mode", ["atomic", "staged"])
@pytest.mark.parametrize("boundary", ["adaptive-append", "adaptive-close"])
def test_cli_adaptive_append_crash_recovers_one_proposal_and_requires_explicit_close(
    tmp_path, mode, boundary
):
    directory = str(tmp_path / "experiment")
    cli(
        "demo",
        "--directory",
        directory,
        "--commit-mode",
        mode,
        "--crash-at",
        boundary,
        "--hard-crash",
        expected=75,
    )
    interrupted = cli("inspect", "--directory", directory)
    adaptive = next(
        group for group in interrupted["groups"] if group["id"] == "adaptive"
    )
    assert adaptive["unit_ids"]
    assert adaptive["closed"] == (boundary == "adaptive-close")
    assert len(interrupted["proposals"]) == 1
    assert len(interrupted["units"]) == 3
    assert len(interrupted["points"]) == 6
    assert len(interrupted["provider_calls"]) == 2
    accepted = interrupted["proposals"][0]["proposal"]
    source = next(
        snapshot
        for snapshot in interrupted["snapshots"]
        if snapshot["group_id"] == "high"
    )
    assert accepted["source_snapshot"] == source["id"]
    assert accepted["rule_id"]
    points = interrupted["points"]
    completed = cli("recover", "--directory", directory)
    assert completed["points"] == points
    assert completed["proposals"] == interrupted["proposals"]
    assert len(completed["snapshots"]) == 3
    assert len(completed["events"]) == 3
    assert len(completed["provider_calls"]) == 3
