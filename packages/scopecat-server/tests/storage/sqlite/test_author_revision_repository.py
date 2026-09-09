"""Complete-source retention and CAS publication independently of device execution."""

from pathlib import Path

import pytest
from scopecat.project import load_project
from scopecat.project_sources import capture_sources, materialize_sources

from scopecat_server.storage.sqlite.author_revision_repository import (
    AuthorRevisionConflict,
    AuthorRevisionRepository,
)
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def test_revision_captures_helper_analysis_and_keeps_previous_objects(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    (root / "src" / "lab" / "authors").mkdir(parents=True)
    (root / "scopecat.toml").write_text(
        '[lab]\n[authors]\nsource_roots=["src"]\nrefresh_roots=["src/lab/authors"]\n'
    )
    helper = root / "src/lab/authors/helper.py"
    helper.write_text("def response(): return 1\n")
    analysis = root / "src/lab/authors/analysis.py"
    analysis.write_text("def result(): return 2\n")
    (root / "src/lab/driver.py").write_text("driver = 1\n")
    project = load_project(root / "scopecat.toml")
    first = capture_sources(project)
    helper.write_text("def response(): return 3\n")
    second = capture_sources(project)
    analysis.write_text("def result(): return 4\n")
    third = capture_sources(project)
    assert len({item.manifest.ref.content_hash for item in (first, second, third)}) == 3
    assert first.manifest.maintenance_hash == third.manifest.maintenance_hash
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "control.sqlite3"), tmp_path / "objects"
    )
    store.bootstrap()
    try:
        repository = AuthorRevisionRepository(store)
        repository.publish(first, expected_generation=0)
        repository.publish(second, expected_generation=1)
        with pytest.raises(AuthorRevisionConflict):
            repository.publish(third, expected_generation=1)
        assert repository.state().active == second.manifest.ref
        retained = repository.get(first.manifest.ref)
        tree = materialize_sources(retained, tmp_path / "code")
        assert (
            tree / "src/lab/authors/helper.py"
        ).read_text() == "def response(): return 1\n"
        assert (
            tree / "src/lab/authors/analysis.py"
        ).read_text() == "def result(): return 2\n"
        (tree / "src/lab/authors/helper.py").write_text("tampered\n")
        with pytest.raises(ValueError, match="source changed"):
            materialize_sources(retained, tmp_path / "code")
    finally:
        store.close()
    (root / "src/lab/driver.py").write_text("driver = 2\n")
    assert (
        capture_sources(project).manifest.maintenance_hash
        != first.manifest.maintenance_hash
    )
