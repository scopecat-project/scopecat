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
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.run import ConfigRegistryRunConfigSource

from scopecat_server.http.transport import create_app


def _submission_request() -> dict[str, object]:
    request = LaunchRequest(action="preview", experiment="diagnostic", version="1")
    return request.model_copy(
        update={
            "action": "submit",
            "request_key": "one",
            "expected_request_hash": request.request_hash,
            "config_source": ConfigRegistryRunConfigSource(
                selector="active",
                entry_id="baseline",
                config_ref="baseline",
                content_hash="sha256:" + "a" * 64,
                registry_generation=1,
            ),
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


def client() -> TestClient:
    return TestClient(
        create_app(
            cast(
                "DaemonApplication",
                cast(
                    "object",
                    SimpleNamespace(
                        project_root=Path.cwd(), manual_previews=_manual_previews()
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
        assert response.json() == {"entries": [], "code_revision": None}
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


def test_timeout_has_a_bounded_error() -> None:
    with patch(
        "scopecat_server.http.transport.subprocess.run",
        side_effect=subprocess.TimeoutExpired("worker", 60),
    ):
        assert client().get("/api/v1/experiment-launcher").status_code == 504


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
    assert capsys.readouterr().out.strip() == '{"code_revision":null,"entries":[]}'


def test_admission_survives_dispatch_failure(tmp_path: Path) -> None:
    automation = SimpleNamespace(get=Mock(return_value=SimpleNamespace(state="ready")))
    app = create_app(
        cast(
            "DaemonApplication",
            cast(
                "object",
                SimpleNamespace(
                    project_root=tmp_path,
                    automation=automation,
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
            side_effect=OSError("cannot spawn"),
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
        restored.tick()
        spawn.assert_called_once()
        restored.dispatch("p1")
        assert spawn.call_count == 2


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
    with patch("scopecat_server.http.transport.ProjectProcedureWorkers") as manager:
        with TestClient(
            create_app(
                cast(
                    "DaemonApplication",
                    cast(
                        "object",
                        SimpleNamespace(
                            project_root=Path.cwd(), manual_previews=_manual_previews()
                        ),
                    ),
                )
            )
        ):
            manager.return_value.start.assert_called_once()
            manager.return_value.stop.assert_not_called()
        manager.return_value.stop.assert_called_once()


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
    assert result.stderr.strip() == "操作者 → μ"


def test_worker_rejects_undeclared_control_edits_before_provider_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        with pytest.raises(ValueError, match="unknown control"):
            launch_worker.main()
    assert provider.call_count == 1
    assert provider.call_args.args[1].action == "list"
