"""Atomic completion preserves distinct physical and scientific boundaries."""

from copy import deepcopy

import pytest
from experiments.result_ledger.core import Conflict, Store


def point(identity):
    return {"id": identity, "coordinates": {"x": {"value": 1, "unit": "V"}}}


def unit(identity, point_id):
    return {
        "id": identity,
        "point_ids": [point_id],
        "required_outputs": ["signal"],
        "selection_policy": "sole_candidate",
    }


def outputs(point_id="p", *, dtype="float64", value=1.0):
    return [
        {
            "id": "signal",
            "point_ids": [point_id],
            "dims": ["point"],
            "shape": [1],
            "dtype": dtype,
            "unit": "V",
            "values": [value],
            "availability": [True],
            "coordinates": {},
            "provenance": {"provider": "simulated"},
        }
    ]


def initialize(store, *, open_group=False, multiple=False):
    points = [point("p")]
    units = [unit("u", "p")]
    if multiple:
        points.append(point("q"))
        units.append(unit("v", "q"))
    store.initialize(
        "atomic",
        points,
        units,
        [
            {
                "id": "g",
                "unit_ids": [item["id"] for item in units],
                "closed": not open_group,
            }
        ],
        author_intent={"experiment": "declared scan"},
    )
    return store.acquire("writer")


def complete(store, fence, attempt, *, point_id="p", publish_groups=("g",), hook=None):
    return store.complete_attempt(
        fence,
        attempt,
        outputs(point_id),
        {"source": "simulated"},
        selection_policy="sole_candidate",
        publish_groups=publish_groups,
        fault_hook=hook,
    )


def test_atomic_completion_has_two_transactions_and_audits_selection(tmp_path):
    with Store(tmp_path / "atomic.db") as store:
        fence = initialize(store)
        statements = []
        store.connection.set_trace_callback(statements.append)
        attempt = store.begin_attempt(fence, "u")
        committed = complete(store, fence, attempt)
        assert sum(sql == "COMMIT" for sql in statements) == 2
        store.connection.set_trace_callback(None)
        before = store.state()
        assert before["author_intent"] == {"experiment": "declared scan"}
        assert before["readiness"]["g"] == store.readiness("g")
        choice = before["selection_history"][0]
        assert choice["policy"] == "sole_candidate"
        assert choice["owner"] == "writer"
        assert choice["fence"] == fence
        assert committed["snapshot_ids"]["g"] == before["snapshots"][0]["id"]
        assert len(before["events"]) == 1
        # Lost-response replay returns the same outputs without duplicating history.
        assert complete(store, fence, attempt) == committed
        assert store.state() == before
        current = store.acquire("new-owner")
        with pytest.raises(Conflict, match="fence"):
            complete(store, fence, attempt)
        assert complete(store, current, attempt) == committed
        assert store.state()["selection_history"] == before["selection_history"]


@pytest.mark.parametrize("boundary", ["result-save", "selection"])
def test_atomic_hook_failure_rolls_back_all_post_provider_state(tmp_path, boundary):
    path = tmp_path / "atomic.db"
    with Store(path) as store:
        fence = initialize(store)
        attempt = store.begin_attempt(fence, "u")

        def fail(at):
            if at == boundary:
                raise RuntimeError("injected crash")

        with pytest.raises(RuntimeError, match="injected"):
            complete(store, fence, attempt, hook=fail)
        state = store.state()
        for name in (
            "results",
            "selections",
            "selection_history",
            "snapshots",
            "events",
        ):
            assert state[name] == []
        assert state["attempts"][0]["status"] == "running"
    with Store(path) as store:
        current = store.acquire("recovery")
        assert store.reconcile_unknowns(current) == [attempt]
        assert store.state()["attempts"][0]["physical_status"] == "unknown"
        with pytest.raises(Conflict, match="unknown"):
            store.begin_attempt(current, "u")


def test_pending_closed_and_open_groups_are_not_prematurely_published(tmp_path):
    with Store(tmp_path / "pending.db") as store:
        fence = initialize(store, multiple=True)
        first = store.begin_attempt(fence, "u")
        assert complete(store, fence, first)["snapshot_ids"] == {}
        assert store.events() == []
        second = store.begin_attempt(fence, "v")
        assert "g" in complete(store, fence, second, point_id="q")["snapshot_ids"]
        assert (
            len(store.snapshot(store.events()[0]["payload"]["snapshot_id"])["units"])
            == 2
        )
    with Store(tmp_path / "open.db") as store:
        fence = initialize(store, open_group=True)
        attempt = store.begin_attempt(fence, "u")
        saved = complete(store, fence, attempt)
        assert saved["snapshot_ids"] == {}
        assert store.readiness("g")["closed"] is False
        store.close_group(fence, "g")
        published = store.select_and_publish(
            fence, "u", saved["bundle_hash"], publish_groups=("g",)
        )
        assert set(published) == {"g"}
        assert store.state()["selection_history"][0]["policy"] == "sole_candidate"


def test_sole_candidate_refuses_ambiguous_scientific_choice(tmp_path):
    with Store(tmp_path / "ambiguous.db") as store:
        fence = initialize(store)
        first = store.begin_attempt(fence, "u")
        digest = store.save_result(fence, first, outputs(), {"source": "simulated"})
        second = store.begin_attempt(fence, "u")
        with pytest.raises(Conflict, match="sole_candidate"):
            complete(store, fence, second)
        assert len(store.state()["results"]) == 1
        assert store.state()["selections"] == []
        published = store.select_and_publish(fence, "u", digest, publish_groups=("g",))
        assert store.snapshot(published["g"])["selection"][0]["bundle_hash"] == digest
        assert store.state()["selection_history"][0]["policy"] == "explicit"
        # Explicitly selecting a known result never invents the other attempt's outcome.
        assert store.state()["attempts"][1]["physical_status"] == "unconfirmed"


def test_atomic_publication_rejects_unrelated_group_and_rolls_back_result(tmp_path):
    with Store(tmp_path / "unrelated.db") as store:
        store.initialize(
            "atomic",
            [point("p"), point("q")],
            [unit("u", "p"), unit("v", "q")],
            [{"id": "g", "unit_ids": ["v"], "closed": True}],
        )
        fence = store.acquire("writer")
        attempt = store.begin_attempt(fence, "u")
        with pytest.raises(Conflict, match="contain"):
            complete(store, fence, attempt)
        assert store.state()["results"] == []


def test_proposal_identity_provenance_and_work_commit_together(tmp_path):
    with Store(tmp_path / "proposal.db") as store:
        fence = initialize(store, open_group=True)
        points, units = [point("q")], [unit("v", "q")]
        proposal = {"id": "proposal-1", "rule": "synthetic adaptive step"}
        store.append_work(fence, points, units, "g", proposal=proposal)
        state = store.state()
        assert state["proposals"][0]["proposal"] == proposal
        assert state["proposals"][0]["points"] == points
        assert state["proposals"][0]["units"] == units
        assert state["proposals"][0]["owner"] == "writer"
        store.close_group(fence, "g")
        store.append_work(fence, points, units, "g", proposal=proposal)
        assert len(store.state()["proposals"]) == 1
        changed = deepcopy(proposal)
        changed["rule"] = "a different rule"
        with pytest.raises(Conflict, match="different"):
            store.append_work(fence, points, units, "g", proposal=changed)
        assert len(store.state()["points"]) == 2


def test_unverifiable_proposal_source_and_changed_author_intent_are_rejected(tmp_path):
    with Store(tmp_path / "proposal.db") as store:
        fence = initialize(store, open_group=True)
        with pytest.raises(Conflict, match="snapshots"):
            store.append_work(
                fence,
                [point("q")],
                [unit("v", "q")],
                "g",
                proposal={"id": "proposal-1", "source_snapshot": "missing"},
            )
        assert len(store.state()["points"]) == 1
        assert store.state()["proposals"] == []
        with pytest.raises(Conflict, match="intent"):
            store.initialize(
                "atomic",
                [point("p")],
                [unit("u", "p")],
                [{"id": "g", "unit_ids": ["u"], "closed": False}],
                author_intent={"experiment": "changed scan"},
            )


@pytest.mark.parametrize("dtype,value", [("float32", 0.1), ("complex64", [0.1, 0.2])])
def test_narrow_float_types_are_explicitly_unsupported(tmp_path, dtype, value):
    with Store(tmp_path / "dtype.db") as store:
        fence = initialize(store)
        attempt = store.begin_attempt(fence, "u")
        with pytest.raises(ValueError, match=r"unsupported.*dtype"):
            store.save_result(
                fence,
                attempt,
                outputs(dtype=dtype, value=value),
                {"source": "simulated"},
            )
        assert store.state()["results"] == []


@pytest.mark.parametrize(
    "dtype,value",
    [("float64", 2**54), ("complex128", [2**54, -(2**54)])],
)
def test_exact_large_binary64_integer_components_are_preserved(tmp_path, dtype, value):
    with Store(tmp_path / "dtype.db") as store:
        fence = initialize(store)
        attempt = store.begin_attempt(fence, "u")
        store.save_result(
            fence, attempt, outputs(dtype=dtype, value=value), {"source": "simulated"}
        )
        assert store.state()["results"][0]["outputs"][0]["values"] == [value]


@pytest.mark.parametrize(
    "dtype,value",
    [
        ("float64", 2**53 + 1),
        ("float64", -(2**53 + 1)),
        ("float64", 10**400),
        ("float64", float("inf")),
        ("float64", float("nan")),
        ("complex128", [2**53 + 1, 0]),
        ("complex128", [0, -(2**53 + 1)]),
        ("complex128", [10**400, 0]),
    ],
)
def test_binary64_rejects_rounding_overflow_and_nonfinite_components(
    tmp_path, dtype, value
):
    with Store(tmp_path / "dtype.db") as store:
        fence = initialize(store)
        attempt = store.begin_attempt(fence, "u")
        with pytest.raises(ValueError, match=r"binary64|outside"):
            store.save_result(
                fence,
                attempt,
                outputs(dtype=dtype, value=value),
                {"source": "simulated"},
            )
        assert store.state()["results"] == []
