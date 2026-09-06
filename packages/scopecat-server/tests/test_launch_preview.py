import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from scopecat_server.http.transport import create_app


def client():
    return TestClient(create_app(SimpleNamespace(project_root=Path.cwd())))


def test_catalog_runs_a_fixed_separate_worker():
    with patch("scopecat_server.http.transport.subprocess.run") as run:
        run.return_value = SimpleNamespace(
            returncode=0, stdout='{"calibrations": []}', stderr=""
        )
        response = client().get("/api/v1/experiment-launcher")
        assert response.json() == {"calibrations": []}
        assert run.call_args.args[0][1:3] == [
            "-m",
            "scopecat.application.launch_worker",
        ]
        assert '"action":"list"' in run.call_args.kwargs["input"]


def test_preview_failure_is_visible_and_start_is_not_supported():
    with patch("scopecat_server.http.transport.subprocess.run") as run:
        run.return_value = SimpleNamespace(
            returncode=1, stdout="", stderr="traceback\nValueError: bad target"
        )
        response = client().post(
            "/api/v1/experiment-launcher/preview",
            json={"action": "preview", "experiment": "rabi", "inputs": {}},
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "ValueError: bad target"
        run.reset_mock()
        response = client().post(
            "/api/v1/experiment-launcher/preview", json={"action": "start"}
        )
        assert response.status_code == 422
        run.assert_not_called()


def test_timeout_has_a_bounded_error():
    with patch(
        "scopecat_server.http.transport.subprocess.run",
        side_effect=subprocess.TimeoutExpired("worker", 60),
    ):
        assert client().get("/api/v1/experiment-launcher").status_code == 504


def test_worker_loads_manifest_file_and_supports_empty_project(
    tmp_path, monkeypatch, capsys
):
    import io

    from scopecat.application import launch_worker

    monkeypatch.setattr("sys.argv", ["launch_worker", str(tmp_path)])
    monkeypatch.setattr("sys.stdin", io.StringIO('{"action":"list"}'))
    with patch.object(launch_worker, "load_project") as load:
        load.return_value.load_application.return_value = SimpleNamespace(
            launch_provider=None
        )
        launch_worker.main()
        load.assert_called_once_with(tmp_path / "scopecat.toml")
    assert capsys.readouterr().out.strip() == '{"calibrations": []}'


def test_admission_survives_dispatch_failure(tmp_path):
    automation = SimpleNamespace(get=lambda _: SimpleNamespace(state="ready"))
    app = create_app(SimpleNamespace(project_root=tmp_path, automation=automation))
    with (
        patch("scopecat_server.http.transport.subprocess.run") as run,
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
            json={"action": "submit", "request_key": "one"},
        )
    assert result.status_code == 200
    assert result.json() == {"procedure_id": "p1", "dispatch_error": "cannot spawn"}


def test_dispatch_deduplicates_live_workers(tmp_path):
    from unittest.mock import Mock

    from scopecat_server.services.project_workers import ProjectProcedureWorkers

    workers = ProjectProcedureWorkers(lambda: tmp_path)
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


def test_worker_continues_after_review_and_stops_when_closed():
    from unittest.mock import Mock

    from scopecat.application.launch_worker import run_procedure

    first = SimpleNamespace(state="ready", resume=Mock())
    repeated = SimpleNamespace(state="ready", resume=Mock())
    lab = SimpleNamespace(
        procedures=SimpleNamespace(
            get=Mock(
                side_effect=[
                    first,
                    SimpleNamespace(state="waiting_for_input"),
                    repeated,
                    SimpleNamespace(state="closed"),
                ]
            )
        )
    )
    with patch("scopecat.application.launch_worker.time.sleep") as sleep:
        run_procedure(lab, "p1")
    first.resume.assert_called_once()
    repeated.resume.assert_called_once()
    sleep.assert_called_once_with(1)
