"""Qualification is explicit; complete tables retain selection membership."""

from scopecat.kernel.value_types import Float, Scalar, Table, TableColumn
from scopecat.records.calibration_dependencies import (
    CalibrationDependencies,
    DependencyCoverage,
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
