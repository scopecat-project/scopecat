"""Qualification is explicit; complete tables retain selection membership."""

import pytest
from pydantic import ValidationError

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
from scopecat.records.calibration_dependencies import (
    CalibrationDependencies,
    DependencyComparison,
    DependencyCoverage,
    TableRowDependency,
    compare_calibration_dependencies,
)
from scopecat.records.parameter import (
    ParameterCatalog,
    ParameterDefinition,
    ParameterSnapshot,
    ScalarParameterValue,
    TableParameterValue,
)
from scopecat.records.parameter_revision import (
    ParameterRevision,
    parameter_revision_hash,
)


def revision(
    identity: str, *, unrelated: float = 1, rows: tuple[float, ...] = (1,)
) -> ParameterRevision:
    catalog = ParameterCatalog(
        id="test",
        definitions=(
            ParameterDefinition(
                id="drive",
                value_type=Table(
                    columns=(TableColumn(id="amplitude", value_type=Scalar(Float())),)
                ),
            ),
            ParameterDefinition(id="other", value_type=Scalar(Float())),
        ),
    )
    values = ParameterSnapshot(
        id="values",
        values=(
            TableParameterValue(
                id="drive", rows=tuple({"amplitude": value} for value in rows)
            ),
            ScalarParameterValue(id="other", value=unrelated),
        ),
    )
    return ParameterRevision(
        id=identity,
        catalog=catalog,
        parameters=values,
        content_hash=parameter_revision_hash(catalog, values),
        actor="test",
    )


def qualification() -> CalibrationDependencies:
    return CalibrationDependencies(
        qualification="simulated-drive-v1",
        execution=DependencyCoverage(
            parameters=("drive",),
            basis="All simulated inputs come from the drive table; no external inputs.",
        ),
        analysis=DependencyCoverage(
            parameters=(), basis="Uses retained measurement arrays only."
        ),
        physical=DependencyCoverage(
            parameters=("drive",), basis="Declared simulation has no coupling to other."
        ),
    )


def test_whole_table_membership_and_unrelated_parameters() -> None:
    contract = qualification()
    before = revision("before")
    comparison = compare_calibration_dependencies(
        contract, contract, before, revision("after", unrelated=2)
    )
    assert comparison.status == "unchanged"
    assert comparison.compared_parameters == ("drive",)
    added = compare_calibration_dependencies(
        contract, contract, before, revision("added", rows=(1, 2))
    )
    assert added.status == "changed"
    assert added.changed_parameters == ("drive",)


def test_unknown_physical_coupling_and_contract_change_never_qualify() -> None:
    before, after = revision("before"), revision("after", unrelated=2)
    contract = qualification()
    unknown = contract.model_copy(update={"physical": None})
    comparison = compare_calibration_dependencies(unknown, unknown, before, after)
    assert comparison.status == "unknown"
    assert comparison.reasons == ("physical_coverage_unknown",)
    assert (
        compare_calibration_dependencies(None, contract, before, after).status
        == "unknown"
    )
    assert (
        compare_calibration_dependencies(contract, unknown, before, after).status
        == "unknown"
    )


def test_schema_changes_and_missing_dependencies() -> None:
    before = revision("before")
    contract = qualification()
    missing = contract.model_copy(
        update={
            "physical": DependencyCoverage(
                parameters=("missing",), basis="Explicit but unavailable"
            )
        }
    )
    assert compare_calibration_dependencies(
        missing, missing, before, before
    ).reasons == ("declared_parameter_missing",)
    catalog = before.catalog.model_copy(update={"id": "changed-schema"})
    after = ParameterRevision(
        id="after",
        catalog=catalog,
        parameters=before.parameters,
        content_hash=parameter_revision_hash(catalog, before.parameters),
        actor="test",
    )
    assert compare_calibration_dependencies(
        contract, contract, before, after
    ).reasons == ("parameter_schema_changed",)


def selected_revision(
    rows: tuple[tuple[str, str, float], ...], *, schema: str = "selected"
) -> ParameterRevision:
    catalog = ParameterCatalog(
        id=schema,
        definitions=(
            ParameterDefinition(
                id="drive",
                value_type=Table(
                    columns=(
                        TableColumn(id="target", value_type=Scalar(String())),
                        TableColumn(id="group", value_type=Scalar(String())),
                        TableColumn(id="amplitude", value_type=Scalar(Float())),
                    ),
                    primary_key=("target",),
                ),
            ),
        ),
    )
    parameters = ParameterSnapshot(
        id="selected-values",
        values=(
            TableParameterValue(
                id="drive",
                rows=tuple(
                    {"target": target, "group": group, "amplitude": amplitude}
                    for target, group, amplitude in rows
                ),
            ),
        ),
    )
    return ParameterRevision(
        id="selected",
        catalog=catalog,
        parameters=parameters,
        content_hash=parameter_revision_hash(catalog, parameters),
        actor="test",
    )


def selected_qualification() -> CalibrationDependencies:
    coverage = DependencyCoverage(
        parameters=(),
        rows=(
            TableRowDependency(
                parameter="drive",
                equals=(ScalarParameterValue(id="group", value="read"),),
            ),
        ),
        basis="Reviewed synthetic model consumes complete rows in group read only.",
    )
    return CalibrationDependencies(
        qualification="synthetic-selected-v1",
        execution=coverage,
        analysis=DependencyCoverage(parameters=(), basis="Retained arrays only."),
        physical=coverage,
    )


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ((("q0", "read", 1), ("q1", "other", 9)), "selected_rows_unchanged"),
        ((("q0", "read", 2), ("q1", "other", 2)), "selected_rows_changed"),
        (
            (("q0", "read", 1), ("q1", "other", 2), ("q2", "read", 3)),
            "selection_membership_changed",
        ),
        (
            (("q0", "read", 1), ("q1", "other", 2), ("q2", "other", 3)),
            "selected_rows_unchanged",
        ),
        ((("q0", "other", 1), ("q1", "read", 2)), "selection_membership_changed"),
        ((("q1", "other", 2),), "selection_membership_changed"),
        ((("q1", "other", 2), ("q0", "read", 1)), "selected_rows_unchanged"),
    ],
)
def test_selected_rows_reselect_members_and_explain_invalidation(
    rows: tuple[tuple[str, str, float], ...], reason: str
) -> None:
    before = selected_revision((("q0", "read", 1), ("q1", "other", 2)))
    declared = selected_qualification()
    # Wire roundtrips retain the exact reviewed contract and comparison details.
    requested = CalibrationDependencies.model_validate_json(declared.model_dump_json())
    result = compare_calibration_dependencies(
        declared, requested, before, selected_revision(rows)
    )
    unchanged = reason == "selected_rows_unchanged"
    assert result.status == ("unchanged" if unchanged else "changed")
    assert result.compared_parameters == ("drive",)
    assert result.changed_parameters == (() if unchanged else ("drive",))
    assert (
        len(result.row_comparisons) == 1
    )  # Same selector across domains is deduplicated.
    assert result.row_comparisons[0].reason == reason
    assert result.row_comparisons[0].before_count == 1
    assert result.row_comparisons[0].after_count == sum(
        row[1] == "read" for row in rows
    )
    assert DependencyComparison.model_validate_json(result.model_dump_json()) == result


def test_empty_selection_and_selected_row_order_are_not_ignored() -> None:
    contract = selected_qualification()
    empty = selected_revision((("q1", "other", 2),))
    selected = selected_revision((("q0", "read", 1), ("q2", "read", 3)))
    assert (
        compare_calibration_dependencies(contract, contract, empty, empty).status
        == "unchanged"
    )
    added = compare_calibration_dependencies(contract, contract, empty, selected)
    assert added.status == "changed"
    assert added.row_comparisons[0].reason == "selection_membership_changed"
    reordered = selected_revision((("q2", "read", 3), ("q0", "read", 1)))
    assert (
        compare_calibration_dependencies(contract, contract, selected, reordered).status
        == "changed"
    )


def test_row_qualification_never_weakens_whole_parameters_or_unknown_domains() -> None:
    contract = selected_qualification()
    before = selected_revision((("q0", "read", 1), ("q1", "other", 2)))
    after = selected_revision((("q0", "read", 1), ("q1", "other", 9)))
    whole_physical = contract.model_copy(
        update={
            "physical": DependencyCoverage(
                parameters=("drive",), basis="Physical coupling includes every row."
            )
        }
    )
    assert (
        compare_calibration_dependencies(
            whole_physical, whole_physical, before, after
        ).status
        == "changed"
    )
    for domain in ("execution", "analysis", "physical"):
        unknown = contract.model_copy(update={domain: None})
        result = compare_calibration_dependencies(unknown, unknown, before, after)
        assert result.status == "unknown"
        assert result.reasons == (f"{domain}_coverage_unknown",)
    assert compare_calibration_dependencies(
        contract, whole_physical, before, after
    ).reasons == ("qualification_changed",)
    schema = selected_revision((("q0", "read", 1),), schema="changed")
    assert compare_calibration_dependencies(
        contract, contract, before, schema
    ).reasons == ("parameter_schema_changed",)


@pytest.mark.parametrize("column,value", [("missing", "read"), ("amplitude", "bad")])
def test_invalid_selector_does_not_authorize_empty_selection(
    column: str, value: str
) -> None:
    contract = selected_qualification().model_copy(
        update={
            "execution": DependencyCoverage(
                parameters=(),
                basis="Invalid selector must remain unknown.",
                rows=(
                    TableRowDependency(
                        parameter="drive",
                        equals=(ScalarParameterValue(id=column, value=value),),
                    ),
                ),
            )
        }
    )
    before = selected_revision((("q0", "read", 1),))
    result = compare_calibration_dependencies(contract, contract, before, before)
    assert result.status == "unknown"
    assert result.reasons == ("selector_coverage_unknown",)
    assert result.row_comparisons[0].reason == "selector_invalid"
    assert result.row_comparisons[0].before_count is None


def test_incomplete_cells_and_scalar_parameters_are_unknown() -> None:
    contract = selected_qualification()
    before = selected_revision((("q0", "read", 1),))
    for value in (
        TableParameterValue(id="drive", rows=({"target": "q0", "amplitude": 1},)),
        ScalarParameterValue(id="drive", value=1),
    ):
        parameters = ParameterSnapshot(id="invalid", values=(value,))
        after = ParameterRevision(
            id="invalid",
            catalog=before.catalog,
            parameters=parameters,
            content_hash=parameter_revision_hash(before.catalog, parameters),
            actor="test",
        )
        result = compare_calibration_dependencies(contract, contract, before, after)
        assert result.status == "unknown"
        assert result.row_comparisons[0].reason == "selector_invalid"


@pytest.mark.parametrize("columns", [(), ("",), ("group", "group")])
def test_selector_requires_bounded_unique_predicates(columns: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        TableRowDependency(
            parameter="drive",
            equals=tuple(
                ScalarParameterValue(id=column, value="read") for column in columns
            ),
        )


def typed_revision(column: Scalar, values: tuple[object, ...]) -> ParameterRevision:
    catalog = ParameterCatalog(
        id="typed",
        definitions=(
            ParameterDefinition(
                id="drive",
                value_type=Table(columns=(TableColumn(id="key", value_type=column),)),
            ),
        ),
    )
    parameters = ParameterSnapshot.model_validate(
        {
            "id": "typed",
            "values": [
                {
                    "id": "drive",
                    "shape": "table",
                    "rows": [{"key": value} for value in values],
                }
            ],
        }
    )
    return ParameterRevision(
        id="typed",
        catalog=catalog,
        parameters=parameters,
        content_hash=parameter_revision_hash(catalog, parameters),
        actor="test",
    )


def typed_qualification(value: object) -> CalibrationDependencies:
    selector = TableRowDependency.model_validate(
        {
            "parameter": "drive",
            "equals": [
                {
                    "id": "key",
                    "value": value,
                }
            ],
        }
    )
    coverage = DependencyCoverage(
        parameters=(), rows=(selector,), basis="Typed synthetic inputs."
    )
    return CalibrationDependencies(
        qualification="typed",
        execution=coverage,
        analysis=DependencyCoverage(parameters=(), basis="Retained arrays."),
        physical=coverage,
    )


def test_boolean_and_numeric_selectors_never_share_qualification_or_deduplication() -> (
    None
):
    before = typed_revision(Scalar(Bool()), (True,))
    valid, invalid = typed_qualification(True), typed_qualification(1)
    assert compare_calibration_dependencies(valid, invalid, before, before).reasons == (
        "qualification_changed",
    )
    mixed = valid.model_copy(update={"physical": invalid.physical})
    result = compare_calibration_dependencies(mixed, mixed, before, before)
    assert result.status == "unknown"
    assert len(result.row_comparisons) == 2
    assert result.row_comparisons[1].reason == "selector_invalid"


def test_quantity_predicates_use_query_units_and_ignore_incompatible_dimensions() -> (
    None
):
    before = typed_revision(
        Scalar(QuantityType()), (Quantity(1, "Hz"), Quantity(1, "s"))
    )
    contract = typed_qualification(Quantity(1, "s"))
    result = compare_calibration_dependencies(contract, contract, before, before)
    assert result.status == "unchanged"
    assert result.row_comparisons[0].before_count == 1
    frequency = typed_qualification(Quantity(0.001, "kHz"))
    assert (
        compare_calibration_dependencies(frequency, frequency, before, before)
        .row_comparisons[0]
        .before_count
        == 1
    )


def test_entity_identity_and_conjunctive_predicates() -> None:
    before = typed_revision(
        Scalar(Entity(entity_kind="qubit")), (EntityRef(id="q0", kind="qubit"),)
    )
    contract = typed_qualification(
        EntityRef(id="q0", kind="qubit", metadata={"label": "label"})
    )
    result = compare_calibration_dependencies(contract, contract, before, before)
    assert result.status == "unchanged"
    assert result.row_comparisons[0].before_count == 1
    selected = selected_revision((("q0", "read", 1), ("q1", "read", 2)))
    contract = selected_qualification()
    conjunctive = DependencyCoverage(
        parameters=(),
        basis="Synthetic conjunction.",
        rows=(
            TableRowDependency(
                parameter="drive",
                equals=(
                    ScalarParameterValue(id="group", value="read"),
                    ScalarParameterValue(id="target", value="q0"),
                ),
            ),
        ),
    )
    contract = contract.model_copy(
        update={"execution": conjunctive, "physical": conjunctive}
    )
    result = compare_calibration_dependencies(contract, contract, selected, selected)
    assert result.status == "unchanged"
    assert result.row_comparisons[0].before_count == 1


def test_mapping_order_is_not_row_or_qualification_content() -> None:
    before = selected_revision((("q0", "read", 1),))
    parameters = ParameterSnapshot(
        id="reordered",
        values=(
            TableParameterValue(
                id="drive",
                rows=({"amplitude": 1, "group": "read", "target": "q0"},),
            ),
        ),
    )
    after = ParameterRevision(
        id="reordered",
        catalog=before.catalog,
        parameters=parameters,
        content_hash=parameter_revision_hash(before.catalog, parameters),
        actor="test",
    )
    contract = selected_qualification()
    assert (
        compare_calibration_dependencies(contract, contract, before, after).status
        == "unchanged"
    )
    entity = EntityRef(id="q0", kind="qubit", metadata={"a": 1, "b": True})
    entity_before = typed_revision(Scalar(Entity(entity_kind="qubit")), (entity,))
    declared = typed_qualification(entity)
    requested = typed_qualification(
        entity.model_copy(update={"metadata": {"b": True, "a": 1}})
    )
    assert (
        compare_calibration_dependencies(
            declared, requested, entity_before, entity_before
        ).status
        == "unchanged"
    )


def test_existing_whole_parameter_wire_defaults_to_no_selectors() -> None:
    original = qualification()
    wire = original.model_dump(mode="json")
    for domain in ("execution", "analysis", "physical"):
        del wire[domain]["rows"]
    retained = CalibrationDependencies.model_validate(wire)
    assert retained == original
    before = revision("before")
    result = compare_calibration_dependencies(
        retained, original, before, revision("after", unrelated=2)
    )
    assert result.status == "unchanged"
    assert result.row_comparisons == ()
