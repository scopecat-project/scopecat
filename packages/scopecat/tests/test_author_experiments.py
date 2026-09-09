"""Author discovery owns a bounded declaration surface, not a second registry DSL."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import cast

import pytest

from scopecat.api.lab import LabClient
from scopecat.application.authoring import AuthorExperiments
from scopecat.application.launch import LaunchCatalog, LaunchRequest
from scopecat.automation.definition import ProcedureRegistry

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
