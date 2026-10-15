"""Declared six-channel software plant; no physical device or lease claims."""

import os
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import cast

from pydantic import BaseModel, ConfigDict, field_validator

import scopecat as sc
from scopecat.analysis.calibration import CHECK_RESULT
from scopecat.api.calibration_tasks import task_call
from scopecat.api.lab import LabClient
from scopecat.api.procedures import LabProcedureContext
from scopecat.api.run import RunHandle
from scopecat.automation import AnalysisPublicationOutputRef, RunOutputRef
from scopecat.automation.calibration_tasks import (
    CalibrationTaskInputs,
    CalibrationTaskPlan,
    CalibrationTaskStage,
)
from scopecat.daemon.calibration_tasks import (
    CalibrationRepairBudget,
    CalibrationStageRepair,
    CalibrationTaskCall,
    CalibrationTaskView,
)
from scopecat.daemon.views import ParameterResolution
from scopecat.kernel.frozen import thaw_json_value
from scopecat.records.analysis import RunAnalysisSubject
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationCheckResult,
    CalibrationScope,
)
from scopecat.records.candidate_input import AnalysisCandidateRunConfigSource
from scopecat.records.parameter_branch import ParameterBranch

TARGETS = tuple(f"q{i}" for i in range(int(os.environ["SCOPECAT_TEST_ARRAY_SIZE"])))
GROUPS = {
    f"readout-{chr(ord('a') + i // 2)}": TARGETS[i : i + 2]
    for i in range(0, len(TARGETS), 2)
}
PEERS = dict(zip(TARGETS, (*TARGETS[1:], TARGETS[0]), strict=True))


class Channel(sc.ParameterModel, table="channels"):
    id: sc.Param[str] = sc.param(key=True)
    offset: sc.Param[float] = sc.param()


@sc.compute
def response(own: float, peer: float, target: str, drift: float) -> float:
    # Coupling appears only once the aggregate candidate changes adjacent rows.
    return (
        own - (0.2 + 0.01 * int(target[1:])) + 0.1 * (own - 0.1) * (peer - 0.1) + drift
    )


@sc.experiment(id="maintenance.measure")
def measure(
    _: sc.ExperimentContext, target: str, drift: float = 0.0
) -> dict[str, object]:
    own = sc.parameter_ref(Channel.offset, target)
    peer = sc.parameter_ref(Channel.offset, PEERS[target])
    return {"setting": own, "residual": response(own, peer, target, drift)}


@sc.compute
def readout_health(group: str, failed: bool) -> float:
    if failed:
        raise RuntimeError(f"synthetic readout outage: {group}")
    return 1.0


@sc.experiment(id="maintenance.readout")
def readout(
    _: sc.ExperimentContext, group: str, failed: bool = False
) -> dict[str, object]:
    return {"health": readout_health(group, failed)}


class CheckIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    initial: ParameterResolution
    calibration_check: CalibrationCheckRequest
    target: str
    failed_group: str | None = None
    reject_fit: bool = False
    proposal_shift: float = 0.0

    @field_validator("initial", mode="before")
    @classmethod
    def thaw(cls, value: object) -> object:
        return thaw_json_value(value)


@sc.analysis_step(id="maintenance.assess")
def assess(
    ctx: sc.AnalysisContext,
    *,
    target: str,
    scope: CalibrationScope,
    reject_fit: bool = False,
    proposal_shift: float = 0.0,
) -> sc.Analysis:
    data = ctx.measurements()
    result = ctx.result("Declared array check")
    if target in GROUPS:
        passed = cast("float", data["health"].require_values()[0]) == 1.0
    else:
        estimate = cast("float", data["setting"].require_values()[0]) - cast(
            "float", data["residual"].require_values()[0]
        )
        passed = 0.0 <= estimate <= 1.0 and not reject_fit
        if passed:
            result = result.propose(
                "offset",
                sc.parameter_update(Channel.offset, target, estimate + proposal_shift),
            )
    return result.fact(
        "check", CalibrationCheckResult(scope, passed), schema=CHECK_RESULT
    )


@sc.procedure(id="maintenance.check", version="1", intent=CheckIntent)
def check(ctx: LabProcedureContext, intent: CheckIntent) -> None:
    run = acquire(ctx, intent)
    ctx.analyze_run(
        "assess",
        run,
        assess(
            target=intent.target,
            scope=intent.calibration_check.scope,
            reject_fit=intent.reject_fit,
            proposal_shift=intent.proposal_shift,
        ),
    )


def acquire(ctx: LabProcedureContext, intent: CheckIntent) -> RunOutputRef:
    invocation = (
        readout.build(intent.target, intent.target == intent.failed_group)
        if intent.target in GROUPS
        else measure.build(intent.target)
    )
    parameters = intent.calibration_check.context.parameters
    if isinstance(parameters, AnalysisCandidateRunConfigSource):
        candidate = ctx.published_analysis(
            AnalysisPublicationOutputRef(
                subject=RunAnalysisSubject(run_id=parameters.source_run_id),
                analysis_record_id=parameters.analysis_record_id,
            )
        ).candidate_config(parameters.proposal_id)
        return ctx.run("measure", invocation, config=candidate)
    return ctx.run(
        "measure",
        invocation,
        config=intent.initial.config,
        config_source=intent.initial.config_source,
    )


@sc.analysis_step(id="maintenance.probe-result")
def probe_result(ctx: sc.AnalysisContext, *, scope: CalibrationScope) -> sc.Analysis:
    residual = cast("float", ctx.measurements()["residual"].require_values()[0])
    return ctx.result("Residual check").fact(
        "check",
        CalibrationCheckResult(scope, abs(residual) <= 0.01),
        schema=CHECK_RESULT,
    )


@sc.procedure(id="maintenance.probe", version="1", intent=CheckIntent)
def probe(ctx: LabProcedureContext, intent: CheckIntent) -> None:
    ctx.analyze_run(
        "assess",
        acquire(ctx, intent),
        probe_result(scope=intent.calibration_check.scope),
    )


@dataclass(frozen=True)
class Decision:
    accepted: bool
    checked: tuple[str, ...]
    rejected: tuple[str, ...]


DECISION = sc.AnalysisFactSchema("maintenance.complete-array.v1", Decision)


@sc.analysis_step(id="maintenance.verify")
def verify(
    ctx: sc.AnalysisContext,
    *,
    baselines: dict[str, RunHandle],
    checks: dict[str, RunHandle],
) -> sc.Analysis:
    rejected: list[str] = []
    residuals: dict[str, float] = {}
    for target in TARGETS:
        before = ctx.measurements(baselines[target], id=f"before-{target}")
        after = ctx.measurements(checks[target], id=f"after-{target}")
        old = abs(cast("float", before["residual"].require_values()[0]))
        error = abs(cast("float", after["residual"].require_values()[0]))
        if error > 0.01 or (old > 0.01 and error >= old):
            rejected.append(target)
        residuals[target] = error
    result = ctx.result("Whole-array verification")
    for target, error in residuals.items():
        result = result.fact(f"residual-{target}", error)
    return result.fact(
        "decision", Decision(not rejected, TARGETS, tuple(rejected)), schema=DECISION
    )


class FinishIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    calibration_task: CalibrationTaskInputs | None = None
    destination: ParameterBranch
    drift_target: str | None = None
    revision_name: str
    repair_mode: bool = False


@sc.procedure(id="maintenance.finish", version="1", intent=FinishIntent)
def finish(ctx: LabProcedureContext, intent: FinishIntent) -> None:
    inputs = intent.calibration_task
    assert inputs is not None
    sources = (
        inputs.repairs if intent.repair_mode else {t: inputs.checks[t] for t in TARGETS}
    )
    composed = (
        ctx.combine_parameter_candidates(
            "compose",
            tuple(
                (inputs.analysis(t, from_repair=intent.repair_mode), "offset")
                for t in sources
            ),
            name="array-offsets",
        )
        if sources
        else None
    )
    baseline = ctx.run_handle(inputs.measurement(TARGETS[0]))
    candidate = (
        ctx.published_analysis(composed).candidate_config("array-offsets")
        if composed
        else baseline.config
    )

    baselines = {
        t: inputs.measurement(t, from_repair=t in inputs.repairs) for t in TARGETS
    }
    checks = {
        t: ctx.run(
            f"verify-{t}",
            measure.build(t, 0.04 if t == intent.drift_target else 0.0),
            config=candidate,
            config_source=None if composed else baseline.snapshot.config_source,
            inputs=(composed,) if composed else (),
        )
        for t in TARGETS
    }
    verified = ctx.analyze_project(
        "verify",
        verify(
            baselines={t: ctx.run_handle(r) for t, r in baselines.items()},
            checks={t: ctx.run_handle(r) for t, r in checks.items()},
        ),
        inputs=(*baselines.values(), *checks.values()),
    )
    decision = ctx.published_analysis(verified).fact_as("decision", DECISION)
    if not decision.accepted:
        raise ValueError(f"array verification rejected: {decision.rejected}")
    if composed is None:
        return
    ctx.publish_parameter_candidate(
        "publish",
        composed,
        proposal_id="array-offsets",
        verification=verified,
        decision_output_id="decision",
        branch=intent.destination,
        name=intent.revision_name,
        actor="maintainer",
    )


def create_task(
    lab: LabClient,
    initial: ParameterResolution,
    destination: ParameterBranch,
    *,
    failed_group: str | None = None,
    drift_target: str | None = None,
    repair_mode: bool = False,
    max_repairs: int = 6,
    reject_fit: str | None = None,
    bad_candidate: str | None = None,
) -> CalibrationTaskView:
    context = lab.resolve_context(
        parameters=destination.revision, setup=initial.config_source.setup
    ).context
    stages: list[CalibrationTaskStage] = []
    calls: dict[str, CalibrationTaskCall] = {}
    repairs: dict[str, CalibrationStageRepair] = {}
    for target in (*GROUPS, *TARGETS):
        scope = CalibrationScope(
            "array.readout"
            if target in GROUPS
            else "array.residual"
            if repair_mode
            else "array.fit",
            GROUPS.get(target, (target,)),
            "synthetic-array-v1",
            "health-or-bounded-linear-fit-v1",
        )
        declaration = CalibrationCheckRequest(
            setup=initial.config_source.setup,
            scope=scope,
            context=context,
            measurement_step="measure",
            analysis_step="assess",
        )
        dependencies = (
            ()
            if target in GROUPS
            else (next(g for g, members in GROUPS.items() if target in members),)
        )
        stages.append(
            CalibrationTaskStage(id=target, check=declaration, depends_on=dependencies)
        )
        calls[target] = task_call(
            probe if repair_mode and target in TARGETS else check,
            CheckIntent(
                initial=initial,
                calibration_check=declaration,
                target=target,
                failed_group=failed_group,
            ),
        )
        if repair_mode and target in TARGETS:
            repairs[target] = CalibrationStageRepair(
                call=task_call(
                    check,
                    CheckIntent(
                        initial=initial,
                        calibration_check=declaration.model_copy(
                            update={
                                "scope": replace(
                                    declaration.scope, capability="array.fit"
                                )
                            }
                        ),
                        target=target,
                        reject_fit=target == reject_fit,
                        proposal_shift=0.05 if target == bad_candidate else 0.0,
                    ),
                ),
                proposal_id="offset",
            )
    return lab.calibration_tasks.create(
        "array-round",
        CalibrationTaskPlan(stages=tuple(stages)),
        calls=calls,
        repairs=repairs,
        repair_budget=CalibrationRepairBudget(
            max_repairs=max_repairs, elapsed=timedelta(minutes=10)
        )
        if repair_mode
        else None,
        finalization=task_call(
            finish,
            FinishIntent(
                destination=destination,
                drift_target=drift_target,
                revision_name="verified-array",
                repair_mode=repair_mode,
            ),
        ),
    )
