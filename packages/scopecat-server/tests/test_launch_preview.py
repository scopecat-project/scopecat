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
