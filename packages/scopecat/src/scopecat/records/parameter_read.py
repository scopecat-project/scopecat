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
