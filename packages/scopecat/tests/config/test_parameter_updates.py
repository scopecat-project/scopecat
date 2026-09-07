from __future__ import annotations

import pytest

from scopecat.config.parameter_updates import (
    apply_parameter_change_deltas,
    materialize_parameter_updates,
)
from scopecat.config.parameters import (
    delete_parameter_rows,
    insert_parameter_rows,
    replace_scalar_parameter,
    replace_table_parameter,
    update_parameter_rows,
)
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import (
    Bool,
    Entity,
    Float,
    Scalar,
    String,
    Table,
    TableColumn,
)
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.records.parameter import (
    ParameterCatalog,
    ParameterDefinition,
    ParameterSnapshot,
    ScalarParameterValue,
    TableParameterValue,
)


def test_typed_replacements_materialize_authoritative_snapshot_and_deltas() -> None:
    source = _snapshot()

    candidate, deltas = materialize_parameter_updates(
        catalog=_catalog(),
        base=source,
        candidate_id="candidate",
        updates=(
            replace_scalar_parameter(
                "drive.lo_frequency",
                Quantity(value=5100, unit="MHz"),
            ),
            replace_table_parameter(
                "drive_channels",
                [
                    {
                        "channel_id": "xy2",
                        "resource_id": "drive-c",
                        "enabled": True,
                        "gain": 1,
                        "fixed_if": Quantity(value=140, unit="MHz"),
                    }
                ],
            ),
        ),
    )

    frequency = candidate.get("drive.lo_frequency")
    channels = candidate.get("drive_channels")
    assert frequency == ScalarParameterValue(
        id="drive.lo_frequency",
        value=Quantity(value=5100, unit="MHz"),
    )
    assert isinstance(channels, TableParameterValue)
    assert channels.rows == (
        {
            "channel_id": "xy2",
            "resource_id": "drive-c",
            "enabled": True,
            "gain": 1.0,
            "fixed_if": Quantity(value=140, unit="MHz"),
        },
    )
    assert [delta.parameter_id for delta in deltas] == [
        "drive.lo_frequency",
        "drive_channels",
    ]
    assert all(candidate.get(delta.parameter_id) == delta.after for delta in deltas)
    assert source == _snapshot()


def test_row_updates_materialize_one_whole_value_delta_without_mutating_base() -> None:
    source = _snapshot()

    candidate, deltas = materialize_parameter_updates(
        catalog=_catalog(),
        base=source,
        candidate_id="row-candidate",
        updates=(
            update_parameter_rows(
                "drive_channels",
                key={"channel_id": "xy0"},
                values={"gain": 0.7},
            ),
            insert_parameter_rows(
                "drive_channels",
                rows=[
                    {
                        "channel_id": "xy2",
                        "resource_id": "drive-c",
                        "enabled": True,
                        "gain": 0.25,
                        "fixed_if": Quantity(value=140, unit="MHz"),
                    }
                ],
            ),
            delete_parameter_rows(
                "drive_channels",
                key={"channel_id": "xy1"},
            ),
        ),
    )

    table = candidate.get("drive_channels")
    assert isinstance(table, TableParameterValue)
    assert table.rows == (
        {
            "channel_id": "xy0",
            "resource_id": "drive-a",
            "enabled": True,
            "gain": 0.7,
            "fixed_if": Quantity(value=100, unit="MHz"),
        },
        {
            "channel_id": "xy2",
            "resource_id": "drive-c",
            "enabled": True,
            "gain": 0.25,
            "fixed_if": Quantity(value=140, unit="MHz"),
        },
    )
    assert len(deltas) == 1
    assert deltas[0].parameter_id == "drive_channels"
    assert deltas[0].before == source.get("drive_channels")
    assert deltas[0].after == table
    assert source == _snapshot()


def test_deltas_are_authoritative_candidate_input() -> None:
    source = _snapshot()
    candidate, deltas = materialize_parameter_updates(
        catalog=_catalog(),
        base=source,
        candidate_id="candidate",
        updates=(
            replace_scalar_parameter(
                "drive.lo_frequency",
                Quantity(value=5.1, unit="GHz"),
            ),
        ),
    )
    merged = apply_parameter_change_deltas(
        base=source,
        deltas=deltas,
        candidate_id="merged",
    )
    assert merged == candidate.model_copy(update={"id": "merged"})

    [delta] = deltas
    invalid = delta.model_copy(
        update={
            "before": ScalarParameterValue(
                id=delta.parameter_id,
                value=Quantity(value=4.9, unit="GHz"),
            )
        },
    )
    with pytest.raises(ValueError, match="before value does not match"):
        apply_parameter_change_deltas(
            base=source,
            deltas=(invalid,),
            candidate_id="merged",
        )


def test_materialization_rejects_unknown_and_wrong_shape_updates() -> None:
    source = _snapshot()

    for update, message in (
        (replace_scalar_parameter("unknown", True), "not defined"),
        (
            replace_table_parameter("drive.lo_frequency", []),
            "replacement shape",
        ),
        (
            update_parameter_rows(
                "drive.lo_frequency",
                key={"id": "x"},
                values={"value": 1},
            ),
            "not table-shaped",
        ),
    ):
        with pytest.raises(ValueError, match=message):
            materialize_parameter_updates(
                catalog=_catalog(),
                base=source,
                candidate_id="invalid",
                updates=(update,),
            )


def test_materialization_retains_explicit_equivalent_quantity_representation() -> None:
    candidate, deltas = materialize_parameter_updates(
        catalog=_catalog(),
        base=_snapshot(),
        candidate_id="representation",
        updates=(
            replace_scalar_parameter("drive.lo_frequency", Quantity(5000, "MHz")),
        ),
    )
    assert candidate.get("drive.lo_frequency") == ScalarParameterValue(
        id="drive.lo_frequency", value=Quantity(5000, "MHz")
    )
    assert deltas[0].before != deltas[0].after


def test_quantity_primary_key_lookup_uses_semantic_equality() -> None:
    catalog = ParameterCatalog(
        id="current-catalog",
        definitions=(
            ParameterDefinition(
                id="currents",
                value_type=Table(
                    columns=(
                        TableColumn(
                            id="current",
                            value_type=Scalar(QuantityType(dimension="current")),
                        ),
                        TableColumn(id="label", value_type=Scalar(String())),
                    ),
                    primary_key=("current",),
                ),
            ),
        ),
    )
    source = ParameterSnapshot.model_validate(
        {
            "id": "current-snapshot",
            "values": [
                {
                    "shape": "table",
                    "id": "currents",
                    "rows": [
                        {
                            "current": {"value": 100, "unit": "uA"},
                            "label": "old",
                        }
                    ],
                }
            ],
        }
    )

    candidate, _deltas = materialize_parameter_updates(
        catalog=catalog,
        base=source,
        candidate_id="current-candidate",
        updates=(
            update_parameter_rows(
                "currents",
                key={"current": Quantity(value=0.0001, unit="A")},
                values={"label": "new"},
            ),
        ),
    )

    table = candidate.get("currents")
    assert isinstance(table, TableParameterValue)
    assert table.rows == (
        {
            "current": Quantity(value=100, unit="uA"),
            "label": "new",
        },
    )


def test_entity_primary_key_lookup_ignores_metadata_but_includes_kind() -> None:
    catalog = ParameterCatalog(
        id="entity-catalog",
        definitions=(
            ParameterDefinition(
                id="entities",
                value_type=Table(
                    columns=(
                        TableColumn(id="entity", value_type=Scalar(Entity())),
                        TableColumn(id="label", value_type=Scalar(String())),
                    ),
                    primary_key=("entity",),
                ),
            ),
        ),
    )
    source = ParameterSnapshot(
        id="entity-snapshot",
        values=(
            TableParameterValue(
                id="entities",
                rows=(
                    {
                        "entity": EntityRef(
                            id="q0",
                            kind="qubit",
                            metadata={"labels": ["data"], "index": 0},
                        ),
                        "label": "old",
                    },
                ),
            ),
        ),
    )

    candidate, _deltas = materialize_parameter_updates(
        catalog=catalog,
        base=source,
        candidate_id="entity-candidate",
        updates=(
            update_parameter_rows(
                "entities",
                key={
                    "entity": EntityRef(
                        id="q0",
                        kind="qubit",
                        metadata={"index": 1, "labels": ["ancilla"]},
                    )
                },
                values={"label": "new"},
            ),
        ),
    )

    table = candidate.get("entities")
    assert isinstance(table, TableParameterValue)
    assert table.rows[0]["label"] == "new"

    with pytest.raises(ValueError, match="has no row matching key"):
        materialize_parameter_updates(
            catalog=catalog,
            base=source,
            candidate_id="wrong-kind",
            updates=(
                update_parameter_rows(
                    "entities",
                    key={"entity": EntityRef(id="q0", kind="resonator")},
                    values={"label": "new"},
                ),
            ),
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
                        "enabled": True,
                        "gain": 0.6,
                        "fixed_if": Quantity(value=120, unit="MHz"),
                    },
                ),
            ),
        ),
    )


def test_scoped_edit_preserves_untouched_non_catalog_units_and_original_base() -> None:
    source = _snapshot()
    original = source.get("drive_channels")
    assert isinstance(original, TableParameterValue)
    table = original.model_copy(
        update={
            "rows": (
                dict(original.rows[0]) | {"fixed_if": Quantity(0.1, "GHz")},
                original.rows[1],
            )
        }
    )
    source = source.model_copy(
        update={
            "values": tuple(
                table if item.id == table.id else item for item in source.values
            )
        }
    )
    candidate, deltas = materialize_parameter_updates(
        catalog=_catalog(),
        base=source,
        candidate_id="scoped",
        updates=(
            update_parameter_rows(
                "drive_channels", key={"channel_id": "xy0"}, values={"gain": 0.75}
            ),
        ),
    )
    result = candidate.get("drive_channels")
    assert isinstance(result, TableParameterValue)
    assert result.rows[0]["fixed_if"] == Quantity(0.1, "GHz")
    assert result.rows[1] == table.rows[1]
    assert deltas[0].cells is not None
    [edit] = deltas[0].cells
    assert edit.key == {"channel_id": "xy0"}
    assert edit.field == "gain"
    assert edit.before == table.rows[0]["gain"]
    assert edit.after == 0.75
    assert edit.change_kind == "physical"
    _, equivalent = materialize_parameter_updates(
        catalog=_catalog(),
        base=source,
        candidate_id="unit-edit",
        updates=(
            update_parameter_rows(
                "drive_channels",
                key={"channel_id": "xy0"},
                values={"fixed_if": Quantity(100, "MHz")},
            ),
        ),
    )
    assert equivalent[0].cells is not None
    [representation] = equivalent[0].cells
    assert representation.change_kind == "representation"
    assert representation.before == Quantity(0.1, "GHz")
    assert representation.after == Quantity(100, "MHz")


def test_keyed_row_reorder_has_authoritative_empty_cell_evidence() -> None:
    source = _snapshot()
    table = source.get("drive_channels")
    assert isinstance(table, TableParameterValue)
    candidate, deltas = materialize_parameter_updates(
        catalog=_catalog(),
        base=source,
        candidate_id="row-order",
        updates=(
            replace_table_parameter("drive_channels", tuple(reversed(table.rows))),
        ),
    )
    assert deltas[0].cells == ()
    assert candidate.get("drive_channels") == table.model_copy(
        update={"rows": tuple(reversed(table.rows))}
    )
