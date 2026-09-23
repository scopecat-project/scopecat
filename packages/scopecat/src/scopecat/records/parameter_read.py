"""Retained keyed reads, independent of author models and recipe implementations."""

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
