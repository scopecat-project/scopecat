"""Qualify offline application installation and online author Help preparation.

Usage: python scripts/verify_installed_help_kernels.py <toolchain-payload> <fresh-dir>
Add --runtime-python PYTHON for an installed runtime and verified format 2 resources.
Without that option, the legacy delivery/toolchain installer remains supported.
Includes explicit snapshot recovery with a separately backed-up author fixture.
Pass --all-topics for the slower qualification of all seven shipped Help topics.
Does not restore Help continuation or exercise a browser/native editor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Protocol, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if TYPE_CHECKING:
    from nbformat import NotebookNode

    from scopecat_app.application_runtime import ApplicationRuntime
    from scopecat_app.bundle import Bundle


def python_in(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def install_and_verify(payload: Path, work: Path, *, all_topics: bool = False) -> None:
    started = perf_counter()
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
    env = journey_environment(work)
    application = work / "application-python"
    for name, command in (
        (
            "installation",
            [str(python), "-I", str(payload / "install.py"), str(application)],
        ),
        (
            "journey",
            [
                str(python_in(application)),
                "-I",
                str(Path(__file__).resolve()),
                str(payload),
                str(work),
                "--installed",
                *(["--all-topics"] if all_topics else []),
            ],
        ),
    ):
        phase = perf_counter()
        subprocess.run(command, cwd=work, env=env, check=True)  # noqa: S603
        print(f"{name}: {perf_counter() - phase:.3f}s", flush=True)
    receipt = work / "acceptance.json"
    evidence = cast(
        "dict[str, object]", json.loads(receipt.read_text(encoding="utf-8"))
    )
    evidence["total_seconds"] = perf_counter() - started
    receipt.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def journey_environment(work: Path) -> dict[str, str]:
    """Isolate a single invocation without borrowing user or repository imports."""
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
    return env


def verify_runtime(
    payload: Path, work: Path, python: Path, *, all_topics: bool = False
) -> None:
    """Run and verify resources with an existing application interpreter."""
    if json.loads((payload / "bundle.json").read_text(encoding="utf-8"))["format"] != 2:
        raise ValueError("An installed runtime requires format 2 author resources")
    work.mkdir(parents=True, exist_ok=False)
    env = journey_environment(work)
    # A format 2 app carries author requirements, not an offline dependency cache.
    env["UV_OFFLINE"] = "0"
    subprocess.run(  # noqa: S603 - explicitly selected installed runtime
        [
            str(python),
            "-I",
            str(Path(__file__).resolve()),
            str(payload),
            str(work),
            "--installed",
            *(["--all-topics"] if all_topics else []),
        ],
        cwd=work,
        env=env,
        check=True,
    )


def verified_author_payload(payload: Path, work: Path) -> tuple[Path, Bundle]:
    from scopecat_app.author_resources import verify_resources

    document = verify_resources(payload)
    author_payload = payload
    if document["format"] == 1:
        # Compatibility for jobs still providing a full legacy installer payload.
        from scripts.app_build.author_resources import build as build_author_resources

        author_payload = build_author_resources(
            payload, work / "author-resources", python=Path(sys.executable)
        )
        verify_resources(author_payload)
    return author_payload, document


def verify_installed(payload: Path, work: Path, *, all_topics: bool = False) -> None:
    import ctypes
    from copy import deepcopy
    from importlib import import_module
    from importlib.resources import files

    import httpx2
    from nbclient import NotebookClient
    from nbclient.exceptions import CellExecutionError
    from testing.app_support.notebook_io import notebook_io
    from testing.app_support.verify_groups import (
        ADD_ANALYSIS,
        GROUP_CHECKS,
        GROUP_REOPEN_CELLS,
    )

    from scopecat.application_instance import open_instance
    from scopecat.automation import ProcedureRun
    from scopecat.daemon.endpoint import DaemonEndpointRecord
    from scopecat.daemon.views import DaemonHealth, RunSummary, RunSummaryPage
    from scopecat.records.practice import PracticeScope
    from scopecat_app.application_runtime import ApplicationRuntime
    from scopecat_app.author_environment import (
        create_client_environment,
        prepare_execution_environment,
    )
    from scopecat_app.bundle import file_hash
    from scopecat_app.notebook import kernel_command
    from scopecat_app.notebook_journey import current, prepare
    from scopecat_server.author_registration import (  # noqa: TID251 - installed recovery integration
        register_author_workspace,
    )
    from scopecat_server.scaffold import (  # noqa: TID251 - installed source fixture
        write_author_scaffold,
    )
    from scopecat_server.snapshots import (  # noqa: TID251 - installed recovery integration
        create_snapshot,
        restore_snapshot,
        verify_snapshot,
    )

    # Detached daemons must remain waitable in containers without a reaping init.
    if sys.platform == "linux":
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    assert Path(os.environ["UV_CACHE_DIR"]) == work / "empty-cache"
    origins = {}
    for name in ("scopecat", "scopecat_server", "scopecat_app", "scopecat_tutorials"):
        origin = Path(str(import_module(name).__file__)).resolve()
        assert origin.is_relative_to(Path(sys.prefix)), (name, origin)
        origins[name] = str(origin)
    # Installation must not seed a cache that conceals missing author dependencies.
    author_cache = work / "author-empty-cache"
    author_cache.mkdir()
    author_payload, document = verified_author_payload(payload, work)
    assert os.environ["UV_OFFLINE"] == ("1" if document["format"] == 1 else "0")
    nbformat = notebook_io()
    runtime = ApplicationRuntime(work / "application")
    material = files("scopecat_tutorials.course_material").joinpath("lessons")
    timings: dict[str, float] = {}
    application_checks: dict[str, object] = {
        "result": "incomplete",
        "phase": "not-started",
    }
    evidence: dict[str, object] = {
        "result": "incomplete",
        "application_checks": application_checks,
        "bundle_sha256": file_hash(payload / "bundle.json"),
        "author_resources_sha256": file_hash(author_payload / "bundle.json"),
        "author_wheels": sorted(
            path.name for path in (author_payload / "wheels").glob("*.whl")
        ),
        "build_id": document.get("build_id"),
        "payload_format": document["format"],
        "runtime": document["runtime"],
        "sources": document["sources"],
        "installed_python": sys.executable,
        "carrier": "legacy-installer"
        if document["format"] == 1
        else "installed-runtime",
        "installed_origins": origins,
        "application_installation_offline": True if document["format"] == 1 else None,
        "cache_started_empty": True,
        "author_cache_started_empty": True,
        "native_editor": "not evaluated",
        "help_continue_restore": "not evaluated",
        "phase_seconds": timings,
    }

    def runs() -> tuple[RunSummary, ...]:
        response = httpx2.get(
            runtime.start().base_url + "/api/v1/runs", trust_env=False, timeout=30
        )
        response.raise_for_status()
        return RunSummaryPage.model_validate_json(response.content).items

    def empty_workbench() -> DaemonEndpointRecord:
        record = runtime.start()
        assert runtime.start() == record
        with httpx2.Client(
            base_url=record.base_url, trust_env=False, timeout=30
        ) as client:
            page = client.get("/")
            assert page.status_code == 200
            assert page.content == (payload / "gui/index.html").read_bytes()
            response = client.get("/api/v1/runs")
            assert response.status_code == 200
            assert response.json()["items"] == []
        return record

    def source_files(root: Path) -> dict[str, str]:
        return {
            str(path.relative_to(root)): file_hash(path)
            for path in root.rglob("*")
            if path.is_file()
        }

    def execute(
        root: Path,
        cells: list[NotebookNode],
        name: str,
        *,
        python: Path | None = None,
    ) -> None:
        started = perf_counter()
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
                skip_cells_with_tag="scopecat-desktop-open",
                kernel_name="scopecat-lab",
                timeout=180,
                startup_timeout=120,
                resources={"metadata": {"path": str(root / "notebooks")}},
            ).execute()
        finally:
            nbformat.write(notebook, work / f"{name}.ipynb")
            timings[name] = perf_counter() - started

    try:
        phase = perf_counter()
        # Practice uses this invocation's cache (seeded by the legacy installer
        # or populated online), never the independent cold Help author cache.
        application_checks["phase"] = "empty-application"
        registered_source = work / "registered-source" / "实验代码"
        write_author_scaffold(registered_source)
        (registered_source / "owner-notes.txt").write_text(
            "保留实验记录\n", encoding="utf-8"
        )
        selected = runtime.configure(
            static_dir=payload / "gui", delivery_root=author_payload
        )
        identity = runtime.register_source(registered_source)
        retained_source = source_files(registered_source)
        application_checks.update(
            source_id=identity,
            source_files=retained_source,
            environment=selected.environment,
            practice_cache="invocation cache (online for format 2)",
        )
        empty_endpoint = empty_workbench()
        application_checks["practice_pid"] = empty_endpoint.pid
        application_checks["empty_workbench_before_practice"] = "passed"
        application_checks["phase"] = "manual-practice"
        with httpx2.Client(
            base_url=empty_endpoint.base_url, trust_env=False, timeout=30
        ) as client:
            response = client.post(
                "/api/v1/practice", json={"request_key": "installed-practice"}
            )
            response.raise_for_status()
            scope = PracticeScope.model_validate(response.json())
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                task = ProcedureRun.model_validate(
                    client.get(f"/api/v1/procedures/{scope.procedure_id}").json()
                )
                if task.state == "waiting_for_input":
                    break
                assert task.state not in {"closed", "attention_required"}, task
                time.sleep(0.1)
            else:
                raise AssertionError("Practice did not reach manual input")
            note = Path(scope.directory) / "my-notes.txt"
            note.write_text("Keep this observation", encoding="utf-8")
            cleared = client.post(
                f"/api/v1/practice/{scope.id}/clear", json={"files": "preserve"}
            )
            cleared.raise_for_status()
            assert cleared.json()["state"] == "cleared", cleared.text
            assert note.read_text(encoding="utf-8") == "Keep this observation"
            assert runtime.start() == empty_endpoint
            assert client.get("/api/v1/runs").json()["items"] == []
        application_checks["same_service_practice_and_owned_cleanup"] = "passed"
        application_checks["phase"] = "runtime-requalification"
        runtime.stop()
        assert runtime.status().state == "stopped"
        runtime.select(
            runtime.qualify(
                selected.python, selected.static_dir, delivery_root=author_payload
            )
        )
        assert runtime.source(registered_source) == identity
        assert source_files(registered_source) == retained_source
        assert not (runtime.home / "host/services.sqlite").exists()
        # Restart before Help, both to check the still-empty requalified app and
        # to give its daemon the independent cold author cache, not practice's.
        assert list(author_cache.iterdir()) == []
        os.environ["UV_CACHE_DIR"] = str(author_cache)
        os.environ["UV_OFFLINE"] = "0"
        os.environ["UV_PYTHON_DOWNLOADS"] = "automatic"
        evidence["author_preparation"] = "online with independent uv Python"
        runtime = ApplicationRuntime(work / "application")
        first_endpoint = empty_workbench()
        assert first_endpoint != empty_endpoint
        assert runtime.source(registered_source) == identity
        assert source_files(registered_source) == retained_source
        application_checks.update(
            empty_workbench_after_requalification="passed",
            requalification_preserves_files_and_identity="passed",
            requalified_pid=first_endpoint.pid,
            author_cache_empty_after_practice=True,
            phase="awaiting-Help-restart",
        )
        timings["application_start_practice_and_requalification"] = (
            perf_counter() - phase
        )
        phase = perf_counter()
        assert runs() == ()
        parameters = prepare(runtime, str(work), "parameters")
        groups = prepare(runtime, str(work), "groups")
        timings["initial_start_and_prepare"] = perf_counter() - phase
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
            code.write_bytes(code.read_bytes() + b"\n# retained author note\n")
            notebook = nbformat.read(journey.notebook, as_version=4)
            cast("list[NotebookNode]", notebook.cells).append(
                nbformat.v4.new_code_cell("# My retained notes")
            )
            nbformat.write(notebook, journey.notebook)
            retained[journey.topic] = (code.read_bytes(), journey.notebook.read_bytes())
        phase = perf_counter()
        runtime.stop()
        assert runtime.status().state == "stopped"
        runtime = ApplicationRuntime(work / "application")
        restarted_endpoint = runtime.start()
        assert restarted_endpoint != first_endpoint
        assert runtime.source(registered_source) == identity
        assert source_files(registered_source) == retained_source
        assert note.read_text(encoding="utf-8") == "Keep this observation"
        assert not (runtime.home / "host/services.sqlite").exists()
        application_checks.update(
            application_reopen="passed",
            one_runtime_without_manager="passed",
            result="passed",
            phase="complete",
        )
        for journey in (parameters, groups):
            assert current(runtime, journey.topic) == journey
            assert prepare(runtime, topic=journey.topic) == journey
            assert (
                (journey.directory / "src/my_experiment/teaching.py").read_bytes(),
                journey.notebook.read_bytes(),
            ) == retained[journey.topic]
        timings["restart_and_continue"] = perf_counter() - phase
        # Exercise the explicit repair boundary with real independent environments.
        # Kernels are closed here; source edits and retained results must survive.
        repaired = prepare(runtime, topic="parameters", repair=True)
        assert repaired.directory == parameters.directory and repaired.ready
        assert list(repaired.directory.glob(".venv-retained-*"))
        assert (
            (repaired.directory / "src/my_experiment/teaching.py").read_bytes(),
            repaired.notebook.read_bytes(),
        ) == retained["parameters"]
        execute(
            repaired.directory,
            lessons["parameters"][:2],
            "parameters-repaired-connection",
        )
        assert runs() == before
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
        # External editable source and comparison evidence are an independent
        # verifier backup, NOT contents promised by the application snapshot.
        phase = perf_counter()
        source_id = runtime.source(groups.directory)
        health = httpx2.get(
            runtime.start().base_url + "/api/v1/health", trust_env=False, timeout=30
        )
        health.raise_for_status()
        original_identity = DaemonHealth.model_validate_json(health.content).project_id
        restored_source = work / "restored-author"
        shutil.copytree(
            groups.directory,
            restored_source,
            ignore=shutil.ignore_patterns(
                ".venv",
                ".scopecat-python",
                ".scopecat",
                "scopecat.runtime.toml",
                "__pycache__",
                "*.egg-info",
            ),
        )
        assert not (restored_source / ".venv").exists()
        assert not (restored_source / ".scopecat-python").exists()
        backup_hashes = {
            str(path.relative_to(restored_source)): file_hash(path)
            for path in restored_source.rglob("*")
            if path.is_file()
        }
        timings["independent_source_backup"] = perf_counter() - phase
        phase = perf_counter()
        runtime.stop()
        assert runtime.status().state == "stopped"
        snapshot = work / "snapshot"
        create_snapshot(open_instance(runtime.root), snapshot)
        verify_snapshot(snapshot)
        recovered = ApplicationRuntime(work / "recovered-application")
        restore_snapshot(snapshot, recovered.root)
        assert not (recovered.root / "scopecat.runtime.toml").exists()
        assert not (recovered.root / ".scopecat/author-workspaces.json").exists()
        timings["snapshot_verify_restore"] = perf_counter() - phase
        # Move only this verifier's synthetic fixtures. No old absolute source or
        # environment path may satisfy the recovery check; keep bytes for diagnosis.
        unavailable = work / "unavailable-originals"
        unavailable.mkdir()
        obsolete = [unavailable / runtime.home.name / "environments", author_cache]
        for source in (parameters.directory, groups.directory):
            obsolete.extend(
                unavailable / source.name / name
                for name in (".venv", ".scopecat-python")
                if (source / name).exists()
            )
        for path in (
            runtime.home,
            parameters.directory,
            groups.directory,
            registered_source,
        ):
            path.rename(unavailable / path.name)
            assert not path.exists()
        runtime = recovered
        phase = perf_counter()
        runtime.configure(static_dir=payload / "gui", delivery_root=author_payload)
        recovery_cache = work / "recovery-empty-cache"
        recovery_cache.mkdir()
        os.environ["UV_CACHE_DIR"] = str(recovery_cache)
        create_client_environment(runtime, restored_source)
        execution_python = prepare_execution_environment(runtime, restored_source)
        register_author_workspace(
            runtime.root, restored_source, identity=source_id, python=execution_python
        )
        assert runtime.source(restored_source) == source_id
        assert all(
            file_hash(restored_source / name) == digest
            for name, digest in backup_hashes.items()
        )
        timings["recovery_environments_and_registration"] = perf_counter() - phase
        phase = perf_counter()
        recovered_endpoint = runtime.start()
        health = httpx2.get(
            recovered_endpoint.base_url + "/api/v1/health", trust_env=False, timeout=30
        )
        health.raise_for_status()
        assert (
            DaemonHealth.model_validate_json(health.content).project_id
            == original_identity
        )
        assert runs() == before
        timings["recovery_start"] = perf_counter() - phase
        execute(
            restored_source,
            [nbformat.v4.new_code_cell(c) for c in (*GROUP_REOPEN_CELLS, ADD_ANALYSIS)],
            "groups-restored",
        )
        assert {run.run_id for run in runs()} == {run.run_id for run in before}
        evidence["installed_help_topics"] = (
            verify_remaining_topics(runtime, work, execute)
            if all_topics
            else ["parameters", "groups"]
        )
        evidence.update(
            snapshot_restore="passed",
            recovery_source_id=source_id,
            project_id=original_identity,
            independent_source_backup=backup_hashes,
            old_locations_unavailable=True,
            recovery_cache_started_empty=True,
        )
    finally:
        try:
            runtime.stop()
            evidence["cleanup"] = "stopped"
        finally:
            # Preserve partial check/phase evidence on failure. Only the final
            # successful cleanup below may replace "incomplete" with "passed".
            (work / "acceptance.json").write_text(
                json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
            )
    assert runtime.status().state == "stopped"

    def logical_bytes() -> dict[str, int]:
        return {
            name: sum(
                path.stat().st_size
                for path in (work / name).rglob("*")
                if path.is_file() and not path.is_symlink()
            )
            for name in (
                "snapshot",
                "restored-author",
                "recovered-application",
                "unavailable-originals",
                "application-python",
                "application-base",
                "empty-cache",
                "author-empty-cache",
                "recovery-empty-cache",
            )
        }

    evidence["logical_bytes_before_cleanup"] = logical_bytes()
    phase = perf_counter()
    # Only a successful recovery reaches here. These generated interpreters
    # have already lost their original paths; keep source/scientific evidence,
    # the recovered environments and their cache available for inspection.
    retained_files = {
        path: file_hash(path)
        for path in unavailable.rglob("*")
        if path.is_file() and not any(path.is_relative_to(root) for root in obsolete)
    }
    for directory in obsolete:
        assert directory.is_relative_to(work) and not directory.is_symlink()
        shutil.rmtree(directory)
    assert all(
        path.is_file() and file_hash(path) == digest
        for path, digest in retained_files.items()
    )
    verify_snapshot(snapshot)
    timings["discard_obsolete_generated_environments"] = perf_counter() - phase
    evidence["discarded_generated_directories"] = [
        str(p.relative_to(work)) for p in obsolete
    ]
    evidence["retained_original_evidence_files"] = len(retained_files)
    evidence["logical_bytes"] = logical_bytes()
    evidence.update(result="passed", cleanup="stopped")
    (work / "acceptance.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )


def verify_remaining_topics(
    runtime: ApplicationRuntime,
    work: Path,
    execute: Callable[[Path, list[NotebookNode], str], None],
) -> list[str]:
    """Run remaining shipped Help cells with minimal installed resources."""
    from testing.app_support.notebook_io import notebook_io

    from scopecat_app.notebook_journey import prepare
    from scopecat_tutorials.lessons import TOPICS, LessonTopic

    nbformat = notebook_io()
    verified = ["parameters", "groups"]
    for topic in TOPICS:
        if topic in verified:
            continue
        journey = prepare(runtime, str(work), cast("LessonTopic", topic))
        notebook = nbformat.read(journey.notebook, as_version=4)
        cells = cast("list[NotebookNode]", notebook.cells)
        execute(
            journey.directory,
            [c for c in cells if c["cell_type"] == "code"],
            f"{topic}-installed",
        )
        verified.append(topic)
    return verified


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
    all_topics: bool
    runtime_python: Path | None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("work", type=Path)
    parser.add_argument(
        "--runtime-python", type=Path, help="Existing application Python (format 2)"
    )
    parser.add_argument("--installed", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--all-topics", action="store_true", help="Execute all seven Help topics"
    )
    args = cast("Arguments", cast("object", parser.parse_args()))
    if args.runtime_python:
        if args.installed:
            parser.error("--installed cannot be combined with --runtime-python")
        verify_runtime(
            args.payload.resolve(),
            args.work.resolve(),
            args.runtime_python.absolute(),
            all_topics=args.all_topics,
        )
    else:
        action = verify_installed if args.installed else install_and_verify
        action(args.payload.resolve(), args.work.resolve(), all_topics=args.all_topics)
