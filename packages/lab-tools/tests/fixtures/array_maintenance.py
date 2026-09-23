"""Declared six-channel software plant; no physical device or lease claims."""

from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel, ConfigDict, field_validator

import scopecat as sc
from scopecat.analysis.calibration import CHECK_RESULT
from scopecat.api.calibration_tasks import task_call
from scopecat.api.lab import LabClient
from scopecat.api.procedures import LabProcedureContext
from scopecat.api.run import RunHandle
from scopecat.automation.calibration_tasks import (
    CalibrationTaskInputs,
    CalibrationTaskPlan,
    CalibrationTaskStage,
)
from scopecat.daemon.calibration_tasks import CalibrationTaskCall, CalibrationTaskView
from scopecat.daemon.views import ParameterResolution
from scopecat.kernel.frozen import thaw_json_value
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationCheckResult,
    CalibrationScope,
)
from scopecat.records.parameter_branch import ParameterBranch

TARGETS = tuple(f"q{i}" for i in range(6))
GROUPS = {"readout-a": TARGETS[:2], "readout-b": TARGETS[2:4], "readout-c": TARGETS[4:]}
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

    @field_validator("initial", mode="before")
    @classmethod
    def thaw(cls, value: object) -> object:
        return thaw_json_value(value)


@sc.analysis_step(id="maintenance.assess")
def assess(
    ctx: sc.AnalysisContext, *, target: str, scope: CalibrationScope
) -> sc.Analysis:
    data = ctx.measurements()
    result = ctx.result("Declared array check")
    if target in GROUPS:
        passed = cast("float", data["health"].require_values()[0]) == 1.0
    else:
        estimate = cast("float", data["setting"].require_values()[0]) - cast(
            "float", data["residual"].require_values()[0]
        )
        passed = 0.0 <= estimate <= 1.0
        if passed:
            result = result.propose(
                "offset", sc.parameter_update(Channel.offset, target, estimate)
            )
    return result.fact(
        "check", CalibrationCheckResult(scope, passed), schema=CHECK_RESULT
    )


@sc.procedure(id="maintenance.check", version="1", intent=CheckIntent)
def check(ctx: LabProcedureContext, intent: CheckIntent) -> None:
    invocation = (
        readout.build(intent.target, intent.target == intent.failed_group)
        if intent.target in GROUPS
        else measure.build(intent.target)
    )
    run = ctx.run(
        "measure",
        invocation,
        config=intent.initial.config,
        config_source=intent.initial.config_source,
    )
    ctx.analyze_run(
        "assess",
        run,
        assess(target=intent.target, scope=intent.calibration_check.scope),
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
        if error > 0.01 or error >= old:
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


@sc.procedure(id="maintenance.finish", version="1", intent=FinishIntent)
def finish(ctx: LabProcedureContext, intent: FinishIntent) -> None:
    inputs = intent.calibration_task
    assert inputs is not None
    composed = ctx.combine_parameter_candidates(
        "compose",
        tuple((inputs.analysis(t), "offset") for t in TARGETS),
        name="array-offsets",
    )
    candidate = ctx.published_analysis(composed).candidate_config("array-offsets")
    baselines = {t: inputs.measurement(t) for t in TARGETS}
    checks = {
        t: ctx.run(
            f"verify-{t}",
            measure.build(t, 0.04 if t == intent.drift_target else 0.0),
            config=candidate,
            inputs=(composed,),
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
) -> CalibrationTaskView:
    context = lab.resolve_context(
        parameters=destination.revision, setup=initial.config_source.setup
    ).context
    stages: list[CalibrationTaskStage] = []
    calls: dict[str, CalibrationTaskCall] = {}
    for target in (*GROUPS, *TARGETS):
        scope = CalibrationScope(
            "array.readout" if target in GROUPS else "array.fit",
            GROUPS.get(target, (target,)),
            "synthetic-array-v1",
            "health-or-bounded-linear-fit-v1",
        )
        declaration = CalibrationCheckRequest(
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
            check,
            CheckIntent(
                initial=initial,
                calibration_check=declaration,
                target=target,
                failed_group=failed_group,
            ),
        )
    return lab.calibration_tasks.create(
        "array-round",
        CalibrationTaskPlan(stages=tuple(stages)),
        calls=calls,
        finalization=task_call(
            finish,
            FinishIntent(
                destination=destination,
                drift_target=drift_target,
                revision_name="verified-array",
            ),
        ),
    )
