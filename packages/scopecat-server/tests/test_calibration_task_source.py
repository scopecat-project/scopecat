"""Task source validation is outside writes and fenced before admission."""

from pathlib import Path

import pytest
from scopecat.automation import ProcedureSource, ProcedureSubmitCommand
from scopecat.daemon.calibration_tasks import (
    CalibrationTaskControl,
    CalibrationTaskDispatch,
)
from scopecat.records.author_revision import AuthorRevisionRef

from scopecat_server import BackendConflict

from .test_calibration_check_admission import _check_case, _task


def test_retained_templates_admission_and_control_race(tmp_path: Path) -> None:
    with _check_case(tmp_path) as (runtime, check, _):
        tasks = runtime.application.calibration_tasks
        source = ProcedureSource(
            workspace_id="workspace",
            code_revision=AuthorRevisionRef(content_hash="sha256:" + "a" * 64),
        )
        specification = _task(check).model_copy(update={"source": source})
        with pytest.raises(BackendConflict, match="unavailable"):
            tasks.create(specification)
        validated: list[ProcedureSubmitCommand] = []

        def validate(command: ProcedureSubmitCommand) -> None:
            # A second writer must remain available throughout worker validation.
            with tasks._sqlite.write_transaction() as connection:
                connection.execute("SELECT 1")
            validated.append(command)

        tasks.validate_call = validate
        created = tasks.create(specification)
        assert len(validated) == len(specification.calls)
        assert all(command.source == source for command in validated)
        assert tasks.create(specification) == created
        assert len(validated) == len(specification.calls)

        def race(command: ProcedureSubmitCommand) -> None:
            validate(command)
            tasks.control(
                CalibrationTaskControl(
                    task_id=specification.task_id,
                    expected_revision=1,
                    action="pause",
                    actor="test",
                    reason="concurrent pause",
                )
            )

        tasks.validate_call = race
        dispatch = CalibrationTaskDispatch(task_id=specification.task_id, stage_id="a")
        with pytest.raises(BackendConflict, match="task changed"):
            tasks.dispatch(dispatch)
        assert not tasks.get(specification.task_id).task.attempts
        paused = tasks.get(specification.task_id)
        tasks.control(
            CalibrationTaskControl(
                task_id=specification.task_id,
                expected_revision=paused.task.control_revision,
                action="start",
                actor="test",
                reason="resume",
            )
        )
        tasks.validate_call = validate
        admitted = tasks.dispatch(dispatch)
        run = runtime.application.automation.get(admitted.task.executions["a"])
        assert run.source == source
        assert validated[-1].intent == run.intent
        count = len(validated)
        assert tasks.dispatch(dispatch) == admitted
        assert len(validated) == count


def test_task_retained_worker_and_reconnect(tmp_path: Path) -> None:
    import importlib
    import time

    from scopecat.api.lab import LabClient
    from scopecat.application.author_imports import release_notebook_imports
    from scopecat.automation.calibration_tasks import (
        CalibrationTaskPlan,
        CalibrationTaskStage,
    )
    from scopecat.daemon.calibration_tasks import (
        CalibrationTaskCall,
        CalibrationTaskCreate,
    )
    from scopecat.daemon.client import DaemonConflictError
    from scopecat.records.calibration_check import (
        CalibrationCheckRequest,
        CalibrationScope,
    )
    from scopecat.records.config import InstrumentRegistry, RoutingGraph
    from scopecat.records.execution_scenario import SoftwareExecutionScenario
    from scopecat.records.setup import ExecutableSetupSnapshot
    from scopecat_testkit.project_loading import isolated_project_imports

    from scopecat_server.lifecycle import (
        initialize_project,
        start_project,
        stop_project,
    )

    from .test_author_procedures import _SOURCE, _wait
    from .test_project_analysis_runtime import _config

    project = initialize_project(tmp_path / "task")
    manifest = project.root / "scopecat.toml"
    manifest.write_text(
        manifest.read_text()
        + '\n[lab.capabilities]\nprocedures = ["scopecat_lab.workflow:ordinary"]\n'
    )
    source = project.root / "src/scopecat_lab/workflow.py"
    source.write_text(
        _SOURCE.replace(
            "class Intent(BaseModel):",
            "from scopecat.records.calibration_check import CalibrationCheckRequest\n"
            "\nclass Intent(BaseModel):\n"
            "    calibration_check: CalibrationCheckRequest",
        )
    )
    helper = source.with_name("helper.py")
    helper.write_text('LABEL = "task retained source"\n')
    start_project(project, timeout=60)
    try:
        with isolated_project_imports(), project.authoring() as author:
            author.refresh()
            module = importlib.import_module("scopecat_lab.workflow")
            lab = LabClient(author)
            config = _config()
            config = config.model_copy(
                update={
                    "system": config.system.model_copy(
                        update={
                            "instrument_registry": InstrumentRegistry(instruments=[]),
                            "routing": RoutingGraph(),
                            "domain_target": None,
                            "scenario": SoftwareExecutionScenario(
                                id="task",
                                label="Test",
                                model_id="fixture",
                                model_version="1",
                                capabilities=("drive",),
                            ),
                        }
                    )
                }
            )
            parameters = lab.parameters.save(
                name="initial",
                catalog=config.parameter_catalog,
                parameters=config.parameter_snapshot,
            )
            setup = lab.setup.import_recipe(
                ExecutableSetupSnapshot.from_config(config), name="task-setup"
            )
            context = lab.resolve_context(parameters=parameters, setup=setup).context
            check = CalibrationCheckRequest(
                setup=setup.ref,
                scope=CalibrationScope("drive", ("q0",), "test", "1"),
                context=context,
                measurement_step="measure",
                analysis_step="assess",
            )
            prepared = author.procedures.prepare(
                module.ordinary,
                module.Intent(label="Task", calibration_check=check),
                request_key="task-template",
            )
            call = CalibrationTaskCall(
                definition=prepared.command.definition, intent=prepared.command.intent
            )
            specification = CalibrationTaskCreate(
                task_id="retained-task",
                source=prepared.command.source,
                plan=CalibrationTaskPlan(
                    stages=(CalibrationTaskStage(id="check", check=check),)
                ),
                calls={"check": call},
            )
            helper.write_text('LABEL = "changed working tree"\n')
            forged = specification.model_copy(
                update={
                    "task_id": "forged",
                    "calls": {
                        "check": call.model_copy(
                            update={
                                "definition": call.definition.model_copy(
                                    update={"fingerprint": "sha256:" + "0" * 64}
                                )
                            }
                        )
                    },
                }
            )
            with pytest.raises(DaemonConflictError, match="fingerprint"):
                author.create_calibration_task(forged)
            noncanonical = specification.model_copy(
                update={
                    "task_id": "noncanonical",
                    "calls": {
                        "check": call.model_copy(
                            update={"intent": {**call.intent, "repetitions": True}}
                        )
                    },
                }
            )
            with pytest.raises(DaemonConflictError, match="not canonical"):
                author.create_calibration_task(noncanonical)
            created = author.create_calibration_task(specification)
            author.control_calibration_task(
                CalibrationTaskControl(
                    task_id=specification.task_id,
                    expected_revision=created.task.control_revision,
                    action="start",
                    actor="test",
                    reason="retained worker",
                )
            )
        with project.authoring() as reconnected:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                task = reconnected.get_calibration_task(specification.task_id)
                if task.task.executions:
                    break
                time.sleep(0.1)
            else:
                pytest.fail("task did not admit its check")
            handle = reconnected.procedures.get(task.task.executions["check"])
            _wait(handle, "waiting_for_input")
            assert handle.snapshot.source == specification.source
            request = handle.progress().steps.items[0].interpretation_request
            assert (
                request is not None and request.instructions == "task retained source"
            )
            assert (
                reconnected.create_calibration_task(specification).task.executions
                == task.task.executions
            )
            handle.cancel(actor="test", reason="journey complete")
    finally:
        release_notebook_imports(project.root)
        stop_project(project)


@pytest.mark.parametrize("reject_first", [False, True])
def test_advance_does_not_revalidate_ready_stages_while_work_is_active(
    tmp_path: Path,
    reject_first: bool,
) -> None:
    with _check_case(tmp_path) as (runtime, check, _):
        tasks = runtime.application.calibration_tasks
        source = ProcedureSource(
            workspace_id="workspace",
            code_revision=AuthorRevisionRef(content_hash="sha256:" + "a" * 64),
        )
        spec = _task(check).model_copy(update={"source": source})
        validated: list[ProcedureSubmitCommand] = []
        tasks.validate_call = validated.append
        tasks.create(spec)
        tasks.control(
            CalibrationTaskControl(
                task_id=spec.task_id,
                expected_revision=1,
                action="start",
                actor="test",
                reason="advance independent stages",
            )
        )
        validated.clear()

        def validate(command: ProcedureSubmitCommand) -> None:
            validated.append(command)
            if reject_first and len(validated) == 1:
                raise BackendConflict("first stage validation rejected")

        tasks.validate_call = validate
        admitted = tasks.advance(spec.task_id)
        assert len(validated) == 2
        assert set(admitted.task.executions) == ({"b"} if reject_first else {"a"})
        if reject_first:
            assert admitted.task.dispatch_errors == {
                "a": "first stage validation rejected"
            }
        for _ in range(3):
            assert tasks.advance(spec.task_id) == admitted
        assert len(validated) == 2
