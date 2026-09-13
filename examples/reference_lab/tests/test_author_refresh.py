"""A complete revision survives edits, failed refresh and stopped-store restore."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx2
import pytest
from scopecat.application import LabApplication
from scopecat.application.launch import LaunchPreview, LaunchSubmission
from scopecat.daemon.client import DaemonClient
from scopecat.project import load_project
from scopecat.records.launch_request import LaunchRequest
from scopecat_server.lifecycle import start_project, stop_project
from scopecat_server.snapshots import create_snapshot, restore_snapshot

from reference_lab.configuration import EXAMPLE_ROOT


def preview_request(project_root: Path) -> tuple[LaunchRequest, LaunchPreview]:
    with load_project(project_root / "scopecat.toml").authoring() as authors:
        catalog = authors.catalog()
        entry = next(item for item in catalog.entries if item.id == "signal")
        request = LaunchRequest(
            action="preview", experiment=entry.id, version=entry.version
        )
        preview = authors.preview(request)
        assert preview.code_revision == catalog.code_revision
        assert preview.code_revision is not None
        return request, preview


def admit_without_dispatch(root: Path, key: str) -> str:
    request, preview = preview_request(root)
    command = request.model_copy(
        update={
            "action": "submit",
            "request_key": key,
            "expected_request_hash": preview.request_hash,
            "config_source": preview.config_source,
            "code_revision": preview.code_revision,
            "manual_state": preview.manual_state,
        }
    )
    completed = subprocess.run(  # noqa: S603 - fixed internal project worker
        [sys.executable, "-m", "scopecat_server.launch_worker", str(root)],
        input=command.model_dump_json(),
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=True,
        env={
            key: value
            for key, value in os.environ.items()
            if key != "SCOPECAT_DAEMON_URL"
        },
    )
    return LaunchSubmission.model_validate_json(completed.stdout).procedure_id


def run_admitted(root: Path, procedure_id: str) -> None:
    subprocess.run(  # noqa: S603 - original intent selects the immutable worker code
        [
            sys.executable,
            "-m",
            "scopecat_server.launch_worker",
            str(root),
            "--procedure",
            procedure_id,
        ],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )


def test_refresh_freezes_admission_and_analysis_across_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    root = tmp_path / "project"
    root.mkdir()
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
    source_path = root / "src/reference_lab/workflows/authored/signal.py"
    source = source_path.read_text()
    # Ordinary code is split into adjacent experiment/helper/analysis files.
    helper_start = source.index("def response(")
    helper_end = source.index("@sc.experiment", helper_start)
    helper = source[helper_start:helper_end]
    source = (
        source[:helper_start]
        + "from .response import response\n\n\n"
        + source[helper_end:]
    )
    analysis_start = source.index("@sc.analysis_step")
    analysis_end = source.index("@sc.experiment", analysis_start)
    analysis = source[analysis_start:analysis_end]
    source = source[:analysis_start] + source[analysis_end:]
    source_path.write_text(source.replace("import numpy as np\n", ""))
    helper_path = source_path.with_name("response.py")
    helper_path.write_text("import scopecat as sc\n\n" + helper)
    analysis_path = source_path.with_name("analysis.py")
    analysis_path.write_text("import scopecat as sc\nimport numpy as np\n\n" + analysis)
    project = load_project(root / "scopecat.toml")
    endpoint = start_project(project)
    try:
        with project.authoring() as authors:
            initial = authors.state()
            assert initial.active is not None
            first = initial.active
            old_prepared = authors.prepare("signal")
            admitted = admit_without_dispatch(root, "frozen-before-edits")
            with DaemonClient(endpoint.base_url) as client:
                assert (
                    client.get_procedure(admitted).intent["code_revision"]
                    == first.model_dump()
                )
            helper_path.write_text(
                helper_path.read_text().replace("return gain /", "return 2 * gain /")
            )
            second = authors.refresh()
            assert second.active != first
            assert old_prepared.preview.code_revision == first
            assert authors.prepare("signal").preview.code_revision == second.active
            assert (
                authors.prepare("signal", code_revision=first).preview.code_revision
                == first
            )
            source_path.write_text(
                source_path.read_text()
                .replace(
                    'sc.ControlSpec(title="Gain")] = 1.0',
                    'sc.ControlSpec(title="Gain")] = 3.0',
                )
                .replace('= "positive",', '= "negative",')
            )
            third = authors.refresh(expected_generation=second.generation)
            assert third.active != second.active
            analysis_path.write_text(
                analysis_path.read_text().replace(
                    "float(selected.mean())", "10 * float(selected.mean())"
                )
            )
            fourth = authors.refresh(expected_generation=third.generation)
            assert fourth.active is not None and fourth.active != third.active
            good = analysis_path.read_text()
            analysis_path.write_text(good + "\ndef broken(:\n")
            with pytest.raises(httpx2.HTTPStatusError) as syntax:
                authors.refresh(expected_generation=fourth.generation)
            assert "SyntaxError" in syntax.value.response.text
            assert "analysis.py" in syntax.value.response.text
            assert "line" in syntax.value.response.text
            assert authors.state() == fourth
            assert authors.catalog().code_revision == fourth.active
            analysis_path.write_text(good + "\nimport missing_author_dependency\n")
            with pytest.raises(httpx2.HTTPStatusError) as missing:
                authors.refresh(expected_generation=fourth.generation)
            assert "missing_author_dependency" in missing.value.response.text
            assert authors.state() == fourth
            analysis_path.write_text(good)
            fifth = authors.refresh(expected_generation=fourth.generation)
            assert fifth.active == fourth.active
            # The previously admitted task is intentionally still ready. New worker
            # processes must load its original revision after all the edits above.
            with DaemonClient(endpoint.base_url) as client:
                stored = client.get_procedure(admitted)
                assert stored.state == "ready"
                assert stored.intent["inputs"] == {"polarity": "positive"}
                assert stored.intent["code_revision"] == first.model_dump()
            newer = admit_without_dispatch(root, "new-definition")
            run_admitted(root, newer)
            with LabApplication().connect(endpoint.base_url) as lab:
                runs = lab.runs().items
                assert len(runs) == 1
                latest = runs[0]
                assert latest.measurements()["result"].require_values() == (-6.0,)
                assert (
                    latest.request.metadata["author_code_revision"]
                    == fourth.active.content_hash
                )
            # Daemon process and virtual instrument service remain alive throughout.
            assert start_project(project).pid == endpoint.pid
    finally:
        stop_project(project)
    snapshot = tmp_path / "snapshot"
    create_snapshot(project, snapshot)
    restored_root = tmp_path / "restored"
    restore_snapshot(snapshot, restored_root)
    restored = load_project(restored_root / "scopecat.toml")
    restored_endpoint = start_project(restored)
    try:
        run_admitted(restored_root, admitted)
        with (
            LabApplication().connect(restored_endpoint.base_url) as lab,
            restored.authoring() as authors,
        ):
            retained = next(
                run
                for run in lab.runs().items
                if run.request.metadata["author_code_revision"] == first.content_hash
            )
            assert retained.measurements()["result"].require_values() == (1.0,)
            for revision, expected in ((first, 1.0), (fourth.active, 10.0)):
                result = authors.analyze(
                    retained.id,
                    "reference_lab.workflows.authored.analysis:selected_mean",
                    code_revision=revision,
                    key=f"revision-{expected}",
                )
                publication = retained.published_analysis(result.analysis_id)
                assert publication.fact("mean").value == expected
                assert (
                    publication.fact("author_code_revision").value
                    == revision.content_hash
                )
    finally:
        stop_project(restored)


def test_refreshed_pulse_helper_keeps_admitted_recipe_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A device-free author run evaluates the retained recipe's expanded amplitude."""
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    root = tmp_path / "recipe-project"
    root.mkdir()
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
    authored = root / "src/reference_lab/workflows/authored"
    example = (
        EXAMPLE_ROOT.parents[1]
        / "packages/scopecat-quantum/examples/quantity_recipe.py"
    ).read_text()
    helper = example[: example.index('if __name__ == "__main__":')]
    helper += """

def recipe_amplitude() -> float:
    bound = q.bind(experiment, {"qubit": "q0", "duration": Quantity(16, "ns")})
    lowered = plan_quantum_pulse_lowering(
        bound.verified, ResolvedPulseImplementations(),
        output_id=PulseProgramId("preview"),
    )
    [event] = schedule(materialize_quantum_pulse_program(lowered)).events
    return event.instruction.envelope.amplitude.value
"""
    helper_path = authored / "recipe.py"
    helper_path.write_text(helper)
    signal_path = authored / "signal.py"
    signal_path.write_text(
        signal_path.read_text()
        .replace(
            "def response(",
            "from .recipe import recipe_amplitude\n\ndef response(",
        )
        .replace("return gain /", "return recipe_amplitude() * gain /")
    )
    project = load_project(root / "scopecat.toml")
    endpoint = start_project(project)
    try:
        with project.authoring() as authors:
            first = authors.state()
            old_run = admit_without_dispatch(root, "old-recipe")
            helper_path.write_text(
                helper.replace('Quantity(0.18, "arb")', 'Quantity(0.27, "arb")')
            )
            refreshed = authors.refresh(expected_generation=first.generation)
            assert refreshed.active != first.active
            new_run = admit_without_dispatch(root, "new-recipe")
            run_admitted(root, new_run)
            run_admitted(root, old_run)
        with LabApplication().connect(endpoint.base_url) as lab:
            by_revision = {
                run.request.metadata["author_code_revision"]: run.measurements()[
                    "result"
                ].require_values()
                for run in lab.runs().items
            }
            assert first.active is not None and refreshed.active is not None
            assert by_revision[first.active.content_hash] == (0.18,)
            assert by_revision[refreshed.active.content_hash] == (0.27,)
    finally:
        stop_project(project)
