"""Declared checks are admitted against authority before executing laboratory code."""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from scopecat.automation import (
    ProcedureDefinitionRef,
    ProcedureRunListQuery,
    ProcedureStepBeginCommand,
    ProcedureSubmitCommand,
    ProcedureWorkerLeaseAcquireCommand,
    procedure_step_operation_id,
)
from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.control.models import RunPlanSummary
from scopecat.daemon.wire import (
    ParameterResolveCommand,
    ParameterSaveCommand,
    RunSubmission,
    SampleCreateCommand,
    SetupActivateCommand,
    SetupSaveCommand,
)
from scopecat.records.calibration_check import (
    CalibrationCheckRequest,
    CalibrationContext,
    CalibrationScope,
)
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.plan_ref import ProcedureChildSubmission
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import SampleRevisionDraft, SampleSelector
from scopecat.records.scientific_binding import UnboundSubject
from scopecat_testkit.workflow_fixtures import load_config

from scopecat_server import BackendConflict, LocalDaemonRuntime

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
