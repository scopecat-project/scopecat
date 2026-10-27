"""Single-source intent preserves scientific membership without hand-built IDs."""

from dataclasses import replace

import pytest
from experiments.result_ledger.authoring import Axis, Intent
from experiments.result_ledger.core import Conflict, Store
from experiments.result_ledger.execution import drain


def intent(**changes) -> Intent:
    return replace(
        Intent(
            run_id="author-test",
            axes=(
                Axis("frequency", (5.0, 5.1), "GHz"),
                Axis("bias", (0.0, 0.2), "V", ("low", "high")),
            ),
            outputs={
                "signal": {
                    "dims": ["point"],
                    "shape": [None],
                    "dtype": "float64",
                    "unit": "V",
                    "coordinates": {},
                }
            },
            work_by=("bias",),
            group_by=("bias",),
            static_closed=True,
            reverse_axes=("bias",),
            open_groups=("adaptive",),
            partition_identity="axis-index",
        ),
        **changes,
    )


def provider(attempt_id: str, unit: dict, points: dict) -> list[dict]:
    return [
        {
            "id": "signal",
            "point_ids": unit["point_ids"],
            "dims": ["point"],
            "shape": [len(unit["point_ids"])],
            "dtype": "float64",
            "unit": "V",
            "values": [
                points[point]["coordinates"]["frequency"]["value"]
                for point in unit["point_ids"]
            ],
            "availability": [True] * len(unit["point_ids"]),
            "coordinates": {},
            "provenance": {"provider": "synthetic-test", "command_id": attempt_id},
        }
    ]


def test_same_2d_membership_and_logical_order_without_redundant_author_lists():
    plan = intent().compile()
    points = {point["id"]: point for point in plan["points"]}

    def coordinates(identity):
        return tuple(
            points[identity]["coordinates"][name]["value"]
            for name in ("frequency", "bias")
        )

    assert [coordinates(point["id"]) for point in plan["points"]] == [
        (5.0, 0.0),
        (5.0, 0.2),
        (5.1, 0.0),
        (5.1, 0.2),
    ]
    assert [
        [coordinates(identity) for identity in unit["point_ids"]]
        for unit in plan["units"]
    ] == [[(5.0, 0.2), (5.1, 0.2)], [(5.0, 0.0), (5.1, 0.0)]]
    assert [group["id"] for group in plan["groups"]] == ["high", "low", "adaptive"]
    assert plan["groups"][0]["unit_ids"] == [plan["units"][0]["id"]]
    assert plan["groups"][-1] == {"id": "adaptive", "unit_ids": [], "closed": False}
    assert all(
        unit["required_outputs"] == list(intent().outputs) for unit in plan["units"]
    )
    assert "points" not in plan["author_intent"]["intent"]
    assert "units" not in plan["author_intent"]["intent"]
    assert "groups" not in plan["author_intent"]["intent"]


def test_duplicate_coordinate_values_have_explicit_distinct_index_partitions():
    declaration = intent(
        axes=(
            Axis("frequency", (5.0, 5.0), "GHz"),
            Axis("bias", (0.0, 0.0), "V", ("first", "second")),
        )
    )
    plan = declaration.compile()
    assert len({point["id"] for point in plan["points"]}) == 4
    assert len(plan["units"]) == 2
    assert [group["id"] for group in plan["groups"]][:2] == ["second", "first"]
    assert declaration.document()["partition_identity"] == "axis-index"
    with pytest.raises(ValueError, match="axis-index"):
        intent(partition_identity="coordinate-value")


def test_work_cannot_cross_analysis_groups_and_traversal_is_not_point_order():
    with pytest.raises(ValueError, match="cannot cross"):
        intent(work_by=(), reverse_axes=())
    forward, reverse = intent(reverse_axes=()).compile(), intent().compile()
    assert forward["points"] == reverse["points"]
    assert forward["units"] == list(reversed(reverse["units"]))
    with pytest.raises(ValueError, match="collide"):
        intent(open_groups=("high",)).compile()


def test_reopen_checks_admitted_author_intent_and_execution_mode(tmp_path):
    path = tmp_path / "intent.db"
    with Store(path) as store:
        intent().initialize(store, commit_mode="atomic")
    with Store(path) as store:
        intent().verify(store)
        with pytest.raises(Conflict, match="author intent"):
            intent(reverse_axes=()).verify(store)
        fence = store.acquire("test")
        with pytest.raises(Conflict, match="commit mode"):
            drain(store, fence, provider, mode="staged")


@pytest.mark.parametrize("mode", ["atomic", "staged"])
def test_generic_execution_and_explicit_open_group_closure_survive_reopen(
    tmp_path, mode
):
    path = tmp_path / "execution.db"
    declaration = intent()
    with Store(path) as store:
        declaration.initialize(store, commit_mode=mode)
        fence = store.acquire("one")
        completed = drain(store, fence, provider)
        assert completed["published_groups"] == ["high", "low"]
        assert len(completed["executed_units"]) == 2
        source = store.state()["groups"][0]["published_snapshot"]
        declaration.extend(
            store,
            fence,
            "adaptive",
            "proposal-1",
            {"frequency": (5.2, 5.3), "bias": (0.15,)},
            source,
            "explicit-test-rule/v1",
        )
        assert not store.readiness("adaptive")["closed"]
    with Store(path) as store:
        declaration.verify(store)
        fence = store.acquire("two")
        completed = drain(store, fence, provider)
        assert len(completed["executed_units"]) == 1
        assert completed["published_groups"] == []
        assert not store.readiness("adaptive")["ready"]
        store.close_group(fence, "adaptive")
        completed = drain(store, fence, provider)
        assert completed["executed_units"] == []
        assert completed["published_groups"] == ["adaptive"]
        before = store.state()
        declaration.extend(
            store,
            fence,
            "adaptive",
            "proposal-1",
            {"frequency": (5.2, 5.3), "bias": (0.15,)},
            source,
            "explicit-test-rule/v1",
        )
        assert store.state() == before
        assert before["proposals"][0]["proposal"]["source_snapshot"] == source
        assert before["proposals"][0]["proposal"]["rule_id"] == "explicit-test-rule/v1"
        assert (
            before["proposals"][0]["proposal"]["author_intent_fingerprint"]
            == declaration.fingerprint
        )
        assert drain(store, fence, provider)["executed_units"] == []


def test_coarser_analysis_group_waits_for_all_derived_units(tmp_path):
    declaration = intent(work_by=("frequency", "bias"))
    with Store(tmp_path / "coarser.db") as store:
        declaration.initialize(store)
        fence = store.acquire("test")
        observations = []
        drain(
            store,
            fence,
            provider,
            on_publish=lambda group, snapshot: observations.append(
                (group, len(store.snapshot(snapshot)["units"]))
            ),
        )
        assert observations == [("high", 2), ("low", 2)]
