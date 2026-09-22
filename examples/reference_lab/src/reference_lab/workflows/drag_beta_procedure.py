"""Durable baseline-to-verification orchestration for DRAG calibration."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from scopecat.api.procedures import LabProcedureContext
from scopecat.automation import procedure
from scopecat.kernel.frozen import thaw_json_value
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.config_context import ContextRunConfigSource

from reference_lab.workflows.drag_beta_analysis import drag_beta_analysis
from reference_lab.workflows.drag_beta_experiment import (
    DragBetaQubit,
    drag_beta_experiment,
)
from reference_lab.workflows.drag_beta_verification import (
    DRAG_BETA_MINIMUM_IMPROVEMENT,
    DRAG_BETA_VERIFICATION_SCHEMA,
    drag_beta_candidate_verification,
)

DRAG_BETA_VERIFICATION_PROCEDURE_ID = "reference-lab.drag-beta-verification"
DRAG_BETA_VERIFICATION_PROCEDURE_VERSION = "10"


class DragBetaVerificationIntent(BaseModel):
    """Exact target and saved working point for a verify-only member."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    qubit: DragBetaQubit
    initial_config: ConfigProfileSnapshot
    initial_config_source: ContextRunConfigSource
    minimum_improvement: float = Field(
        default=DRAG_BETA_MINIMUM_IMPROVEMENT,
        ge=0.0,
    )

    @field_validator("initial_config", mode="before")
    @classmethod
    def thaw_durable_initial_config(cls, value: object) -> object:
        return thaw_json_value(value)

    @model_validator(mode="after")
    def validate_initial_registry_state(self) -> DragBetaVerificationIntent:
        source = self.initial_config_source
        if source.content_hash != config_content_hash(self.initial_config):
            raise ValueError("initial config source hash does not match its snapshot")
        return self


class DragBetaCandidateRejectedError(RuntimeError):
    """A complete verification produced the known scientific result rejected."""


@procedure(
    id=DRAG_BETA_VERIFICATION_PROCEDURE_ID,
    version=DRAG_BETA_VERIFICATION_PROCEDURE_VERSION,
    intent=DragBetaVerificationIntent,
)
def drag_beta_verification_procedure(
    context: LabProcedureContext,
    intent: DragBetaVerificationIntent,
) -> None:
    """Measure and verify one target without publishing a config revision."""

    baseline = context.run(
        "baseline",
        drag_beta_experiment.build(intent.qubit),
        config=intent.initial_config,
        config_source=intent.initial_config_source,
        name=f"{intent.qubit} DRAG beta calibration baseline",
        tags=("calibration", "gate-pulse", intent.qubit),
    )
    fit = context.analyze_run(
        "fit",
        baseline,
        drag_beta_analysis(qubit=intent.qubit),
    )
    candidate = context.published_analysis(fit).candidate_config()
    candidate_run = context.run(
        "candidate",
        drag_beta_experiment.build(intent.qubit),
        config=candidate,
        inputs=(fit,),
        name=f"{intent.qubit} DRAG beta candidate check",
        tags=("calibration", "candidate", intent.qubit),
    )
    verification = context.analyze_project(
        "verification",
        drag_beta_candidate_verification(
            baseline_run=context.run_handle(baseline),
            candidate_run=context.run_handle(candidate_run),
            qubit=intent.qubit,
            minimum_improvement=intent.minimum_improvement,
        ),
        inputs=(baseline, candidate_run),
    )
    decision = context.published_analysis(verification).fact_as(
        "decision",
        DRAG_BETA_VERIFICATION_SCHEMA,
    )
    if not decision.accepted:
        raise DragBetaCandidateRejectedError(
            f"{intent.qubit} DRAG beta verification returned accepted=false"
        )


__all__ = [
    "DRAG_BETA_VERIFICATION_PROCEDURE_ID",
    "DRAG_BETA_VERIFICATION_PROCEDURE_VERSION",
    "DragBetaCandidateRejectedError",
    "DragBetaVerificationIntent",
    "drag_beta_verification_procedure",
]
