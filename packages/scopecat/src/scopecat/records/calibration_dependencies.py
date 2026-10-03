"""Explicit laboratory contracts, never inferred from observed Python reads."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.records.parameter_revision import ParameterRevision


class DependencyCoverage(BaseModel):
    """A reviewed, complete set of whole parameters for one dependency domain.

    Empty parameters explicitly declares independence. Missing coverage instead
    means unknown. The basis must describe code/physical assumptions, including
    external inputs; observed reads alone cannot establish completeness.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    parameters: tuple[str, ...] = Field(max_length=256)
    basis: str = Field(min_length=1, max_length=4000)

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
    All table rows are compared, including newly matching selection members.
    Globals, closures, files or unsupported queries left unqualified are unknown.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    qualification: str = Field(min_length=1, max_length=256)
    execution: DependencyCoverage | None = None
    analysis: DependencyCoverage | None = None
    physical: DependencyCoverage | None = None


class DependencyComparison(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status: Literal["unchanged", "changed", "unknown"]
    reasons: tuple[str, ...]
    compared_parameters: tuple[str, ...] = ()
    changed_parameters: tuple[str, ...] = ()


def compare_calibration_dependencies(
    declared: CalibrationDependencies | None,
    requested: CalibrationDependencies | None,
    before: ParameterRevision,
    after: ParameterRevision,
) -> DependencyComparison:
    """Compare saved inputs under an identical explicit qualification contract."""
    if declared is None or requested is None:
        return DependencyComparison(status="unknown", reasons=("coverage_missing",))
    if declared != requested:
        return DependencyComparison(
            status="unknown", reasons=("qualification_changed",)
        )
    missing = tuple(
        f"{name}_coverage_unknown"
        for name, coverage in (
            ("execution", declared.execution),
            ("analysis", declared.analysis),
            ("physical", declared.physical),
        )
        if coverage is None
    )
    if missing:
        return DependencyComparison(status="unknown", reasons=missing)
    parameters = tuple(
        sorted(
            {
                key
                for coverage in (
                    declared.execution,
                    declared.analysis,
                    declared.physical,
                )
                if coverage is not None
                for key in coverage.parameters
            }
        )
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
    changed = tuple(
        key
        for key in parameters
        if before.parameters.get(key) != after.parameters.get(key)
    )
    return DependencyComparison(
        status="changed" if changed else "unchanged",
        reasons=(
            "dependency_values_changed"
            if changed
            else "declared_dependencies_unchanged",
        ),
        compared_parameters=parameters,
        changed_parameters=changed,
    )
