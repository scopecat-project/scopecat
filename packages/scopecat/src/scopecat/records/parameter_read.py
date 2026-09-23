"""Retained keyed reads, independent of author models and recipe implementations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.parameter import ScalarParameterValue


class KeyedParameterRead(BaseModel):
    """One successful lookup's resolved key and the stored cells it consumed.

    This records query-value coverage only. It does not capture catalog schema,
    arbitrary Python reads, runtime reads, analysis reads or physical coupling.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    table: str
    key: tuple[ScalarParameterValue, ...] = Field(min_length=1)
    cells: tuple[ScalarParameterValue, ...] = Field(min_length=1)


class ScalarExpressionReadEvidence(BaseModel):
    """Observed reads for one evaluation context, not whole-run coverage.

    Keys use relation-expression matching, including entity/string identifiers.
    Point overrides must be compared against the same effective point context.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    coverage: Literal["scalar_expression"] = "scalar_expression"
    scalars: tuple[ScalarParameterValue, ...] = ()
    keyed: tuple[KeyedParameterRead, ...] = ()
    incomplete_reasons: tuple[str, ...] = ()


class DomainInputParameterRead(BaseModel):
    """Expression reads for one input at one logical run point."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    point_ordinal: int = Field(ge=0)
    input_kind: Literal["program", "compiler"]
    input_id: str
    evidence: ScalarExpressionReadEvidence


class DomainInputParameterEvidence(BaseModel):
    """Input materialization coverage, separate from target-internal reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    format: Literal["scopecat.domain-input-reads.v1"] = "scopecat.domain-input-reads.v1"
    coverage: Literal["domain_input_materialization"] = "domain_input_materialization"
    entries: tuple[DomainInputParameterRead, ...]
    # Binding may already have folded parameter expressions before this phase.
    incomplete_reasons: tuple[str, ...] = ("upstream_binding_not_captured",)
