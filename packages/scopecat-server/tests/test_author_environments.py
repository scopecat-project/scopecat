"""Execution dependencies do not have to be installed in the application."""

import os
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

from scopecat.application import LabApplication
from scopecat.author_workspaces import LocalAuthorWorkspaces, author_bindings_path
from scopecat.daemon.endpoint import resolve_daemon_endpoint

from scopecat_server.author_registration import register_author_workspace
from scopecat_server.lifecycle import initialize_project, start_project, stop_project


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
