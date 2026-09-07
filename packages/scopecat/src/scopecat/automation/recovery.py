"""Explicit project adapters for a completed-run / failed-analysis recovery.

Adapters only construct typed intent. They must not acquire, activate configuration,
or perform any other effect while offering a plan. A recovery never reopens its source.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from scopecat.automation.definition import ProcedureDefinition
from scopecat.automation.models import (
    ProcedureDefinitionRef,
    ProcedureIntent,
    ProcedureRecoverySource,
    ProcedureRecoveryStep,
    ProcedureRun,
    ProcedureStepAttempt,
    RunOutputRef,
)
from scopecat.records.run import RunSnapshot
from scopecat.records.sample import SampleSelector


class ProcedureRecoveryPlan(BaseModel):
    """Frozen new invocation and the exact durable facts authorizing reuse."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    definition: ProcedureDefinitionRef
    intent: ProcedureIntent
    samples: tuple[SampleSelector, ...]
    recovery: ProcedureRecoverySource


@dataclass(frozen=True, slots=True)
class ProcedureRecoveryAvailability:
    """One explicitly supplied adapter's available plan or rejection reason."""

    adapter_id: str
    plan: ProcedureRecoveryPlan | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class ProcedureRecoveryAdapter[SourceIntent: BaseModel, TargetIntent: BaseModel]:
    """An audited project mapping, not a retry engine or definition migration.

    ``build_intent`` only maps source intent and the retained run into destination
    intent. Both exact definitions must remain installed in the existing registry.
    """

    id: str
    source: ProcedureDefinition[SourceIntent]
    destination: ProcedureDefinition[TargetIntent]
    run_step: str
    failed_analysis_step: str
    build_intent: Callable[[SourceIntent, RunOutputRef], TargetIntent]

    def source_ref(
        self, source: ProcedureRun, attempts: Sequence[ProcedureStepAttempt]
    ) -> ProcedureRecoverySource:
        if source.definition != self.source.ref:
            raise ValueError("recovery source definition does not match the adapter")
        run_step = _latest(attempts, self.run_step)
        failed = _latest(attempts, self.failed_analysis_step)
        if not isinstance(run_step.output, RunOutputRef):
            raise ValueError("recovery requires the declared completed run output")
        return ProcedureRecoverySource(
            adapter_id=self.id,
            procedure_run_id=source.procedure_run_id,
            revision=source.revision,
            definition=source.definition,
            run_step=_step_ref(run_step),
            failed_analysis_step=_step_ref(failed),
            retained_run=run_step.output,
        )

    def plan(
        self,
        source: ProcedureRun,
        attempts: Sequence[ProcedureStepAttempt],
        run: RunSnapshot,
    ) -> ProcedureRecoveryPlan:
        recovery = self.source_ref(source, attempts)
        validate_recovery_source(recovery, source, attempts, run)
        intent = self.build_intent(
            self.source.validate_intent(source.intent), recovery.retained_run
        )
        return ProcedureRecoveryPlan(
            definition=self.destination.ref,
            intent=self.destination.encode_intent(intent),
            samples=source.samples,
            recovery=recovery,
        )


def validate_recovery_source(
    recovery: ProcedureRecoverySource,
    source: ProcedureRun,
    attempts: Sequence[ProcedureStepAttempt],
    run: RunSnapshot,
) -> None:
    """Check all history, never hide an earlier unknown behind a later success."""
    if (
        source.procedure_run_id != recovery.procedure_run_id
        or source.revision != recovery.revision
        or source.definition != recovery.definition
    ):
        raise ValueError("recovery source identity or revision changed")
    if source.closure is None or source.closure.status != "failed":
        raise ValueError("recovery requires a closed failed procedure")
    completed = _latest(attempts, recovery.run_step.step_key)
    failed = _latest(attempts, recovery.failed_analysis_step.step_key)
    if (
        _step_ref(completed) != recovery.run_step
        or _step_ref(failed) != recovery.failed_analysis_step
    ):
        raise ValueError("recovery attempt identity or output contract changed")
    if (
        completed.operation != "run"
        or completed.state != "succeeded"
        or completed.output != recovery.retained_run
    ):
        raise ValueError("recovery requires the declared successful run step")
    if (
        failed.operation != "analysis"
        or failed.state != "failed"
        or failed.inputs != (recovery.retained_run,)
    ):
        raise ValueError("recovery requires the declared failed analysis of that run")
    for attempt in attempts:
        if attempt.procedure_run_id != source.procedure_run_id:
            raise ValueError("recovery attempt belongs to another procedure")
        if attempt.operation in {"config_activation", "config_publish"}:
            raise ValueError("recovery excludes attempted configuration acceptance")
        if attempt != failed and attempt.state != "succeeded":
            raise ValueError(
                "recovery excludes unknown, attention, or other failed effects"
            )
    if (
        run.run_id != recovery.retained_run.run_id
        or run.outcome is None
        or run.outcome.result != "succeeded"
    ):
        raise ValueError(
            "recovery requires a retained run with a known successful outcome"
        )


def _latest(attempts: Sequence[ProcedureStepAttempt], key: str) -> ProcedureStepAttempt:
    selected = [attempt for attempt in attempts if attempt.step_key == key]
    if not selected:
        raise ValueError(f"recovery source has no declared step: {key}")
    return max(selected, key=lambda attempt: attempt.attempt)


def _step_ref(attempt: ProcedureStepAttempt) -> ProcedureRecoveryStep:
    return ProcedureRecoveryStep(
        step_key=attempt.step_key,
        attempt=attempt.attempt,
        revision=attempt.revision,
        intent_hash=attempt.intent_hash,
    )
