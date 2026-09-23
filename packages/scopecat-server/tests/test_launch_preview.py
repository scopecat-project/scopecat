from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from scopecat.application.launch import LaunchPreview
from scopecat.records.author_revision import AuthorRevisionRef, AuthorRevisionState
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.run import ConfigRegistryRunConfigSource
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.records.scientific_selection import ReviewedScientificSelection

from scopecat_server.http.transport import create_app
from scopecat_server.services.revision_workers import (
    AuthorWorkerBinding,
    RevisionWorkers,
)


def _submission_request() -> dict[str, object]:
    request = LaunchRequest(
        action="preview",
        experiment="diagnostic",
        version="1",
        reviewed=ReviewedScientificSelection(
            binding=ResolvedScientificBinding(
                subject=UnboundSubject(),
                config_content_hash="sha256:" + "a" * 64,
                setup_content_hash="sha256:" + "b" * 64,
            ),
            config_source=ConfigRegistryRunConfigSource(
                selector="active",
                entry_id="baseline",
                config_ref="baseline",
                content_hash="sha256:" + "a" * 64,
                registry_generation=1,
            ),
        ),
    )
    return request.model_copy(
        update={
            "action": "submit",
            "request_key": "one",
            "expected_request_hash": request.request_hash,
        }
    ).model_dump(mode="json")


if TYPE_CHECKING:
    from scopecat.api.lab import LabClient

    from scopecat_server.services.application import DaemonApplication


def _manual_previews() -> Mock:
    service = Mock()
    service.cursor.return_value = 0

    def record(preview: LaunchPreview, *, cursor: int) -> LaunchPreview:
        del cursor
        return preview

    service.record_preview.side_effect = record
    return service


def client(
    state: AuthorRevisionState | None = None,
    *,
    service: SimpleNamespace | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            cast(
                "DaemonApplication",
                cast(
                    "object",
                    SimpleNamespace(
                        project_root=Path.cwd(),
                        calibration_tasks=Mock(),
                        manual_previews=_manual_previews(),
                        author_revisions=service
                        or SimpleNamespace(
                            root=Path.cwd(),
                            state=lambda: state or AuthorRevisionState(),
                            get=Mock(),
                            worker_binding=AuthorWorkerBinding(
                                Path.cwd(), Path(sys.executable)
                            ),
                            workers=RevisionWorkers(),
                            close=Mock(),
                        ),
                    ),
                ),
            )
        )
    )


def test_catalog_runs_a_fixed_separate_worker() -> None:
    with patch("scopecat_server.http.transport.subprocess.run") as run:
        run.return_value = SimpleNamespace(
            returncode=0, stdout='{"entries": []}', stderr=""
        )
        response = client().get("/api/v1/experiment-launcher")
        assert response.json() == {
            "workspace_id": "legacy",
            "entries": [],
            "code_revision": None,
        }
        assert run.call_args.args[0][1:3] == [
            "-m",
            "scopecat_server.launch_worker",
        ]
        assert '"action":"list"' in run.call_args.kwargs["input"]
        assert run.call_args.kwargs["encoding"] == "utf-8"


def test_preview_failure_is_visible_and_start_is_not_supported() -> None:
    with patch("scopecat_server.http.transport.subprocess.run") as run:
        run.return_value = SimpleNamespace(
            returncode=1, stdout="", stderr="traceback\nValueError: bad target"
        )
        response = client().post(
            "/api/v1/experiment-launcher/preview",
            json={
                "action": "preview",
                "experiment": "rabi",
                "version": "1",
                "inputs": {},
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "ValueError: bad target"
        run.reset_mock()
        response = client().post(
            "/api/v1/experiment-launcher/preview", json={"action": "start"}
        )
        assert response.status_code == 422
        run.assert_not_called()


@pytest.mark.parametrize(
    ("action", "operation"),
    [("list", "catalog loading"), ("preview", "preview"), ("submit", "submission")],
)
def test_timeout_identifies_operation_without_retry(
    action: str, operation: str
) -> None:
    with patch(
        "scopecat_server.http.transport.subprocess.run",
        side_effect=subprocess.TimeoutExpired(
            "worker",
            60,
            stderr=b"Scopecat worker stage: author revision initialization\n",
        ),
    ) as run:
        if action == "list":
            response = client().get("/api/v1/experiment-launcher")
        elif action == "preview":
            response = client().post(
                "/api/v1/experiment-launcher/preview",
                json={"action": "preview", "experiment": "signal", "version": "1"},
            )
        else:
            response = client().post(
                "/api/v1/experiment-launcher/submit", json=_submission_request()
            )
        assert response.status_code == 504
        detail = response.json()["detail"]
        assert f"Experiment {operation} timed out after 60 seconds" in detail
        assert "author revision initialization" in detail
        assert run.call_count == 1
        assert run.call_args.kwargs["timeout"] == 60
        if action == "submit":
            assert "outcome is unknown" in detail
            assert "original request key" in detail
            assert '"request_key":"one"' in run.call_args.kwargs["input"]
        else:
            assert "No acquisition was submitted" in detail


def test_worker_loads_manifest_file_and_supports_empty_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    from scopecat_server import launch_worker

    monkeypatch.setattr("sys.argv", ["launch_worker", str(tmp_path)])
    monkeypatch.setattr("sys.stdin", io.StringIO('{"action":"list"}'))
    with patch.object(launch_worker, "load_project") as load:
        load.return_value.source_roots = ()
        load.return_value.load_application.return_value = SimpleNamespace(
            launch_provider=None
        )
        launch_worker.main()
        load.assert_called_once_with(tmp_path / "scopecat.toml")
    assert (
        capsys.readouterr().out.strip()
        == '{"workspace_id":"legacy","code_revision":null,"entries":[]}'
    )


@pytest.mark.parametrize("routing", [False, True])
def test_admission_survives_dispatch_failure(tmp_path: Path, routing: bool) -> None:
    from scopecat_server.services.project_workers import ProcedureDispatchError

    automation = SimpleNamespace(get=Mock(return_value=SimpleNamespace(state="ready")))
    app = create_app(
        cast(
            "DaemonApplication",
            cast(
                "object",
                SimpleNamespace(
                    project_root=tmp_path,
                    calibration_tasks=Mock(),
                    automation=automation,
                    author_revisions=SimpleNamespace(
                        root=Path.cwd(),
                        state=lambda: AuthorRevisionState(),
                        close=Mock(),
                    ),
                    manual_previews=_manual_previews(),
                ),
            ),
        )
    )
    with (
        patch("scopecat_server.http.transport.subprocess.run") as run,
        patch(
            "scopecat_server.http.transport.read_procedure_operator",
            return_value=SimpleNamespace(dispatch_blocked_reason=None),
        ),
        patch(
            "scopecat_server.http.transport.ProjectProcedureWorkers.dispatch",
            side_effect=ProcedureDispatchError("cannot spawn")
            if routing
            else OSError("cannot spawn"),
        ),
    ):
        run.return_value = SimpleNamespace(
            returncode=0, stdout='{"procedure_id":"p1"}', stderr=""
        )
        result = TestClient(app).post(
            "/api/v1/experiment-launcher/submit",
            json=_submission_request(),
        )
    assert result.status_code == 200
    assert result.json() == {"procedure_id": "p1", "dispatch_error": "cannot spawn"}


@pytest.mark.parametrize("failure", ["route", "spawn", "state"])
def test_worker_failure_isolated_and_not_attributed_to_another_dispatch(
    tmp_path: Path,
    failure: str,
) -> None:
    from scopecat_server.services.project_workers import (
        ProcedureDispatchError,
        ProjectProcedureWorkers,
    )

    def state(key: str) -> str:
        if key == "broken" and failure == "state":
            raise ValueError("missing procedure")
        return "ready"

    def root(key: str) -> Path:
        if key == "broken" and failure == "route":
            raise ValueError("missing author workspace")
        return tmp_path

    manager = ProjectProcedureWorkers(lambda: tmp_path, state, resolve_root=root)
    manager.manage("broken")
    child = Mock()
    child.poll.return_value = None

    def spawn(args: list[str], **_kwargs: object) -> Mock:
        if args[-1] == "broken" and failure == "spawn":
            raise OSError("cannot spawn")
        return child

    with patch(
        "scopecat_server.services.project_workers.subprocess.Popen", side_effect=spawn
    ) as launch:
        # Dispatching a healthy procedure also encounters the previously queued
        # broken one. Only the broken one pauses; the healthy caller succeeds.
        manager.dispatch("healthy")
        assert manager.snapshot("broken").management == "paused"
        failure_view = manager.snapshot("broken").failure
        assert failure_view is not None and failure_view.kind == "dispatch"
        assert manager.snapshot("healthy").worker_running
        count = launch.call_count
        manager.manage("broken")
        manager.tick()
        assert launch.call_count == count
        with pytest.raises(ProcedureDispatchError):
            manager.dispatch("broken")
    restored = ProjectProcedureWorkers(lambda: tmp_path, state, resolve_root=root)
    assert restored.snapshot("broken").failure is not None
    with patch.object(restored, "_spawn") as launch:
        restored.tick()
    assert all(call.args[0] != "broken" for call in launch.call_args_list)


def test_background_handoffs_scan_workers_once_per_tick(tmp_path: Path) -> None:
    from scopecat_server.services.project_workers import ProjectProcedureWorkers

    state = Mock(return_value="waiting_for_input")
    manager = ProjectProcedureWorkers(lambda: tmp_path, state)
    for index in range(40):
        manager.manage(f"p{index}")
    state.assert_not_called()
    manager.tick()
    assert state.call_count == 40


def test_worker_logs_are_per_execution_and_preserve_previous_output(
    tmp_path: Path,
) -> None:
    from scopecat_server.services.project_workers import ProjectProcedureWorkers

    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
    previous = manager._path().parent / "console-worker.log"
    previous.parent.mkdir(parents=True, exist_ok=True)
    previous.write_text("retained old output", encoding="utf-8")
    child = Mock()
    child.poll.return_value = None
    with patch(
        "scopecat_server.services.project_workers.subprocess.Popen", return_value=child
    ):
        manager.dispatch("first/experiment")
        manager.dispatch("second/experiment")
    first = manager.snapshot("first/experiment").log_path
    second = manager.snapshot("second/experiment").log_path
    assert first is not None and second is not None and first != second
    assert Path(first).is_file() and Path(second).is_file()
    assert previous.read_text(encoding="utf-8") == "retained old output"


def test_worker_log_read_is_bounded_and_cannot_select_another_file(
    tmp_path: Path,
) -> None:
    from scopecat_server.services.project_workers import ProjectProcedureWorkers

    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
    assert not manager.read_log("p1", 16).available
    path = manager._worker_dir("p1") / "worker.log"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"older output\n" + "新输出".encode())
    tail = manager.read_log("p1", 9)
    assert tail.available and tail.truncated
    assert tail.text == "新输出"
    assert tail.total_bytes == path.stat().st_size
    # Cutting a multibyte character is rendered with replacement, never a 500.
    assert manager.read_log("p1", 1).text == "\ufffd"
    assert not manager.read_log("../../p1", 16).available
    path.write_bytes(b"")
    empty = manager.read_log("p1", 16)
    assert empty.available and empty.text == "" and not empty.truncated


def test_worker_log_route_validates_identity_and_read_budget() -> None:
    from scopecat.daemon.procedure_views import ProcedureWorkerLog

    from scopecat_server import BackendNotFound

    application = SimpleNamespace(
        automation=SimpleNamespace(
            get=Mock(side_effect=BackendNotFound("missing procedure"))
        )
    )
    with patch("scopecat_server.http.transport.ProjectProcedureWorkers") as manager:
        app = create_app(cast("DaemonApplication", cast("object", application)))
        reader = TestClient(app)
        for budget in (0, 65537):
            assert (
                reader.get(
                    f"/api/v1/procedures/p1/worker-log?max_bytes={budget}"
                ).status_code
                == 422
            )
        assert reader.get("/api/v1/procedures/p1/worker-log").status_code == 404
        manager.return_value.read_log.assert_not_called()
        application.automation.get.side_effect = None
        manager.return_value.read_log.return_value = ProcedureWorkerLog(
            available=False,
            text="",
            total_bytes=0,
            truncated=False,
        )
        assert (
            reader.get("/api/v1/procedures/p1/worker-log?max_bytes=65536").status_code
            == 200
        )
        manager.return_value.read_log.assert_called_once_with("p1", 65536)


def test_dispatch_deduplicates_live_workers(tmp_path: Path) -> None:
    from scopecat_server.services.project_workers import ProjectProcedureWorkers

    workers = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
    child = Mock()
    child.poll.return_value = None
    with patch(
        "scopecat_server.services.project_workers.subprocess.Popen", return_value=child
    ) as spawn:
        workers.dispatch("p1")
        workers.dispatch("p1")
        spawn.assert_called_once()
        child.poll.return_value = 0
        workers.dispatch("p1")
        assert spawn.call_count == 2


def test_worker_exits_at_review_without_polling() -> None:
    from scopecat_server.launch_worker import run_procedure

    handle = SimpleNamespace(state="waiting_for_input", resume=Mock())
    lab = SimpleNamespace(procedures=SimpleNamespace(get=Mock(return_value=handle)))
    run_procedure(cast("LabClient", cast("object", lab)), "p1")
    handle.resume.assert_not_called()
    lab.procedures.get.assert_called_once_with("p1")
    handle.state = "ready"
    run_procedure(cast("LabClient", cast("object", lab)), "p1")
    handle.resume.assert_called_once()


@pytest.mark.parametrize(
    "waiting_state", ["waiting_for_input", "waiting_for_resources"]
)
def test_manager_recovers_waiting_members_and_bounds_processes(
    tmp_path: Path, waiting_state: str
) -> None:
    from scopecat_server.services.project_workers import ProjectProcedureWorkers

    states = {"p1": waiting_state, "p2": "ready"}
    manager = ProjectProcedureWorkers(
        lambda: tmp_path, states.__getitem__, max_workers=1
    )
    with patch("scopecat_server.services.project_workers.subprocess.Popen") as spawn:
        manager.dispatch("p1")
        spawn.assert_not_called()
        restored = ProjectProcedureWorkers(
            lambda: tmp_path, states.__getitem__, max_workers=1
        )
        states["p1"] = "ready"
        first, second = Mock(), Mock()
        first.poll.return_value = second.poll.return_value = None
        spawn.side_effect = [first, second]
        restored.tick()
        restored.dispatch("p2")
        assert spawn.call_count == 1
        states["p1"] = "closed"
        first.poll.return_value = 0
        restored.tick()
        assert spawn.call_count == 2
        assert spawn.call_args.args[0][-1] == "p2"


def test_failed_process_requires_explicit_dispatch_even_after_restart(
    tmp_path: Path,
) -> None:
    from scopecat_server.services.project_workers import ProjectProcedureWorkers

    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
    child = Mock()
    child.poll.return_value = None
    with patch(
        "scopecat_server.services.project_workers.subprocess.Popen", return_value=child
    ) as spawn:
        manager.dispatch("p1")
        child.poll.return_value = 1
        manager.tick()
        restored = ProjectProcedureWorkers(lambda: tmp_path, lambda _: "ready")
        failure = restored.snapshot("p1").failure
        assert failure is not None and failure.exit_code == 1
        assert restored.snapshot("p1").log_path is not None
        restored.tick()
        restored.manage("p1")  # task handoff must not undo the failed-worker pause
        spawn.assert_called_once()
        child.poll.return_value = None
        restored.dispatch("p1")
        assert spawn.call_count == 2
        assert restored.snapshot("p1").failure is None


def test_review_arriving_before_previous_worker_exit_is_not_lost(
    tmp_path: Path,
) -> None:
    from scopecat_server.services.project_workers import ProjectProcedureWorkers

    state = ["ready"]
    manager = ProjectProcedureWorkers(lambda: tmp_path, lambda _: state[0])
    child = Mock()
    child.poll.return_value = None
    with patch(
        "scopecat_server.services.project_workers.subprocess.Popen", return_value=child
    ) as spawn:
        manager.dispatch("p1")
        state[0] = "waiting_for_input"
        manager.tick()
        state[0] = "ready"
        manager.tick()
        spawn.assert_called_once()
        child.poll.return_value = 0
        manager.tick()
        assert spawn.call_count == 2


def test_manager_drops_terminal_and_attention_procedures(tmp_path: Path) -> None:
    from scopecat_server.services.project_workers import ProjectProcedureWorkers

    states = {"closed": "waiting_for_input", "attention": "waiting_for_input"}
    manager = ProjectProcedureWorkers(lambda: tmp_path, states.__getitem__)
    for key in states:
        manager.dispatch(key)
    states.update(closed="closed", attention="attention_required")
    manager.tick()
    # Durable cancellation is represented by closed state; neither it nor
    # attention may be restarted just because the daemon restarts.
    restarted = ProjectProcedureWorkers(lambda: tmp_path, states.__getitem__)
    with patch.object(restarted, "_spawn", Mock()) as spawn:
        restarted.tick()
        spawn.assert_not_called()
    assert restarted._load() == {}


def test_http_lifespan_starts_and_stops_manager() -> None:
    with (
        patch("scopecat_server.http.transport.ProjectProcedureWorkers") as manager,
        patch("scopecat_server.http.transport.CalibrationTaskRunner") as tasks,
    ):
        with TestClient(
            create_app(
                cast(
                    "DaemonApplication",
                    cast(
                        "object",
                        SimpleNamespace(
                            project_root=Path.cwd(),
                            calibration_tasks=Mock(),
                            manual_previews=_manual_previews(),
                            author_revisions=SimpleNamespace(
                                root=Path.cwd(),
                                state=lambda: AuthorRevisionState(),
                                close=Mock(),
                            ),
                        ),
                    ),
                )
            )
        ):
            manager.return_value.start.assert_called_once()
            tasks.return_value.start.assert_called_once()
            manager.return_value.stop.assert_not_called()
        manager.return_value.stop.assert_called_once()
        tasks.return_value.stop.assert_called_once()


def test_catalog_rejects_a_submission_shaped_worker_result() -> None:
    from pydantic import ValidationError

    with patch("scopecat_server.http.transport.subprocess.run") as run:
        run.return_value = SimpleNamespace(
            returncode=0, stdout='{"procedure_id":"wrong-action"}', stderr=""
        )
        with pytest.raises(ValidationError):
            client().get("/api/v1/experiment-launcher")


def test_worker_json_protocol_is_utf8_under_ascii_process_defaults(
    tmp_path: Path,
) -> None:
    script = """
import contextlib
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import patch
from scopecat.application.launch import (
    LaunchCatalog, LaunchCatalogEntry, LaunchInputSchema,
)

def provider(lab, request):
    assert request.actor == "操作者 → μ"
    print(request.actor)
    return LaunchCatalog(entries=(LaunchCatalogEntry(
        id="diagnostic", version="1", title=request.actor,
        description="测量 → 结果", actions=("preview",), kind="diagnostic",
        configuration_effect="none", request=LaunchInputSchema(),
    ),))

with (
    patch("scopecat.project.load_project") as load,
    patch("scopecat.daemon.endpoint.resolve_daemon_endpoint", return_value="http://unused"),
):
    load.return_value.source_roots = ()
    load.return_value.load_application.return_value = SimpleNamespace(
        launch_provider=provider,
        connect=lambda *_args, **_kwargs: contextlib.nullcontext(None)
    )
    sys.argv = ["launch_worker", sys.argv[1]]
    runpy.run_module("scopecat_server.launch_worker", run_name="__main__")
"""
    result = subprocess.run(  # noqa: S603 - fixed test interpreter and script
        [sys.executable, "-c", script, str(tmp_path)],
        input=LaunchRequest(action="list", actor="操作者 → μ").model_dump_json(),
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
        check=True,
        timeout=15,
    )
    from scopecat.application.launch import LaunchCatalog

    catalog = LaunchCatalog.model_validate_json(result.stdout)
    assert catalog.entries[0].title == "操作者 → μ"
    assert catalog.entries[0].description == "测量 → 结果"
    assert result.stderr.splitlines() == [
        "Scopecat worker stage: project application load",
        "Scopecat worker stage: launch provider",
        "操作者 → μ",
    ]


def test_worker_rejects_undeclared_control_edits_before_provider_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    from scopecat.application.launch import (
        LaunchCatalog,
        LaunchCatalogEntry,
        LaunchInputSchema,
    )

    from scopecat_server import launch_worker

    provider = Mock(
        return_value=LaunchCatalog(
            entries=(
                LaunchCatalogEntry(
                    id="legacy",
                    version="1",
                    title="Legacy",
                    description="No declared controls",
                    actions=("preview",),
                    kind="diagnostic",
                    configuration_effect="none",
                    request=LaunchInputSchema(properties={}),
                ),
            )
        )
    )
    monkeypatch.setattr("sys.argv", ["launch_worker", str(tmp_path)])
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            '{"action":"preview","experiment":"legacy","version":"1",'
            '"control_edits":{"frequency":{"mode":"fixed","value":5.0}}}'
        ),
    )
    with (
        patch.object(launch_worker, "load_project") as load,
        patch.object(
            launch_worker, "resolve_daemon_endpoint", return_value="http://unused"
        ),
    ):
        load.return_value.source_roots = ()
        load.return_value.load_application.return_value = SimpleNamespace(
            launch_provider=provider,
            connect=Mock(return_value=contextlib.nullcontext(None)),
        )
        launch_worker.main()
    from scopecat.records.launch_rejection import LaunchRejection

    rejection = LaunchRejection.model_validate_json(capsys.readouterr().out)
    assert "unknown control" in rejection.message
    assert rejection.problems == ()
    assert rejection.scenario is None
    assert provider.call_count == 1
    assert provider.call_args.args[1].action == "list"


def test_author_submit_requires_preview_revision_before_dispatch() -> None:
    state = AuthorRevisionState(
        enabled=True, active=AuthorRevisionRef(content_hash="sha256:" + "a" * 64)
    )
    with (
        patch("scopecat_server.http.transport.subprocess.run") as run,
        patch("scopecat_server.http.transport.RevisionWorkers.call") as pooled,
    ):
        response = client(state).post(
            "/api/v1/experiment-launcher/submit", json=_submission_request()
        )
    assert response.status_code == 422
    assert (
        "submit requires the preview's author code revision"
        in response.json()["detail"]
    )
    run.assert_not_called()
    pooled.assert_not_called()


@pytest.mark.parametrize("failure", ["preparation", "revision_owner"])
def test_source_selection_failure_does_not_dispatch(failure: str) -> None:
    ref = AuthorRevisionRef(content_hash="sha256:" + "a" * 64)
    service = SimpleNamespace(
        state=Mock(return_value=AuthorRevisionState(enabled=True, active=ref)),
        get=Mock(),
    )
    if failure == "preparation":
        detail = "Author preparation failed during application import"
        service.state.side_effect = ValueError(detail)
    else:
        detail = "Source revision does not belong to the selected author workspace"
        service.get.side_effect = ValueError(detail)
    with (
        patch("scopecat_server.http.transport.subprocess.run") as run,
        patch("scopecat_server.http.transport.RevisionWorkers.call") as pooled,
    ):
        response = client(service=service).get("/api/v1/experiment-launcher")
    assert response.status_code == 422
    assert response.json()["detail"] == detail
    run.assert_not_called()
    pooled.assert_not_called()
    if failure == "preparation":
        service.get.assert_not_called()
    else:
        service.get.assert_called_once_with(ref)


def test_pinned_catalog_uses_pool_and_exposes_nested_timing() -> None:
    ref = AuthorRevisionRef(content_hash="sha256:" + "a" * 64)
    with patch("scopecat_server.http.transport.RevisionWorkers.call") as call:
        call.return_value = subprocess.CompletedProcess(
            "worker",
            0,
            '{"entries": []}',
            'Scopecat launch timing: {"provider": 0.002, "worker": 0.003}\n',
        )
        response = client(AuthorRevisionState(enabled=True, active=ref)).get(
            "/api/v1/experiment-launcher"
        )
        assert response.status_code == 200
        assert call.call_args.args[1].code_revision == ref
        assert 0 < call.call_args.kwargs["timeout"] <= 60
        assert "provider;dur=2.000" in response.headers["server-timing"]
        assert "worker;dur=3.000" in response.headers["server-timing"]


def test_request_rejection_is_reported_as_422() -> None:
    from scopecat.records.launch_rejection import LaunchRejection

    with patch("scopecat_server.http.transport.subprocess.run") as run:
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout=LaunchRejection(
                message="unknown control 'amplitudes'"
            ).model_dump_json(),
            stderr="",
        )
        response = client().post(
            "/api/v1/experiment-launcher/preview",
            json={"action": "preview", "experiment": "diagnostic", "version": "1"},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "unknown control 'amplitudes'"
