from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from scopecat.config.resolution import validate_config_profile
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import (
    DAEMON_URL_ENV,
    DaemonEndpointRecord,
    daemon_record_path,
    read_daemon_endpoint_record,
)
from scopecat.project import (
    Project,
    load_instrument_backend_factory,
    open_project,
)
from scopecat.records.measurement import MeasurementScalar
from scopecat.records.parameter import ScalarParameterValue
from scopecat_testkit.project_loading import isolated_project_imports
from typer.testing import CliRunner

from scopecat_server.cli import app
from scopecat_server.lifecycle import (
    DaemonLifecycleError,
    DaemonStatus,
    initialize_project,
    inspect_daemon,
    start_project,
    stop_project,
    write_daemon_endpoint_record,
)


@pytest.fixture(autouse=True)
def isolate_project_loader() -> Iterator[None]:
    with isolated_project_imports():
        yield


def test_init_creates_runnable_python_project_and_does_not_overwrite(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_text("results/\n", encoding="utf-8")

    project = initialize_project(tmp_path)

    assert project.manifest.read_text(encoding="utf-8") == (
        "[lab]\n"
        'bootstrap = "scopecat_lab.application:create_bootstrap"\n'
        'application = "scopecat_lab.application:create_application"\n'
        'instrument_backend = "scopecat_lab.backend:create_backend"\n'
    )
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == (
        "results/\n.scopecat/\n"
    )
    assert not (tmp_path / ".scopecat").exists()
    assert (tmp_path / "src/scopecat_lab/__init__.py").is_file()
    assert (tmp_path / "src/scopecat_lab/application.py").is_file()
    assert (tmp_path / "src/scopecat_lab/backend.py").is_file()
    assert (tmp_path / "src/scopecat_lab/configuration.py").is_file()
    notebook = tmp_path / "notebooks/01_first_run.py"
    assert notebook.is_file()
    notebook_source = notebook.read_text(encoding="utf-8")
    assert "quantum_lab_demo" not in notebook_source
    assert 'lab.run(first_run(), name="First run")' in notebook_source
    assert "lab.prepare(" not in notebook_source

    bootstrap = project.load_bootstrap()
    assert bootstrap.bootstrap_config is not None
    config = validate_config_profile(bootstrap.bootstrap_config())
    assert config.id == "default"
    assert config.primary_entity_id == "subject"
    assert config.parameter_snapshot.get("repetitions") == ScalarParameterValue(
        id="repetitions",
        value=128,
    )
    assert project.instrument_backend_spec is not None
    create_backend = load_instrument_backend_factory(
        project.instrument_backend_spec,
        project.root,
    )
    assert (
        create_backend(project.root).provider.provider_id
        == "scopecat.instruments.configured"
    )

    with pytest.raises(DaemonLifecycleError, match="already initialized"):
        initialize_project(tmp_path)
    assert project.manifest.read_text(encoding="utf-8").startswith("[lab]\n")


def test_init_rejects_scaffold_collision_before_writing_manifest(
    tmp_path: Path,
) -> None:
    application = tmp_path / "src/scopecat_lab/application.py"
    application.parent.mkdir(parents=True)
    application.write_text("# user-owned\n", encoding="utf-8")

    with pytest.raises(
        DaemonLifecycleError,
        match=r"scaffold path already exists: src/scopecat_lab/application.py",
    ):
        initialize_project(tmp_path)

    assert application.read_text(encoding="utf-8") == "# user-owned\n"
    assert not (tmp_path / "scopecat.toml").exists()


def test_endpoint_record_is_private_and_round_trips(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    record = _record(tmp_path, pid=123, process_create_time=45)

    path = write_daemon_endpoint_record(record)

    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert read_daemon_endpoint_record(tmp_path) == record


def test_stale_pid_identity_is_removed_without_terminating_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = initialize_project(tmp_path)
    write_daemon_endpoint_record(_record(tmp_path, pid=4321, process_create_time=10))
    process = _FakeProcess(create_time=20)

    def process_factory(_pid: int | None = None) -> _FakeProcess:
        return process

    monkeypatch.setattr(
        "scopecat_server.lifecycle.psutil.Process",
        process_factory,
    )

    observed = stop_project(project)

    assert observed.state == "stale"
    assert process.terminate_calls == 0
    assert not daemon_record_path(tmp_path).exists()


def test_matching_process_with_unreachable_health_is_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = initialize_project(tmp_path)
    record = _record(tmp_path, pid=4321, process_create_time=10)
    write_daemon_endpoint_record(record)

    def process_factory(_pid: int | None = None) -> _FakeProcess:
        return _FakeProcess(create_time=10)

    monkeypatch.setattr(
        "scopecat_server.lifecycle.psutil.Process",
        process_factory,
    )

    observed = inspect_daemon(project, health_timeout=0.01)

    assert observed.state == "degraded"
    assert observed.record == record


@pytest.mark.parametrize(
    ("lease_ttl", "expected_seconds"),
    [
        (None, None),
        (timedelta(seconds=1), "1.0"),
    ],
)
def test_start_project_forwards_only_explicit_lease_ttl_to_detached_serve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lease_ttl: timedelta | None,
    expected_seconds: str | None,
) -> None:
    project = initialize_project(tmp_path)
    record = _record(tmp_path, pid=4321, process_create_time=10)
    observations = iter(
        (
            DaemonStatus(state="stopped"),
            DaemonStatus(state="running", record=record),
        )
    )
    commands: list[list[str]] = []
    process_options: list[dict[str, object]] = []

    class StartedProcess:
        pid = record.pid

        @staticmethod
        def poll() -> None:
            return None

    def start_process(command: list[str], **kwargs: object) -> StartedProcess:
        commands.append(command)
        process_options.append(kwargs)
        return StartedProcess()

    def inspect_project(_project: Project) -> DaemonStatus:
        return next(observations)

    monkeypatch.setattr(
        "scopecat_server.lifecycle.inspect_daemon",
        inspect_project,
    )
    monkeypatch.setattr(
        "scopecat_server.lifecycle.subprocess.Popen",
        start_process,
    )

    started = start_project(project, lease_ttl=lease_ttl, timeout=0.1)

    assert started == record
    command = commands[0]
    option = "--executor-lease-ttl-seconds"
    if expected_seconds is None:
        assert option not in command
    else:
        option_index = command.index(option)
        assert command[option_index + 1] == expected_seconds
    if sys.platform == "win32":
        assert process_options[0]["start_new_session"] is False
        assert process_options[0]["creationflags"] == subprocess.CREATE_NO_WINDOW
    else:
        assert process_options[0]["start_new_session"] is True
        assert process_options[0]["creationflags"] == 0


def test_cli_init_prints_copyable_next_steps_at_narrow_width(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "40")

    initialized = CliRunner().invoke(app, ["init", str(tmp_path)])

    assert initialized.exit_code == 0, initialized.output
    assert str(tmp_path / "src/scopecat_lab/configuration.py") in initialized.output
    assert f"scopecat config check {tmp_path}" in initialized.output
    assert f"python {tmp_path / 'notebooks/01_first_run.py'}" in initialized.output


def test_cli_daemon_first_use_loop_uses_dynamic_port_and_cleans_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = CliRunner()
    initialize_project(tmp_path)
    static_dir = tmp_path / "test-ui"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<main>test GUI</main>")
    started = runner.invoke(
        app,
        ["start", str(tmp_path), "--static-dir", str(static_dir)],
    )
    assert started.exit_code == 0, started.output
    project = open_project(tmp_path)
    record = read_daemon_endpoint_record(tmp_path)
    assert record is not None
    try:
        assert record.base_url != "http://127.0.0.1:0"
        assert int(record.base_url.rsplit(":", maxsplit=1)[1]) > 0
        if os.name != "nt":
            assert stat.S_IMODE(daemon_record_path(tmp_path).stat().st_mode) == 0o600
        with httpx2.Client(timeout=2, trust_env=False) as client:
            health = client.get(f"{record.base_url}/api/v1/health")
            gui = client.get(record.base_url)
        assert health.status_code == 200
        assert gui.text == "<main>test GUI</main>"

        first_run = subprocess.run(  # noqa: S603
            [sys.executable, str(tmp_path / "notebooks/01_first_run.py")],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
            env=_project_subprocess_environment(),
        )
        assert first_run.returncode == 0, first_run.stderr
        assert "'status': 'completed'" in first_run.stdout
        summary_line, console_url = first_run.stdout.strip().splitlines()
        summary = ast.literal_eval(summary_line)
        run_id = str(summary["run_id"])
        assert console_url == f"{record.base_url}/?run={run_id}"
        with DaemonClient(record.base_url) as client:
            preview = client.measurement_preview(run_id)
        [measurement] = preview.items
        temperature = measurement.observables["temperature"]
        assert isinstance(temperature, MeasurementScalar)
        assert temperature.value == 0.02 and temperature.unit == "K"
        assert measurement.acquisition_evidence.events[0].instrument_id == "thermometer"

        status = runner.invoke(app, ["status", str(tmp_path)])
        assert status.exit_code == 0, status.output
        assert "running" in status.output

        opened_urls: list[str] = []

        def open_browser(url: str) -> bool:
            opened_urls.append(url)
            return True

        monkeypatch.setattr(
            "scopecat_server.lifecycle.webbrowser.open",
            open_browser,
        )
        opened = runner.invoke(app, ["open", str(tmp_path)])
        assert opened.exit_code == 0, opened.output
        assert opened_urls == [record.base_url]

        stopped = runner.invoke(app, ["stop", str(tmp_path)])
        assert stopped.exit_code == 0, stopped.output
        restarted = runner.invoke(
            app,
            ["start", str(tmp_path), "--static-dir", str(static_dir)],
        )
        assert restarted.exit_code == 0, restarted.output
        restored_record = read_daemon_endpoint_record(tmp_path)
        assert restored_record is not None
        with DaemonClient(restored_record.base_url) as client:
            assert client.measurement_preview(run_id) == preview
    finally:
        if daemon_record_path(tmp_path).exists():
            stop_project(project)

    assert not daemon_record_path(tmp_path).exists()


def test_cli_start_rejects_conflicting_gui_modes(tmp_path: Path) -> None:
    initialize_project(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "start",
            str(tmp_path),
            "--api-only",
            "--static-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "--api-only and --static-dir cannot be used together" in result.output
    assert read_daemon_endpoint_record(tmp_path) is None


def test_cli_start_explains_missing_source_gui_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_project(tmp_path)
    monkeypatch.setattr(
        "scopecat_server.cli._DEFAULT_STATIC_DIR",
        tmp_path / "missing-ui",
    )

    result = CliRunner().invoke(app, ["start", str(tmp_path)])

    output = " ".join(result.output.split())
    assert result.exit_code == 1
    assert "GUI bundle is not installed" in output
    assert "--static-dir" in output
    assert "--api-only" in output
    assert read_daemon_endpoint_record(tmp_path) is None


def _project_subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop(DAEMON_URL_ENV, None)
    return environment


def _record(
    project_root: Path,
    *,
    pid: int,
    process_create_time: float,
) -> DaemonEndpointRecord:
    return DaemonEndpointRecord(
        project_root=project_root,
        pid=pid,
        process_create_time=process_create_time,
        base_url="http://127.0.0.1:1",
        shutdown_token="test-shutdown-token" * 2,
        started_at=datetime.now(UTC),
    )


class _FakeProcess:
    def __init__(self, *, create_time: float) -> None:
        self._create_time = create_time
        self.terminate_calls = 0

    def create_time(self) -> float:
        return self._create_time

    def terminate(self) -> None:
        self.terminate_calls += 1
