"""Explicit laboratory contracts, never inferred from observed Python reads."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.kernel.content_identity import canonical_json
from scopecat.kernel.value_identity import scalar_identity, scalar_values_equal
from scopecat.kernel.value_types import Table
from scopecat.kernel.value_validation import coerce_literal, coerce_table_rows
from scopecat.records.parameter import ScalarParameterValue, TableParameterValue
from scopecat.records.parameter_revision import ParameterRevision


class TableRowDependency(BaseModel):
    """Complete rows selected by a conjunction of typed equality predicates.

    Selection is evaluated again in both saved revisions, including empty and
    newly matching sets. Predicates use parameter-query scalar equality, not
    arbitrary Python or relation expressions. Row order remains significant.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    parameter: str = Field(min_length=1)
    equals: tuple[ScalarParameterValue, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_columns(self) -> TableRowDependency:
        columns = tuple(item.id for item in self.equals)
        if any(not column for column in columns) or len(set(columns)) != len(columns):
            raise ValueError("selector columns must be nonempty and unique")
        return self


class DependencyCoverage(BaseModel):
    """A reviewed, complete set of whole parameters and selected table rows.

    Empty parameters and rows explicitly declare independence. Missing coverage
    instead means unknown. The basis must describe code/physical assumptions,
    including external inputs; observed reads cannot establish completeness.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    parameters: tuple[str, ...] = Field(max_length=256)
    basis: str = Field(min_length=1, max_length=4000)
    rows: tuple[TableRowDependency, ...] = Field(default=(), max_length=256)

    @model_validator(mode="after")
    def unique_parameters(self) -> DependencyCoverage:
        if any(not item for item in self.parameters) or len(
            set(self.parameters)
        ) != len(self.parameters):
            raise ValueError("dependency parameters must be nonempty and unique")
        return self


class CalibrationDependencies(BaseModel):
    """Versioned author qualification of execution, analysis and physical inputs.

    Whole catalog schema and complete setup/subject/scenario remain exact fences.
    Selected rows include newly matching members. Globals, closures, files or
    unsupported queries left unqualified are unknown.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    qualification: str = Field(min_length=1, max_length=256)
    execution: DependencyCoverage | None = None
    analysis: DependencyCoverage | None = None
    physical: DependencyCoverage | None = None


class RowDependencyComparison(BaseModel):
    """Explain one declared selector without claiming observed-read coverage."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    selector: TableRowDependency
    status: Literal["unchanged", "changed", "unknown"]
    reason: Literal[
        "selector_invalid",
        "selection_membership_changed",
        "selected_rows_changed",
        "selected_rows_unchanged",
    ]
    before_count: int | None = Field(default=None, ge=0)
    after_count: int | None = Field(default=None, ge=0)


class DependencyComparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["unchanged", "changed", "unknown"]
    reasons: tuple[str, ...]
    compared_parameters: tuple[str, ...] = ()
    changed_parameters: tuple[str, ...] = ()
    row_comparisons: tuple[RowDependencyComparison, ...] = ()


def _wire_identity(model: BaseModel) -> str:
    return canonical_json(model.model_dump(mode="json"))


def _predicate_matches(left: object, right: object) -> bool:
    try:
        return scalar_values_equal(left, right)
    except ValueError:
        # As in parameter queries, incompatible quantities select different rows.
        return False


def _compare_rows(
    selector: TableRowDependency,
    before: ParameterRevision,
    after: ParameterRevision,
) -> RowDependencyComparison:
    definition = before.catalog.get(selector.parameter)
    left = before.parameters.get(selector.parameter)
    right = after.parameters.get(selector.parameter)
    unknown = RowDependencyComparison(
        selector=selector, status="unknown", reason="selector_invalid"
    )
    if (
        definition is None
        or not isinstance(definition.value_type, Table)
        or not isinstance(left, TableParameterValue)
        or not isinstance(right, TableParameterValue)
    ):
        return unknown
    table_type = definition.value_type
    columns = {column.id: column.value_type for column in table_type.columns}
    if any(cell.id not in columns for cell in selector.equals):
        return unknown
    try:
        predicates = tuple(
            (cell.id, coerce_literal(columns[cell.id], cell.value))
            for cell in selector.equals
        )
        # Missing or invalid cells cannot silently become a known empty selection.
        normalized = tuple(
            coerce_table_rows(table_type, table.rows, path=(selector.parameter,))
            for table in (left, right)
        )
    except ValueError:
        return unknown
    selected = tuple(
        tuple(
            index
            for index, row in enumerate(rows)
            if all(
                _predicate_matches(row[column], value) for column, value in predicates
            )
        )
        for rows in normalized
    )
    left_indexes, right_indexes = selected
    membership_changed = len(left_indexes) != len(right_indexes)
    if table_type.primary_key:
        keys = tuple(
            tuple(
                tuple(
                    scalar_identity(rows[index][key]) for key in table_type.primary_key
                )
                for index in indexes
            )
            for rows, indexes in zip(normalized, selected, strict=True)
        )
        membership_changed = membership_changed or keys[0] != keys[1]
    # Compare complete stored rows, not a projection or normalized predicate values.
    changed = _wire_identity(
        TableParameterValue(
            id=selector.parameter,
            rows=tuple(left.rows[index] for index in left_indexes),
        )
    ) != _wire_identity(
        TableParameterValue(
            id=selector.parameter,
            rows=tuple(right.rows[index] for index in right_indexes),
        )
    )
    return RowDependencyComparison(
        selector=selector,
        status="changed" if membership_changed or changed else "unchanged",
        reason=(
            "selection_membership_changed"
            if membership_changed
            else "selected_rows_changed"
            if changed
            else "selected_rows_unchanged"
        ),
        before_count=len(left_indexes),
        after_count=len(right_indexes),
    )


def compare_calibration_dependencies(
    declared: CalibrationDependencies | None,
    requested: CalibrationDependencies | None,
    before: ParameterRevision,
    after: ParameterRevision,
) -> DependencyComparison:
    """Compare saved inputs under an identical explicit qualification contract."""
    if declared is None or requested is None:
        return DependencyComparison(status="unknown", reasons=("coverage_missing",))
    # Python/Pydantic equality can conflate bool and number predicate values.
    if _wire_identity(declared) != _wire_identity(requested):
        return DependencyComparison(
            status="unknown", reasons=("qualification_changed",)
        )
    domains = (
        ("execution", declared.execution),
        ("analysis", declared.analysis),
        ("physical", declared.physical),
    )
    missing = tuple(
        f"{name}_coverage_unknown" for name, coverage in domains if coverage is None
    )
    if missing:
        return DependencyComparison(status="unknown", reasons=missing)
    whole_parameters = {
        key
        for _, coverage in domains
        if coverage is not None
        for key in coverage.parameters
    }
    selectors: list[TableRowDependency] = []
    selector_ids: set[str] = set()
    for _, coverage in domains:
        if coverage is not None:
            for selector in coverage.rows:
                identity = _wire_identity(selector)
                if identity not in selector_ids:
                    selector_ids.add(identity)
                    selectors.append(selector)
    parameters = tuple(
        sorted(whole_parameters | {selector.parameter for selector in selectors})
    )
    if before.catalog != after.catalog:
        return DependencyComparison(
            status="changed",
            reasons=("parameter_schema_changed",),
            compared_parameters=parameters,
        )
    if any(
        before.catalog.get(key) is None
        or before.parameters.get(key) is None
        or after.parameters.get(key) is None
        for key in parameters
    ):
        return DependencyComparison(
            status="unknown",
            reasons=("declared_parameter_missing",),
            compared_parameters=parameters,
        )
    row_comparisons = tuple(
        _compare_rows(selector, before, after) for selector in selectors
    )
    changed = tuple(
        sorted(
            {
                key
                for key in whole_parameters
                if before.parameters.get(key) != after.parameters.get(key)
            }
            | {
                result.selector.parameter
                for result in row_comparisons
                if result.status == "changed"
            }
        )
    )
    unknown = any(result.status == "unknown" for result in row_comparisons)
    return DependencyComparison(
        status="unknown" if unknown else "changed" if changed else "unchanged",
        reasons=(("selector_coverage_unknown",) if unknown else ())
        + (("dependency_values_changed",) if changed else ())
        or ("declared_dependencies_unchanged",),
        compared_parameters=parameters,
        changed_parameters=changed,
        row_comparisons=row_comparisons,
    )
