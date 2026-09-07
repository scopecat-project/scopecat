from __future__ import annotations

from scopecat_testkit.workflow_fixtures import load_config

from scopecat.config.drafts import ConfigDraft
from scopecat.kernel.problems import ModelLocation
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import (
    Bool,
    Float,
    Scalar,
    String,
    Table,
    TableColumn,
)
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.parameter import (
    ParameterCatalog,
    ParameterDefinition,
    ParameterSnapshot,
    ScalarParameterValue,
    TableParameterValue,
)


def test_draft_builds_candidate_and_deltas() -> None:
    source = _config()
    source_before = source.model_copy(deep=True)
    draft = ConfigDraft.from_snapshot(source).replace_scalar(
        "drive.lo_frequency",
        Quantity(value=5100, unit="MHz"),
    )
    (
        draft.table("drive_channels")
        .update(key={"channel_id": "xy0"}, values={"gain": 0.7})
        .insert(
            [
                {
                    "channel_id": "xy2",
                    "resource_id": "drive-c",
                    "enabled": True,
                    "gain": 0.25,
                    "fixed_if": Quantity(value=140, unit="MHz"),
                }
            ]
        )
        .delete(key={"channel_id": "xy1"})
    )

    result = draft.check(candidate_id="candidate")

    assert draft.base_content_hash == config_content_hash(source)
    assert result.ok
    assert result.problems == ()
    assert result.candidate is not None
    assert result.candidate.id == "candidate"
    assert result.candidate.parameter_snapshot.id == "candidate.parameters"
    assert [delta.parameter_id for delta in result.deltas] == [
        "drive.lo_frequency",
        "drive_channels",
    ]
    frequency = result.candidate.parameter_snapshot.get("drive.lo_frequency")
    assert frequency == ScalarParameterValue(
        id="drive.lo_frequency",
        value=Quantity(value=5100, unit="MHz"),
    )
    table_delta = result.deltas[1]
    assert isinstance(table_delta.before, TableParameterValue)
    assert isinstance(table_delta.after, TableParameterValue)
    assert [row["channel_id"] for row in table_delta.before.rows] == ["xy0", "xy1"]
    assert [row["channel_id"] for row in table_delta.after.rows] == ["xy0", "xy2"]
    assert table_delta.after.rows[0]["gain"] == 0.7
    assert source == source_before


def test_draft_supports_complete_table_replacement() -> None:
    draft = ConfigDraft(_config())
    draft.table("drive_channels").replace(
        [
            {
                "channel_id": "xy0",
                "resource_id": "drive-a",
                "enabled": False,
                "gain": 0.5,
                "fixed_if": Quantity(value=100, unit="MHz"),
            }
        ]
    )
    result = draft.check()

    assert result.ok
    [delta] = result.deltas
    assert delta.parameter_id == "drive_channels"
    assert isinstance(delta.before, TableParameterValue)
    assert isinstance(delta.after, TableParameterValue)
    assert len(delta.before.rows) == 2
    assert len(delta.after.rows) == 1
    assert delta.after.rows[0]["enabled"] is False


def test_invalid_draft_returns_cell_addressable_problem() -> None:
    result = ConfigDraft(_config()).replace_scalar("drive.lo_frequency", True).check()

    assert not result.ok
    assert result.candidate is None
    assert result.deltas == ()
    [problem] = result.problems
    assert problem.code == "invalid_parameter_quantity"
    assert isinstance(problem.location, ModelLocation)
    assert problem.location.root == "parameter_snapshot"
    assert problem.location.path == ("values", "drive.lo_frequency", "value")


def test_empty_draft_rejected_but_explicit_representation_edit_is_candidate() -> None:
    empty = ConfigDraft(_config()).check()
    no_op = (
        ConfigDraft(_config())
        .replace_scalar(
            "drive.lo_frequency",
            Quantity(value=5000, unit="MHz"),
        )
        .check()
    )

    assert not empty.ok
    assert empty.problems[0].code == "config_draft_empty"
    assert no_op.ok
    assert no_op.deltas[0].before != no_op.deltas[0].after


def _config() -> ConfigProfileSnapshot:
    base = load_config()
    catalog = _catalog()
    return base.model_copy(
        update={
            "system": base.system.model_copy(
                update={"parameter_catalog": catalog},
            ),
            "parameter_snapshot": _snapshot(),
        },
        deep=True,
    )


def _catalog() -> ParameterCatalog:
    return ParameterCatalog(
        id="catalog",
        definitions=(
            ParameterDefinition(
                id="drive.lo_frequency",
                value_type=Scalar(QuantityType(unit="GHz", minimum=4.0, maximum=6.0)),
            ),
            ParameterDefinition(
                id="drive_channels",
                value_type=Table(
                    columns=(
                        TableColumn(
                            id="channel_id",
                            value_type=Scalar(String()),
                        ),
                        TableColumn(
                            id="resource_id",
                            value_type=Scalar(String()),
                        ),
                        TableColumn(id="enabled", value_type=Scalar(Bool())),
                        TableColumn(id="gain", value_type=Scalar(Float())),
                        TableColumn(
                            id="fixed_if",
                            value_type=Scalar(QuantityType(unit="MHz")),
                        ),
                    ),
                    primary_key=("channel_id",),
                ),
            ),
        ),
    )


def _snapshot() -> ParameterSnapshot:
    return ParameterSnapshot(
        id="accepted",
        values=(
            ScalarParameterValue(
                id="drive.lo_frequency",
                value=Quantity(value=5.0, unit="GHz"),
            ),
            TableParameterValue(
                id="drive_channels",
                rows=(
                    {
                        "channel_id": "xy0",
                        "resource_id": "drive-a",
                        "enabled": True,
                        "gain": 0.5,
                        "fixed_if": Quantity(value=100, unit="MHz"),
                    },
                    {
                        "channel_id": "xy1",
                        "resource_id": "drive-b",
                        "enabled": False,
                        "gain": 0.25,
                        "fixed_if": Quantity(value=120, unit="MHz"),
                    },
                ),
            ),
        ),
    )
