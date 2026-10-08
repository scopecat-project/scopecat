"""Execution dependencies do not have to be installed in the application."""

import os
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest
from scopecat.application import LabApplication
from scopecat.author_workspaces import LocalAuthorWorkspaces, author_bindings_path
from scopecat.daemon.endpoint import resolve_daemon_endpoint

from scopecat_server import author_environment
from scopecat_server.author_registration import register_author_workspace
from scopecat_server.lifecycle import initialize_project, start_project, stop_project


@pytest.mark.parametrize("action", ["capture", "capture-driver", "check"])
@pytest.mark.parametrize(
    "stderr", [None, "partial diagnostic", b"partial diagnostic\xff"]
)
def test_environment_timeout_retains_operation_and_stderr(
    monkeypatch: pytest.MonkeyPatch, action: str, stderr: str | bytes | None
) -> None:
    failure = subprocess.TimeoutExpired(
        "environment worker", 60, output=b"private result", stderr=stderr
    )
    run = Mock(side_effect=failure)
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="timed out") as caught:
        author_environment._call(Path("author-python"), action, "private request")
    message = str(caught.value)
    assert f"Author environment {action} timed out after 60 seconds" in message
    assert "author-python" in message
    assert ("partial diagnostic" in message) == (stderr is not None)
    assert "private result" not in message
    assert "private request" not in message
    assert caught.value.__cause__ is failure
    run.assert_called_once()
    assert run.call_args.kwargs["timeout"] == 60


def test_environment_timeout_bounds_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = subprocess.TimeoutExpired(
        "environment worker",
        60,
        stderr=b"old diagnostic" + b"x" * 8192 + b"last message",
    )
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=failure))
    with pytest.raises(ValueError, match="timed out") as caught:
        author_environment._call(Path("author-python"), "capture", "{}")
    message = str(caught.value)
    assert "old diagnostic" not in message
    assert "[earlier stderr omitted; retaining last 8 KiB]" in message
    assert message.endswith("last message")
    assert len(message) < 8400


def test_environment_timeout_keeps_real_child_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = subprocess.run
    children: list[subprocess.TimeoutExpired] = []

    def synthetic_worker(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 60
        assert command[-1] == "capture"
        # Only the synthetic test process uses a short deadline.
        try:
            return run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys,time; "
                        "print('synthetic import reached', "
                        "file=sys.stderr, flush=True); "
                        "time.sleep(30)"
                    ),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=2,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            children.append(error)
            raise

    monkeypatch.setattr(subprocess, "run", synthetic_worker)
    with pytest.raises(ValueError, match="timed out") as caught:
        author_environment._call(Path(sys.executable), "capture", "{}")
    assert "synthetic import reached" in str(caught.value)
    assert len(children) == 1
    assert caught.value.__cause__ is children[0]
    assert isinstance(children[0].stderr, bytes)


def environment(root: Path, version: int) -> Path:
    uv = shutil.which("uv")
    assert uv
    subprocess.run(  # noqa: S603 - fixture-owned interpreter and arguments
        [uv, "venv", "--python", sys.executable, "--system-site-packages", str(root)],
        check=True,
    )
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = Path(
        subprocess.check_output(  # noqa: S603 - fixed sysconfig query
            [
                str(python),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            text=True,
        ).strip()
    )
    (site / "test-framework.pth").write_text(
        f"import site; site.addsitedir({sysconfig.get_path('purelib')!r})\n"
    )
    wheel = root / f"author_extra-{version}.0-py3-none-any.whl"
    info = f"author_extra-{version}.0.dist-info"
    with zipfile.ZipFile(wheel, "w") as output:
        output.writestr("author_extra.py", f"SCALE = {version}\n")
        output.writestr(
            f"{info}/METADATA",
            f"Metadata-Version: 2.1\nName: author-extra\nVersion: {version}.0\n",
        )
        output.writestr(
            f"{info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n",
        )
        output.writestr(f"{info}/RECORD", "")
    subprocess.run(  # noqa: S603 - fixture-owned wheel
        [uv, "pip", "install", "--python", str(python), str(wheel)], check=True
    )
    return python


def test_retained_tasks_use_their_environment_after_selection_and_restart(
    tmp_path: Path,
) -> None:
    first = environment(tmp_path / "environment-one", 1)
    second = environment(tmp_path / "environment-two", 2)
    project = initialize_project(tmp_path / "application")
    source = project.root / "src/scopecat_lab/authored/signal.py"
    source.write_text(
        source.read_text().replace(
            "return scale /",
            "from author_extra import SCALE\n    return SCALE * scale /",
        )
    )
    (project.root / "pyproject.toml").write_text(
        '[project]\nname="example"\nversion="0.1"\ndependencies=["author-extra>=1"]\n'
    )
    registered = register_author_workspace(project.root, project.root, python=first)
    start_project(project, timeout=60)
    try:
        with project.authoring() as author:
            selection = author.setup.import_template(
                author.setup.templates()[0], name="bench"
            ).selection
            author.use(selection=selection)
            initial = author.state()
            old = author.prepare("signal")
            plan = old.save_plan("retained", saved_by="test")
            path = author_bindings_path(project.root)
            registry = LocalAuthorWorkspaces.model_validate_json(path.read_bytes())
            path.write_text(
                registry.model_copy(
                    update={
                        "items": (
                            registered.model_copy(
                                update={"python": second, "retained_pythons": (first,)}
                            ),
                        )
                    }
                ).model_dump_json()
            )
            changed = author.refresh_authors(expected_generation=initial.generation)
            assert changed.active != initial.active
            run = author.prepare("signal").run().wait(timeout=60).result()
            assert list(run.measurements()["result"].require_values()) == [2.0]
            run = old.run().wait(timeout=60).result()
            assert list(run.measurements()["result"].require_values()) == [1.0]
    finally:
        stop_project(project)
    start_project(project, timeout=60)
    try:
        with project.authoring() as author:
            run = author.prepare_plan(plan.ref).run().wait(timeout=60).result()
            assert list(run.measurements()["result"].require_values()) == [1.0]
        with LabApplication().connect(resolve_daemon_endpoint(project.root)) as lab:
            assert lab.get_run(run.id).id == run.id
        import importlib.util

        assert importlib.util.find_spec("author_extra") is None
    finally:
        stop_project(project)
