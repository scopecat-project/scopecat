"""Actual IPython cells use one workspace while requests retain their source."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import nbformat
import pytest
from IPython.core.interactiveshell import InteractiveShell
from jupyter_client import KernelManager
from nbclient import NotebookClient
from testing.app_support.project import create_project

from scopecat.project import open_project
from scopecat_server.lifecycle import start_project, stop_project


def test_notebook_workspace_cells(
    tmp_path: Path, notebook_imports: pytest.MonkeyPatch
) -> None:
    notebook_module = importlib.import_module("scopecat.notebook_workspace")
    shell = InteractiveShell()
    notebook_imports.setattr(notebook_module, "_shell", lambda: shell)
    root = tmp_path / "notebook"
    create_project(root)
    project = open_project(root)
    start_project(project, timeout=120)
    # A retained environment must not override the registered workspace's
    # compatible client interpreter. Keep its contents throughout the journey.
    retained = root / ".venv/retained.txt"
    retained.parent.mkdir()
    retained.write_text("old environment stays here")
    from scopecat.author_workspaces import local_author_workspaces

    assert any(item.root == root for item in local_author_workspaces(root))
    shell.user_ns["root"] = root
    other = tmp_path / "other"
    create_project(other)
    shell.user_ns["other"] = other

    # The client may have no server and different analysis dependencies.
    # Only the selected execution interpreter can identify a runnable revision.
    from scopecat.execution_environment import execution_packages

    def reject_client_execution_capture(*args, **kwargs):
        raise AssertionError("Client attempted to capture execution dependencies")

    notebook_imports.setattr(
        "scopecat.execution_environment.execution_packages",
        reject_client_execution_capture,
    )

    def cell(code: str) -> None:
        result = shell.run_cell(code)
        assert result.error_before_exec is None
        assert result.error_in_exec is None

    try:
        # Registration must not waive SDK compatibility checks before imports.
        from scopecat import execution_environment

        actual_distribution = execution_environment.distribution

        def incompatible_sdk(name):
            dist = actual_distribution(name)
            return (
                SimpleNamespace(version="0.1.99", requires=dist.requires)
                if name == "scopecat"
                else dist
            )

        with notebook_imports.context() as incompatible:
            incompatible.setattr(
                execution_environment,
                "distribution",
                incompatible_sdk,
            )
            result = shell.run_cell("import scopecat as sc; sc.notebook(root)")
            assert isinstance(result.error_in_exec, ValueError)
            message = str(result.error_in_exec)
            assert "execution dependency scopecat==" in message
            assert "does not accept installed 0.1.99" in message
        cell("""
import scopecat as sc
session = sc.notebook(root)
assert sc.notebook(root) is session
import my_experiment.teaching as experiments
from my_experiment.teaching import teaching_rabi as rabi
from my_experiment.setup import open_parameters
params = open_parameters(session)
assert 'branch teaching-table' in repr(session)
collection = session.create_record_collection("Notebook context")
selected = session.use(collection=collection.id, operator="notebook-author")
assert sc.notebook(root).selection == selected
before = rabi()
prepared = session.prepare(before, parameters=params)
generation = session.state().generation
assert experiments.teaching_rabi().values['seed'] == 200
assert session.state().generation == generation
assert 'live' in repr(session)
assert '<pre>' in session._repr_html_()
try:
    sc.notebook(other)
except ValueError as error:
    assert 'Close' in str(error)
else:
    raise AssertionError('workspace switched implicitly')
""")
        source = root / "src/my_experiment/teaching.py"
        original = source.read_text(encoding="utf-8")
        changed = original.replace("seed: int = 200", "seed: int = 401")
        source.write_text(changed, encoding="utf-8")
        cell("""
assert rabi().values['seed'] == 401
assert experiments.teaching_rabi().values['seed'] == 401
assert before.values['seed'] == 200
session.use(operator="after-refresh")
assert session.selection.collection == collection.id
assert prepared.request.actor == "notebook-author"
assert prepared.request.record_collection == collection.id
# Reading an old revision must not redirect subsequent live calls.
pinned = session.load_experiment(
    before.declaration, code_revision=before.declaration.code_revision
)
assert rabi().values['seed'] == 401
assert rabi().declaration.code_revision != before.declaration.code_revision
""")
        # New module import succeeds at the next cell with no refresh/import magic.
        extra = changed.replace('id="teaching.rabi"', 'id="teaching.extra"').replace(
            "def teaching_rabi(", "def extra_rabi("
        )
        (source.parent / "extra.py").write_text(extra, encoding="utf-8")
        cell("""
import my_experiment.extra as extra
assert extra.extra_rabi().declaration.id == 'teaching.extra'
""")
        # A new attribute in an already imported module updates its module alias.
        addition = (
            changed[changed.index("@sc.experiment") :]
            .replace('id="teaching.rabi"', 'id="teaching.another"')
            .replace("def teaching_rabi(", "def new_name(")
        )
        source.write_text(changed + "\n" + addition, encoding="utf-8")
        cell("assert experiments.new_name().values['seed'] == 401")
        cell("assert sc.notebook(root, live=False) is session")
        source.write_text(changed.replace("401", "402"), encoding="utf-8")
        cell("assert rabi().values['seed'] == 401")
        cell(
            "assert sc.notebook(root, live=True) is session\n"
            "assert rabi().values['seed'] == 402"
        )
        source.write_text(changed + "\ninvalid python !\n", encoding="utf-8")
        cell("""
from scopecat.daemon.preparation import AuthorPreparationFailed
try:
    rabi()
except AuthorPreparationFailed:
    pass
else:
    raise AssertionError('invalid saved source silently used old code')
try:
    session.prepare('teaching.rabi', parameters=params)
except AuthorPreparationFailed:
    pass
else:
    raise AssertionError('ID-based preparation silently used old code')
""")
        source.write_text(changed, encoding="utf-8")
        cell("""
assert rabi().values['seed'] == 401
# The old prepared experiment still runs its original source after all edits.
run = prepared.run().wait(timeout=120).result()
assert run.request.operator == "notebook-author"
number = session.run_number(run)
assert session.run(number).id == run.id
run_id = run.id
session.close()
session = sc.notebook(root)
assert session.selection.collection is None
assert session.run(number, collection=collection.id).id == run_id
assert rabi().values['seed'] == 401
assert sc.notebook(root) is session
session.close()
""")
        assert notebook_module._workspace is None
        # A real kernel must obey the same compatibility rules as IPython cells.
        document = nbformat.v4.new_notebook(
            cells=[
                nbformat.v4.new_code_cell(
                    "import sys\nfrom pathlib import Path\nimport scopecat as sc\n"
                    "assert Path(sys.prefix).resolve() != "
                    "(Path.cwd() / '.venv').resolve()\n"
                    "session = sc.notebook()\n"
                    "from my_experiment.teaching import teaching_rabi\n"
                    "assert teaching_rabi().values['seed'] == 401\n"
                    "session.close()"
                )
            ]
        )
        manager = KernelManager(kernel_name="python3")
        assert manager.kernel_spec is not None
        manager.kernel_spec.argv = [
            sys.executable,
            "-m",
            "ipykernel_launcher",
            "-f",
            "{connection_file}",
        ]
        try:
            NotebookClient(
                document,
                km=manager,
                timeout=120,
                resources={"metadata": {"path": str(root)}},
            ).execute()
        finally:
            if manager.has_kernel:
                manager.shutdown_kernel(now=True)
        notebook_imports.setattr(
            "scopecat.execution_environment.execution_packages", execution_packages
        )
        other_project = open_project(other)
        start_project(other_project, timeout=120)
        try:
            cell("""
session = sc.notebook(other)
try:
    rabi()
except ValueError as error:
    assert 'another workspace' in str(error)
else:
    raise AssertionError('old project alias rebound to a different project')
import my_experiment.teaching as experiments
assert experiments.teaching_rabi().values['seed'] == 200
session.close()
""")
        finally:
            stop_project(other_project)
    finally:
        if notebook_module._workspace is not None:
            notebook_module._workspace.close()
        stop_project(project)
        assert retained.read_text() == "old environment stays here"


@pytest.mark.parametrize("other_registered", [False, True])
def test_unregistered_workspace_keeps_kernel_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, other_registered: bool
) -> None:
    from scopecat.author_workspaces import (
        LocalAuthorWorkspace,
        LocalAuthorWorkspaces,
        author_bindings_path,
    )

    root = tmp_path / "unregistered"
    create_project(root)
    (root / ".venv").mkdir()
    notebook_module = importlib.import_module("scopecat.notebook_workspace")
    monkeypatch.setattr(notebook_module, "_shell", InteractiveShell)
    if other_registered:
        path = author_bindings_path(root)
        path.parent.mkdir(parents=True)
        path.write_text(
            LocalAuthorWorkspaces(
                service_root=root,
                items=(
                    LocalAuthorWorkspace(
                        id="other",
                        name="Other",
                        root=tmp_path / "other",
                        python=Path(sys.executable),
                    ),
                ),
            ).model_dump_json()
        )
    with pytest.raises(RuntimeError, match="Select Kernel"):
        notebook_module.notebook(root)


def test_independent_kernel_discovers_application_and_reconnects(
    tmp_path: Path, author_kernel_python: Path
) -> None:
    """An editor-launched kernel needs neither an injected URL nor four paths."""
    import os

    from scopecat_app.application_runtime import ApplicationRuntime

    root = tmp_path / "author"
    create_project(root)
    before = {str(path.relative_to(root)) for path in root.rglob("*")}
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>application</html>")
    runtime = ApplicationRuntime(tmp_path / "application")
    runtime.configure(static_dir=gui)
    identity = runtime.register_source(root, python=author_kernel_python)
    assert {str(path.relative_to(root)) for path in root.rglob("*")} == before
    assert not (root / "scopecat.runtime.toml").exists()
    manager = KernelManager(kernel_name="python3")
    assert manager.kernel_spec is not None
    manager.kernel_spec.argv = [
        str(author_kernel_python),
        "-m",
        "ipykernel_launcher",
        "-f",
        "{connection_file}",
    ]
    environment = dict(os.environ)
    for name in (
        "SCOPECAT_DAEMON_URL",
        "SCOPECAT_APPLICATION_ROOT",
        "PYTHONPATH",
        "PYTHONHOME",
    ):
        environment.pop(name, None)
    runtime.start()
    try:
        manager.start_kernel(cwd=str(root), env=environment)
        client = manager.blocking_client()
        client.start_channels()
        client.wait_for_ready(timeout=30)

        def cell(code: str) -> None:
            response = client.execute_interactive(code, timeout=120)
            assert response["content"]["status"] == "ok", response

        cell(
            "import scopecat as sc\n"
            "session = sc.notebook()\n"
            f"assert session.workspace_id == {identity!r}\n"
            "revision = session.state().active\n"
            "session.close()\n"
        )
        runtime.stop()
        cell(
            "try:\n"
            "    sc.notebook()\n"
            "except RuntimeError:\n"
            "    pass\n"
            "else:\n"
            "    raise AssertionError('offline application must fail')\n"
        )
        runtime.start()
        cell(
            "session = sc.notebook()\n"
            f"assert session.workspace_id == {identity!r}\n"
            "assert session.state().active == revision\n"
            "session.close()\n"
        )
        client.stop_channels()
    finally:
        if manager.has_kernel:
            manager.shutdown_kernel(now=True)
        runtime.stop()
    assert not (root / ".scopecat").exists()
