"""Author discovery owns a bounded declaration surface, not a second registry DSL."""

from __future__ import annotations

import importlib
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from scopecat.api.lab import LabClient
from scopecat.application import LabApplication
from scopecat.application.authoring import AuthorExperiments
from scopecat.application.launch import LaunchCatalog, LaunchRequest
from scopecat.automation.definition import ProcedureRegistry
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.system import ExperimentSystem
from scopecat.records.config import ConfigProfileSnapshot

SOURCE = '''import scopecat as sc
LEVEL = sc.Control("level", default=1.0, minimum=0, scannable=True)
CONTROLS = sc.ControlSet((LEVEL,))
@sc.experiment(controls=CONTROLS)
def small(experiment: sc.ExperimentContext):
    """One editable experiment."""
    return LEVEL.ref
'''


def test_copy_discovery_and_exact_initial_declaration_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    package = tmp_path / "author_copy"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    path = package / "small.py"
    path.write_text(SOURCE, encoding="utf-8")
    first = AuthorExperiments.discover("author_copy")
    assert [item.entry.id for item in first.experiments] == ["small"]
    assert first.experiments[0].entry.controls[0].minimum == 0
    assert first.experiments[0].source["source"].endswith("return LEVEL.ref")
    retained_ref = first.procedures[0].ref
    path.write_text(
        SOURCE.replace("return LEVEL.ref", "return LEVEL.ref + 2"), encoding="utf-8"
    )
    monkeypatch.delitem(sys.modules, "author_copy.small")
    importlib.invalidate_caches()
    second = AuthorExperiments.discover("author_copy")
    assert first.experiments[0].fingerprint != second.experiments[0].fingerprint
    assert first.procedures[0].ref == retained_ref
    with pytest.raises(ValueError, match="fingerprint"):
        ProcedureRegistry(second.procedures).resolve(retained_ref)
    monkeypatch.delitem(sys.modules, "author_copy.small")
    monkeypatch.delitem(sys.modules, "author_copy")


def test_missing_author_control_contract_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    (tmp_path / "bad_author.py").write_text(
        SOURCE.replace("@sc.experiment(controls=CONTROLS)", "@sc.experiment"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="bad_author:small: declare a ControlSet"):
        AuthorExperiments.discover("bad_author")
    monkeypatch.delitem(sys.modules, "bad_author")


def test_reexports_are_not_discovered_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    package = tmp_path / "author_reexport"
    package.mkdir()
    (package / "__init__.py").write_text("from .small import small\n", encoding="utf-8")
    (package / "small.py").write_text(SOURCE, encoding="utf-8")
    assert len(AuthorExperiments.discover("author_reexport").experiments) == 1
    monkeypatch.delitem(sys.modules, "author_reexport.small")
    monkeypatch.delitem(sys.modules, "author_reexport")


def test_maintained_catalog_collision_rejects_direct_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    (tmp_path / "overlapping_author.py").write_text(SOURCE, encoding="utf-8")
    authors = AuthorExperiments.discover("overlapping_author")
    entry = authors.experiments[0].entry
    provider = authors.compose(lambda _lab, _request: LaunchCatalog(entries=(entry,)))
    with pytest.raises(ValueError, match="overlap"):
        provider(
            cast("LabClient", object()),
            LaunchRequest(action="preview", experiment=entry.id, version=entry.version),
        )
    monkeypatch.delitem(sys.modules, "overlapping_author")


def test_application_replace_preserves_discovered_authors_without_reloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    (tmp_path / "retained_author.py").write_text(SOURCE, encoding="utf-8")
    application = LabApplication(author_modules=("retained_author",))
    assert application.authors is not None

    def unexpected_discovery(*_sources: str) -> AuthorExperiments:
        raise AssertionError("replace must not rediscover source files")

    monkeypatch.setattr(AuthorExperiments, "discover", unexpected_discovery)

    def builder(
        _config: ConfigProfileSnapshot, catalog: InstrumentContractCatalog
    ) -> ExperimentSystem:
        return ExperimentSystem(instrument_catalog=catalog)

    copied = replace(application, build_experiment_system=builder)
    assert copied.build_experiment_system is builder
    assert application.build_experiment_system is None
    assert copied.authors is not None
    assert copied.authors is application.authors
    assert copied.launch_provider is application.launch_provider
    assert copied.procedures is application.procedures
    assert copied.procedure_schedules is application.procedure_schedules
    assert copied.calibrations is application.calibrations
    assert copied.calibration_publications is application.calibration_publications
    assert copied.procedures.refs == tuple(
        item.ref for item in copied.authors.procedures
    )
    monkeypatch.delitem(sys.modules, "retained_author")


def test_project_loading_pins_complete_revision_into_discovered_procedures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scopecat_testkit.project_loading import isolated_project_imports

    from scopecat.project import load_project
    from scopecat.project_sources import loading_revision
    from scopecat.records.author_revision import AuthorRevisionRef

    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    (tmp_path / "revision_author.py").write_text(
        SOURCE + "\nfrom scopecat.application import LabApplication\n"
        "def create(root):\n"
        "    return LabApplication(author_modules=(__name__,))\n"
    )
    manifest = tmp_path / "scopecat.toml"
    manifest.write_text('[lab]\napplication="revision_author:create"\n')
    project = load_project(manifest)
    first = AuthorRevisionRef(content_hash="sha256:" + "1" * 64)
    second = AuthorRevisionRef(content_hash="sha256:" + "2" * 64)
    with isolated_project_imports():
        original = replace(project, code_revision=first).load_application()
        revised = replace(project, code_revision=second).load_application()
        assert original.authors is not None and revised.authors is not None
        assert original.authors.experiments[0].code_revision == first
        assert revised.authors.experiments[0].code_revision == second
        assert original.procedures.refs != revised.procedures.refs
        assert (
            original.authors.experiments[0].provenance["author_code_revision"]
            == first.content_hash
        )
    assert loading_revision.get() is None
