"""Adversarial scientific and persistence contracts for the isolated experiment."""

import sqlite3
from copy import deepcopy

import pytest
from experiments.result_ledger.core import Conflict, Store


def point(identity: str, x: float) -> dict:
    return {"id": identity, "coordinates": {"frequency": {"value": x, "unit": "Hz"}}}


def unit(identity: str, members: list[str]) -> dict:
    return {
        "id": identity,
        "point_ids": members,
        "required_outputs": ["wave", "quality"],
    }


def outputs(members: list[str]) -> list[dict]:
    count = len(members)
    return [
        {
            "id": "wave",
            "point_ids": members,
            "dims": ["point", "channel", "sample"],
            "shape": [count, 2, 3],
            "dtype": "float64",
            "unit": "V",
            "values": [1.0, 2.0, None, 4.0, 5.0, 6.0] * count,
            "availability": [True, True, False, True, True, True] * count,
            "coordinates": {
                "channel": {"values": ["I", "Q"], "unit": ""},
                "sample": {"values": [0.0, 0.5, 1.0], "unit": "ns"},
            },
            "provenance": {"command_id": "synthetic-command", "source": "simulated"},
        },
        {
            "id": "quality",
            "point_ids": members,
            "dims": ["point"],
            "shape": [count],
            "dtype": "float64",
            "unit": "ratio",
            "values": [0.75] * count,
            "availability": [True] * count,
            "coordinates": {},
            "provenance": {"source": "simulated"},
        },
    ]


def initialized(path, *, closed=True):
    store = Store(path)
    store.initialize(
        "test-run",
        [point("p0", 10), point("p1", 10)],
        [unit("u0", ["p0", "p1"])],
        [{"id": "g0", "unit_ids": ["u0"], "closed": closed}],
    )
    return store, store.acquire("executor-a")


def saved(store, fence, identity="u0", members=None):
    attempt = store.begin_attempt(fence, identity)
    bundle = store.save_result(
        fence, attempt, outputs(members or ["p0", "p1"]), {"provider": "simulated"}
    )
    return attempt, bundle


def test_multi_point_multi_output_is_not_a_point_record_and_snapshot_is_exact(tmp_path):
    with initialized(tmp_path / "ledger.db")[0] as store:
        fence = store.acquire("writer")
        attempt, bundle = saved(store, fence)
        assert not store.readiness("g0")["ready"]
        store.select_result(fence, "u0", bundle)
        assert store.readiness("g0")["ready"]
        snapshot_id = store.publish_group(fence, "g0")
        before = store.snapshot(snapshot_id)
        assert before["points"] == [point("p0", 10), point("p1", 10)]
        assert before["bundles"][0]["outputs"] == outputs(["p0", "p1"])
        state = store.state()
        assert len(state["points"]) == 2
        assert len(state["attempts"]) == len(state["results"]) == 1
        # The durable snapshot must contain scientific structure and unavailable values.
        import json

        encoded = json.dumps(before, sort_keys=True)
        for expected in (
            '"p0"',
            '"p1"',
            '"wave"',
            '"quality"',
            '"ns"',
            '"V"',
            "null",
            "false",
        ):
            assert expected in encoded
        assert store.publish_group(fence, "g0") == snapshot_id
        assert len(store.events()) == 1
        with pytest.raises((Conflict, ValueError)):
            store.begin_attempt(fence, "u0")
        assert store.snapshot(snapshot_id) == before
        assert attempt


def test_saved_result_replay_is_idempotent_but_changed_result_conflicts(tmp_path):
    store, fence = initialized(tmp_path / "ledger.db")
    with store:
        attempt, bundle = saved(store, fence)
        assert (
            store.save_result(
                fence, attempt, outputs(["p0", "p1"]), {"provider": "simulated"}
            )
            == bundle
        )
        changed = outputs(["p0", "p1"])
        changed[0]["values"][0] = 999.0
        with pytest.raises((Conflict, ValueError)):
            store.save_result(fence, attempt, changed, {"provider": "simulated"})
        assert len(store.state()["results"]) == 1


@pytest.mark.parametrize(
    "damage",
    [
        "missing-output",
        "shape",
        "available-null",
        "nan",
        "wrong-point",
        "duplicate-dim",
        "overlapping-block",
    ],
)
def test_invalid_scientific_bundle_cannot_become_durable(tmp_path, damage):
    store, fence = initialized(tmp_path / "ledger.db")
    with store:
        attempt = store.begin_attempt(fence, "u0")
        candidate = outputs(["p0", "p1"])
        if damage == "missing-output":
            candidate.pop()
        elif damage == "shape":
            candidate[0]["shape"][-1] = 4
        elif damage == "available-null":
            candidate[0]["availability"][2] = True
        elif damage == "nan":
            candidate[0]["values"][0] = float("nan")
        elif damage == "wrong-point":
            candidate[0]["point_ids"][0] = "unadmitted"
        elif damage == "duplicate-dim":
            candidate[0]["dims"] = ["point", "sample", "sample"]
        elif damage == "overlapping-block":
            candidate.append(deepcopy(candidate[0]))
        with pytest.raises((Conflict, ValueError)):
            store.save_result(fence, attempt, candidate, {"provider": "simulated"})
        assert store.state()["results"] == []
        assert not store.readiness("g0")["ready"]


def test_unknown_physical_attempt_requires_explicit_retry_authorization(tmp_path):
    path = tmp_path / "ledger.db"
    store, fence = initialized(path)
    old_attempt = store.begin_attempt(fence, "u0")
    store.close()
    with Store(path) as recovered:
        current = recovered.acquire("replacement")
        recovered.reconcile_unknowns(current)
        with pytest.raises(Conflict):
            recovered.begin_attempt(current, "u0")
        with pytest.raises((Conflict, ValueError)):
            recovered.authorize_retry(current, old_attempt, "")
        recovered.authorize_retry(
            current, old_attempt, "operator accepts possible repeated physical action"
        )
        with pytest.raises(Conflict):
            recovered.authorize_retry(current, old_attempt, "rewrite the authorization")
        new_attempt, bundle = saved(recovered, current)
        assert old_attempt != new_attempt
        assert len(recovered.state()["attempts"]) == 2
        recovered.select_result(current, "u0", bundle)
        recovered.publish_group(current, "g0")
        assert len(recovered.state()["attempts"]) == 2


def test_duplicate_active_attempt_and_another_takeover_do_not_erase_unknown(tmp_path):
    path = tmp_path / "ledger.db"
    store, fence = initialized(path)
    with store:
        store.begin_attempt(fence, "u0")
        with pytest.raises(Conflict):
            store.begin_attempt(fence, "u0")
        newer = store.acquire("second-owner")
        store.reconcile_unknowns(newer)
        newest = store.acquire("third-owner")
        store.reconcile_unknowns(newest)
        with pytest.raises(Conflict):
            store.begin_attempt(newest, "u0")
        assert [attempt["status"] for attempt in store.state()["attempts"]] == [
            "unknown"
        ]


def test_selection_changes_are_auditable_until_any_containing_group_publishes(tmp_path):
    store = Store(tmp_path / "ledger.db")
    with store:
        store.initialize(
            "run",
            [point("p0", 1)],
            [unit("u0", ["p0"])],
            [
                {"id": "first", "unit_ids": ["u0"], "closed": True},
                {"id": "second", "unit_ids": ["u0"], "closed": True},
            ],
        )
        fence = store.acquire("writer")
        _, first = saved(store, fence, members=["p0"])
        _, second = saved(store, fence, members=["p0"])
        assert first != second
        store.select_result(fence, "u0", first)
        store.select_result(fence, "u0", second)
        snapshot_id = store.publish_group(fence, "first")
        with pytest.raises(Conflict):
            store.select_result(fence, "u0", first)
        later_id = store.publish_group(fence, "second")
        assert (
            store.snapshot(snapshot_id)["selection"]
            == store.snapshot(later_id)["selection"]
        )
        assert [
            selection["bundle_hash"] for selection in store.state()["selection_history"]
        ] == [first, second]


def test_partitioned_blocks_preserve_different_local_shapes_and_full_coverage(tmp_path):
    store, fence = initialized(tmp_path / "ledger.db")
    with store:
        attempt = store.begin_attempt(fence, "u0")
        blocks = outputs(["p0"]) + outputs(["p1"])
        wave = blocks[2]
        wave["shape"][-1] = 2
        wave["values"] = [11.0, 12.0, 13.0, 14.0]
        wave["availability"] = [True] * 4
        wave["coordinates"]["sample"]["values"] = [0.0, 1.0]
        bundle = store.save_result(fence, attempt, blocks, {"provider": "simulated"})
        store.select_result(fence, "u0", bundle)
        snap = store.snapshot(store.publish_group(fence, "g0"))
        assert snap["bundles"][0]["outputs"] == blocks


def test_sparse_group_can_publish_without_earlier_work_and_event_cursor_replays(
    tmp_path,
):
    with Store(tmp_path / "ledger.db") as store:
        store.initialize(
            "run",
            [point("p0", 1), point("p1", 2)],
            [unit("u0", ["p0"]), unit("u1", ["p1"])],
            [
                {"id": "g0", "unit_ids": ["u0"], "closed": True},
                {"id": "g1", "unit_ids": ["u1"], "closed": True},
            ],
        )
        fence = store.acquire("writer")
        _, later = saved(store, fence, "u1", ["p1"])
        store.select_result(fence, "u1", later)
        first_readable = store.publish_group(fence, "g1")
        assert store.snapshot(first_readable)["points"] == [point("p1", 2)]
        assert store.readiness("g0")["missing_units"] == ["u0"]
        event = store.events()[0]
        assert store.events(event["seq"]) == []
        _, earlier = saved(store, fence, "u0", ["p0"])
        store.select_result(fence, "u0", earlier)
        store.publish_group(fence, "g0")
        caught_up = store.events(event["seq"])
        assert len(caught_up) == 1
        assert caught_up[0]["payload"]["group_id"] == "g0"


def test_fence_revokes_every_old_executor_mutation(tmp_path):
    path = tmp_path / "ledger.db"
    store, fence = initialized(path, closed=False)
    with store, Store(path) as replacement:
        attempt, bundle = saved(store, fence)
        replacement.acquire("new-owner")
        mutations = [
            lambda: store.begin_attempt(fence, "u0"),
            lambda: store.save_result(
                fence, attempt, outputs(["p0", "p1"]), {"provider": "simulated"}
            ),
            lambda: store.select_result(fence, "u0", bundle),
            lambda: store.append_work(
                fence, [point("p2", 12)], [unit("u1", ["p2"])], "g0"
            ),
            lambda: store.close_group(fence, "g0"),
            lambda: store.publish_group(fence, "g0"),
            lambda: store.reconcile_unknowns(fence),
            lambda: store.authorize_retry(fence, attempt, "operator authorization"),
        ]
        before = replacement.state()
        for mutate in mutations:
            with pytest.raises(Conflict):
                mutate()
            assert replacement.state() == before


def test_open_group_addition_and_closure_are_explicit_and_survive_restart(tmp_path):
    path = tmp_path / "ledger.db"
    store, fence = initialized(path, closed=False)
    _, bundle = saved(store, fence)
    store.select_result(fence, "u0", bundle)
    assert not store.readiness("g0")["ready"]
    store.append_work(fence, [point("p2", 12)], [unit("u1", ["p2"])], "g0")
    store.close()
    with Store(path) as recovered:
        current = recovered.acquire("replacement")
        recovered.reconcile_unknowns(current)
        recovered.close_group(current, "g0")
        assert recovered.readiness("g0")["missing_units"] == ["u1"]
        with pytest.raises((Conflict, ValueError)):
            recovered.publish_group(current, "g0")
        _, added = saved(recovered, current, "u1", ["p2"])
        recovered.select_result(current, "u1", added)
        assert recovered.readiness("g0")["ready"]
        recovered.publish_group(current, "g0")
        with pytest.raises((Conflict, ValueError)):
            recovered.append_work(
                current, [point("p3", 13)], [unit("u2", ["p3"])], "g0"
            )


def test_publication_and_notification_are_one_transaction(tmp_path):
    path = tmp_path / "ledger.db"
    store, fence = initialized(path)
    with store:
        _, bundle = saved(store, fence)
        store.select_result(fence, "u0", bundle)
        before = store.state()
        # Fail the actual event insertion, after publication work has begun.
        with sqlite3.connect(path) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            table = "events" if "events" in tables else "outbox"
            conn.execute(
                f"CREATE TRIGGER fail_event BEFORE INSERT ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'injected event failure'); END"
            )
        with pytest.raises(sqlite3.DatabaseError, match="injected event failure"):
            store.publish_group(fence, "g0")
        assert store.state() == before
        with sqlite3.connect(path) as conn:
            conn.execute("DROP TRIGGER fail_event")
        snapshot_id = store.publish_group(fence, "g0")
        assert store.snapshot(snapshot_id)
        assert len(store.events()) == 1


@pytest.mark.parametrize(
    "changed", ["unit", "dtype", "shape", "coordinate", "undeclared"]
)
def test_declared_scientific_contract_rejects_self_consistent_wrong_results(
    tmp_path, changed
):
    declared = unit("u0", ["p0", "p1"])
    original = outputs(["p0", "p1"])
    declared["output_contracts"] = {
        output["id"]: {
            "dims": output["dims"],
            "shape": [None, *output["shape"][1:]],
            "dtype": output["dtype"],
            "unit": output["unit"],
            "coordinates": deepcopy(output["coordinates"]),
        }
        for output in original
    }
    with Store(tmp_path / "ledger.db") as store:
        store.initialize(
            "run",
            [point("p0", 10), point("p1", 11)],
            [declared],
            [{"id": "g", "unit_ids": ["u0"], "closed": True}],
        )
        fence = store.acquire("writer")
        attempt = store.begin_attempt(fence, "u0")
        wrong = deepcopy(original)
        if changed == "unit":
            wrong[0]["unit"] = "A"
        elif changed == "dtype":
            wrong[0]["dtype"] = "float32"
        elif changed == "shape":
            wrong[0]["shape"][-1] = 4
            wrong[0]["values"] = [1.0] * 16
            wrong[0]["availability"] = [True] * 16
            wrong[0]["coordinates"]["sample"]["values"] = [0.0, 0.5, 1.0, 1.5]
        elif changed == "coordinate":
            wrong[0]["coordinates"]["sample"]["unit"] = "s"
        else:
            extra = deepcopy(wrong[1])
            extra["id"] = "unexpected"
            wrong.append(extra)
        with pytest.raises(ValueError, match=r"contract|declared"):
            store.save_result(fence, attempt, wrong, {"source": "simulated"})
        assert store.state()["results"] == []
        accepted = store.save_result(fence, attempt, original, {"source": "simulated"})
        store.select_result(fence, "u0", accepted)
        snap = store.snapshot(store.publish_group(fence, "g"))
        assert snap["units"][0]["output_contracts"] == declared["output_contracts"]
