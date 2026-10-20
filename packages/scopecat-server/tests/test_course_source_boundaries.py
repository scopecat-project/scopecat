"""Explicit HTTP identity cannot be replaced by a retained source identity."""

import sys
from collections.abc import Iterator
from contextvars import copy_context
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from scopecat.automation import (
    ProcedureDefinitionRef,
    ProcedureScheduleCreateCommand,
    ProcedureSource,
    ProcedureSubmitCommand,
)
from scopecat.records.calibration_check import CalibrationCheckRequest
from scopecat.records.calibration_policy import (
    CalibrationProfile,
    CalibrationRequirement,
)
from scopecat.records.practice import PracticeScope

from scopecat_server import LocalDaemonRuntime
from scopecat_server.author_registration import register_author_workspace
from scopecat_server.lifecycle import initialize_project
from scopecat_server.request_workspace import request_workspace
from scopecat_server.storage.sqlite.practice import PracticeOwnership

from .test_calibration_check_admission import _check_case, _task

_HASH = "sha256:" + "a" * 64
_DEFINITION = ProcedureDefinitionRef(
    id="identity-matrix", version="1", fingerprint=_HASH
)
type Resource = Literal["procedure", "schedule", "calibration_task"]
type Case = tuple[
    LocalDaemonRuntime, TestClient, CalibrationCheckRequest, dict[str, ProcedureSource]
]


@pytest.fixture(scope="module")
def case(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Case]:
    root = tmp_path_factory.mktemp("source-matrix")
    service = root / "service"
    service.mkdir()
    (service / "scopecat.toml").write_text("[lab]\n")
    with (
        _check_case(service) as (runtime, check, child),
        TestClient(runtime.app()) as client,
    ):
        # Code execution validation is outside this identity matrix; real author
        # registration, source binding, HTTP context and owner checks remain intact.
        def validate(_command: ProcedureSubmitCommand) -> None:
            pass

        runtime.application.calibration_tasks.validate_call = validate
        sqlite = runtime.application.calibration_tasks._sqlite
        with sqlite.write_transaction() as connection:
            owners = PracticeOwnership(connection)
            for identity in ("one", "two", "empty"):
                owners.save(
                    PracticeScope(
                        id=identity,
                        title=identity,
                        lesson="parameters",
                        directory=identity,
                        kind="notebook",
                        workspace_id=identity,
                    )
                )
                owners.claim(identity, "source", identity)
                connection.execute(
                    "INSERT INTO author_workspaces(workspace_id,name) VALUES (?,?)",
                    (identity, identity),
                )
        with sqlite.write_transaction() as connection:
            connection.execute(
                "INSERT INTO author_workspaces(workspace_id,name) VALUES (?,?)",
                ("ordinary", "Ordinary"),
            )
        sources: dict[str, ProcedureSource] = {}
        for identity in ("one", "two", "ordinary"):
            project = initialize_project(root / identity)
            register_author_workspace(
                service, project.root, identity=identity, python=Path(sys.executable)
            )
            author = runtime.application.author_workspaces.get(identity)
            assert author.baseline is not None
            sources[identity] = ProcedureSource(
                workspace_id=identity, code_revision=author.baseline.manifest.ref
            )
        from scopecat.records.practice import PracticeCreateCommand
        from scopecat.records.setup import SetupRevisionRef

        from scopecat_server.storage.sqlite.parameter_revisions import (
            ParameterRevisionRepository,
        )
        from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository

        peak = runtime.application.practice.create(
            PracticeCreateCommand(request_key="fence-fixture")
        )
        assert peak.procedure_id is not None
        peak_run = runtime.application.automation.get(peak.procedure_id)
        ref = SetupRevisionRef.model_validate(peak_run.intent["setup"])
        with sqlite.write_transaction() as connection:
            repository = SQLiteSetupRepository(connection)
            base = repository.read_revision(ref.revision_id)
            for owner in ("one", "two"):
                setup = base.model_copy(update={"id": "owned-fence-" + owner})
                repository.save_revision(setup)
                PracticeOwnership(connection).claim(owner, "setup", setup.id)
                parameters = (
                    ParameterRevisionRepository(connection)
                    .get("practice:" + peak.id)
                    .model_copy(update={"id": "owned-parameters-" + owner})
                )
                ParameterRevisionRepository(connection).save(parameters)
                PracticeOwnership(connection).claim(owner, "parameters", parameters.id)
        from scopecat.config.scientific_binding import bind_scientific_evidence
        from scopecat.daemon.wire import ParameterResolveCommand
        from scopecat.records.run_request import RunRequest

        with sqlite.read_transaction() as connection:
            setup = SQLiteSetupRepository(connection).read_revision("owned-fence-one")
            parameters = ParameterRevisionRepository(connection).get(
                "owned-parameters-one"
            )
        resolved = runtime.application.config.resolve_parameters(
            ParameterResolveCommand(parameters=parameters.ref, setup=setup.ref)
        )
        binding = bind_scientific_evidence(
            catalog_id=runtime.application.project_id,
            config=resolved.config,
            samples=(),
            sample_revisions={},
        )
        from scopecat.daemon.wire import ExecutorStartRequest, TerminalRunCommitCommand
        from scopecat.kernel.run_outcome import RunOutcome

        admitted = runtime.application.submit_run(
            child.model_copy(
                update={
                    "submission_id": "analysis-fixture",
                    "execution_setup": setup.ref,
                    "config": resolved.config,
                    "config_source": resolved.config_source,
                    "scientific_binding": binding,
                    "request": RunRequest(experiment_id="check"),
                    "plan": child.plan.model_copy(
                        update={
                            "point_count": 0,
                            "initial_point_count": 0,
                            "point_limit": 0,
                        }
                    ),
                }
            )
        )
        lease = runtime.application.executor.start_executor(
            admitted.run_id, ExecutorStartRequest(executor_id="configuration-fixture")
        )
        runtime.application.executor.commit_terminal(
            admitted.run_id,
            TerminalRunCommitCommand(
                lease_id=lease.lease_id,
                outcome=RunOutcome(
                    run_id=admitted.run_id, result="succeeded", certainty="known"
                ),
            ),
        )
        yield runtime, client, check, sources


def command(
    resource: Resource, check: CalibrationCheckRequest, selected: ProcedureSource | None
) -> tuple[str, dict[str, object]]:
    identity = "matrix-" + uuid4().hex
    if resource == "procedure":
        return "/api/v1/procedures", ProcedureSubmitCommand(
            definition=_DEFINITION, request_key=identity, intent={}, source=selected
        ).model_dump(mode="json")
    if resource == "schedule":
        return "/api/v1/procedure-schedules", ProcedureScheduleCreateCommand(
            schedule_id=identity,
            definition=_DEFINITION,
            intent={},
            due_at=datetime(2020, 1, 1, tzinfo=UTC),
            source=selected,
        ).model_dump(mode="json")
    return "/api/v1/calibration-tasks", _task(check).model_copy(
        update={"source": selected, "task_id": identity}
    ).model_dump(mode="json")


def retained_id(resource: Resource, payload: dict[str, object]) -> str:
    return str(payload["schedule_id" if resource == "schedule" else "task_id"])


@pytest.mark.parametrize("resource", ["procedure", "schedule", "calibration_task"])
@pytest.mark.parametrize("request_identity", [None, "one", "two"])
@pytest.mark.parametrize("selected_source", [None, "one", "two"])
@pytest.mark.parametrize("replay", [False, True])
def test_http_request_source_create_and_replay_matrix(
    case: Case,
    resource: Resource,
    request_identity: str | None,
    selected_source: str | None,
    replay: bool,
) -> None:
    runtime, client, check, sources = case
    path, payload = command(
        resource, check, sources.get(selected_source) if selected_source else None
    )
    allowed = selected_source is None or request_identity == selected_source
    sqlite = runtime.application.calibration_tasks._sqlite
    queries = {
        "procedure": "SELECT COUNT(*) FROM procedure_runs",
        "schedule": "SELECT COUNT(*) FROM procedure_schedules",
        "calibration_task": "SELECT COUNT(*) FROM calibration_tasks",
    }
    expected_status = 200 if resource == "calibration_task" else 201
    identity: str | None = None
    if replay:
        seed_workspace = request_identity if allowed else selected_source
        seed = client.post(
            path,
            json=payload,
            headers={"X-Scopecat-Workspace": seed_workspace} if seed_workspace else {},
        )
        assert seed.status_code == expected_status, seed.text
        identity = (
            str(seed.json()["run"]["procedure_run_id"])
            if resource == "procedure"
            else retained_id(resource, payload)
        )
    with sqlite.read_transaction() as connection:
        before = connection.execute(queries[resource]).fetchone()[0]
    response = client.post(
        path,
        json=payload,
        headers={"X-Scopecat-Workspace": request_identity} if request_identity else {},
    )
    if not allowed:
        assert response.status_code == 409, response.text
        assert "workspace" in response.text.lower()
        with sqlite.read_transaction() as connection:
            assert connection.execute(queries[resource]).fetchone()[0] == before
            if identity is not None:
                assert (
                    PracticeOwnership(connection).owner(resource, identity)
                    == selected_source
                )
        return
    assert response.status_code == expected_status, response.text
    identity = (
        str(response.json()["run"]["procedure_run_id"])
        if resource == "procedure"
        else retained_id(resource, payload)
    )
    repeated = client.post(
        path,
        json=payload,
        headers={"X-Scopecat-Workspace": request_identity} if request_identity else {},
    )
    assert repeated.status_code == expected_status, repeated.text
    assert repeated.json() == response.json()
    with sqlite.read_transaction() as connection:
        assert (
            PracticeOwnership(connection).owner(resource, identity) == request_identity
        )


@pytest.mark.parametrize("resource", ["procedure", "schedule", "calibration_task"])
@pytest.mark.parametrize("selected_source", ["one", "two"])
def test_no_request_internal_source_creation_and_replay(
    case: Case, resource: Resource, selected_source: str
) -> None:
    runtime, _, check, sources = case
    assert request_workspace not in copy_context()
    _, payload = command(
        resource, check, sources.get(selected_source) if selected_source else None
    )
    app = runtime.application
    if resource == "procedure":
        receipt = app.automation.submit(ProcedureSubmitCommand.model_validate(payload))
        assert (
            app.automation.submit(ProcedureSubmitCommand.model_validate(payload))
            == receipt
        )
        identity = receipt.run.procedure_run_id
    elif resource == "schedule":
        receipt = app.procedure_schedules.create(
            ProcedureScheduleCreateCommand.model_validate(payload)
        )
        assert (
            app.procedure_schedules.create(
                ProcedureScheduleCreateCommand.model_validate(payload)
            )
            == receipt
        )
        identity = receipt.schedule.schedule_id
    else:
        from scopecat.daemon.calibration_tasks import CalibrationTaskCreate

        specification = CalibrationTaskCreate.model_validate(payload)
        receipt = app.calibration_tasks.create(specification)
        assert app.calibration_tasks.create(specification) == receipt
        identity = specification.task_id
    with app.calibration_tasks._sqlite.read_transaction() as connection:
        assert (
            PracticeOwnership(connection).owner(resource, identity) == selected_source
        )


def test_profile_catalog_pages_never_cross_course_owners(case: Case) -> None:
    _, client, check, _ = case
    for index in range(3):
        for workspace in (None, "one", "two"):
            profile = CalibrationProfile(
                id=f"{workspace or 'ordinary'}-{index}",
                requirements=(
                    CalibrationRequirement(
                        id="check", scope=check.scope, max_age=timedelta(hours=1)
                    ),
                ),
            )
            response = client.post(
                "/api/v1/calibration-profiles",
                json=profile.model_dump(mode="json"),
                headers={"X-Scopecat-Workspace": workspace} if workspace else {},
            )
            assert response.status_code == 200, response.text
    for workspace in (None, "one", "two", "empty"):
        headers = {"X-Scopecat-Workspace": workspace} if workspace else {}
        ids: list[str] = []
        cursor: int | None = None
        while True:
            response = client.get(
                "/api/v1/calibration-profiles",
                params={"limit": 1, **({"cursor": cursor} if cursor else {})},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            page = response.json()
            ids.extend(item["profile"]["id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert ids == (
            []
            if workspace == "empty"
            else [f"{workspace or 'ordinary'}-{index}" for index in reversed(range(3))]
        )


@pytest.mark.parametrize("resource", ["procedure", "schedule", "calibration_task"])
def test_named_ordinary_source_stays_ordinary_and_requires_matching_request(
    case: Case, resource: Resource
) -> None:
    runtime, client, check, sources = case
    path, payload = command(resource, check, sources["ordinary"])
    response = client.post(
        path, json=payload, headers={"X-Scopecat-Workspace": "ordinary"}
    )
    assert response.status_code == (200 if resource == "calibration_task" else 201), (
        response.text
    )
    assert (
        client.post(
            path, json=payload, headers={"X-Scopecat-Workspace": "ordinary"}
        ).json()
        == response.json()
    )
    identity = (
        str(response.json()["run"]["procedure_run_id"])
        if resource == "procedure"
        else retained_id(resource, payload)
    )
    for mismatch in (None, "one", "two"):
        assert (
            client.post(
                path,
                json=payload,
                headers={"X-Scopecat-Workspace": mismatch} if mismatch else {},
            ).status_code
            == 409
        )
    with runtime.application.calibration_tasks._sqlite.read_transaction() as connection:
        assert PracticeOwnership(connection).owner(resource, identity) is None


def test_managed_ordinary_source_worker_preserves_source_request_identity(
    case: Case, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx2
    from scopecat.api.lab import LabClient
    from scopecat.application.lab import LabApplication
    from scopecat.automation import ProcedureRegistry
    from scopecat.daemon.client import DaemonClient
    from scopecat.project import Project, open_project
    from scopecat.records.author_revision import AuthorRevisionRef

    from scopecat_server import launch_worker

    from .test_procedure_schedule_runtime import (
        _scheduled_worker_procedure,
        _ScheduledWorkerIntent,
    )

    runtime, transport, _, sources = case
    registered = sources["ordinary"]
    root = runtime.application.author_workspaces.get("ordinary").root
    response = transport.post(
        "/api/v1/procedures",
        headers={"X-Scopecat-Workspace": "ordinary"},
        json=ProcedureSubmitCommand(
            definition=_scheduled_worker_procedure.ref,
            request_key="ordinary-managed-" + uuid4().hex,
            intent={"output_run_id": "ordinary-parent-output"},
            source=registered,
        ).model_dump(mode="json"),
    )
    assert response.status_code == 201, response.text
    parent = response.json()["run"]["procedure_run_id"]
    registry = ProcedureRegistry((_scheduled_worker_procedure,))

    def application(_project: Project) -> LabApplication:
        return LabApplication(procedures=registry)

    def revision(path: Path, _ref: AuthorRevisionRef) -> Project:
        return open_project(path)

    def endpoint(_root: Path) -> str:
        return "http://testserver"

    monkeypatch.setattr(Project, "load_application", application)
    monkeypatch.setattr(launch_worker, "revision_project", revision)
    monkeypatch.setattr(launch_worker, "resolve_daemon_endpoint", endpoint)
    original_init = DaemonClient.__init__

    def send(request: httpx2.Request) -> httpx2.Response:
        result = transport.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx2.Response(
            result.status_code, content=result.content, headers=dict(result.headers)
        )

    def connected(
        self: DaemonClient, base_url: str, *, workspace_id: str | None = None
    ) -> None:
        original_init(
            self,
            base_url,
            workspace_id=workspace_id,
            transport=httpx2.MockTransport(send),
        )

    monkeypatch.setattr(DaemonClient, "__init__", connected)
    run = launch_worker.run_procedure
    nested: list[str] = []

    def exercise(lab: LabClient, identity: str) -> None:
        nested.append(
            lab.procedures.submit(
                _scheduled_worker_procedure,
                _ScheduledWorkerIntent(output_run_id="ordinary-child-output"),
                request_key="ordinary-nested-" + uuid4().hex,
            ).id
        )
        run(lab, identity)

    monkeypatch.setattr(launch_worker, "run_procedure", exercise)
    launch_worker.run_project_procedure(root, parent)
    assert runtime.application.automation.get(parent).state == "closed"
    assert runtime.application.automation.get(nested[0]).source == registered
    with runtime.application.calibration_tasks._sqlite.read_transaction() as connection:
        assert PracticeOwnership(connection).owner("procedure", parent) is None
        assert PracticeOwnership(connection).owner("procedure", nested[0]) is None


@pytest.mark.parametrize("replay", [False, True])
def test_bound_ordinary_source_less_procedure_cannot_borrow_course_setup(
    case: Case, replay: bool
) -> None:
    from scopecat.records.configuration_fence import SetupRevisionFence

    from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository

    runtime, client, _, _ = case
    with runtime.application.calibration_tasks._sqlite.read_transaction() as connection:
        setup = SQLiteSetupRepository(connection).read_revision("owned-fence-one")
    command = ProcedureSubmitCommand(
        definition=_DEFINITION,
        request_key="borrow-fence-" + uuid4().hex,
        intent={},
        expected_configuration=SetupRevisionFence(revision=setup.ref),
    )
    payload = command.model_dump(mode="json")
    if replay:
        seeded = client.post(
            "/api/v1/procedures", json=payload, headers={"X-Scopecat-Workspace": "one"}
        )
        assert seeded.status_code == 201, seeded.text
    refused = client.post("/api/v1/procedures", json=payload)
    assert refused.status_code == 409, refused.text
    matching = client.post(
        "/api/v1/procedures", json=payload, headers={"X-Scopecat-Workspace": "one"}
    )
    assert matching.status_code == 201, matching.text
    assert (
        client.post(
            "/api/v1/procedures", json=payload, headers={"X-Scopecat-Workspace": "one"}
        ).json()
        == matching.json()
    )
    assert request_workspace not in copy_context()
    internal = runtime.application.automation.submit(
        command.model_copy(update={"request_key": "internal-fence-" + uuid4().hex})
    )
    with runtime.application.calibration_tasks._sqlite.read_transaction() as connection:
        assert (
            PracticeOwnership(connection).owner(
                "procedure", internal.run.procedure_run_id
            )
            == "one"
        )


@pytest.mark.parametrize("request_identity", [None, "one", "two"])
@pytest.mark.parametrize("replay", [False, True])
def test_project_analysis_admission_matches_request_owner(
    case: Case, request_identity: str | None, replay: bool
) -> None:
    from scopecat.daemon.wire import (
        AnalysisFactOutputPayload,
        AnalysisSaveCommand,
        ConfigurationAnalysisInputPayload,
    )
    from scopecat.records.analysis import (
        CONFIGURATION_ANALYSIS_INPUT_CODEC,
        AnalysisFact,
    )
    from scopecat.runs.refs import CONFIG_PROFILE_SNAPSHOT_REF
    from scopecat.sdk.compute import PYTHON_JSON_CODEC

    runtime, client, _, _ = case
    run = runtime.application.runs.find_run_by_submission_id("analysis-fixture")
    assert run is not None
    view = runtime.application.runs.get_run_config(run.run_id)
    command = AnalysisSaveCommand(
        title="Owner policy",
        analysis_key="owned-analysis-" + uuid4().hex,
        inputs=(
            ConfigurationAnalysisInputPayload(
                id="configuration",
                run_id=run.run_id,
                target=CONFIG_PROFILE_SNAPSHOT_REF,
                content_hash=view.config_content_hash,
                codec=CONFIGURATION_ANALYSIS_INPUT_CODEC,
                role="configuration",
            ),
        ),
        outputs=(
            AnalysisFactOutputPayload(
                kind="fact",
                id="result",
                title="Result",
                content=AnalysisFact(
                    schema_id="tests.owner.v1",
                    schema_codec="scopecat.analysis-fact-schema.v1",
                    schema_hash=_HASH,
                    codec=PYTHON_JSON_CODEC,
                    value={"ok": True},
                ),
            ),
        ),
    )
    payload = command.model_dump(mode="json")
    if replay:
        seeded = client.post(
            "/api/v1/analyses", json=payload, headers={"X-Scopecat-Workspace": "one"}
        )
        assert seeded.status_code == 201, seeded.text
    sqlite = runtime.application.calibration_tasks._sqlite
    with sqlite.read_transaction() as connection:
        before = PracticeOwnership(connection).resources("one", "analysis")
    response = client.post(
        "/api/v1/analyses",
        json=payload,
        headers={"X-Scopecat-Workspace": request_identity} if request_identity else {},
    )
    if request_identity != "one":
        assert response.status_code == 409, response.text
        with sqlite.read_transaction() as connection:
            assert PracticeOwnership(connection).resources("one", "analysis") == before
    else:
        assert response.status_code == 201, response.text
        repeated = client.post(
            "/api/v1/analyses", json=payload, headers={"X-Scopecat-Workspace": "one"}
        )
        assert repeated.status_code == 201, repeated.text
        assert repeated.json() == response.json()
        with sqlite.read_transaction() as connection:
            assert (
                PracticeOwnership(connection).owner(
                    "analysis", response.json()["record"]["id"]
                )
                == "one"
            )


@pytest.mark.parametrize("request_identity", [None, "one", "two"])
@pytest.mark.parametrize("copy", [False, True])
def test_plan_creation_and_copy_match_request_owner(
    case: Case, request_identity: str | None, copy: bool
) -> None:
    from scopecat.config.scientific_binding import bind_scientific_evidence
    from scopecat.daemon.wire import ParameterResolveCommand
    from scopecat.records.experiment_plan import (
        ExperimentPlanDefinition,
        ExperimentPlanSave,
    )
    from scopecat.records.scientific_selection import (
        ParameterConfiguration,
        ScientificSelection,
    )

    from scopecat_server.storage.sqlite.parameter_revisions import (
        ParameterRevisionRepository,
    )
    from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository

    runtime, client, _, _ = case
    sqlite = runtime.application.calibration_tasks._sqlite
    with sqlite.read_transaction() as connection:
        setup = SQLiteSetupRepository(connection).read_revision("owned-fence-one")
        parameters = ParameterRevisionRepository(connection).get("owned-parameters-one")
    resolved = runtime.application.config.resolve_parameters(
        ParameterResolveCommand(parameters=parameters.ref, setup=setup.ref)
    )
    binding = bind_scientific_evidence(
        catalog_id=runtime.application.project_id,
        config=resolved.config,
        samples=(),
        sample_revisions={},
    )
    command = ExperimentPlanSave(
        name="Owner policy",
        saved_by="test",
        definition=ExperimentPlanDefinition(
            workspace_id="one",
            experiment="signal",
            version="1",
            definition_hash=_HASH,
            selection=ScientificSelection(
                configuration=ParameterConfiguration(
                    setup=setup.ref, ref=parameters.ref
                )
            ),
            scientific_binding=binding,
        ),
    )
    if copy:
        parent = runtime.application.plans.save(command)
        command = command.model_copy(update={"copied_from": parent.ref})
    with sqlite.read_transaction() as connection:
        before = PracticeOwnership(connection).resources("one", "plan")
    response = client.post(
        "/api/v1/experiment-plans",
        json=command.model_dump(mode="json"),
        headers={"X-Scopecat-Workspace": request_identity} if request_identity else {},
    )
    if request_identity != "one":
        assert response.status_code == 409, response.text
        with sqlite.read_transaction() as connection:
            assert PracticeOwnership(connection).resources("one", "plan") == before
    else:
        assert response.status_code == 200, response.text
        with sqlite.read_transaction() as connection:
            assert (
                PracticeOwnership(connection).owner(
                    "plan", response.json()["ref"]["plan_id"]
                )
                == "one"
            )


@pytest.mark.parametrize("foreign_inputs", [False, True])
def test_internal_task_stage_keeps_retained_task_owner(
    case: Case, monkeypatch: pytest.MonkeyPatch, foreign_inputs: bool
) -> None:
    from scopecat.daemon.calibration_tasks import CalibrationTaskControl
    from scopecat.records.calibration_check import CalibrationScope
    from scopecat.records.measurement_context import MeasurementContext

    from scopecat_server.services.project_workers import ProjectProcedureWorkers
    from scopecat_server.storage.sqlite.parameter_revisions import (
        ParameterRevisionRepository,
    )
    from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository

    runtime, client, ordinary_check, _ = case

    def manage(_self: ProjectProcedureWorkers, _identity: str) -> None:
        pass  # This case validates stage admission, not executing authored code.

    monkeypatch.setattr(ProjectProcedureWorkers, "manage", manage)
    sqlite = runtime.application.calibration_tasks._sqlite
    selected = ordinary_check
    if not foreign_inputs:
        with sqlite.read_transaction() as connection:
            setup = SQLiteSetupRepository(connection).read_revision("owned-fence-one")
            parameters = ParameterRevisionRepository(connection).get(
                "owned-parameters-one"
            )
        retained = runtime.application.runs.find_run_by_submission_id(
            "analysis-fixture"
        )
        assert retained is not None and retained.snapshot.scientific_binding is not None
        binding = retained.snapshot.scientific_binding
        selected = ordinary_check.model_copy(
            update={
                "setup": setup.ref,
                "context": MeasurementContext(
                    parameters.ref,
                    binding.subject,
                    binding.setup_content_hash,
                    binding.scenario,
                ),
                "scope": CalibrationScope("synthetic", ("subject",), "software", "1"),
            }
        )
    specification = _task(selected).model_copy(
        update={"task_id": "task-stage-owner-" + uuid4().hex}
    )
    if not foreign_inputs:
        specification = specification.model_copy(
            update={
                "calls": {
                    key: call.model_copy(update={"samples": ()})
                    for key, call in specification.calls.items()
                }
            }
        )
    created = client.post(
        "/api/v1/calibration-tasks",
        json=specification.model_dump(mode="json"),
        headers={"X-Scopecat-Workspace": "one"},
    )
    assert created.status_code == 200, created.text
    start = client.post(
        "/api/v1/calibration-tasks/control",
        json=CalibrationTaskControl(
            task_id=specification.task_id,
            expected_revision=created.json()["task"]["control_revision"],
            action="start",
            actor="test",
            reason="owner replay",
        ).model_dump(mode="json"),
        headers={"X-Scopecat-Workspace": "one"},
    )
    assert start.status_code == 200, start.text
    assert request_workspace not in copy_context()
    view = runtime.application.calibration_tasks.advance(specification.task_id)
    if foreign_inputs:
        assert not view.task.executions
        assert view.task.dispatch_errors
    else:
        assert view.task.executions
        with sqlite.read_transaction() as connection:
            for identity in view.task.executions.values():
                assert (
                    PracticeOwnership(connection).owner("procedure", identity) == "one"
                )


@pytest.mark.parametrize("kind", ["analysis", "attachment"])
@pytest.mark.parametrize("request_identity", [None, "one", "two"])
@pytest.mark.parametrize("replay", [False, True])
def test_run_owned_publication_matches_request_owner(
    case: Case, kind: str, request_identity: str | None, replay: bool
) -> None:
    from scopecat.daemon.wire import (
        AnalysisFactOutputPayload,
        AnalysisSaveCommand,
        RunAttachmentCommand,
    )
    from scopecat.records.analysis import AnalysisFact
    from scopecat.sdk.compute import PYTHON_JSON_CODEC

    runtime, client, _, _ = case
    run = runtime.application.runs.find_run_by_submission_id("analysis-fixture")
    assert run is not None
    if kind == "analysis":
        payload = AnalysisSaveCommand(
            title="Run owner",
            analysis_key="run-policy-" + uuid4().hex,
            outputs=(
                AnalysisFactOutputPayload(
                    kind="fact",
                    id="result",
                    title="Result",
                    content=AnalysisFact(
                        schema_id="tests.owner.v1",
                        schema_codec="scopecat.analysis-fact-schema.v1",
                        schema_hash=_HASH,
                        codec=PYTHON_JSON_CODEC,
                        value=True,
                    ),
                ),
            ),
        ).model_dump(mode="json")
        path = f"/api/v1/runs/{run.run_id}/analyses"
    else:
        payload = RunAttachmentCommand(
            key="note-" + uuid4().hex, text="Owner policy"
        ).model_dump(mode="json")
        path = f"/api/v1/runs/{run.run_id}/attachments"
    if replay:
        seeded = client.post(
            path, json=payload, headers={"X-Scopecat-Workspace": "one"}
        )
        assert seeded.status_code == 201, seeded.text
    before = runtime.application.runs.list_run_contents(run.run_id).items
    response = client.post(
        path,
        json=payload,
        headers={"X-Scopecat-Workspace": request_identity} if request_identity else {},
    )
    if request_identity != "one":
        assert response.status_code == 409, response.text
        assert runtime.application.runs.list_run_contents(run.run_id).items == before
    else:
        assert response.status_code == 201, response.text
        assert (
            client.post(
                path, json=payload, headers={"X-Scopecat-Workspace": "one"}
            ).json()
            == response.json()
        )
