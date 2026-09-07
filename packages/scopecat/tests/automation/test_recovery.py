from datetime import UTC, datetime

import pytest

from scopecat.automation import (
    ProcedureClosure,
    ProcedureDefinitionRef,
    ProcedureRun,
    ProcedureStepAttempt,
    RunOutputRef,
    procedure_intent_hash,
)
from scopecat.automation.models import ProcedureRecoverySource, ProcedureRecoveryStep
from scopecat.automation.recovery import validate_recovery_source
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.run import RunSnapshot

NOW = datetime(2026, 1, 1, tzinfo=UTC)
REF = ProcedureDefinitionRef(id="source", version="1", fingerprint="sha256:" + "1" * 64)


def _facts():
    run_ref = RunOutputRef(run_id="completed-run")
    source = ProcedureRun(
        procedure_run_id="failed-procedure",
        request_key="original",
        definition=REF,
        intent={},
        intent_hash=procedure_intent_hash(REF, {}),
        revision=7,
        state="closed",
        created_at=NOW,
        updated_at=NOW,
        closure=ProcedureClosure(
            status="failed", closed_at=NOW, reason="analysis failed"
        ),
    )
    common = {
        "procedure_run_id": source.procedure_run_id,
        "attempt": 1,
        "revision": 2,
        "started_at": NOW,
        "updated_at": NOW,
        "finished_at": NOW,
    }
    acquired = ProcedureStepAttempt(
        **common,
        step_key="acquire",
        operation="run",
        intent_hash="sha256:" + "2" * 64,
        state="succeeded",
        output=run_ref,
    )
    failed = ProcedureStepAttempt(
        **common,
        step_key="fit",
        operation="analysis",
        intent_hash="sha256:" + "3" * 64,
        state="failed",
        failure_reason="bad analysis",
        inputs=(run_ref,),
    )

    def step_ref(step):
        return ProcedureRecoveryStep(
            step_key=step.step_key,
            attempt=step.attempt,
            revision=step.revision,
            intent_hash=step.intent_hash,
        )

    recovery = ProcedureRecoverySource(
        adapter_id="fit-recovery",
        procedure_run_id=source.procedure_run_id,
        revision=source.revision,
        definition=REF,
        run_step=step_ref(acquired),
        failed_analysis_step=step_ref(failed),
        retained_run=run_ref,
    )
    run = RunSnapshot(
        run_id=run_ref.run_id,
        created_at=NOW,
        config_content_hash="sha256:" + "4" * 64,
        outcome=RunOutcome(
            run_id=run_ref.run_id,
            result="succeeded",
            certainty="known",
            finished_at=NOW,
        ),
    )
    return recovery, source, (acquired, failed), run


def test_link_changes_only_recovery_hash_and_old_json_remains_readable():
    recovery, source, attempts, run = _facts()
    validate_recovery_source(recovery, source, attempts, run)
    old = source.model_dump(mode="json", exclude={"recovery"})
    assert ProcedureRun.model_validate(old) == source
    assert procedure_intent_hash(REF, {}) == source.intent_hash
    assert procedure_intent_hash(REF, {}, recovery=recovery) != source.intent_hash


@pytest.mark.parametrize("operation", ["config_activation", "config_publish"])
def test_any_acceptance_attempt_is_excluded(operation):
    recovery, source, attempts, run = _facts()
    forbidden = attempts[1].model_copy(
        update={"step_key": "accept", "operation": operation, "inputs": ()}
    )
    with pytest.raises(ValueError, match="attempted configuration"):
        validate_recovery_source(recovery, source, (*attempts, forbidden), run)


def test_earlier_unknown_cannot_be_hidden_by_later_success():
    recovery, source, attempts, run = _facts()
    earlier = attempts[0].model_copy(
        update={
            "state": "attention_required",
            "output": None,
            "attention_reason": "unknown write",
        }
    )
    later = attempts[0].model_copy(update={"attempt": 2})
    recovery = recovery.model_copy(
        update={"run_step": recovery.run_step.model_copy(update={"attempt": 2})}
    )
    with pytest.raises(ValueError, match="unknown, attention"):
        validate_recovery_source(recovery, source, (earlier, later, attempts[1]), run)


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"revision": 8}, "identity or revision"),
        (
            {
                "definition": REF.model_copy(
                    update={"fingerprint": "sha256:" + "5" * 64}
                )
            },
            "identity or revision",
        ),
        ({"retained_run": RunOutputRef(run_id="other-run")}, "successful run step"),
    ],
)
def test_changed_source_or_output_contract_rejected(change, reason):
    recovery, source, attempts, run = _facts()
    with pytest.raises(ValueError, match=reason):
        validate_recovery_source(
            recovery.model_copy(update=change), source, attempts, run
        )


def test_unsuccessful_retained_run_rejected():
    recovery, source, attempts, run = _facts()
    with pytest.raises(ValueError, match="known successful outcome"):
        validate_recovery_source(
            recovery, source, attempts, run.model_copy(update={"outcome": None})
        )
