"""Declared checks are admitted against authority before executing laboratory code."""

import sqlite3
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from scopecat.analysis.calibration import CHECK_RESULT
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ProcedureCancelCommand,
    ProcedureDefinitionRef,
    ProcedureRunListQuery,
    ProcedureStepBeginCommand,
    ProcedureStepCompleteCommand,
    ProcedureSubmitCommand,
    ProcedureWorkerLeaseAcquireCommand,
    RunOutputRef,
    procedure_step_operation_id,
)
from scopecat.automation.calibration_tasks import (
    CalibrationTaskPlan,
    CalibrationTaskProgress,
    CalibrationTaskStage,
)
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.control.models import RunPlanSummary
from scopecat.daemon.calibration_checks import (
    CalibrationCheckObservation,
    CalibrationCheckObservationResult,
    CalibrationCheckPage,
    CalibrationCheckQuery,
    CalibrationTaskPreview,
)
from scopecat.daemon.calibration_tasks import (
    CalibrationTaskCall,
    CalibrationTaskControl,
    CalibrationTaskCreate,
    CalibrationTaskDispatch,
    CalibrationTaskListQuery,
    CalibrationTaskRecord,
    CalibrationTaskView,
)
from scopecat.daemon.wire import (
    AnalysisFactOutputPayload,
    AnalysisSaveCommand,
    ParameterResolveCommand,
    ParameterSaveCommand,
    RunSubmission,
    SampleCreateCommand,
    SetupActivateCommand,
    SetupSaveCommand,
)
from scopecat.project import load_project
from scopecat.records.analysis import AnalysisFact, RunAnalysisSubject
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationCheckResult,
    CalibrationContext,
    CalibrationScope,
)
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.plan_ref import ProcedureChildSubmission
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import SampleRevisionDraft, SampleSelector
from scopecat.records.scientific_binding import UnboundSubject
from scopecat.sdk.compute import PYTHON_JSON_CODEC
from scopecat_testkit.workflow_fixtures import load_config

from scopecat_server import BackendConflict, LocalDaemonRuntime
from scopecat_server.services.calibration_task_runner import CalibrationTaskRunner
from scopecat_server.services.project_workers import ProjectProcedureWorkers
from scopecat_server.snapshots import create_snapshot, restore_snapshot
from scopecat_server.storage.sqlite.calibration_checks import CheckRequestPage

type CheckCase = tuple[LocalDaemonRuntime, CalibrationCheckRequest, RunSubmission]


@pytest.fixture
def check_case(
    tmp_path: Path,
) -> Iterator[CheckCase]:
    with _check_case(tmp_path) as case:
        yield case


@contextmanager
def _check_case(tmp_path: Path) -> Generator[CheckCase]:
    config = load_config()
    with LocalDaemonRuntime(tmp_path, bootstrap_config=config) as runtime:
        app = runtime.application
        app.samples.create(
            SampleCreateCommand(
                operation_id="sample",
                sample_id="chip",
                kind="chip",
                actor="test",
                content=SampleRevisionDraft(
                    display_name="Chip",
                    topology=config.system.topology,
                ),
            )
        )
        revision = app.config.save_parameters(
            ParameterSaveCommand(
                revision_id="parameters",
                catalog=config.parameter_catalog,
                parameters=config.parameter_snapshot,
                actor="test",
            )
        )
        resolved = app.config.resolve_parameters(
            ParameterResolveCommand(
                parameters=revision.ref,
                setup=app.setup.current().revision.ref,
            )
        )
        binding = bind_scientific_evidence(
            catalog_id=app.project_id,
            config=resolved.config,
            sample_revisions={},
            samples=app.samples.resolve_bindings(
                (SampleSelector(sample_id="chip", revision=1),)
            ),
        )
        declaration = CalibrationCheckRequest(
            scope=CalibrationScope("readout", ("q0",), "cold", "1"),
            context=CalibrationContext(
                revision.ref,
                binding.subject,
                binding.setup_content_hash,
                binding.scenario,
            ),
            measurement_step="measure",
            analysis_step="assess",
        )
        child = RunSubmission(
            submission_id="measurement",
            config=resolved.config,
            config_source=resolved.config_source,
            scientific_binding=binding,
            request=RunRequest(
                experiment_id="check", samples=binding.sample_selectors()
            ),
            plan=RunPlanSummary(
                experiment_id="check",
                experiment_kind="check",
                point_plan_fingerprint="a" * 64,
                measurement_contract_fingerprint="b" * 64,
                point_count=1,
                initial_point_count=1,
                point_limit=1,
            ),
        )
        yield runtime, declaration, child


def _command(declaration: CalibrationCheckRequest) -> ProcedureSubmitCommand:
    return ProcedureSubmitCommand(
        request_key="check",
        definition=ProcedureDefinitionRef(
            id="check",
            version="1",
            fingerprint="sha256:" + "a" * 64,
        ),
        intent={"calibration_check": declaration.model_dump(mode="json")},
        samples=(SampleSelector(sample_id="chip", revision=1),),
    )


def test_rejects_invalid_declarations_without_queueing(check_case: CheckCase) -> None:
    runtime, declaration, child = check_case
    service = runtime.application.automation
    command = _command(declaration)
    context = declaration.context
    cases = (
        ({"codec": "unknown"}, "invalid calibration check"),
        (
            declaration.model_copy(
                update={"analysis_step": declaration.measurement_step}
            ).model_dump(mode="json"),
            "distinct steps",
        ),
        (
            declaration.model_copy(
                update={
                    "context": replace(
                        context,
                        setup_content_hash="sha256:" + "f" * 64,
                    )
                }
            ).model_dump(mode="json"),
            "check setup",
        ),
        (
            declaration.model_copy(
                update={
                    "context": replace(
                        context,
                        parameters=context.parameters.model_copy(
                            update={"content_hash": "sha256:" + "f" * 64}
                        ),
                    )
                }
            ).model_dump(mode="json"),
            "reference differs",
        ),
        (
            declaration.model_copy(
                update={
                    "context": replace(
                        context,
                        subject=UnboundSubject(),
                        scenario=None,
                    )
                }
            ).model_dump(mode="json"),
            "require a declared subject",
        ),
        (
            declaration.model_copy(
                update={
                    "context": replace(
                        context,
                        scenario=SoftwareExecutionScenario(
                            id="fake",
                            label="Fake",
                            model_id="fake",
                            model_version="1",
                            capabilities=("readout",),
                        ),
                    )
                }
            ).model_dump(mode="json"),
            "retained evidence",
        ),
    )
    for payload, message in cases:
        with pytest.raises(BackendConflict, match=message):
            service.submit(
                command.model_copy(update={"intent": {"calibration_check": payload}})
            )
    with pytest.raises(BackendConflict, match="sample selection"):
        service.submit(command.model_copy(update={"samples": ()}))
    with pytest.raises(BackendConflict, match="procedure scientific binding"):
        service.submit(
            command.model_copy(
                update={
                    "scientific_binding": child.scientific_binding.model_copy(
                        update={"config_content_hash": "sha256:" + "f" * 64},
                    )
                }
            )
        )
    assert service.list(ProcedureRunListQuery()).items == ()
    assert (
        runtime.application.calibration_checks.query(CalibrationCheckQuery()).items
        == ()
    )


def test_indexed_check_query_filters_before_pagination(check_case: CheckCase) -> None:
    runtime, declaration, child = check_case
    app = runtime.application
    other_parameters = app.config.save_parameters(
        ParameterSaveCommand(
            revision_id="second",
            catalog=child.config.parameter_catalog,
            parameters=child.config.parameter_snapshot,
            actor="test",
        )
    )
    declarations = (
        declaration,
        declaration.model_copy(
            update={"scope": replace(declaration.scope, capability="drive")}
        ),
        declaration.model_copy(
            update={
                "context": replace(declaration.context, parameters=other_parameters.ref)
            }
        ),
        declaration,
    )
    checks = tuple(
        app.automation.submit(
            _command(item).model_copy(update={"request_key": f"check-{index}"})
        ).run
        for index, item in enumerate(declarations)
    )
    # New ordinary work must neither fill a domain page nor move its head.
    for index in range(8):
        app.automation.submit(
            _command(declaration).model_copy(
                update={
                    "request_key": f"ordinary-{index}",
                    "intent": {},
                }
            )
        )
    with TestClient(runtime.app()) as client:

        def query(value: CalibrationCheckQuery) -> CalibrationCheckPage:
            response = client.post(
                "/api/v1/calibration-checks/query", json=value.model_dump(mode="json")
            )
            assert response.status_code == 200, response.text
            return CalibrationCheckPage.model_validate(response.json())

        filtered = CalibrationCheckQuery(
            scope=declaration.scope, context=declaration.context, limit=1
        )
        page = query(filtered)
        assert tuple(item.execution for item in page.items) == (checks[3],)
        assert page.items[0].request == declaration
        assert page.items[0].evidence is None
        assert page.next_cursor is not None
        tail = query(filtered.model_copy(update={"cursor": page.next_cursor}))
        assert tuple(item.execution for item in tail.items) == (checks[0],)
        assert tail.next_cursor is None
        assert tuple(
            item.execution for item in query(CalibrationCheckQuery()).items
        ) == tuple(reversed(checks))
        assert tuple(
            item.execution
            for item in query(CalibrationCheckQuery(scope=declaration.scope)).items
        ) == (
            checks[3],
            checks[2],
            checks[0],
        )
        assert tuple(
            item.execution
            for item in query(CalibrationCheckQuery(context=declaration.context)).items
        ) == (
            checks[3],
            checks[1],
            checks[0],
        )
        assert (
            query(
                CalibrationCheckQuery(
                    scope=replace(declaration.scope, conditions="absent")
                )
            ).items
            == ()
        )
        acquired = app.automation.acquire_lease(
            ProcedureWorkerLeaseAcquireCommand(
                procedure_run_id=checks[3].procedure_run_id,
                worker_id="test",
                expected_run_revision=checks[3].revision,
            )
        )
        assert tuple(item.execution for item in query(filtered).items) == (
            acquired.run,
        )
        # An idempotent submit cannot duplicate the projection.
        app.automation.submit(
            _command(declaration).model_copy(update={"request_key": "check-3"})
        )
        assert len(query(CalibrationCheckQuery()).items) == 4


def test_batch_observation_compares_head_and_revisions_at_one_snapshot(
    check_case: CheckCase,
) -> None:
    runtime, declaration, _ = check_case
    app = runtime.application
    first = app.automation.submit(_command(declaration)).run
    other_declaration = declaration.model_copy(
        update={"scope": replace(declaration.scope, capability="other")}
    )
    other = app.automation.submit(
        _command(other_declaration).model_copy(update={"request_key": "other"})
    ).run
    observation = CalibrationCheckObservation(
        scope=declaration.scope,
        context=declaration.context,
        head=first.procedure_run_id,
        revisions={first.procedure_run_id: first.revision},
    )
    with TestClient(runtime.app()) as client:

        def observe(
            value: CalibrationCheckObservation,
        ) -> CalibrationCheckObservationResult:
            response = client.post(
                "/api/v1/calibration-checks/observe", json=value.model_dump(mode="json")
            )
            assert response.status_code == 200, response.text
            return CalibrationCheckObservationResult.model_validate(response.json())

        stable = observe(observation)
        assert not stable.head_changed and stable.changed_procedures == ()
        absent = observe(
            observation.model_copy(
                update={
                    "scope": replace(declaration.scope, conditions="absent"),
                    "head": None,
                    "revisions": {},
                }
            )
        )
        assert not absent.head_changed and absent.changed_procedures == ()
        mismatched = observe(
            observation.model_copy(
                update={
                    "revisions": {other.procedure_run_id: other.revision, "missing": 1}
                }
            )
        )
        assert mismatched.changed_procedures == (other.procedure_run_id, "missing")
        assert not mismatched.head_changed
        query_store = app.calibration_checks._checks
        original = query_store.revisions_in_transaction

        def advancing(
            connection: sqlite3.Connection,
            query: CalibrationCheckQuery,
            ids: tuple[str, ...],
        ) -> dict[str, int]:
            app.automation.acquire_lease(
                ProcedureWorkerLeaseAcquireCommand(
                    procedure_run_id=first.procedure_run_id,
                    worker_id="test",
                    expected_run_revision=first.revision,
                )
            )
            app.automation.submit(
                _command(declaration).model_copy(update={"request_key": "new"})
            )
            return original(connection, query, ids)

        with patch.object(query_store, "revisions_in_transaction", advancing):
            during = observe(observation)
        assert during == stable
        after = observe(observation)
        assert after.head_changed
        assert after.changed_procedures == (first.procedure_run_id,)
        assert (
            client.post(
                "/api/v1/calibration-checks/observe", json={"revisions": {"bad": 0}}
            ).status_code
            == 422
        )
        oversized = {f"check-{index}": 1 for index in range(2001)}
        bounded = dict(list(oversized.items())[:2000])
        assert (
            len(
                observe(
                    observation.model_copy(update={"revisions": bounded})
                ).changed_procedures
            )
            == 2000
        )
        assert (
            client.post(
                "/api/v1/calibration-checks/observe", json={"revisions": oversized}
            ).status_code
            == 422
        )


def test_task_preview_binds_exact_checks_without_submitting_work(
    check_case: CheckCase,
) -> None:
    runtime, declaration, _ = check_case
    app = runtime.application
    run = app.automation.submit(_command(declaration)).run
    plan = CalibrationTaskPlan(
        stages=(
            CalibrationTaskStage(id="a", check=declaration),
            CalibrationTaskStage(id="next", check=declaration, depends_on=("a",)),
            CalibrationTaskStage(id="independent", check=declaration),
        )
    )
    preview = CalibrationTaskPreview(plan=plan, executions={"a": run.procedure_run_id})
    before = app.automation.list(ProcedureRunListQuery())
    with TestClient(runtime.app()) as client:
        response = client.post(
            "/api/v1/calibration-tasks/preview", json=preview.model_dump(mode="json")
        )
        assert response.status_code == 200, response.text
        progress = CalibrationTaskProgress.model_validate(response.json())
        assert progress.ready == ("independent",)
        assert tuple(stage.state for stage in progress.stages) == (
            "queued",
            "waiting",
            "ready",
        )
        assert progress.stages[1].blocked_by == ("a",)
        assert not progress.complete and not progress.successful
        wrong = declaration.model_copy(
            update={"scope": replace(declaration.scope, conditions="different")}
        )
        mismatch = CalibrationTaskPreview(
            plan=CalibrationTaskPlan(
                stages=(CalibrationTaskStage(id="a", check=wrong),)
            ),
            executions=preview.executions,
        )
        rejected = client.post(
            "/api/v1/calibration-tasks/preview", json=mismatch.model_dump(mode="json")
        )
        assert rejected.status_code == 409
        assert "declared check" in rejected.text
    assert app.automation.list(ProcedureRunListQuery()) == before


def _task(declaration: CalibrationCheckRequest) -> CalibrationTaskCreate:
    command = _command(declaration)
    return CalibrationTaskCreate(
        task_id="round-1",
        plan=CalibrationTaskPlan(
            stages=(
                CalibrationTaskStage(id="a", check=declaration),
                CalibrationTaskStage(id="b", check=declaration),
                CalibrationTaskStage(
                    id="after-a", check=declaration, depends_on=("a",)
                ),
            )
        ),
        calls={
            stage: CalibrationTaskCall(
                definition=command.definition,
                intent=command.intent,
                samples=command.samples,
            )
            for stage in ("a", "b", "after-a")
        },
    )


def test_task_dispatch_is_atomic_and_does_not_admit_unready_stages(
    check_case: CheckCase,
) -> None:
    runtime, declaration, _ = check_case
    app = runtime.application
    spec = _task(declaration)
    tasks = app.calibration_tasks
    created = tasks.create(spec)
    assert tasks.create(spec) == created
    altered_call = spec.calls["a"].model_copy(
        update={
            "definition": spec.calls["a"].definition.model_copy(update={"version": "2"})
        }
    )
    with pytest.raises(BackendConflict, match="different specification"):
        tasks.create(
            spec.model_copy(update={"calls": {**spec.calls, "a": altered_call}})
        )
    with pytest.raises(BackendConflict, match="prerequisites"):
        tasks.dispatch(
            CalibrationTaskDispatch(task_id=spec.task_id, stage_id="after-a")
        )
    dispatch = CalibrationTaskDispatch(task_id=spec.task_id, stage_id="a")
    with (
        patch.object(
            tasks._store,
            "update",
            side_effect=RuntimeError("interrupted task association"),
        ),
        pytest.raises(RuntimeError, match="interrupted task association"),
    ):
        tasks.dispatch(dispatch)
    assert tasks.get(spec.task_id).task.executions == {}
    assert app.automation.list(ProcedureRunListQuery()).items == ()
    assert app.calibration_checks.query(CalibrationCheckQuery()).items == ()
    with TestClient(runtime.app()) as client:
        first = client.post(
            "/api/v1/calibration-tasks/dispatch", json=dispatch.model_dump(mode="json")
        )
        assert first.status_code == 200, first.text
        admitted = CalibrationTaskView.model_validate(first.json())
        assert (
            client.post(
                "/api/v1/calibration-tasks/dispatch",
                json=dispatch.model_dump(mode="json"),
            ).json()
            == first.json()
        )
        assert client.get("/api/v1/calibration-tasks/round-1").status_code == 200
    assert set(admitted.task.executions) == {"a"}
    assert len(app.automation.list(ProcedureRunListQuery()).items) == 1
    independent = tasks.dispatch(
        CalibrationTaskDispatch(task_id=spec.task_id, stage_id="b")
    )
    assert set(independent.task.executions) == {"a", "b"}
    assert tasks.list(CalibrationTaskListQuery()).items == (independent.task,)


def test_task_survives_restart_and_current_format_restore(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    manifest = source / "scopecat.toml"
    manifest.write_text("[lab]\n")
    with _check_case(source) as (runtime, declaration, _):
        spec = _task(declaration)
        runtime.application.calibration_tasks.create(spec)
        command = CalibrationTaskDispatch(task_id=spec.task_id, stage_id="a")
        retained = runtime.application.calibration_tasks.dispatch(command)
        retained = runtime.application.calibration_tasks.control(
            CalibrationTaskControl(
                task_id=spec.task_id,
                expected_revision=1,
                action="start",
                actor="test",
                reason="continue after restart",
            )
        )
    with LocalDaemonRuntime(source) as restarted:
        assert restarted.application.calibration_tasks.get(spec.task_id) == retained
        assert restarted.application.calibration_tasks.dispatch(command) == retained
    create_snapshot(load_project(manifest), tmp_path / "snapshot")
    restore_snapshot(tmp_path / "snapshot", tmp_path / "restored")
    with LocalDaemonRuntime(tmp_path / "restored") as restored:
        tasks = restored.application.calibration_tasks
        assert tasks.get(spec.task_id) == retained
        assert tasks.create(spec) == retained
        assert tasks.dispatch(command) == retained
        assert set(
            tasks.dispatch(
                CalibrationTaskDispatch(task_id=spec.task_id, stage_id="b")
            ).task.executions
        ) == {"a", "b"}


def test_task_controls_stop_new_admission_and_reject_stale_commands(
    check_case: CheckCase,
) -> None:
    runtime, declaration, _ = check_case
    tasks = runtime.application.calibration_tasks
    spec = _task(declaration)
    tasks.create(spec)
    start = CalibrationTaskControl(
        task_id=spec.task_id,
        expected_revision=1,
        action="start",
        actor="test",
        reason="run",
    )
    started = tasks.control(start)
    assert tasks.control(start) == started
    admitted = tasks.advance(spec.task_id)
    assert set(admitted.task.executions) == {"a"}
    assert tasks.advance(spec.task_id) == admitted  # sequential, no second child
    pause = start.model_copy(update={"expected_revision": 2, "action": "pause"})
    paused = tasks.control(pause)
    assert tasks.advance(spec.task_id) == paused
    with pytest.raises(BackendConflict, match="revision changed"):
        tasks.control(start)
    with pytest.raises(BackendConflict, match="paused"):
        tasks.dispatch(CalibrationTaskDispatch(task_id=spec.task_id, stage_id="b"))
    assert (
        tasks.dispatch(CalibrationTaskDispatch(task_id=spec.task_id, stage_id="a"))
        == paused
    )
    cancelled = tasks.control(
        start.model_copy(update={"expected_revision": 3, "action": "cancel"})
    )
    assert tasks.advance(spec.task_id) == cancelled
    assert tasks.running_ids() == []
    with pytest.raises(BackendConflict, match="cancelled"):
        tasks.control(start.model_copy(update={"expected_revision": 4}))
    assert (
        runtime.application.automation.get(admitted.task.executions["a"]).state
        == "ready"
    )


def test_automatic_admission_failure_does_not_block_independent_stage(
    check_case: CheckCase,
) -> None:
    runtime, declaration, _ = check_case
    tasks = runtime.application.calibration_tasks
    spec = _task(declaration)
    tasks.create(spec)
    start = CalibrationTaskControl(
        task_id=spec.task_id,
        expected_revision=1,
        action="start",
        actor="test",
        reason="run",
    )
    tasks.control(start)
    admit = tasks._admit
    count = 0

    def fail_first(
        connection: sqlite3.Connection, task: CalibrationTaskRecord, stage_id: str
    ) -> CalibrationTaskRecord:
        nonlocal count
        count += 1
        run = admit(connection, task, stage_id)
        if count == 1:
            raise BackendConflict("changed setup")
        return run

    with patch.object(tasks, "_admit", side_effect=fail_first):
        view = tasks.advance(spec.task_id)
    assert view.task.dispatch_errors == {"a": "changed setup"}
    assert set(view.task.executions) == {"b"}
    assert len(runtime.application.automation.list(ProcedureRunListQuery()).items) == 1
    assert tasks.advance(spec.task_id) == view
    child = runtime.application.automation.get(view.task.executions["b"])
    runtime.application.automation.cancel(
        ProcedureCancelCommand(
            procedure_run_id=child.procedure_run_id,
            expected_run_revision=child.revision,
            actor="test",
            reason="finish independent branch",
        )
    )
    with patch.object(tasks, "_admit") as retry:
        stalled = tasks.advance(spec.task_id)
    retry.assert_not_called()
    assert stalled.task.dispatch_errors == {"a": "changed setup"}
    resumed = tasks.control(start.model_copy(update={"expected_revision": 2}))
    assert resumed.task.dispatch_errors == {}
    assert set(tasks.advance(spec.task_id).task.executions) == {"a", "b"}


def test_runner_recovers_admitted_handoff_and_rechecks_pause(
    check_case: CheckCase,
) -> None:
    runtime, declaration, _ = check_case
    tasks = runtime.application.calibration_tasks
    spec = _task(declaration)
    tasks.create(spec)
    tasks.control(
        CalibrationTaskControl(
            task_id=spec.task_id,
            expected_revision=1,
            action="start",
            actor="test",
            reason="run",
        )
    )
    admitted = tasks.advance(spec.task_id)  # daemon stopped before the worker handoff
    workers = Mock(spec=ProjectProcedureWorkers)
    runner = CalibrationTaskRunner(tasks, workers)
    runner.tick()
    workers.manage.assert_called_once_with(admitted.task.executions["a"])
    workers.reset_mock()
    discover = tasks.running_ids

    def pause_after_discovery(after: int) -> list[tuple[int, str]]:
        page = discover(after)
        if page:
            tasks.control(
                CalibrationTaskControl(
                    task_id=spec.task_id,
                    expected_revision=2,
                    action="pause",
                    actor="test",
                    reason="inspect",
                )
            )
        return page

    with patch.object(tasks, "running_ids", side_effect=pause_after_discovery):
        runner.tick()
    workers.manage.assert_not_called()
    assert tasks.get(spec.task_id).task.executions == admitted.task.executions


def test_exact_retry_survives_setup_change(check_case: CheckCase) -> None:
    runtime, declaration, _ = check_case
    app = runtime.application
    command = _command(declaration)
    parent = app.automation.submit(command).run
    spec = _task(declaration)
    app.calibration_tasks.create(spec)
    dispatched = CalibrationTaskDispatch(task_id=spec.task_id, stage_id="a")
    retained = app.calibration_tasks.dispatch(dispatched)
    current = app.setup.current()
    changed = app.setup.save(
        SetupSaveCommand(
            revision_id="changed",
            actor="test",
            setup=current.revision.setup.model_copy(
                update={
                    "scenario": SoftwareExecutionScenario(
                        id="other",
                        label="Other",
                        model_id="other",
                        model_version="1",
                        capabilities=("readout",),
                    )
                }
            ),
        )
    )
    app.setup.activate(
        SetupActivateCommand(
            operation_id="change",
            revision=changed.ref,
            expected_generation=current.activation.generation,
            actor="test",
        )
    )
    assert app.automation.submit(command).run == parent
    assert app.calibration_tasks.dispatch(dispatched) == retained
    with pytest.raises(BackendConflict, match="check setup"):
        app.calibration_tasks.dispatch(
            CalibrationTaskDispatch(task_id=spec.task_id, stage_id="b")
        )
    assert (
        app.calibration_tasks.get(spec.task_id).task.executions
        == retained.task.executions
    )
    with pytest.raises(BackendConflict, match="check setup"):
        app.automation.submit(command.model_copy(update={"request_key": "new"}))


@pytest.mark.parametrize("different_revision", [False, True])
def test_measurement_must_use_admitted_parameter_revision(
    check_case: CheckCase, different_revision: bool
) -> None:
    runtime, declaration, child = check_case
    app = runtime.application
    parent = app.automation.submit(_command(declaration)).run
    lease = app.automation.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=parent.procedure_run_id,
            worker_id="test",
            expected_run_revision=parent.revision,
        )
    )
    if different_revision:
        revision = app.config.save_parameters(
            ParameterSaveCommand(
                revision_id="other",
                catalog=child.config.parameter_catalog,
                parameters=child.config.parameter_snapshot,
                actor="test",
            )
        )
        resolved = app.config.resolve_parameters(
            ParameterResolveCommand(
                parameters=revision.ref,
                setup=app.setup.current().revision.ref,
            )
        )
        child = child.model_copy(
            update={
                "config": resolved.config,
                "config_source": resolved.config_source,
                "scientific_binding": child.scientific_binding.model_copy(
                    update={
                        "config_content_hash": resolved.config_source.content_hash,
                    }
                ),
            }
        )
    child = child.model_copy(
        update={
            "submission_id": procedure_step_operation_id(
                parent.procedure_run_id, "measure"
            ),
            "procedure_child": ProcedureChildSubmission(
                procedure_run_id=parent.procedure_run_id,
                step_key="measure",
            ),
        }
    )
    app.automation.begin_step(
        ProcedureStepBeginCommand(
            procedure_run_id=parent.procedure_run_id,
            lease_token=lease.lease.lease_token,
            expected_run_revision=lease.run.revision,
            step_key="measure",
            operation="run",
            intent_hash="sha256:" + child.intent_content_hash,
        )
    )
    if different_revision:
        with pytest.raises(BackendConflict, match="admitted declaration"):
            app.submit_run(child)
    else:
        admitted = app.submit_run(child)
        assert app.submit_run(child).run_id == admitted.run_id


def test_check_result_registration_rejects_wrong_evidence_and_retains_negative(
    check_case: CheckCase,
) -> None:
    runtime, declaration, child = check_case
    app = runtime.application
    service = app.automation
    parent = service.submit(_command(declaration)).run
    lease = service.acquire_lease(
        ProcedureWorkerLeaseAcquireCommand(
            procedure_run_id=parent.procedure_run_id,
            worker_id="test",
            expected_run_revision=parent.revision,
        )
    )
    measure = service.begin_step(
        ProcedureStepBeginCommand(
            procedure_run_id=parent.procedure_run_id,
            lease_token=lease.lease.lease_token,
            expected_run_revision=lease.run.revision,
            step_key="measure",
            operation="run",
            intent_hash="sha256:" + "b" * 64,
        )
    )
    measured = app.submit_run(child)
    measured_output = RunOutputRef(run_id=measured.run_id)
    measured_done = service.complete_step(
        ProcedureStepCompleteCommand(
            procedure_run_id=parent.procedure_run_id,
            lease_token=lease.lease.lease_token,
            expected_run_revision=measure.run.revision,
            step_key="measure",
            attempt=measure.step.attempt,
            expected_step_revision=measure.step.revision,
            output=measured_output,
        )
    )
    assess = service.begin_step(
        ProcedureStepBeginCommand(
            procedure_run_id=parent.procedure_run_id,
            lease_token=lease.lease.lease_token,
            expected_run_revision=measured_done.run.revision,
            step_key="assess",
            operation="analysis",
            intent_hash="sha256:" + "c" * 64,
        )
    )
    subject = RunAnalysisSubject(run_id=measured.run_id)
    complete = ProcedureStepCompleteCommand(
        procedure_run_id=parent.procedure_run_id,
        lease_token=lease.lease.lease_token,
        expected_run_revision=assess.run.revision,
        step_key="assess",
        attempt=assess.step.attempt,
        expected_step_revision=assess.step.revision,
        output=AnalysisPublicationOutputRef(
            subject=subject, analysis_record_id="missing"
        ),
    )
    with pytest.raises(BackendConflict, match="invalid calibration check evidence"):
        service.complete_step(complete)
    other = app.submit_run(
        child.model_copy(update={"submission_id": "another-measurement"})
    )
    with pytest.raises(BackendConflict, match="declared measurement"):
        service.complete_step(
            complete.model_copy(
                update={
                    "output": AnalysisPublicationOutputRef(
                        subject=RunAnalysisSubject(run_id=other.run_id),
                        analysis_record_id="missing",
                    )
                }
            )
        )

    valid = AnalysisFact(
        schema_id=CHECK_RESULT.id,
        schema_codec=CHECK_RESULT.schema_codec,
        schema_hash=CHECK_RESULT.schema_hash,
        codec=PYTHON_JSON_CODEC,
        value=CHECK_RESULT.encode(CalibrationCheckResult(declaration.scope, False)),
    )
    wrong_scope = valid.model_copy(
        update={
            "value": CHECK_RESULT.encode(
                CalibrationCheckResult(
                    replace(declaration.scope, conditions="warm"), True
                ),
            )
        }
    )
    cases = (
        (None, "declared fact output"),
        (
            valid.model_copy(update={"schema_hash": "sha256:" + "f" * 64}),
            "standard result schema",
        ),
        (
            valid.model_copy(update={"value": {"passed": "yes"}}),
            "invalid calibration check evidence",
        ),
        (wrong_scope, "declared scope"),
        (valid, None),
    )
    for index, (fact, error) in enumerate(cases):
        saved = app.runs.save_run_analysis(
            measured.run_id,
            AnalysisSaveCommand(
                title="Check",
                analysis_key=f"check-{index}",
                outputs=()
                if fact is None
                else (
                    AnalysisFactOutputPayload(
                        kind="fact",
                        id=declaration.result_output,
                        title="Check",
                        content=fact,
                    ),
                ),
            ),
        )
        command = complete.model_copy(
            update={
                "output": AnalysisPublicationOutputRef(
                    subject=subject,
                    analysis_record_id=saved.record.id,
                )
            }
        )
        if error is not None:
            with pytest.raises(BackendConflict, match=error):
                service.complete_step(command)
            assert service.get(parent.procedure_run_id).revision == assess.run.revision
        else:
            queries = app.calibration_checks
            original_query = queries._checks.query_in_transaction

            def finishing_query(
                connection: sqlite3.Connection,
                query: CalibrationCheckQuery,
                read: Callable[
                    [sqlite3.Connection, CalibrationCheckQuery], CheckRequestPage
                ] = original_query,
                finish: ProcedureStepCompleteCommand = command,
            ):
                page = read(connection, query)
                service.complete_step(finish)
                return page

            with patch.object(queries._checks, "query_in_transaction", finishing_query):
                before_completion = queries.query(CalibrationCheckQuery()).items[0]
            assert before_completion.execution.revision == assess.run.revision
            assert before_completion.evidence is None
            done = service.complete_step(command)
            assert done.step.state == "succeeded"
            assert service.complete_step(command) == done
            with TestClient(runtime.app()) as client:
                response = client.post("/api/v1/calibration-checks/query", json={})
                assert response.status_code == 200, response.text
                retained = CalibrationCheckPage.model_validate(response.json()).items[0]
            assert retained.execution == done.run
            assert retained.evidence is not None
            assert retained.evidence.measurement.run_id == measured.run_id
            assert retained.evidence.analysis_record_id == saved.record.id
            assert retained.evidence.passed is False
