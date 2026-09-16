"""A retained schema-68 store upgrades without losing measurements or analyses."""

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest
from scopecat.project import load_project
from scopecat_server.lifecycle import start_project, stop_project
from scopecat_server.migrations import migrate_copy
from scopecat_server.snapshots import restore_snapshot

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.workflows.authored.ordinary_analysis import PeakResult

pytestmark = pytest.mark.usefixtures("reference_lab_author_imports")


def _seed_retained_schema(database: Path) -> None:
    """Place synthetic compatible records into the independently retained old DDL.

    This test-only seeding is not a data-downgrade implementation. The fixture's
    schema is from the pinned historical commit; current acquisition supplies the
    same unchanged run/sample/config/source/measurement/analysis record formats.
    """
    source = database.with_suffix(".seed")
    database.rename(source)
    fixture = (
        Path(__file__).parents[3]
        / "packages/scopecat-server/tests/fixtures/schema-68.sql"
    )
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(fixture.read_text())
        connection.execute("ATTACH DATABASE ? AS seed", (str(source),))
        tables = cast(
            "list[tuple[str]]",
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' AND name != 'project_schema'"
            ).fetchall(),
        )
        for (name,) in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            connection.execute(f"DELETE FROM {quoted}")  # noqa: S608
            connection.execute(f"INSERT INTO {quoted} SELECT * FROM seed.{quoted}")  # noqa: S608
        connection.execute("DELETE FROM sqlite_sequence")
        connection.execute(
            "INSERT INTO sqlite_sequence SELECT * FROM seed.sqlite_sequence"
        )
        connection.commit()
    source.unlink()


def test_upgrade_retains_scientific_results_and_separate_new_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    root = tmp_path / "legacy"
    root.mkdir()
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
    project = load_project(root / "scopecat.toml")
    start_project(project)
    analysis = "reference_lab.workflows.authored.ordinary_analysis:estimate_peak"
    try:
        with project.authoring() as author:
            author.refresh()
            run = (
                author.prepare("signal", scans={"frequency": [4.7, 4.8, 4.9]})
                .run()
                .wait(timeout=60)
                .result()
            )
            original = author.analyze_as(run.id, analysis, PeakResult)
            records = run.measurements().records
            snapshot = author.get_run(run.id).snapshot
            revision = author.state().active
    finally:
        stop_project(project)
    _seed_retained_schema(project.runtime_binding.data_root / "control.sqlite3")
    receipt = migrate_copy(project, tmp_path / "upgrade")
    assert receipt.plan.steps == ("68->69", "69->70")
    upgraded = load_project(tmp_path / "upgrade/project/scopecat.toml")
    start_project(upgraded)
    try:
        with upgraded.authoring() as author:
            assert author.get_run(run.id).snapshot == snapshot
            assert author.get_run(run.id).deployment_id is None
            assert author.run(run.id).measurements().records == records
            assert author.state().active == revision
            prior = (
                author.run(run.id)
                .published_analysis(original.publication.id)
                .result_as(PeakResult)
            )
            assert prior.value == original.value
            later = author.analyze_as(
                run.id, analysis, PeakResult, arguments={"minimum_contrast": 2.0}
            )
            assert later.publication.id != prior.publication.id
            assert later.value.frequency is None
            assert (
                author.run(run.id)
                .published_analysis(prior.publication.id)
                .result_as(PeakResult)
                .value
                == original.value
            )
    finally:
        stop_project(upgraded)
    restored = tmp_path / "restored"
    restore_snapshot(tmp_path / "upgrade/original", restored)
    restored_project = load_project(restored / "scopecat.toml")
    migrate_copy(restored_project, tmp_path / "restored-upgrade")
    restored_current = load_project(tmp_path / "restored-upgrade/project/scopecat.toml")
    start_project(restored_current)
    try:
        with restored_current.authoring() as author:
            assert author.run(run.id).measurements().records == records
            assert (
                author.run(run.id)
                .published_analysis(original.publication.id)
                .result_as(PeakResult)
                .value
                == original.value
            )
    finally:
        stop_project(restored_current)
