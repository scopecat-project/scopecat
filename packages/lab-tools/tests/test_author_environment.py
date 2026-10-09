"""Author preparation preserves retry, retained environments and full identity."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lab_tools import author_environment as environments


@pytest.mark.parametrize("rebuild", [False, True])
@pytest.mark.parametrize("stage", ["install", "ensurepip"])
@pytest.mark.parametrize("failure", [ValueError, KeyboardInterrupt])
def test_interrupted_client_preparation_preserves_retry(
    tmp_path, monkeypatch, rebuild, stage, failure
):
    workspace = tmp_path / "authors"
    workspace.mkdir()
    source = workspace / "experiment.py"
    source.write_text("# authored source\n")
    python = environments.environment_python(workspace / ".venv")
    if rebuild:
        python.parent.mkdir(parents=True)
        python.write_bytes(b"original interpreter")
    runtime = Mock()
    monkeypatch.setattr(environments, "_bundle", lambda _: tmp_path)
    monkeypatch.setattr(environments, "_independent_python", lambda *_: Path("base"))
    interrupted = failure("preparation interrupted")

    def install(*args, **kwargs):
        python.parent.mkdir(parents=True)
        python.write_bytes(b"candidate interpreter")
        if stage == "install":
            raise interrupted

    installer = Mock(side_effect=install)
    monkeypatch.setattr(environments, "install_bundle", installer)
    ensurepip = Mock(side_effect=interrupted if stage == "ensurepip" else None)
    monkeypatch.setattr(environments, "_run", ensurepip)

    with pytest.raises(failure, match="preparation interrupted") as raised:
        environments.create_client_environment(runtime, workspace, rebuild=rebuild)
    assert raised.value is interrupted
    assert source.read_text() == "# authored source\n"
    assert not (workspace / "pyproject.toml").exists()
    failed = list(workspace.glob(".venv-failed-*"))
    assert len(failed) == 1
    assert (
        environments.environment_python(failed[0]).read_bytes()
        == b"candidate interpreter"
    )
    assert not list(workspace.glob(".venv-retained-*"))

    if rebuild:
        assert python.read_bytes() == b"original interpreter"
        assert environments.create_client_environment(runtime, workspace) == python
        assert installer.call_count == 1
    else:
        assert not python.exists()

    stage = "complete"
    ensurepip.side_effect = None
    assert (
        environments.create_client_environment(runtime, workspace, rebuild=rebuild)
        == python
    )
    assert installer.call_count == 2
    assert python.read_bytes() == b"candidate interpreter"
    assert source.read_text() == "# authored source\n"
    assert (workspace / "pyproject.toml").is_file()


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname = "example"\n')
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / environments.MANIFEST).write_text("delivery identity")
    (bundle / "requirements.lock").write_text("locked==1\n")
    runtime = Mock(home=tmp_path / "data", lock=threading.Lock())
    runtime.installation.return_value = SimpleNamespace(
        python=Path("application-python"),
        environment={"scopecat": "1", "server": "1"},
    )
    monkeypatch.setattr(environments, "_bundle", lambda _: bundle)
    monkeypatch.setattr(environments, "find_uv_bin", lambda: "uv")
    monkeypatch.setattr(environments, "_independent_python", lambda *_: Path("base"))
    calls = []

    def run(command):
        calls.append(command)
        if "compile" in command:
            Path(command[command.index("--output-file") + 1]).write_text("locked==1\n")

    monkeypatch.setattr(environments, "_run", run)
    capture = Mock()
    monkeypatch.setattr("scopecat_server.author_environment.capture", capture)
    return runtime, source, calls, capture


def test_short_candidates_reuse_full_identity_receipts(preparation):
    runtime, source, calls, capture = preparation
    python = environments.prepare_execution_environment(runtime, source)
    receipts = list((runtime.home / "environments").glob("*/environment.json"))
    assert len(receipts) == 1
    receipt = receipts[0]
    assert len(receipt.parent.name) == 64
    assert python.parent.parent.parent.parent == runtime.home / "environments"
    assert python.parent.parent.parent.name.startswith("e-")
    assert len(python.parent.parent.parent.name) < 16
    assert json.loads(receipt.read_text()) == {"python": str(python)}
    capture.assert_called_once_with(source.resolve(), python)
    assert environments.prepare_execution_environment(runtime, source) == python
    assert sum("install" in command for command in calls) == 1
    # Existing receipts may point at the previous nested layout. Keep using it.
    previous = receipt.parent / "retained-attempt/runtime/bin/python"
    receipt.write_text(json.dumps({"python": str(previous)}))
    assert environments.prepare_execution_environment(runtime, source) == previous
    assert sum("install" in command for command in calls) == 1


@pytest.mark.parametrize("failure", [ValueError, KeyboardInterrupt])
def test_failed_candidate_preserves_other_environments(preparation, failure):
    runtime, source, _, capture = preparation
    retained = runtime.home / "environments/e-retained/runtime"
    retained.mkdir(parents=True)
    (retained / "keep").write_text("existing environment")
    capture.side_effect = failure("capture failed")
    with pytest.raises(failure, match="capture failed"):
        environments.prepare_execution_environment(runtime, source)
    assert (retained / "keep").read_text() == "existing environment"
    assert list((runtime.home / "environments").glob("e-*")) == [retained.parent]
    assert not list((runtime.home / "environments").glob("*/environment.json"))
    capture.side_effect = None
    python = environments.prepare_execution_environment(runtime, source)
    assert python != capture.call_args_list[0].args[1]
    assert retained.is_dir()


def test_concurrent_preparation_publishes_one_candidate_per_identity(preparation):
    runtime, source, calls, capture = preparation
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(
            workers.map(
                lambda _: environments.prepare_execution_environment(runtime, source),
                range(2),
            )
        )
    assert results[0] == results[1]
    capture.assert_called_once()
    assert sum("install" in command for command in calls) == 1
    previous = results[0]
    bundle = source.parent / "bundle"
    (bundle / environments.MANIFEST).write_text("different delivery identity")
    current = environments.prepare_execution_environment(runtime, source)
    assert current != previous
    receipts = list((runtime.home / "environments").glob("*/environment.json"))
    assert len(receipts) == 2
    assert {json.loads(receipt.read_text())["python"] for receipt in receipts} == {
        str(previous),
        str(current),
    }
    assert previous.parent.parent.parent.is_dir()


def test_online_newer_dependency_cannot_override_delivery_lock(tmp_path, monkeypatch):
    """Exercise uv's real resolver/installer against a newer HTTP candidate."""
    import functools
    import subprocess
    import sys
    import zipfile
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    from uv import find_uv_bin

    bundle = tmp_path / "bundle"
    wheels = bundle / "wheels"
    wheels.mkdir(parents=True)
    index = tmp_path / "index"
    (index / "schema-runtime").mkdir(parents=True)

    def wheel(directory, name, version, dependencies=()):
        normalized = name.replace("-", "_")
        path = directory / f"{normalized}-{version}-py3-none-any.whl"
        info = f"{normalized}-{version}.dist-info"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"{normalized}/__init__.py", f'VERSION = "{version}"\n')
            archive.writestr(
                f"{info}/METADATA",
                f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n"
                + "".join(f"Requires-Dist: {item}\n" for item in dependencies),
            )
            archive.writestr(
                f"{info}/WHEEL",
                "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            )
            archive.writestr(f"{info}/RECORD", "")
        return path

    wheel(wheels, "scopecat", "1", ("schema-runtime>=1",))
    wheel(wheels, "scopecat-server", "1", ("scopecat==1",))
    wheel(wheels, "schema-runtime", "1")
    wheel(index / "schema-runtime", "schema-runtime", "2")
    (bundle / environments.MANIFEST).write_text("verified delivery fixture")
    constraints = bundle / "requirements.lock"
    constraints.write_text("scopecat==1\nscopecat-server==1\nschema-runtime==1\n")
    source = tmp_path / "source"
    source.mkdir()
    declaration = source / "pyproject.toml"
    declaration.write_text(
        '[project]\nname="experiment"\nversion="1"\ndependencies=["schema-runtime>=1"]\n'
    )
    runtime = Mock(home=tmp_path / "application", lock=threading.Lock())
    runtime.installation.return_value = SimpleNamespace(
        python=Path(sys.executable), environment={"scopecat": "1", "server": "1"}
    )
    monkeypatch.setattr(environments, "_bundle", lambda _: bundle)
    monkeypatch.setattr(
        environments, "_independent_python", lambda *_: Path(sys.executable)
    )
    monkeypatch.setattr("scopecat_server.author_environment.capture", Mock())
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("UV_NO_CONFIG", "true")
    monkeypatch.delenv("UV_OFFLINE", raising=False)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        functools.partial(SimpleHTTPRequestHandler, directory=str(index)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("UV_DEFAULT_INDEX", f"http://127.0.0.1:{server.server_port}")
    try:
        available = tmp_path / "available.lock"
        subprocess.run(  # noqa: S603 - owned resolver and loopback fixture
            [
                find_uv_bin(),
                "pip",
                "compile",
                str(declaration),
                "--python",
                sys.executable,
                "--find-links",
                str(wheels),
                "--output-file",
                str(available),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        assert "schema-runtime==2" in available.read_text()
        python = environments.prepare_execution_environment(runtime, source)
        result = subprocess.check_output(  # noqa: S603 - test-owned interpreter
            [
                str(python),
                "-I",
                "-c",
                "import schema_runtime; print(schema_runtime.VERSION)",
            ],
            text=True,
        )
        assert result.strip() == "1"
        retained = python.parent.parent.parent / "requirements.lock"
        before = retained.read_bytes()
        declaration.write_text(declaration.read_text().replace(">=1", ">=2"))
        with pytest.raises(ValueError, match="依赖准备未完成"):
            environments.prepare_execution_environment(runtime, source)
        assert python.is_file() and retained.read_bytes() == before
        assert (
            len(list((runtime.home / "environments").glob("*/environment.json"))) == 1
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
