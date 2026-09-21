"""A real installed adapter wheel serves an independently editable code folder."""

import json
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from lab_tools.first_run import SetupRequest, setup
from lab_tools.services import Services
from scopecat_server.scaffold import write_project_scaffold


def run(arguments: list[str], *, cwd: Path, environment: dict[str, str]) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed fixture/tool entry points
        arguments,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


def build_adapter(tmp_path: Path, environment: dict[str, str]) -> Path:
    """Build with the installed backend; wheel installation is tested separately."""
    scaffold = tmp_path / "scaffold"
    write_project_scaffold(scaffold)
    source = scaffold / "src/scopecat_lab"
    build = tmp_path / "adapter-build"
    package = build / "src/test_lab"
    shutil.copytree(source, package)
    trace = """
import json, os
from pathlib import Path

def record_origin(role):
    destination = Path(os.environ["SCOPECAT_ADAPTER_TRACE"])
    record = {"role": role, "pid": os.getpid(), "file": __file__}
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\\n")
"""
    (package / "origin.py").write_text(trace, encoding="utf-8")
    for module, function, role in (
        ("application.py", "create_bootstrap", "bootstrap"),
        ("backend.py", "create_backend", "instrument"),
    ):
        path = package / module
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "from pathlib import Path",
            "from pathlib import Path\nfrom .origin import record_origin",
        )
        line = next(
            line for line in text.splitlines() if line.startswith(f"def {function}(")
        )
        text = text.replace(line, line + f'\n    record_origin("{role}")')
        path.write_text(text, encoding="utf-8")
    signal = package / "authored/signal.py"
    signal.write_text(
        signal.read_text().replace('id="signal"', 'id="installed_signal"')
    )
    (package / "adapter.toml").write_text("""[lab]
bootstrap = "test_lab.application:create_bootstrap"
instrument_backend = "test_lab.backend:create_backend"
[lab.capabilities]
author_modules = ["test_lab.authored"]
[authors.packages]
test_lab = "test-lab-adapter"
""")
    (build / "pyproject.toml").write_text("""[project]
name = "test-lab-adapter"
version = "1.0.0"
requires-python = ">=3.14"
dependencies = ["scopecat", "scopecat-instruments"]
[build-system]
requires = ["uv_build>=0.12.3,<0.13"]
build-backend = "uv_build"
[tool.uv.build-backend]
module-name = "test_lab"
""")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    run(
        [
            sys.executable,
            "-c",
            "import sys; from uv_build import build_wheel; build_wheel(sys.argv[1])",
            str(wheels),
        ],
        cwd=build,
        environment=environment,
    )
    return next(wheels.glob("*.whl"))


def install_adapter_environment(
    virtualenv: Path, wheel: Path, environment: dict[str, str], tmp_path: Path
) -> tuple[Path, Path]:
    run(
        [sys.executable, "-m", "venv", "--without-pip", str(virtualenv)],
        cwd=tmp_path,
        environment=environment,
    )
    python = virtualenv / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    site = Path(
        run(
            [
                str(python),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            cwd=tmp_path,
            environment=environment,
        ).strip()
    )
    # Share only already-installed framework dependencies; the adapter belongs
    # exclusively to this disposable environment and is installed from its wheel.
    (site / "framework-test-dependencies.pth").write_text(
        f"import site; site.addsitedir({sysconfig.get_path('purelib')!r})\n"
    )
    uv = shutil.which("uv")
    assert uv is not None
    run(
        [
            uv,
            "pip",
            "install",
            "--offline",
            "--no-deps",
            "--python",
            str(python),
            str(wheel),
        ],
        cwd=tmp_path,
        environment=environment,
    )
    return python, site


_AUTHOR_JOURNEY = """
import json, sys
from pathlib import Path
import scopecat as sc
from scopecat.project_sources import capture_sources
project = sc.open_project(Path(sys.argv[1]))
application = project.load_application()
assert application.authors is not None
assert len(application.authors.experiments) == 2
from local_experiments import signal
with project.authoring() as author:
    signal = author.load_experiment(signal)
    request = signal(center=0.0)
    request.values["position"] = sc.Scan([-1.0, 0.0, 1.0])
    prepared = author.prepare(request)
    plan = prepared.save_plan("Retained source-only experiment", saved_by="test")
    original = prepared.run().wait(timeout=60).result()
    source = project.root / "src/local_experiments.py"
    source.write_text(source.read_text().replace("return 1.0 /", "return 2.0 /"))
    signal = author.refresh(signal)
    refreshed = signal(center=0.0)
    refreshed.values["position"] = sc.Scan([-1.0, 0.0, 1.0])
    assert refreshed.declaration.code_revision != request.declaration.code_revision
    changed = author.prepare(refreshed).run().wait(timeout=60).result()
    assert tuple(original.measurements()["result"].require_values()) == (0.5, 1.0, 0.5)
    assert tuple(changed.measurements()["result"].require_values()) == (1.0, 2.0, 1.0)
    replay = author.prepare_plan(plan.ref).run().wait(timeout=60).result()
    assert tuple(replay.measurements()["result"].require_values()) == (0.5, 1.0, 0.5)
    import local_experiments
    historical = author.analyze_as(
        original.id, "local_experiments:summarize", local_experiments.Summary
    )
    assert historical.value.mean == 2 / 3
    assert historical.value.points == 3
    bundle = capture_sources(project)
    assert "scopecat.laboratory.toml" in bundle.files
    identity = bundle.manifest.model_dump_json()
    (project.root / "retained-identity.json").write_text(identity)
    print(json.dumps({"original": original.id, "changed": changed.id}))
"""

_IDENTITY_CHECK = """
import sys
from pathlib import Path
from importlib.metadata import distribution
from scopecat.project_sources import require_environment
from scopecat.records.author_revision import AuthorRevisionManifest
manifest = AuthorRevisionManifest.model_validate_json(Path(sys.argv[1]).read_text())
require_environment(manifest)
resource = Path(
    distribution("test-lab-adapter").locate_file("test_lab/configuration.py")
)
original = resource.read_bytes()
try:
    resource.write_bytes(original + b"\\n# same-version wheel content changed\\n")
    try:
        require_environment(manifest)
    except ValueError as error:
        assert "package content changed" in str(error), str(error)
    else:
        raise AssertionError("changed package accepted for historical identity")
finally:
    resource.write_bytes(original)
require_environment(manifest)
"""


_RECOVERY_CHECK = """
import sys
from pathlib import Path
from scopecat.project import open_project, load_captured_project
from scopecat.project_sources import materialize_sources, require_environment
from scopecat.records.author_revision import AuthorRevisionManifest
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import resolve_daemon_endpoint
from scopecat_server.lifecycle import start_project, stop_project
from scopecat_server.snapshots import create_snapshot, restore_snapshot
owner, source, snapshot, destination = map(Path, sys.argv[1:5])
identity = AuthorRevisionManifest.model_validate_json(
    (source / "retained-identity.json").read_text()
)
create_snapshot(open_project(owner), snapshot)
restore_snapshot(snapshot, destination)
restored = open_project(destination)
start_project(restored, timeout=60)
try:
    with DaemonClient(
        resolve_daemon_endpoint(destination), workspace_id=sys.argv[5]
    ) as client:
        bundle = client.author_revision(identity.ref)
    require_environment(bundle.manifest)
    archive = materialize_sources(bundle, destination / "retained")
    archived = load_captured_project(archive)
    assert archived.author_only
    assert archived.lab_adapter.distribution == "test-lab-adapter"
finally:
    stop_project(restored)
"""


def check_notebook_kernel(project: Path, python: Path, kernel_home: Path) -> None:
    from jupyter_client import KernelManager
    from jupyter_client.kernelspec import KernelSpecManager

    from lab_tools.notebook import kernel_command

    _, environment = kernel_command(
        project, python=str(python), source_path=False, kernel_home=kernel_home
    )
    for name in ("PYTHONHOME", "PYTHONPATH", "SCOPECAT_DAEMON_URL"):
        environment.pop(name, None)
    manager = KernelManager(
        kernel_name="scopecat-lab",
        kernel_spec_manager=KernelSpecManager(
            kernel_dirs=[str(kernel_home / "kernels")],
            ensure_native_kernel=False,
        ),
    )
    manager.start_kernel(cwd=str(project), env=environment)
    client = manager.blocking_client()
    client.start_channels()
    try:
        client.wait_for_ready(timeout=30)
        response = client.execute_interactive(
            "import sys\nfrom pathlib import Path\n"
            "from scopecat.project import open_project\n"
            f"assert Path(sys.executable) == Path({str(python)!r})\n"
            f"assert Path.cwd() == Path({str(project)!r})\n"
            "project = open_project(Path.cwd())\n"
            "assert project.author_only\n"
            "assert project.lab_adapter.distribution == 'test-lab-adapter'\n",
            timeout=30,
        )
        assert response["content"]["status"] == "ok", response
    finally:
        client.stop_channels()
        manager.shutdown_kernel(now=True)


def test_installed_adapter_wheel_local_refresh_and_missing_adapter_stop(
    tmp_path: Path, monkeypatch, delivery: Path
) -> None:
    environment = dict(os.environ)
    environment.pop("SCOPECAT_DAEMON_URL", None)
    trace = tmp_path / "adapter-origins.jsonl"
    environment["SCOPECAT_ADAPTER_TRACE"] = str(trace)
    monkeypatch.setenv("SCOPECAT_ADAPTER_TRACE", str(trace))
    wheel = build_adapter(tmp_path, environment)
    project = tmp_path / "experiment"
    (project / "src").mkdir(parents=True)
    (project / "src/local_experiments.py").write_text(
        (tmp_path / "scaffold/src/scopecat_lab/authored/signal.py").read_text()
    )
    (project / "scopecat.toml").write_text("""[authors]
modules = ["local_experiments"]
source_roots = ["src"]
refresh_roots = ["src"]
dependencies = []
""")
    laboratory = tmp_path / "laboratory"
    services = Services(tmp_path / "home")
    # Use a real replacement interpreter and installed private wheel. The small
    # delivery fixture avoids rebuilding every public wheel in this runtime test;
    # installation receipts/GUI verification have dedicated delivery tests.
    from lab_tools import bundle, lab_environment

    def install(source, destination, *, ownership_token, on_process):
        install_adapter_environment(destination, wheel, environment, tmp_path)
        (destination / bundle.OWNERSHIP).write_text(ownership_token)
        (destination / bundle.RECEIPT).write_text(
            json.dumps(
                {
                    "bundle": str(source),
                    "manifest_sha256": bundle.file_hash(source / bundle.MANIFEST),
                }
            )
        )
        return destination

    def prepared(destination, retained):
        return lab_environment.PreparedEnvironment(
            python=destination
            / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python"),
            gui=retained / "gui",
        )

    with monkeypatch.context() as setup_patch:
        setup_patch.setattr(bundle, "install_bundle", install)
        setup_patch.setattr(lab_environment, "_prepared", prepared)
        service = setup(
            tmp_path / "home",
            SetupRequest(
                mode="adapter",
                project=str(laboratory),
                name="installed adapter",
                adapter_distribution="test-lab-adapter",
                adapter_manifest="test_lab/adapter.toml",
                environment_bundle=str(delivery),
                author_workspace=str(project),
            ),
        )
        original_binding = services.for_workspace(project)
        retried = setup(
            tmp_path / "home",
            SetupRequest(
                mode="connect",
                project=str(laboratory),
                name="installed adapter",
                environment_bundle=str(delivery),
                author_workspace=str(project),
            ),
        )
        assert retried == service
        assert services.for_workspace(project) == original_binding
    python = Path(service.python)
    gui = Path(service.static_dir)
    site = Path(
        run(
            [
                str(python),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            cwd=tmp_path,
            environment=environment,
        ).strip()
    )
    selected_service, workspace_id = services.for_workspace(project)
    assert selected_service.id == service.id and workspace_id != "legacy"
    with pytest.raises(ValueError, match="作者目录不能登记为实验服务"):
        services.register(
            project, python, name="Not a second laboratory", static_dir=gui
        )
    assert len(services.list()) == 1
    assert not trace.exists(), "registration imported or invoked adapter code"
    try:
        services.start(service.id)
        origins = [json.loads(line) for line in trace.read_text().splitlines()]
        bootstrap = next(item for item in origins if item["role"] == "bootstrap")
        instrument = next(item for item in origins if item["role"] == "instrument")
        assert bootstrap["pid"] != instrument["pid"]
        assert all(Path(item["file"]).is_relative_to(site) for item in origins)
        result = json.loads(
            run(
                [str(python), "-c", _AUTHOR_JOURNEY, str(project)],
                cwd=project,
                environment=environment,
            )
            .strip()
            .splitlines()[-1]
        )
        assert result["original"] != result["changed"]
        run(
            [
                str(python),
                "-c",
                _IDENTITY_CHECK,
                str(project / "retained-identity.json"),
            ],
            cwd=project,
            environment=environment,
        )
        services.stop(service.id)
        run(
            [
                str(python),
                "-c",
                _RECOVERY_CHECK,
                str(laboratory),
                str(project),
                str(tmp_path / "snapshot"),
                str(tmp_path / "restored"),
                workspace_id,
            ],
            cwd=tmp_path,
            environment=environment,
        )
        with monkeypatch.context() as update_patch:
            update_patch.setattr(bundle, "install_bundle", install)
            update_patch.setattr(lab_environment, "_prepared", prepared)
            updated = services.update_environment(
                service.id, delivery, operation_id="journey"
            )
        assert updated.id == service.id and updated.root == service.root
        assert updated.python != service.python and Path(service.python).is_file()
        assert services.for_workspace(project) == (updated, workspace_id)
        service = updated
        python = Path(updated.python)
        check_notebook_kernel(project, python, tmp_path / "notebook-kernels")
        site = Path(
            run(
                [
                    str(python),
                    "-c",
                    "import sysconfig; print(sysconfig.get_path('purelib'))",
                ],
                cwd=tmp_path,
                environment=environment,
            ).strip()
        )
        services.start(service.id)
        # Stored source evidence remains readable in the replacement environment.
        run(
            [
                str(python),
                "-c",
                _IDENTITY_CHECK,
                str(project / "retained-identity.json"),
            ],
            cwd=project,
            environment=environment,
        )
        origins = [json.loads(line) for line in trace.read_text().splitlines()]
        assert Path(origins[-1]["file"]).is_relative_to(site)
        resource = site / "test_lab/configuration.py"
        original = resource.read_bytes()
        try:
            resource.write_bytes(original + b"\n# same-version installation changed\n")
            with pytest.raises(ValueError, match="适配包已改变"):
                services.start(service.id)
            assert services.views()[0].state == "running"
        finally:
            resource.write_bytes(original)
        # Removing the adapter must not make a live worker impossible to stop.
        shutil.rmtree(site / "test_lab")
        shutil.rmtree(site / "test_lab_adapter-1.0.0.dist-info")
        services.stop(service.id)
        assert services.views()[0].state == "stopped"
    finally:
        services.stop(service.id)
