"""A physical-line confirmation cannot silently authorize first execution on reopen."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.api.lab import LabClient
from scopecat.api.procedures import LabProcedureContext
from scopecat.automation import (
    InterpretationOutputRef,
    InterpretationRequest,
    ProcedureRegistry,
    RunOutputRef,
    procedure,
)
from scopecat.automation.models import ProcedureDefinitionRef
from scopecat.automation.wire import (
    ProcedureStepBeginCommand,
    ProcedureStepInputSubmitCommand,
    ProcedureStepInputWaitCommand,
    ProcedureSubmitCommand,
    ProcedureWorkerLeaseAcquireCommand,
    procedure_step_operation_id,
)
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.control.models import RunPlanSummary
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.wire import (
    AttentionResolutionCommand,
    ExecutorStartRequest,
    RunSubmission,
    TerminalRunCommitCommand,
)
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.manual_line import (
    ManualLineConfirmationRef,
    ManualLineIntent,
    ManualLineReconfirmation,
)
from scopecat.records.plan_ref import ProcedureChildSubmission
from scopecat.records.run_request import RunRequest
from scopecat.records.setup import SetupRevisionRef
from scopecat_testkit.server.instruments import signal_endpoint
from scopecat_testkit.workflow_fixtures import load_config, load_invocation

from scopecat_server import LocalDaemonRuntime
from scopecat_server.errors import BackendConflict


@dataclass(frozen=True)
class Wiring:
    confirmed: bool


class LineIntent(BaseModel):
    model_config = ConfigDict(frozen=True)
    config: ConfigProfileSnapshot
    setup: SetupRevisionRef


@procedure(id="test.manual-line-sdk", version="1", intent=LineIntent)
def line_workflow(context: LabProcedureContext, intent: LineIntent) -> None:
    context.run(
        "measure",
        load_invocation(),
        config=intent.config,
        setup=intent.setup,
        manual_line_id="stable-line-1",
        name="Editable line label",
    )


def daemon_client(transport: TestClient) -> DaemonClient:
    def send(request: httpx2.Request) -> httpx2.Response:
        response = transport.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx2.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    return DaemonClient("http://testserver", transport=httpx2.MockTransport(send))


def test_real_sdk_worker_reopen_attention_reconfirm_and_retry_executes_same_child_once(
    tmp_path: Path,
) -> None:
    registry = ProcedureRegistry((line_workflow,))
    with (
        LocalDaemonRuntime(
            tmp_path,
            bootstrap_config=load_config(),
            instrument_endpoint=signal_endpoint(),
        ) as first,
        TestClient(first.app()) as transport,
        daemon_client(transport) as client,
        LabClient(client, procedures=registry) as lab,
    ):
        task = lab.procedures.submit(
            line_workflow,
            LineIntent(
                config=load_config(),
                setup=first.application.setup.resolve("initial").ref,
            ),
            request_key="sdk-line",
        )
        task.resume()
        assert task.snapshot.state == "waiting_for_input"
        task.respond(
            "measure/wiring",
            Wiring(True),
            schema=AnalysisFactSchema("scopecat.manual-line-wiring.v1", Wiring),
            actor="first-operator",
        )
        identity = task.id
        original_answer = task.output("measure/wiring")
        assert client.list_runs().items == ()
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=signal_endpoint()) as second,
        TestClient(second.app()) as transport,
        daemon_client(transport) as client,
        LabClient(client, procedures=registry) as lab,
    ):
        task = lab.procedures.get(identity)
        task.resume()
        assert task.snapshot.state == "attention_required"
        [child] = client.list_runs().items
        run_id = child.run_id
        assert client.get_run_execution_segments(run_id).items == ()
        assert task.output("measure/wiring") == original_answer
        frozen = client.get_run(run_id).snapshot
        client.resolve_attention(
            run_id, reconfirmation(second, run_id, "sdk-reconfirm")
        )
        task.retry_attention()
        assert task.snapshot.closure is not None
        assert task.snapshot.closure.status == "succeeded"
        assert task.output("measure") == RunOutputRef(run_id=run_id)
        assert len(client.list_runs().items) == 1
        assert len(client.get_run_execution_segments(run_id).items) == 1
        completed = client.get_run(run_id).snapshot
        assert completed.config_content_hash == frozen.config_content_hash
        assert completed.execution_setup == frozen.execution_setup
        assert completed.outcome is not None and completed.outcome.result == "succeeded"


def measurement(runtime: LocalDaemonRuntime) -> RunSubmission:
    config = load_config()
    return RunSubmission(
        submission_id="frozen-preview",
        execution_setup=runtime.application.setup.resolve("initial").ref,
        scientific_binding=bind_scientific_evidence(
            catalog_id="test", config=config, samples=(), sample_revisions={}
        ),
        config=config,
        request=RunRequest(experiment_id="fake-line"),
        plan=RunPlanSummary(
            experiment_id="fake-line",
            experiment_kind="scratch",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=0,
            initial_point_count=0,
            point_limit=0,
        ),
    )


def confirm(
    runtime: LocalDaemonRuntime,
) -> tuple[InterpretationOutputRef, ProcedureStepInputSubmitCommand]:
    automation = runtime.application.automation
    parent = automation.submit(
        ProcedureSubmitCommand(
            request_key="line-list",
            intent={"line_id": "line-1"},
            definition=ProcedureDefinitionRef(
                id="manual-lines", version="1", fingerprint="sha256:" + "a" * 64
            ),
        )
    ).run
    acquired = automation.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=parent.procedure_run_id,
            expected_run_revision=parent.revision,
            worker_id="line-worker",
        )
    )
    schema = AnalysisFactSchema("line-wiring.v1", Wiring)
    request = InterpretationRequest(
        title="Confirm line 1",
        instructions="Inspect the current wiring",
        schema_id=schema.id,
        schema_hash=schema.schema_hash,
        structure=schema.structure,
        manual_line=ManualLineIntent(
            line_id="line-1",
            step_key="measure",
            intent_hash=f"sha256:{measurement(runtime).intent_content_hash}",
        ),
    )
    begun = automation.begin_step(
        ProcedureStepBeginCommand(
            procedure_run_id=parent.procedure_run_id,
            lease_token=acquired.lease.lease_token,
            expected_run_revision=acquired.run.revision,
            step_key="wiring",
            operation="interpretation",
            intent_hash=request.request_hash,
        )
    )
    waiting = automation.wait_step_input(
        ProcedureStepInputWaitCommand(
            procedure_run_id=parent.procedure_run_id,
            lease_token=acquired.lease.lease_token,
            expected_run_revision=begun.run.revision,
            step_key="wiring",
            attempt=begun.step.attempt,
            expected_step_revision=begun.step.revision,
            request=request,
        )
    )
    command = ProcedureStepInputSubmitCommand(
        procedure_run_id=parent.procedure_run_id,
        expected_run_revision=waiting.run.revision,
        step_key="wiring",
        attempt=waiting.step.attempt,
        expected_step_revision=waiting.step.revision,
        request_hash=request.request_hash,
        actor="first-operator",
        actor_kind="human",
        value={"confirmed": True},
    )
    with pytest.raises(BackendConflict, match="human confirmation"):
        automation.submit_step_input(
            command.model_copy(update={"actor_kind": "service"})
        )
    with pytest.raises(BackendConflict, match="explicitly confirm"):
        automation.submit_step_input(
            command.model_copy(update={"value": {"confirmed": False}})
        )
    return automation.submit_step_input(command).output, command


def admit(runtime: LocalDaemonRuntime, confirmation: InterpretationOutputRef) -> str:
    automation = runtime.application.automation
    parent = automation.get(confirmation.procedure_run_id)
    lease = automation.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=parent.procedure_run_id,
            expected_run_revision=parent.revision,
            worker_id="line-worker",
        )
    )
    submission = measurement(runtime).model_copy(
        update={
            "submission_id": procedure_step_operation_id(
                parent.procedure_run_id, "measure"
            ),
            "procedure_child": ProcedureChildSubmission(
                procedure_run_id=parent.procedure_run_id,
                step_key="measure",
                manual_line_confirmation=ManualLineConfirmationRef(
                    procedure_run_id=parent.procedure_run_id,
                    step_key=confirmation.step_key,
                    request_hash=confirmation.request_hash,
                ),
            ),
        }
    )
    automation.begin_step(
        ProcedureStepBeginCommand(
            procedure_run_id=parent.procedure_run_id,
            lease_token=lease.lease.lease_token,
            expected_run_revision=lease.run.revision,
            step_key="measure",
            operation="run",
            intent_hash=f"sha256:{submission.intent_content_hash}",
            inputs=(confirmation,),
        )
    )
    return runtime.application.submit_run(submission).run_id


def reconfirmation(
    runtime: LocalDaemonRuntime, run_id: str, key: str
) -> AttentionResolutionCommand:
    admitted = runtime.application.runs.get_run(run_id).control.admission
    assert admitted.manual_line_confirmation is not None
    return AttentionResolutionCommand(
        disposition="continue",
        run_contract_fingerprint=admitted.run_contract_fingerprint,
        manual_line_reconfirmation=ManualLineReconfirmation(
            request_key=key,
            actor="second-operator",
            original=admitted.manual_line_confirmation,
        ),
    )


@pytest.mark.parametrize("stage", ["confirmed", "queued"])
def test_reopen_requires_fresh_confirmation_of_same_run_and_retries_do_not_refresh(
    tmp_path: Path,
    stage: str,
) -> None:
    with LocalDaemonRuntime(
        tmp_path, bootstrap_config=load_config(), instrument_endpoint=signal_endpoint()
    ) as first:
        output, command = confirm(first)
        run_id = admit(first, output) if stage == "queued" else None
    with LocalDaemonRuntime(tmp_path, instrument_endpoint=signal_endpoint()) as second:
        # The original confirmation is immutable even if its response was lost.
        assert second.application.automation.submit_step_input(command).output == output
        if run_id is None:
            run_id = admit(second, output)
        original = second.application.runs.get_run(run_id)
        with pytest.raises(BackendConflict, match="confirmed again"):
            second.application.executor.start_executor(
                run_id, ExecutorStartRequest(executor_id="worker")
            )
        blocked = second.application.runs.get_run(run_id)
        assert blocked.control.state == "attention_required"
        assert blocked.control.attention_reason == "manual_line_confirmation_expired"
        assert second.application.executor.execution_segments(run_id).items == ()
        with pytest.raises(BackendConflict, match="reconfirmation"):
            second.application.resolve_attention(
                run_id,
                AttentionResolutionCommand.continue_run(
                    run_contract_fingerprint=original.control.admission.run_contract_fingerprint,
                ),
            )
        fresh = reconfirmation(second, run_id, "confirmation-2")
        receipt = second.application.resolve_attention(run_id, fresh)
        assert second.application.resolve_attention(run_id, fresh) == receipt
        retained_confirmation = fresh.manual_line_reconfirmation
        assert retained_confirmation is not None
        changed = fresh.model_copy(
            update={
                "manual_line_reconfirmation": retained_confirmation.model_copy(
                    update={"actor": "different"}
                )
            }
        )
        with pytest.raises(BackendConflict, match="different content"):
            second.application.resolve_attention(run_id, changed)
        assert (
            second.application.runs.get_run(run_id).control.admission
            == original.control.admission
        )
        assert second.application.runs.get_run(run_id).snapshot == original.snapshot
    with LocalDaemonRuntime(tmp_path, instrument_endpoint=signal_endpoint()) as third:
        # Retrying the previous HTTP command is not a new physical confirmation.
        assert third.application.resolve_attention(run_id, fresh) == receipt
        with pytest.raises(BackendConflict, match="confirmed again"):
            third.application.executor.start_executor(
                run_id, ExecutorStartRequest(executor_id="worker")
            )
        assert third.application.resolve_attention(run_id, fresh) == receipt
        assert (
            third.application.runs.get_run(run_id).control.state == "attention_required"
        )
        third.application.resolve_attention(
            run_id, reconfirmation(third, run_id, "confirmation-3")
        )
        lease = third.application.executor.start_executor(
            run_id, ExecutorStartRequest(executor_id="worker")
        )
        assert len(third.application.executor.execution_segments(run_id).items) == 1
        third.application.executor.commit_terminal(
            run_id,
            TerminalRunCommitCommand(
                lease_id=lease.lease_id,
                outcome=RunOutcome(
                    run_id=run_id,
                    result="succeeded",
                    certainty="known",
                    finished_at=datetime.now(UTC),
                ),
            ),
        )
    with LocalDaemonRuntime(tmp_path, instrument_endpoint=signal_endpoint()) as fourth:
        retained = fourth.application.runs.get_run(run_id)
        assert retained.control.admission == original.control.admission
        assert retained.snapshot.outcome is not None
        assert retained.snapshot.outcome.result == "succeeded"
        assert len(fourth.application.executor.execution_segments(run_id).items) == 1


def test_line_reconfirmation_cannot_resolve_started_unknown_run(tmp_path: Path) -> None:
    with LocalDaemonRuntime(
        tmp_path, bootstrap_config=load_config(), instrument_endpoint=signal_endpoint()
    ) as first:
        output, _ = confirm(first)
        run_id = admit(first, output)
        first.application.executor.start_executor(
            run_id, ExecutorStartRequest(executor_id="worker")
        )
    with LocalDaemonRuntime(tmp_path, instrument_endpoint=signal_endpoint()) as second:
        assert (
            second.application.runs.get_run(run_id).control.attention_reason
            == "daemon_restarted"
        )
        with pytest.raises(BackendConflict, match="external-state reconciliation"):
            second.application.resolve_attention(
                run_id, reconfirmation(second, run_id, "not-a-reconciliation")
            )
        assert (
            second.application.runs.get_run(run_id).control.state
            == "attention_required"
        )
        assert len(second.application.executor.execution_segments(run_id).items) == 1


def test_drain_allows_first_lease_fence_but_requires_cancel_before_reconfirmation(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(
        tmp_path, bootstrap_config=load_config(), instrument_endpoint=signal_endpoint()
    ) as first:
        output, _ = confirm(first)
        run_id = admit(first, output)
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=signal_endpoint()) as second,
        TestClient(second.app(request_shutdown=lambda _: True)) as transport,
    ):
        original = second.application.runs.get_run(run_id).control.admission
        assert transport.post("/api/v1/application-exit?wait=true").status_code == 204
        blocked = transport.post(
            f"/api/v1/runs/{run_id}/executor/start",
            json=ExecutorStartRequest(executor_id="line-worker").model_dump(
                mode="json"
            ),
        )
        assert blocked.status_code == 409
        assert "confirmed again" in blocked.text
        report = transport.get("/api/v1/application-exit").json()
        assert any(item["identity"] == run_id for item in report["blockers"])
        command = reconfirmation(second, run_id, "after-cancelling-drain")
        path = f"/api/v1/runs/{run_id}/attention"
        assert (
            transport.post(path, json=command.model_dump(mode="json")).status_code
            == 409
        )
        assert (
            second.application.runs.get_run(run_id).control.state
            == "attention_required"
        )
        assert transport.post("/api/v1/application-exit?wait=false").status_code == 204
        assert (
            transport.post(path, json=command.model_dump(mode="json")).status_code
            == 200
        )
        assert second.application.runs.get_run(run_id).control.admission == original
        assert second.application.executor.execution_segments(run_id).items == ()
