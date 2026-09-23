"""Declared checks are admitted against authority before executing laboratory code."""

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from scopecat.analysis.calibration import CHECK_RESULT
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ProcedureDefinitionRef,
    ProcedureRunListQuery,
    ProcedureStepBeginCommand,
    ProcedureStepCompleteCommand,
    ProcedureSubmitCommand,
    ProcedureWorkerLeaseAcquireCommand,
    RunOutputRef,
    procedure_step_operation_id,
)
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.control.models import RunPlanSummary
from scopecat.daemon.calibration_checks import (
    CalibrationCheckPage,
    CalibrationCheckQuery,
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
from scopecat_server.storage.sqlite.calibration_checks import CheckRequestPage

type CheckCase = tuple[LocalDaemonRuntime, CalibrationCheckRequest, RunSubmission]


@pytest.fixture
def check_case(
    tmp_path: Path,
) -> Iterator[CheckCase]:
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


def test_exact_retry_survives_setup_change(check_case: CheckCase) -> None:
    runtime, declaration, _ = check_case
    app = runtime.application
    command = _command(declaration)
    parent = app.automation.submit(command).run
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
