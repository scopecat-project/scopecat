"""Target-complete DRAG calibration on one captured parameter branch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import scopecat as sc
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from scopecat.api.procedures import LabProcedureContext
from scopecat.api.run import RunHandle
from scopecat.automation import AnalysisPublicationOutputRef, RunOutputRef, procedure
from scopecat.daemon.views import ParameterResolution
from scopecat.kernel.frozen import thaw_json_value
from scopecat.records.parameter_branch import ParameterBranch

from reference_lab.workflows.drag_beta_analysis import drag_beta_analysis
from reference_lab.workflows.drag_beta_experiment import (
    DragBetaQubit,
    drag_beta_experiment,
)
from reference_lab.workflows.drag_beta_verification import (
    DRAG_BETA_MINIMUM_IMPROVEMENT,
    DRAG_BETA_VERIFICATION_SCHEMA,
    DragBetaVerification,
    evaluate_drag_beta_candidate,
)


class DragBranchCalibrationIntent(BaseModel):
    """Immutable requested targets, execution inputs and publication destination."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    targets: tuple[DragBetaQubit, ...] = Field(min_length=1, max_length=2)
    initial: ParameterResolution
    destination: ParameterBranch
    result_revision_id: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    composition: Literal["parallel", "sequential"] = "parallel"
    minimum_improvement: float = Field(default=DRAG_BETA_MINIMUM_IMPROVEMENT, ge=0)

    @field_validator("initial", mode="before")
    @classmethod
    def thaw_initial(cls, value: object) -> object:
        return thaw_json_value(value)

    @field_validator("targets")
    @classmethod
    def unique_targets(
        cls, value: tuple[DragBetaQubit, ...]
    ) -> tuple[DragBetaQubit, ...]:
        if len(value) != len(set(value)):
            raise ValueError("requested targets must be unique")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def same_base(self) -> DragBranchCalibrationIntent:
        if self.initial.config_source.parameters != self.destination.revision:
            raise ValueError("baseline must use the captured destination revision")
        if self.initial.config_source.overrides:
            raise ValueError("calibration requires saved parameters without overrides")
        return self


@dataclass(frozen=True)
class JointDragDecision:
    requested: tuple[str, ...]
    checked: tuple[str, ...]
    rejected: tuple[str, ...]
    missing: tuple[str, ...]
    accepted: bool


JOINT_DRAG_DECISION_SCHEMA = sc.AnalysisFactSchema(
    "reference-lab.joint-drag-decision.v1", JointDragDecision
)


@sc.analysis_step(id="reference-lab.joint-drag-verification")
def verify_joint_drag(
    context: sc.AnalysisContext,
    *,
    targets: tuple[DragBetaQubit, ...],
    baselines: dict[DragBetaQubit, RunHandle],
    candidates: dict[DragBetaQubit, RunHandle],
    minimum_improvement: float,
) -> sc.Analysis:
    """Retain every target score; missing or rejected targets block publication.

    This simulator policy checks leakage on each target under the joint settings.
    A hardware policy must add measurements for relevant coupled effects.
    """
    if not targets or len(set(targets)) != len(targets):
        raise ValueError("verification requires nonempty unique requested targets")
    if (set(baselines) | set(candidates)) - set(targets):
        raise ValueError("verification contains unrequested targets")
    decisions: list[tuple[str, DragBetaVerification]] = []
    checked: list[str] = []
    rejected: list[str] = []
    for target in targets:
        baseline = baselines.get(target)
        candidate = candidates.get(target)
        baseline_data = (
            None
            if baseline is None
            else context.measurements(
                baseline, id=f"baseline-{target}", role="baseline"
            )
        )
        candidate_data = (
            None
            if candidate is None
            else context.measurements(
                candidate, id=f"candidate-{target}", role="candidate"
            )
        )
        if baseline_data is None or candidate_data is None:
            continue
        decision = evaluate_drag_beta_candidate(
            baseline_data,
            candidate_data,
            qubit=target,
            minimum_improvement=minimum_improvement,
        )
        decisions.append((target, decision))
        checked.append(target)
        if not decision.accepted:
            rejected.append(target)
    missing = tuple(target for target in targets if target not in checked)
    result = context.result()
    for target, decision in decisions:
        result = result.fact(target, decision, schema=DRAG_BETA_VERIFICATION_SCHEMA)
    return result.fact(
        "decision",
        JointDragDecision(
            requested=targets,
            checked=tuple(checked),
            rejected=tuple(rejected),
            missing=missing,
            accepted=not missing and not rejected,
        ),
        schema=JOINT_DRAG_DECISION_SCHEMA,
    )


class DragBranchCalibrationRejected(RuntimeError):
    """A retained complete assessment declined branch publication."""


@procedure(
    id="reference-lab.drag-branch-calibration",
    version="2",
    intent=DragBranchCalibrationIntent,
)
def drag_branch_calibration(
    ctx: LabProcedureContext, intent: DragBranchCalibrationIntent
) -> None:
    baselines: dict[DragBetaQubit, RunOutputRef] = {}
    fits: list[tuple[AnalysisPublicationOutputRef, str]] = []
    for target in intent.targets:
        preceding = fits[-1] if fits and intent.composition == "sequential" else None
        candidate_input = (
            ctx.published_analysis(preceding[0]).candidate_config(preceding[1])
            if preceding is not None
            else None
        )
        baseline = ctx.run(
            f"baseline-{target}",
            drag_beta_experiment.build(target),
            config=candidate_input
            if candidate_input is not None
            else intent.initial.config,
            config_source=None
            if candidate_input is not None
            else intent.initial.config_source,
            inputs=(preceding[0],) if preceding is not None else (),
            name=f"{target} DRAG baseline",
        )
        baselines[target] = baseline
        fit = ctx.analyze_run(
            f"fit-{target}", baseline, drag_beta_analysis(qubit=target)
        )
        fits.append((fit, f"{target}-drag-beta"))

    if len(fits) == 1:
        candidate_ref, proposal_id = fits[0]
    else:
        candidate_ref = ctx.combine_parameter_candidates(
            "compose",
            tuple(fits),
            name="joint-drag",
            mode=intent.composition,
        )
        proposal_id = "joint-drag"
    candidate = ctx.published_analysis(candidate_ref).candidate_config(proposal_id)
    checks: dict[DragBetaQubit, RunOutputRef] = {}
    for target in intent.targets:
        checks[target] = ctx.run(
            f"check-{target}",
            drag_beta_experiment.build(target),
            config=candidate,
            inputs=(candidate_ref,),
            name=f"{target} DRAG joint check",
        )
    verification = ctx.analyze_project(
        "verify",
        verify_joint_drag(
            targets=intent.targets,
            baselines={
                target: ctx.run_handle(run) for target, run in baselines.items()
            },
            candidates={target: ctx.run_handle(run) for target, run in checks.items()},
            minimum_improvement=intent.minimum_improvement,
        ),
        inputs=(*baselines.values(), *checks.values()),
    )
    decision = ctx.published_analysis(verification).fact_as(
        "decision", JOINT_DRAG_DECISION_SCHEMA
    )
    if not decision.accepted:
        raise DragBranchCalibrationRejected(
            f"Joint DRAG verification rejected={decision.rejected}, "
            f"missing={decision.missing}"
        )
    ctx.publish_parameter_candidate(
        "publish",
        candidate_ref,
        proposal_id=proposal_id,
        verification=verification,
        decision_output_id="decision",
        branch=intent.destination,
        name=intent.result_revision_id,
        actor=intent.actor,
    )
