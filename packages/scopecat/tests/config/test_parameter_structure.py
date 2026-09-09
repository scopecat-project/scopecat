from __future__ import annotations

import pytest
from scopecat_testkit.config_registry import load_config

from scopecat.config.structure import (
    ParameterStructurePlan,
    StructureConsumer,
    mapped_structure_origins,
    parameter_structure_version,
    preview_parameter_structure,
)
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Float, Int, Scalar, String, Table, TableColumn
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.program.expressions import ParameterLookupUse
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.config_context import (
    ConfigCellRef,
    ConfigContextRef,
    ConfigValueOrigin,
)
from scopecat.records.parameter import (
    ParameterCatalog,
    ParameterDefinition,
    ParameterSnapshot,
    TableParameterValue,
)
from scopecat.records.parameter_structure import (
    AddParameterColumn,
    ChangeParameterColumn,
    ChangeParameterKey,
    ParameterStructureEdit,
    RenameParameterColumn,
    StructureValueDecision,
)


def config() -> ConfigProfileSnapshot:
    base = load_config()
    catalog = ParameterCatalog(
        id="catalog",
        definitions=(
            ParameterDefinition(
                id="observations",
                value_type=Table(
                    columns=(
                        TableColumn("sample", Scalar(String())),
                        TableColumn("frequency", Scalar(QuantityType(unit="GHz"))),
                    ),
                    primary_key=("sample",),
                ),
            ),
        ),
    )
    values = ParameterSnapshot(
        id="values",
        values=(
            TableParameterValue(
                id="observations",
                rows=(
                    {"sample": "a", "frequency": Quantity(4.8, "GHz")},
                    {"sample": "b", "frequency": Quantity(4.9, "GHz")},
                ),
            ),
        ),
    )
    return base.model_copy(
        update={
            "system": base.system.model_copy(update={"parameter_catalog": catalog}),
            "parameter_snapshot": values,
        }
    )


def plan(
    base: ConfigProfileSnapshot, *edits: ParameterStructureEdit
) -> ParameterStructurePlan:
    return ParameterStructurePlan(
        base=ConfigContextRef(entry_id="base", content_hash=config_content_hash(base)),
        structure_version=parameter_structure_version(base.parameter_catalog),
        edits=edits,
    )


def test_optional_column_addition_keeps_unknown_cells_and_old_snapshot() -> None:
    base = config()
    before = base.model_dump_json()
    preview = preview_parameter_structure(
        base,
        plan(
            base,
            AddParameterColumn(
                parameter_id="observations",
                column=ParameterDefinition(id="quality", value_type=Scalar(Float())),
            ),
        ),
    )
    assert preview.impacts[0].missing_rows == (0, 1)
    assert preview.missing_values == (
        "observations[0].quality",
        "observations[1].quality",
    )
    assert preview.origin.before_version != preview.origin.after_version
    assert base.model_dump_json() == before
    assert ConfigProfileSnapshot.model_validate_json(before) == base


def test_explicit_unit_conversion_and_rename_leave_old_values_readable() -> None:
    base = config()
    preview = preview_parameter_structure(
        base,
        plan(
            base,
            ChangeParameterColumn(
                parameter_id="observations",
                column=ParameterDefinition(
                    id="frequency", value_type=Scalar(QuantityType(unit="MHz"))
                ),
                conversion="compatible_unit",
            ),
            RenameParameterColumn(
                parameter_id="observations", column_id="frequency", new_id="carrier"
            ),
        ),
    )
    value = preview.config.parameter_snapshot.get("observations")
    assert isinstance(value, TableParameterValue)
    assert value.rows[0]["carrier"] == Quantity(4800, "MHz")
    assert "frequency" not in value.rows[0]
    original = base.parameter_snapshot.get("observations")
    assert isinstance(original, TableParameterValue)
    assert original.rows[0]["frequency"] == Quantity(4.8, "GHz")
    with pytest.raises(ValueError, match="quantity"):
        preview_parameter_structure(
            base,
            plan(
                base,
                ChangeParameterColumn(
                    parameter_id="observations",
                    column=ParameterDefinition(
                        id="frequency", value_type=Scalar(QuantityType(unit="s"))
                    ),
                    conversion="compatible_unit",
                ),
            ),
        )


def test_key_values_are_explicit_and_duplicate_or_missing_keys_are_rejected() -> None:
    base = config()
    addition = AddParameterColumn(
        parameter_id="observations",
        column=ParameterDefinition(id="position", value_type=Scalar(Int())),
        values=(
            StructureValueDecision(
                key={"sample": "a"},
                value=1,
                origin="imported",
                note="Position register",
            ),
            StructureValueDecision(
                key={"sample": "b"},
                value=2,
                origin="imported",
                note="Position register",
            ),
        ),
    )
    changed = preview_parameter_structure(
        base,
        plan(
            base,
            addition,
            ChangeParameterKey(parameter_id="observations", columns=("position",)),
        ),
    )
    definition = changed.config.parameter_catalog.get("observations")
    assert definition is not None and isinstance(definition.value_type, Table)
    assert definition.value_type.primary_key == ("position",)
    duplicate = addition.model_copy(
        update={
            "values": tuple(
                item.model_copy(update={"value": 1}) for item in addition.values
            )
        }
    )
    with pytest.raises(CheckFailed):
        preview_parameter_structure(
            base,
            plan(
                base,
                duplicate,
                ChangeParameterKey(parameter_id="observations", columns=("position",)),
            ),
        )
    with pytest.raises(CheckFailed):
        preview_parameter_structure(
            base,
            plan(
                base,
                addition.model_copy(update={"values": ()}),
                ChangeParameterKey(parameter_id="observations", columns=("position",)),
            ),
        )


def test_numeric_key_conversion_uses_old_row_identity_and_checks_base() -> None:
    base = config()
    conversion = ChangeParameterColumn(
        parameter_id="observations",
        column=ParameterDefinition(id="sample", value_type=Scalar(Int())),
        conversion="explicit_values",
        values=(
            StructureValueDecision(
                key={"sample": "a"}, value=1, origin="imported", note="New key map"
            ),
            StructureValueDecision(
                key={"sample": "b"}, value=2, origin="imported", note="New key map"
            ),
        ),
    )
    preview = preview_parameter_structure(base, plan(base, conversion))
    value = preview.config.parameter_snapshot.get("observations")
    assert isinstance(value, TableParameterValue)
    assert value.rows[0]["sample"] == 1
    with pytest.raises(ValueError, match="base configuration"):
        preview_parameter_structure(
            base.model_copy(update={"id": "other"}), plan(base, conversion)
        )


@pytest.mark.parametrize("original", [1.25, 9007199254740993])
def test_lossless_numeric_conversion_rejects_rounding(original: float) -> None:
    base = config()
    added = preview_parameter_structure(
        base,
        plan(
            base,
            AddParameterColumn(
                parameter_id="observations",
                column=ParameterDefinition(
                    id="numeric",
                    value_type=Scalar(Int() if isinstance(original, int) else Float()),
                ),
                values=(
                    StructureValueDecision(
                        key={"sample": "a"},
                        value=original,
                        origin="imported",
                        note="Original value",
                    ),
                ),
            ),
        ),
    ).config
    with pytest.raises(ValueError, match=r"lose information|not lossless"):
        preview_parameter_structure(
            added,
            plan(
                added,
                ChangeParameterColumn(
                    parameter_id="observations",
                    column=ParameterDefinition(
                        id="numeric",
                        value_type=Scalar(
                            Float() if isinstance(original, int) else Int()
                        ),
                    ),
                    conversion="lossless_numeric",
                ),
            ),
        )


def test_mapping_keeps_old_source_address_after_key_and_column_rename() -> None:
    base = config()
    edits = plan(
        base,
        RenameParameterColumn(
            parameter_id="observations", column_id="sample", new_id="specimen"
        ),
        RenameParameterColumn(
            parameter_id="observations", column_id="frequency", new_id="carrier"
        ),
        ChangeParameterColumn(
            parameter_id="observations",
            column=ParameterDefinition(
                id="carrier", value_type=Scalar(QuantityType(unit="MHz"))
            ),
            conversion="compatible_unit",
        ),
    )
    preview = preview_parameter_structure(base, edits)
    selected = ConfigContextRef(
        entry_id="new", content_hash=config_content_hash(preview.config)
    )
    origins = mapped_structure_origins(
        base, preview, base_ref=edits.base, selected_ref=selected, inherited=()
    )
    carrier = next(
        item
        for item in origins
        if item.field_id == "carrier" and item.key == {"specimen": "a"}
    )
    assert carrier.source_cell is not None
    assert carrier.source_cell.entry == edits.base
    assert carrier.source_cell.field_id == "frequency"
    assert carrier.source_cell.key == {"sample": "a"}
    assert carrier.evidence is None
    copied = mapped_structure_origins(
        preview.config,
        preview_parameter_structure(
            preview.config,
            plan(
                preview.config,
                RenameParameterColumn(
                    parameter_id="observations", column_id="carrier", new_id="drive"
                ),
            ),
        ),
        base_ref=selected,
        selected_ref=selected,
        inherited=origins,
    )
    drive = next(
        item
        for item in copied
        if item.field_id == "drive" and item.key == {"specimen": "a"}
    )
    assert drive.source_cell == carrier.source_cell


def test_only_explicit_consumer_contracts_are_assessed_after_rename() -> None:
    base = config()
    edits = plan(
        base,
        RenameParameterColumn(
            parameter_id="observations", column_id="frequency", new_id="carrier"
        ),
    )
    edits = edits.model_copy(
        update={
            "consumers": (
                StructureConsumer(
                    name="frequency analysis",
                    contracts=(
                        ParameterLookupUse(
                            table_id="observations",
                            key_input_types=(("sample", Scalar(String())),),
                            literal_key_columns=frozenset(),
                            column_id="frequency",
                            result_type=Scalar(QuantityType(unit="GHz")),
                        ),
                    ),
                ),
            )
        }
    )
    preview = preview_parameter_structure(base, edits)
    assert len(preview.consumers) == 1
    assert preview.consumers[0].name == "frequency analysis"
    assert preview.consumers[0].problems[0].code == "unknown_authoring_parameter_column"
    assert ParameterStructurePlan.model_validate_json(edits.model_dump_json()) == edits


@pytest.mark.parametrize("value", [None, Quantity(5.0, "GHz")])
def test_patch_changes_only_selected_cell_and_retains_other_source(
    value: Quantity | None,
) -> None:
    base = config()
    prior_ref = ConfigContextRef(
        entry_id="original", content_hash=config_content_hash(base)
    )
    evidence = StructureValueDecision(
        key={"sample": "b"},
        value=Quantity(4.9, "GHz"),
        origin="measured",
        note="Retained frequency measurement",
        source_run_id="run-b",
    )
    prior = ConfigValueOrigin(
        parameter_id="observations",
        field_id="frequency",
        key={"sample": "b"},
        layer="context",
        entry=prior_ref,
        evidence=evidence,
        source_cell=ConfigCellRef(
            entry=prior_ref,
            parameter_id="observations",
            field_id="old_frequency",
            key={"sample": "b"},
        ),
    )
    decision = StructureValueDecision(
        key={"sample": "a"},
        value=value,
        origin="unknown" if value is None else "estimated",
        note="One-cell declaration",
    )
    edits = plan(
        base,
        ChangeParameterColumn(
            parameter_id="observations",
            column=ParameterDefinition(
                id="frequency", value_type=Scalar(QuantityType(unit="GHz"))
            ),
            conversion="patch_values",
            values=(decision,),
        ),
    )
    preview = preview_parameter_structure(base, edits)
    table = preview.config.parameter_snapshot.get("observations")
    assert isinstance(table, TableParameterValue)
    assert table.rows[0].get("frequency") == value
    assert table.rows[1]["frequency"] == Quantity(4.9, "GHz")
    origins = mapped_structure_origins(
        base,
        preview,
        base_ref=edits.base,
        selected_ref=ConfigContextRef(
            entry_id="patched", content_hash=config_content_hash(preview.config)
        ),
        inherited=(prior,),
    )
    unchanged = next(
        item
        for item in origins
        if item.field_id == "frequency" and item.key == {"sample": "b"}
    )
    changed = next(
        item
        for item in origins
        if item.field_id == "frequency" and item.key == {"sample": "a"}
    )
    assert unchanged.evidence == prior.evidence
    assert unchanged.source_cell == prior.source_cell
    assert changed.evidence == decision
    assert changed.source_cell is None
    assert ParameterStructurePlan.model_validate_json(edits.model_dump_json()) == edits


def test_patch_does_not_silently_clear_values_incompatible_with_new_type() -> None:
    base = config()
    edit = ChangeParameterColumn(
        parameter_id="observations",
        column=ParameterDefinition(id="frequency", value_type=Scalar(Int())),
        conversion="patch_values",
        values=(
            StructureValueDecision(
                key={"sample": "a"}, value=1, origin="imported", note="New value"
            ),
        ),
    )
    with pytest.raises(CheckFailed):
        preview_parameter_structure(base, plan(base, edit))
    replacement = preview_parameter_structure(
        base,
        plan(base, edit.model_copy(update={"conversion": "explicit_values"})),
    )
    table = replacement.config.parameter_snapshot.get("observations")
    assert isinstance(table, TableParameterValue)
    assert "frequency" not in table.rows[1]
