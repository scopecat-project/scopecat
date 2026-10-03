"""Two registered source owners execute through one deployment and data writer."""

import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx2
import pytest
from filelock import FileLock
from scopecat.application import LabApplication
from scopecat.author_workspaces import author_workspace_id
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import resolve_daemon_endpoint
from scopecat.project import open_project

from scopecat_server.author_registration import register_author_workspace
from scopecat_server.lifecycle import initialize_project, start_project, stop_project
from scopecat_server.runtime import LocalDaemonRuntime
from scopecat_server.snapshots import SnapshotError, create_snapshot, restore_snapshot


@dataclass
class Summary:
    mean: float
    points: int


def test_two_workspace_publication_and_execution(tmp_path: Path) -> None:
    first = initialize_project(tmp_path / "first")
    second = initialize_project(tmp_path / "second")
    # Each source selects its own catalog without copying laboratory capabilities.
    manifest = second.root / "scopecat.toml"
    manifest.write_text(
        manifest.read_text().replace(
            'modules = ["scopecat_lab.authored"]',
            'modules = ["scopecat_lab.authored.signal"]',
        )
    )
    source = second.root / "src/scopecat_lab/authored/signal.py"
    shared = second.root / "src/scopecat_lab/shared_response.py"
    shared.write_text("SCALE = 2.0\n")
    source.write_text(
        source.read_text().replace(
            "return scale /",
            "from scopecat_lab.shared_response import SCALE\n"
            "    return SCALE * scale /",
        )
    )
    (source.parent / "alternate.py").write_text(
        source.read_text().replace('id="signal"', 'id="alternate"')
    )
    register_author_workspace(first.root, first.root)
    registered = register_author_workspace(first.root, second.root)
    with pytest.raises(ValueError, match="service workspace"):
        LocalDaemonRuntime(second.root)
    start_project(first, timeout=60)
    try:
        with (
            LabApplication().connect(resolve_daemon_endpoint(first.root)) as lab,
            first.authoring() as a,
            second.authoring() as b,
        ):
            initial_a = a.state()
            initial_b = b.state()
            driver_versions = a.devices.drivers()
            assert initial_a.active != initial_b.active
            assert b.workspace_id == registered.id
            assert {item.id for item in b.catalog().entries} == {"signal"}
            collection = a.create_record_collection("Shared acquisition")
            imported = lab.setup.import_template(
                lab.setup.templates()[0], name="shared-parameters"
            )
            a.use(selection=imported.selection)
            b.use(selection=imported.selection)
            a.use(collection=collection.id)
            b.use(collection=collection.id)
            prepared_a = a.prepare("signal")
            prepared_b = b.prepare("signal")
            assert prepared_a.request.workspace_id == author_workspace_id(first.root)
            assert prepared_a.request.workspace_id != registered.id
            assert prepared_b.request.workspace_id == registered.id
            saved = prepared_b.save_plan("B source", saved_by="test")
            assert saved.definition.workspace_id == registered.id
            one = prepared_a.run().wait(timeout=60).result()
            two = prepared_b.run().wait(timeout=60).result()
            assert tuple(one.measurements()["result"].require_values()) == (1.0,)
            assert tuple(two.measurements()["result"].require_values()) == (2.0,)
            assert a.run_number(one) == 1 and a.run_number(two.id) == 2
            assert two.request.metadata["author_workspace"] == registered.id
            assert one.request.metadata["author_workspace"] == author_workspace_id(
                first.root
            )
            # A shared scientific module outside refresh_roots belongs to the
            # complete task snapshot too. It must not require an app restart.
            shared.write_text("SCALE = 3.0\n")
            manifest.write_text(
                manifest.read_text().replace(
                    'modules = ["scopecat_lab.authored.signal"]',
                    'modules = ["scopecat_lab.authored"]',
                )
            )
            refreshed_b = b.refresh_authors(expected_generation=initial_b.generation)
            assert a.devices.drivers() == driver_versions
            assert {item.id for item in b.catalog().entries} == {
                "signal",
                "alternate",
                "first_run",
            }
            assert {item.id for item in a.catalog().entries} == {"signal", "first_run"}
            assert {
                item.id for item in b.catalog(code_revision=initial_b.active).entries
            } == {"signal"}
            assert a.state() == initial_a
            assert refreshed_b.active != initial_b.active
            old = (
                b.prepare("signal", code_revision=initial_b.active)
                .run()
                .wait(timeout=60)
                .result()
            )
            assert tuple(old.measurements()["result"].require_values()) == (2.0,)
            newer = b.prepare("signal").run().wait(timeout=60).result()
            assert tuple(newer.measurements()["result"].require_values()) == (3.0,)
            via_plan = a.prepare_plan(saved.ref).run().wait(timeout=60).result()
            assert tuple(via_plan.measurements()["result"].require_values()) == (2.0,)
            analysis = a.analyze_as(
                two.id, "scopecat_lab.authored.signal:summarize", Summary
            )
            assert analysis.value.mean == 2.0
            assert analysis.publication.fact("author_workspace").value == registered.id
            with pytest.raises(ValueError, match=r"generation|changed"):
                b.refresh_authors(expected_generation=initial_b.generation)
    finally:
        stop_project(first)
    with pytest.raises(SnapshotError, match="service workspace"):
        create_snapshot(second, tmp_path / "wrong-snapshot")
    create_snapshot(first, tmp_path / "snapshot")
    restore_snapshot(tmp_path / "snapshot", tmp_path / "restored")
    restored = open_project(tmp_path / "restored")
    shutil.rmtree(second.root)
    start_project(restored, timeout=60)
    try:
        with restored.authoring() as reader:
            assert tuple(
                reader.run(two.id).measurements()["result"].require_values()
            ) == (2.0,)
        with DaemonClient(
            resolve_daemon_endpoint(restored.root), workspace_id=registered.id
        ) as retained:
            assert initial_b.active is not None
            assert (
                retained.author_revision(initial_b.active).manifest.ref
                == initial_b.active
            )
    finally:
        stop_project(restored)


def test_compiler_factory_edit_does_not_replace_a_prepared_tasks_code(
    tmp_path: Path,
) -> None:
    project = initialize_project(tmp_path / "compiler-source")
    manifest = project.root / "scopecat.toml"
    manifest.write_text(
        manifest.read_text()
        + ('\n[lab.capabilities]\nexperiment_system="scopecat_lab.compiler:build"\n')
    )
    compiler = project.root / "src/scopecat_lab/compiler.py"
    implementation = (
        "from scopecat.planning.system import ExperimentSystem\n"
        "def build(config, catalog):\n"
        "    return ExperimentSystem(instrument_catalog=catalog)\n"
    )
    compiler.write_text(implementation)
    register_author_workspace(project.root, project.root)
    start_project(project, timeout=60)
    try:
        with (
            LabApplication().connect(resolve_daemon_endpoint(project.root)) as lab,
            project.authoring() as author,
        ):
            imported = lab.setup.import_template(
                lab.setup.templates()[0], name="compiler-parameters"
            )
            author.use(selection=imported.selection)
            initial = author.state()
            pending = author.prepare("signal")
            compiler.write_text(
                implementation.replace(
                    "return ExperimentSystem(instrument_catalog=catalog)",
                    'raise ValueError("new compiler needs correction")',
                )
            )
            changed = author.refresh_authors(expected_generation=initial.generation)
            assert changed.active != initial.active
            with pytest.raises(
                httpx2.HTTPStatusError, match="new compiler needs correction"
            ):
                author.prepare("signal")
            # The request was prepared before the edit and retains that factory.
            result = pending.run().wait(timeout=60).result()
            assert tuple(result.measurements()["result"].require_values()) == (1.0,)
            assert pending.request.code_revision == initial.active
            compiler.write_text(implementation)
            author.refresh_authors(expected_generation=changed.generation)
            fixed = author.prepare("signal").run().wait(timeout=60).result()
            assert tuple(fixed.measurements()["result"].require_values()) == (1.0,)
    finally:
        stop_project(project)


def test_registration_locks_candidate_and_rebinding_revokes_old_location(
    tmp_path: Path,
) -> None:
    owner = initialize_project(tmp_path / "owner")
    old = initialize_project(tmp_path / "old")
    old.runtime_binding.deployment_root.mkdir(parents=True)
    with (
        FileLock(old.runtime_binding.deployment_root / "deployment.lock"),
        pytest.raises(ValueError, match="another running application"),
    ):
        register_author_workspace(owner.root, old.root)
    assert not (old.root / "scopecat.runtime.toml").exists()
    entry = register_author_workspace(owner.root, old.root)
    with LocalDaemonRuntime(owner.root):
        pass
    moved = tmp_path / "moved"
    shutil.copytree(old.root, moved)
    rebound = register_author_workspace(owner.root, moved, identity=entry.id)
    assert rebound.id == entry.id and author_workspace_id(moved) == entry.id
    with pytest.raises(ValueError, match="service workspace"):
        LocalDaemonRuntime(old.root)
    with pytest.raises(SnapshotError, match="service workspace"):
        create_snapshot(old, tmp_path / "old-snapshot")
    with pytest.raises(ValueError, match="not in this scientific store"):
        register_author_workspace(owner.root, moved, identity="unknown-source")
