from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.automation import (
    InterpretationRequest,
    ProcedureCloseCommand,
    ProcedureDefinitionRef,
    ProcedureRunAttentionCommand,
    ProcedureRunListQuery,
    ProcedureRunnableQuery,
    ProcedureStepAttemptListQuery,
    ProcedureStepAttentionCommand,
    ProcedureStepBeginCommand,
    ProcedureStepCompleteCommand,
    ProcedureStepFailCommand,
    ProcedureStepInputSubmitCommand,
    ProcedureStepInputWaitCommand,
    ProcedureSubmitCommand,
    ProcedureWorkerLeaseAcquireCommand,
    ProcedureWorkerLeaseHeartbeatCommand,
    ProcedureWorkerLeaseReleaseCommand,
    RunOutputRef,
)

from scopecat_server import BackendConflict, LocalDaemonRuntime
from scopecat_server.services.automation import AutomationService
from scopecat_server.storage.sqlite.automation import SQLiteAutomationStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository

_START = datetime(2026, 8, 18, 9, tzinfo=UTC)
_DEFINITION_HASH = "sha256:" + "1" * 64
_STEP_HASH = "sha256:" + "2" * 64
_OTHER_STEP_HASH = "sha256:" + "3" * 64


@dataclass(frozen=True)
class _ResonatorSelection:
    resonator: str


def _definition() -> ProcedureDefinitionRef:
    return ProcedureDefinitionRef(
        id="drag-calibration",
        version="1",
        fingerprint=_DEFINITION_HASH,
    )


def _service(
    tmp_path: Path,
) -> tuple[AutomationService, list[datetime]]:
    sqlite = SQLiteDatabase(tmp_path / "control.sqlite3")
    SQLiteProjectStore(sqlite, tmp_path / "objects").bootstrap()
    now = [_START]
    return (
        AutomationService(
            SQLiteAutomationStore(sqlite),
            runs=SQLiteRunRepository(sqlite, tmp_path / "objects"),
            lease_ttl=timedelta(seconds=30),
            clock=lambda: now[0],
        ),
        now,
    )


def _submit(service: AutomationService, *, key: str = "request-1"):
    return service.submit(
        ProcedureSubmitCommand(
            request_key=key,
            definition=_definition(),
            intent={"qubits": ["q0"]},
        )
    ).run


def test_procedure_submission_is_idempotent_and_bounded(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    first = _submit(service)

    assert _submit(service) == first
    with pytest.raises(BackendConflict, match="different intent"):
        service.submit(
            ProcedureSubmitCommand(
                request_key="request-1",
                definition=_definition(),
                intent={"qubits": ["q1"]},
            )
        )

    second = _submit(service, key="request-2")
    page = service.list(ProcedureRunListQuery(limit=1))
    assert page.items == (second,)
    assert page.next_cursor is not None
    assert service.list(
        ProcedureRunListQuery(limit=1, cursor=page.next_cursor)
    ).items == (first,)


def test_runnable_discovery_uses_exact_capabilities_and_server_lease_clock(
    tmp_path: Path,
) -> None:
    service, now = _service(tmp_path)
    first = _submit(service, key="first")
    second = _submit(service, key="second")
    query = ProcedureRunnableQuery(definitions=(_definition(),), limit=10)
    assert service.runnable(query).items == (first, second)
    assert (
        service.runnable(
            ProcedureRunnableQuery(
                definitions=(
                    _definition().model_copy(
                        update={"fingerprint": "sha256:" + "9" * 64}
                    ),
                )
            )
        ).items
        == ()
    )

    acquired = service.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=first.procedure_run_id,
            worker_id="worker-1",
            expected_run_revision=first.revision,
        )
    )
    assert service.runnable(query).items == (second,)
    now[0] = acquired.lease.expires_at.astimezone(timezone(timedelta(hours=8)))
    assert service.runnable(query).items == (acquired.run, second)


def test_interpretation_waits_without_a_lease_and_resumes_after_typed_input(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    submitted = _submit(service)
    acquired = service.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=submitted.procedure_run_id,
            worker_id="worker-1",
            expected_run_revision=submitted.revision,
        )
    )
    schema = AnalysisFactSchema(
        "lab.resonator-selection.v1",
        _ResonatorSelection,
    )
    request = InterpretationRequest(
        title="Choose the readout resonator",
        instructions="Return the selected resonator label.",
        schema_id=schema.id,
        schema_hash=schema.schema_hash,
        structure=schema.structure,
        response_template={"resonator": "replace after reviewing the trace"},
    )
    begun = service.begin_step(
        ProcedureStepBeginCommand(
            procedure_run_id=submitted.procedure_run_id,
            lease_token=acquired.lease.lease_token,
            expected_run_revision=acquired.run.revision,
            step_key="select-resonator",
            operation="interpretation",
            intent_hash=request.request_hash,
        )
    )
    waiting = service.wait_step_input(
        ProcedureStepInputWaitCommand(
            procedure_run_id=submitted.procedure_run_id,
            lease_token=acquired.lease.lease_token,
            expected_run_revision=begun.run.revision,
            step_key=begun.step.step_key,
            attempt=begun.step.attempt,
            expected_step_revision=begun.step.revision,
            request=request,
        )
    )

    assert waiting.run.state == "waiting_for_input"
    durable_request = waiting.step.interpretation_request
    assert durable_request is not None
    assert durable_request == request
    assert durable_request.response_template == {
        "resonator": "replace after reviewing the trace"
    }
    assert (
        service.runnable(ProcedureRunnableQuery(definitions=(_definition(),))).items
        == ()
    )

    with pytest.raises(BackendConflict, match="does not match its schema"):
        service.submit_step_input(
            ProcedureStepInputSubmitCommand(
                procedure_run_id=submitted.procedure_run_id,
                expected_run_revision=waiting.run.revision,
                step_key=waiting.step.step_key,
                attempt=waiting.step.attempt,
                expected_step_revision=waiting.step.revision,
                request_hash=request.request_hash,
                actor="operator@example.test",
                actor_kind="human",
                value={"resonator": 2},
            )
        )

    answer_command = ProcedureStepInputSubmitCommand(
        procedure_run_id=submitted.procedure_run_id,
        expected_run_revision=waiting.run.revision,
        step_key=waiting.step.step_key,
        attempt=waiting.step.attempt,
        expected_step_revision=waiting.step.revision,
        request_hash=request.request_hash,
        actor="operator@example.test",
        actor_kind="human",
        value={"resonator": "r2"},
        note="clearest isolated dip",
    )
    answered = service.submit_step_input(answer_command)

    assert answered.run.state == "ready"
    assert answered.output.response.value == {"resonator": "r2"}
    assert service.runnable(
        ProcedureRunnableQuery(definitions=(_definition(),))
    ).items == (answered.run,)
    assert service.submit_step_input(answer_command) == answered
    resumed = service.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=submitted.procedure_run_id,
            worker_id="worker-2",
            expected_run_revision=answered.run.revision,
        )
    )
    closed = service.close(
        ProcedureCloseCommand(
            procedure_run_id=submitted.procedure_run_id,
            lease_token=resumed.lease.lease_token,
            expected_run_revision=resumed.run.revision,
            status="succeeded",
        )
    ).run

    assert service.submit_step_input(answer_command).run == closed


def test_runtime_reopens_durable_procedure_state(tmp_path: Path) -> None:
    command = ProcedureSubmitCommand(
        request_key="restart-request",
        definition=_definition(),
        intent={"qubits": ["q0", "q1"]},
    )
    with LocalDaemonRuntime(tmp_path) as runtime:
        admitted = runtime.application.automation.submit(command).run

    with LocalDaemonRuntime(tmp_path) as reopened:
        assert (
            reopened.application.automation.get(admitted.procedure_run_id) == admitted
        )


def test_worker_completes_exact_step_and_closes_procedure(tmp_path: Path) -> None:
    service, now = _service(tmp_path)
    admitted = _submit(service)
    acquired = service.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=admitted.procedure_run_id,
            worker_id="worker-1",
            expected_run_revision=admitted.revision,
        )
    )
    assert acquired.run.revision == 2

    now[0] += timedelta(seconds=5)
    heartbeat = service.heartbeat_lease(
        ProcedureWorkerLeaseHeartbeatCommand(
            procedure_run_id=admitted.procedure_run_id,
            lease_token=acquired.lease.lease_token,
        )
    )
    assert heartbeat.run.revision == acquired.run.revision
    assert heartbeat.lease.expires_at == now[0] + timedelta(seconds=30)

    begin_command = ProcedureStepBeginCommand(
        procedure_run_id=admitted.procedure_run_id,
        lease_token=acquired.lease.lease_token,
        expected_run_revision=acquired.run.revision,
        step_key="baseline",
        operation="run",
        intent_hash=_STEP_HASH,
    )
    begun = service.begin_step(begin_command)
    assert begun.run.revision == 3
    assert begun.step.revision == 1
    assert service.begin_step(begin_command) == begun

    with pytest.raises(BackendConflict, match="different intent"):
        service.begin_step(
            begin_command.model_copy(update={"intent_hash": _OTHER_STEP_HASH})
        )

    output = RunOutputRef(run_id="run-baseline")
    complete_command = ProcedureStepCompleteCommand(
        procedure_run_id=admitted.procedure_run_id,
        lease_token=acquired.lease.lease_token,
        expected_run_revision=begun.run.revision,
        step_key=begun.step.step_key,
        attempt=begun.step.attempt,
        expected_step_revision=begun.step.revision,
        output=output,
    )
    completed = service.complete_step(complete_command)
    assert completed.run.revision == 4
    assert completed.step.state == "succeeded"
    assert completed.step.output == output
    assert service.complete_step(complete_command) == completed

    attempts = service.step_attempts(
        admitted.procedure_run_id,
        ProcedureStepAttemptListQuery(limit=1),
    )
    assert attempts.items == (completed.step,)

    closed = service.close(
        ProcedureCloseCommand(
            procedure_run_id=admitted.procedure_run_id,
            lease_token=acquired.lease.lease_token,
            expected_run_revision=completed.run.revision,
            status="succeeded",
        )
    ).run
    assert closed.state == "closed"
    assert closed.revision == 5


def test_expired_lease_takeover_reuses_running_attempt_and_fences_old_worker(
    tmp_path: Path,
) -> None:
    service, now = _service(tmp_path)
    admitted = _submit(service)
    first = service.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=admitted.procedure_run_id,
            worker_id="worker-1",
            expected_run_revision=1,
        )
    )
    begun = service.begin_step(
        ProcedureStepBeginCommand(
            procedure_run_id=admitted.procedure_run_id,
            lease_token=first.lease.lease_token,
            expected_run_revision=first.run.revision,
            step_key="baseline",
            operation="run",
            intent_hash=_STEP_HASH,
        )
    )

    now[0] += timedelta(seconds=31)
    takeover = service.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=admitted.procedure_run_id,
            worker_id="worker-2",
            expected_run_revision=begun.run.revision,
        )
    )
    assert takeover.run.revision == begun.run.revision + 1
    replay = service.begin_step(
        ProcedureStepBeginCommand(
            procedure_run_id=admitted.procedure_run_id,
            lease_token=takeover.lease.lease_token,
            expected_run_revision=takeover.run.revision,
            step_key="baseline",
            operation="run",
            intent_hash=_STEP_HASH,
        )
    )
    assert replay.step == begun.step

    with pytest.raises(BackendConflict, match="stale"):
        service.fail_step(
            ProcedureStepFailCommand(
                procedure_run_id=admitted.procedure_run_id,
                lease_token=first.lease.lease_token,
                expected_run_revision=takeover.run.revision,
                step_key="baseline",
                attempt=1,
                expected_step_revision=1,
                reason="worker lost",
            )
        )

    failed = service.fail_step(
        ProcedureStepFailCommand(
            procedure_run_id=admitted.procedure_run_id,
            lease_token=takeover.lease.lease_token,
            expected_run_revision=takeover.run.revision,
            step_key="baseline",
            attempt=1,
            expected_step_revision=1,
            reason="known failure",
        )
    )
    assert failed.run.state == "leased"
    assert failed.step.state == "failed"
    with pytest.raises(BackendConflict, match="terminal for this procedure run"):
        service.begin_step(
            ProcedureStepBeginCommand(
                procedure_run_id=admitted.procedure_run_id,
                lease_token=takeover.lease.lease_token,
                expected_run_revision=failed.run.revision,
                step_key="baseline",
                operation="run",
                intent_hash=_STEP_HASH,
            )
        )


def test_release_and_attention_transitions_invalidate_lease(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)

    released_run = _submit(service, key="release")
    released_lease = service.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=released_run.procedure_run_id,
            worker_id="worker-release",
            expected_run_revision=1,
        )
    )
    released = service.release_lease(
        ProcedureWorkerLeaseReleaseCommand(
            procedure_run_id=released_run.procedure_run_id,
            lease_token=released_lease.lease.lease_token,
            expected_run_revision=released_lease.run.revision,
        )
    ).run
    assert released.state == "ready"
    with pytest.raises(BackendConflict, match="leased"):
        service.heartbeat_lease(
            ProcedureWorkerLeaseHeartbeatCommand(
                procedure_run_id=released.procedure_run_id,
                lease_token=released_lease.lease.lease_token,
            )
        )

    run_attention = _submit(service, key="run-attention")
    run_lease = service.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=run_attention.procedure_run_id,
            worker_id="worker-attention",
            expected_run_revision=1,
        )
    )
    attention = service.require_run_attention(
        ProcedureRunAttentionCommand(
            procedure_run_id=run_attention.procedure_run_id,
            lease_token=run_lease.lease.lease_token,
            expected_run_revision=run_lease.run.revision,
            reason="definition cannot be loaded",
        )
    ).run
    assert attention.state == "attention_required"
    with pytest.raises(BackendConflict, match="leased"):
        service.heartbeat_lease(
            ProcedureWorkerLeaseHeartbeatCommand(
                procedure_run_id=attention.procedure_run_id,
                lease_token=run_lease.lease.lease_token,
            )
        )

    step_attention = _submit(service, key="step-attention")
    step_lease = service.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=step_attention.procedure_run_id,
            worker_id="worker-step-attention",
            expected_run_revision=1,
        )
    )
    begun = service.begin_step(
        ProcedureStepBeginCommand(
            procedure_run_id=step_attention.procedure_run_id,
            lease_token=step_lease.lease.lease_token,
            expected_run_revision=step_lease.run.revision,
            step_key="baseline",
            operation="run",
            intent_hash=_STEP_HASH,
        )
    )
    quarantined = service.require_step_attention(
        ProcedureStepAttentionCommand(
            procedure_run_id=step_attention.procedure_run_id,
            lease_token=step_lease.lease.lease_token,
            expected_run_revision=begun.run.revision,
            step_key=begun.step.step_key,
            attempt=begun.step.attempt,
            expected_step_revision=begun.step.revision,
            reason="indeterminate external effect",
        )
    )
    assert quarantined.run.state == "attention_required"
    assert quarantined.step.state == "attention_required"


def test_analysis_verifies_exact_durable_judgment_and_survives_restart(
    tmp_path: Path,
) -> None:
    from scopecat.daemon.wire import (
        AnalysisSaveCommand,
        InterpretationAnalysisInputPayload,
    )

    with LocalDaemonRuntime(tmp_path) as runtime:
        service = runtime.application.automation
        submitted = _submit(service)
        acquired = service.acquire_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=submitted.procedure_run_id,
                worker_id="worker-1",
                expected_run_revision=submitted.revision,
            )
        )
        schema = AnalysisFactSchema(
            "lab.resonator-selection.v1",
            _ResonatorSelection,
        )
        request = InterpretationRequest(
            title="Choose the readout resonator",
            instructions="Return the selected resonator label.",
            schema_id=schema.id,
            schema_hash=schema.schema_hash,
            structure=schema.structure,
            response_template={"resonator": "replace after reviewing the trace"},
        )
        begun = service.begin_step(
            ProcedureStepBeginCommand(
                procedure_run_id=submitted.procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=acquired.run.revision,
                step_key="select-resonator",
                operation="interpretation",
                intent_hash=request.request_hash,
            )
        )
        waiting = service.wait_step_input(
            ProcedureStepInputWaitCommand(
                procedure_run_id=submitted.procedure_run_id,
                lease_token=acquired.lease.lease_token,
                expected_run_revision=begun.run.revision,
                step_key=begun.step.step_key,
                attempt=begun.step.attempt,
                expected_step_revision=begun.step.revision,
                request=request,
            )
        )

        answer_command = ProcedureStepInputSubmitCommand(
            procedure_run_id=submitted.procedure_run_id,
            expected_run_revision=waiting.run.revision,
            step_key=waiting.step.step_key,
            attempt=waiting.step.attempt,
            expected_step_revision=waiting.step.revision,
            request_hash=request.request_hash,
            actor="operator@example.test",
            actor_kind="human",
            value={"resonator": "r2"},
            note="clearest isolated dip",
        )
        answered = service.submit_step_input(answer_command)

        source = answered.output.analysis_reference
        input_ref = InterpretationAnalysisInputPayload(
            id="selection",
            target=source.step_key,
            content_hash=source.response_hash,
            codec="scopecat.interpretation-response.v1",
            role="decision",
            source=source,
        )
        command = AnalysisSaveCommand(
            title="Decision provenance",
            analysis_key="decision-proof",
            inputs=(input_ref,),
        )
        saved = runtime.application.analyses.save(command)
        assert saved.inputs == (input_ref,)
        for field, value in (
            ("request_hash", _OTHER_STEP_HASH),
            ("response_hash", _OTHER_STEP_HASH),
            ("procedure_run_id", "missing-procedure"),
            ("step_key", "missing-step"),
        ):
            changed = source.model_copy(update={field: value})
            forged = input_ref.model_copy(
                update={
                    "source": changed,
                    "target": changed.step_key,
                    "content_hash": changed.response_hash,
                }
            )
            with pytest.raises(BackendConflict, match="successful judgment"):
                runtime.application.analyses.save(
                    command.model_copy(
                        update={"inputs": (forged,), "analysis_key": f"forged-{field}"}
                    )
                )
    with LocalDaemonRuntime(tmp_path) as reopened:
        assert reopened.application.analyses.save(command) == saved
