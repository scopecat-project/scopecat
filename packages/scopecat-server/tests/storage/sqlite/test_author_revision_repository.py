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


def test_maintenance_change_is_rejected_before_author_validation(
    tmp_path: Path,
) -> None:
    from scopecat.project_sources import require_environment

    from scopecat_server.services.author_revisions import AuthorRevisionService

    (tmp_path / "src/authors").mkdir(parents=True)
    (tmp_path / "scopecat.toml").write_text(
        '[lab]\n[authors]\nsource_roots=["src"]\nrefresh_roots=["src/authors"]\n'
    )
    driver = tmp_path / "src/driver.py"
    driver.write_text("driver = 1\n")
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "control.sqlite3"), tmp_path / "objects"
    )
    store.bootstrap()
    try:
        service = AuthorRevisionService(tmp_path, store)
        assert service.baseline is not None
        with pytest.raises(ValueError, match="recorded Python"):
            require_environment(
                service.baseline.manifest.model_copy(update={"python": "0.0"})
            )
        driver.write_text("driver = 2\n")
        with pytest.raises(ValueError, match="maintained composition changed"):
            service.refresh(expected_generation=0)
        assert service.repository.state().active is None
    finally:
        store.close()


def test_analysis_module_must_resolve_inside_configured_author_root(
    tmp_path: Path,
) -> None:
    from scopecat_server.author_worker import author_module_path

    (tmp_path / "src/authors").mkdir(parents=True)
    (tmp_path / "scopecat.toml").write_text(
        '[lab]\n[authors]\nsource_roots=["src"]\nrefresh_roots=["src/authors"]\n'
    )
    analysis = tmp_path / "src/authors/analysis.py"
    analysis.write_text("# permitted local analysis\n")
    project = load_project(tmp_path / "scopecat.toml")
    assert author_module_path(project, "authors.analysis") == analysis
    with pytest.raises(ValueError, match="configured author refresh root"):
        author_module_path(project, "os")
    with pytest.raises(ValueError, match="qualified Python module"):
        author_module_path(project, "../outside")


@pytest.mark.parametrize("name", ["C:/authors", "C:authors"])
def test_windows_drive_paths_are_rejected_on_every_platform(
    tmp_path: Path, name: str
) -> None:
    from scopecat.project import ProjectManifestError
    from scopecat.records.author_revision import AuthorRevisionManifest

    with pytest.raises(ValueError, match="invalid source path"):
        AuthorRevisionManifest.local_paths({name: "sha256:" + "0" * 64})
    manifest = tmp_path / "scopecat.toml"
    manifest.write_text(f'[lab]\n[authors]\nsource_roots=["{name}"]\n')
    with pytest.raises(ProjectManifestError, match="relative subdirectories"):
        load_project(manifest)
