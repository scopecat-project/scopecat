"""Qualify shipped Help cells in offline installed environments and one application.

Usage: python scripts/verify_installed_help_kernels.py <toolchain-payload> <fresh-dir>
Uses existing delivery/toolchain artifacts without changing their manifests.
No browser, native editor, snapshot restore or per-course service is exercised.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import runpy
import subprocess
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast


def python_in(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def install_and_verify(payload: Path, work: Path) -> None:
    # The shipped installer is stdlib-only; use its manifest checks before unpacking.
    installer = runpy.run_path(str(payload / "install.py"))
    cast("Callable[[Path], object]", installer["verify_bundle"])(payload)
    archive = payload / "toolchain/python.tar"
    if not archive.is_file():
        raise ValueError("Use an existing full application payload with its toolchain")
    work.mkdir(parents=True, exist_ok=False)
    base = work / "application-base"
    with tarfile.open(archive) as stream:
        stream.extractall(base, filter="data")
    python = base / ("python.exe" if os.name == "nt" else "bin/python3")
    cache = work / "empty-cache"
    cache.mkdir()
    temporary = work / "temporary"
    temporary.mkdir()
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "SCOPECAT_DAEMON_URL", "JUPYTER_PATH"):
        env.pop(name, None)
    env.update(
        UV_OFFLINE="1",
        UV_CACHE_DIR=str(cache),
        UV_PYTHON_DOWNLOADS="never",
        PYTHONNOUSERSITE="1",
        PYTHONUTF8="1",
        IPYTHONDIR=str(work / "ipython"),
        JUPYTER_CONFIG_DIR=str(work / "jupyter-config"),
        JUPYTER_DATA_DIR=str(work / "jupyter-data"),
        TMPDIR=str(temporary),
        TMP=str(temporary),
        TEMP=str(temporary),
    )
    application = work / "application-python"
    for command in (
        [str(python), "-I", str(payload / "install.py"), str(application)],
        [
            str(python_in(application)),
            "-I",
            str(Path(__file__).resolve()),
            str(payload),
            str(work),
            "--installed",
        ],
    ):
        subprocess.run(command, cwd=work, env=env, check=True)  # noqa: S603


def verify_installed(payload: Path, work: Path) -> None:
    import ctypes
    from copy import deepcopy
    from importlib import import_module
    from importlib.resources import files

    import httpx2
    from nbclient import NotebookClient
    from nbclient.exceptions import CellExecutionError
    from nbformat import NotebookNode

    from lab_tools.application_runtime import ApplicationRuntime
    from lab_tools.bundle import file_hash, verify_bundle
    from lab_tools.notebook import kernel_command
    from lab_tools.notebook_io import notebook_io
    from lab_tools.notebook_journey import current, prepare
    from lab_tools.verify_groups import GROUP_CHECKS, GROUP_REOPEN_CELLS
    from scopecat.daemon.views import RunSummary, RunSummaryPage

    # Detached daemons must remain waitable in containers without a reaping init.
    if sys.platform == "linux":
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    assert os.environ["UV_OFFLINE"] == "1"
    assert Path(os.environ["UV_CACHE_DIR"]) == work / "empty-cache"
    origins = {}
    for name in ("scopecat", "scopecat_server", "lab_tools", "lab_teaching"):
        origin = Path(str(import_module(name).__file__)).resolve()
        assert origin.is_relative_to(Path(sys.prefix)), (name, origin)
        origins[name] = str(origin)
    # Installation must not seed a cache that conceals missing author dependencies.
    author_cache = work / "author-empty-cache"
    author_cache.mkdir()
    os.environ["UV_CACHE_DIR"] = str(author_cache)
    document = cast("dict[str, object]", cast("object", verify_bundle(payload)))
    nbformat = notebook_io()
    runtime = ApplicationRuntime(work / "application")
    runtime.configure(static_dir=payload / "gui", delivery_root=payload)
    material = files("lab_teaching.course_material").joinpath("lessons")
    evidence: dict[str, object] = {
        "bundle_sha256": file_hash(payload / "bundle.json"),
        "build_id": document["build_id"],
        "installed_origins": origins,
        "offline": True,
        "cache_started_empty": True,
        "author_cache_started_empty": True,
        "native_editor": "not evaluated",
        "snapshot_restore": "not evaluated",
    }

    def runs() -> tuple[RunSummary, ...]:
        response = httpx2.get(
            runtime.start().base_url + "/api/v1/runs", trust_env=False, timeout=30
        )
        response.raise_for_status()
        return RunSummaryPage.model_validate_json(response.content).items

    def execute(
        root: Path,
        cells: list[NotebookNode],
        name: str,
        *,
        python: Path | None = None,
    ) -> None:
        _, env = kernel_command(
            root,
            python=str(python or python_in(root / ".venv")),
            source_path=False,
            kernel_home=work / f"kernel-{name}",
        )
        os.environ["JUPYTER_PATH"] = env["JUPYTER_PATH"]
        notebook = nbformat.v4.new_notebook(cells=deepcopy(cells))
        try:
            NotebookClient(
                notebook,
                kernel_name="scopecat-lab",
                timeout=180,
                startup_timeout=120,
                resources={"metadata": {"path": str(root / "notebooks")}},
            ).execute()
        finally:
            nbformat.write(notebook, work / f"{name}.ipynb")

    try:
        first_endpoint = runtime.start()
        assert runs() == ()
        parameters = prepare(runtime, str(work), "parameters")
        groups = prepare(runtime, str(work), "groups")
        assert runtime.source(parameters.directory) != runtime.source(groups.directory)
        assert runs() == ()
        lessons: dict[str, list[NotebookNode]] = {}
        for journey in (parameters, groups):
            source = journey.directory
            assert not (source / ".vscode/tasks.json").exists()
            shipped = material.joinpath(f"{journey.topic}.ipynb").read_bytes()
            assert journey.notebook.read_bytes() == shipped
            evidence[f"{journey.topic}_notebook_sha256"] = hashlib.sha256(
                shipped
            ).hexdigest()
            notebook = nbformat.read(journey.notebook, as_version=4)
            cells = cast("list[NotebookNode]", notebook.cells)
            lessons[journey.topic] = [c for c in cells if c["cell_type"] == "code"]
            # Execute the actual shipped guard in the wrong installed interpreter.
            try:
                execute(
                    source,
                    lessons[journey.topic][:1],
                    f"{journey.topic}-wrong-kernel",
                    python=Path(sys.executable),
                )
            except CellExecutionError as error:
                if "Select Kernel" not in str(error):
                    raise AssertionError("Unexpected wrong-kernel failure") from error
            else:
                raise AssertionError("The application kernel admitted author code")

        execute(
            parameters.directory,
            [*lessons["parameters"], nbformat.v4.new_code_cell(PARAMETERS_SAVE)],
            "parameters-executed",
        )
        execute(
            groups.directory,
            [*lessons["groups"], nbformat.v4.new_code_cell(GROUP_CHECKS)],
            "groups-executed",
        )
        before = runs()
        assert len(before) == 3
        retained = {}
        for journey in (parameters, groups):
            code = journey.directory / "src/my_experiment/teaching.py"
            code.write_text(code.read_text() + "\n# retained author note\n")
            notebook = nbformat.read(journey.notebook, as_version=4)
            cast("list[NotebookNode]", notebook.cells).append(
                nbformat.v4.new_code_cell("# My retained notes")
            )
            nbformat.write(notebook, journey.notebook)
            retained[journey.topic] = (code.read_bytes(), journey.notebook.read_bytes())
        runtime.stop()
        assert runtime.status().state == "stopped"
        runtime = ApplicationRuntime(work / "application")
        restarted_endpoint = runtime.start()
        assert restarted_endpoint != first_endpoint
        for journey in (parameters, groups):
            assert current(runtime, journey.topic) == journey
            assert prepare(runtime, topic=journey.topic) == journey
            assert (
                (journey.directory / "src/my_experiment/teaching.py").read_bytes(),
                journey.notebook.read_bytes(),
            ) == retained[journey.topic]
        execute(
            parameters.directory,
            [
                lessons["parameters"][0],
                lessons["parameters"][1],
                nbformat.v4.new_code_cell(PARAMETERS_REOPEN),
            ],
            "parameters-reopened",
        )
        execute(
            groups.directory,
            [nbformat.v4.new_code_cell(c) for c in GROUP_REOPEN_CELLS],
            "groups-reopened",
        )
        assert runs() == before
        evidence.update(
            retained_runs=3,
            run_ids=[run.run_id for run in before],
            wrong_kernel="rejected",
            first_pid=first_endpoint.pid,
            restarted_pid=restarted_endpoint.pid,
        )
    finally:
        runtime.stop()
    assert runtime.status().state == "stopped"
    evidence.update(result="passed", cleanup="stopped")
    (work / "acceptance.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )


PARAMETERS_SAVE = """\
import json
project = sc.open_project()
assert shots.shape == (7, 64)
np.save(project.root / 'parameters-shots.npy', shots, allow_pickle=False)
params[Drive]['q0'].frequency = 5.152
saved = params.save(note='retained installed choice')
(project.root / 'parameters-run.json').write_text(json.dumps({
    'run_id': run.id, 'parameters': saved.ref.model_dump(mode='json'),
}), encoding='utf-8')
session.close()
"""

PARAMETERS_REOPEN = """\
import json
project = sc.open_project()
from my_experiment.parameters import Drive
bookmark = json.loads((project.root / 'parameters-run.json').read_text())
assert params[Drive]['q0'].frequency == 5.152
assert params.version.ref.model_dump(mode='json') == bookmark['parameters']
before_runs = session.list_runs()
run = session.run(bookmark['run_id'])
np.testing.assert_array_equal(run.measurements()['iq'].require_values(),
    np.load(project.root / 'parameters-shots.npy', allow_pickle=False))
assert session.list_runs() == before_runs
session.close()
"""


class Arguments(Protocol):
    payload: Path
    work: Path
    installed: bool


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("work", type=Path)
    parser.add_argument("--installed", action="store_true", help=argparse.SUPPRESS)
    args = cast("Arguments", cast("object", parser.parse_args()))
    action = verify_installed if args.installed else install_and_verify
    action(args.payload.resolve(), args.work.resolve())
