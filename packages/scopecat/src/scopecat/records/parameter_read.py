"""Retained keyed reads, independent of author models and recipe implementations."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class HostPointParameterRead(BaseModel):
    """Observed host input expressions before effect coalescing, for one point."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    point_ordinal: int = Field(ge=0)
    coverage: Literal["host_input_materialization"] = "host_input_materialization"
    evidence: ScalarExpressionReadEvidence


class BindingParameterRead(BaseModel):
    """Whole-program reads during binding against the base configuration.

    These are not point-local reads or dependencies attributed to one domain call.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    phase: Literal["frontend", "specialization"]
    parameter_scope: Literal["base_configuration"] = "base_configuration"
    coverage: Literal["whole_program_expressions"] = "whole_program_expressions"
    evidence: ScalarExpressionReadEvidence


class HostSuccessStateParameterRead(BaseModel):
    """Fixed completion state uses base parameters, independent of scan overlays."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    coverage: Literal["host_success_state_materialization"] = (
        "host_success_state_materialization"
    )
    parameter_scope: Literal["base_configuration"] = "base_configuration"
    evidence: ScalarExpressionReadEvidence


class HostParameterEvidence(BaseModel):
    """Bounded host preparation observations; never a completion proof."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    format: Literal["scopecat.host.parameter_reads.v2"] = (
        "scopecat.host.parameter_reads.v2"
    )
    entries: tuple[HostPointParameterRead, ...] = Field(default=(), max_length=256)
    success_state: HostSuccessStateParameterRead | None = None
    binding: tuple[BindingParameterRead, ...]
    incomplete_reasons: tuple[str, ...] = ("binding_structure_not_captured",)

    @model_validator(mode="after")
    def validate_scope(self) -> HostParameterEvidence:
        if bool(self.entries) == (self.success_state is not None):
            raise ValueError(
                "host evidence requires either point entries or success state"
            )
        return self


class HostParameterEvidenceRecord(BaseModel):
    """Preparation evidence scoped to the executor segment that published it."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    segment_id: str = Field(min_length=1)
    evidence: HostParameterEvidence


class DomainInputParameterEvidence(BaseModel):
    """Input materialization coverage, separate from target-internal reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    format: Literal["scopecat.domain-input-reads.v2"] = "scopecat.domain-input-reads.v2"
    coverage: Literal["domain_input_materialization"] = "domain_input_materialization"
    entries: tuple[DomainInputParameterRead, ...]
    binding: tuple[BindingParameterRead, ...] = ()
    incomplete_reasons: tuple[str, ...]
