"""Notebook rebinding uses admitted bytes and rolls imports back on failure."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, assert_type, cast

import pytest

from scopecat.application.author_imports import load_revision_experiment
from scopecat.application.author_project import AuthorProject
from scopecat.application.authoring import AuthorExperiment
from scopecat.authoring.experiments import Experiment, ExperimentRequest
from scopecat.project import load_project
from scopecat.project_sources import capture_sources

SOURCE = """from dataclasses import dataclass
import scopecat as sc
from .helper import DEFAULT, response

@dataclass
class Result:
    value: sc.DataRef[float]

@sc.experiment(id="signal")
def signal(ctx: sc.ExperimentContext, *, gain: float = DEFAULT) -> Result:
    return Result(response(gain))
"""
HELPER = """import scopecat as sc
DEFAULT = 1.0
@sc.compute
def response(gain: float) -> float:
    return gain * 2
"""


@pytest.fixture
def project_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "project"
    authored = root / "src/rebind_lab/authored"
    authored.mkdir(parents=True)
    (authored.parent / "__init__.py").write_text("", encoding="utf-8")
    (authored / "__init__.py").write_text("", encoding="utf-8")
    (authored / "signal.py").write_text(SOURCE, encoding="utf-8")
    (authored / "helper.py").write_text(HELPER, encoding="utf-8")
    (root / "scopecat.toml").write_text(
        '[lab]\n[authors]\nsource_roots=["src"]\n'
        'refresh_roots=["src/rebind_lab/authored"]\ndependencies=[]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "path", [str(root / "src"), *sys.path])
    finders = list(sys.meta_path)
    try:
        yield root
    finally:
        sys.meta_path[:] = finders
        for name in tuple(sys.modules):
            if name == "rebind_lab" or name.startswith("rebind_lab."):
                del sys.modules[name]


def imported_experiment() -> Experiment[..., object]:
    return cast(
        "Experiment[..., object]",
        importlib.import_module("rebind_lab.authored.signal").signal,
    )


def test_rebind_defaults_helpers_and_original_requests(project_files: Path) -> None:
    original = imported_experiment()
    old_request = original()
    old_module = sys.modules["rebind_lab.authored.helper"]
    old_parent = sys.modules["rebind_lab.authored"]
    project = load_project(project_files / "scopecat.toml")
    helper = project_files / "src/rebind_lab/authored/helper.py"
    helper.write_text(HELPER.replace("1.0", "3.0"), encoding="utf-8")
    bundle = capture_sources(project)
    import inspect

    # The input schema also carries defaults; get the expected fingerprint through
    # a fresh ordinary import before restoring the notebook's old module bindings.
    previous = dict(sys.modules)
    for name in tuple(sys.modules):
        if name.startswith("rebind_lab.authored"):
            del sys.modules[name]
    importlib.invalidate_caches()
    helper.with_name("__pycache__").mkdir(exist_ok=True)
    for bytecode in helper.with_name("__pycache__").glob("*.pyc"):
        bytecode.unlink()
    expected = imported_experiment()
    fingerprint = AuthorExperiment.from_declaration(
        expected, code_revision=bundle.manifest.ref
    ).fingerprint
    for name in tuple(sys.modules):
        if name.startswith("rebind_lab.authored"):
            del sys.modules[name]
    sys.modules.update(
        {
            name: module
            for name, module in previous.items()
            if name.startswith("rebind_lab.authored")
        }
    )
    # A later workspace edit must not leak into the already admitted snapshot.
    helper.write_text(HELPER.replace("1.0", "9.0"), encoding="utf-8")
    fresh = load_revision_experiment(
        original,
        bundle,
        project_root=project_files,
        cache=project_files / "cache",
        expected_fingerprint=fingerprint,
    )
    assert fresh().snapshot()["gain"] == 3.0
    assert old_request.snapshot()["gain"] == 1.0
    assert old_module.DEFAULT == 1.0
    assert old_parent.helper is old_module
    assert fresh.code_revision == bundle.manifest.ref
    assert fresh.__signature__.parameters["gain"].default == 3.0
    assert sys.modules["rebind_lab.authored.helper"].DEFAULT == 3.0
    assert inspect.signature(fresh).parameters["gain"].default == 3.0


def test_failed_import_restores_notebook_modules(project_files: Path) -> None:
    original = imported_experiment()
    parent = sys.modules["rebind_lab.authored"]
    helper_module = sys.modules["rebind_lab.authored.helper"]
    helper = project_files / "src/rebind_lab/authored/helper.py"
    helper.write_text(
        HELPER + '\nraise RuntimeError("broken helper")\n', encoding="utf-8"
    )
    bundle = capture_sources(load_project(project_files / "scopecat.toml"))
    finders = list(sys.meta_path)
    with pytest.raises(RuntimeError, match="broken helper"):
        load_revision_experiment(
            original,
            bundle,
            project_root=project_files,
            cache=project_files / "cache",
            expected_fingerprint="unused",
        )
    assert sys.meta_path == finders
    assert imported_experiment() is original
    assert sys.modules["rebind_lab.authored"] is parent
    assert parent.helper is helper_module


def test_deleted_helper_does_not_fall_back_to_workspace(project_files: Path) -> None:
    original = imported_experiment()
    helper = project_files / "src/rebind_lab/authored/helper.py"
    helper.unlink()
    bundle = capture_sources(load_project(project_files / "scopecat.toml"))
    helper.write_text(HELPER, encoding="utf-8")
    with pytest.raises(ModuleNotFoundError, match="absent from the selected"):
        load_revision_experiment(
            original,
            bundle,
            project_root=project_files,
            cache=project_files / "cache",
            expected_fingerprint="unused",
        )
    assert imported_experiment() is original


def check_typed_refresh(
    session: AuthorProject, experiment: Experiment[[int], str]
) -> None:
    if TYPE_CHECKING:
        refreshed = assert_type(session.refresh(experiment), Experiment[[int], str])
        loaded = assert_type(
            session.load_experiment(experiment), Experiment[[int], str]
        )
        assert_type(refreshed(1), ExperimentRequest[str])
        assert_type(loaded(1), ExperimentRequest[str])
        live = session.live(experiment)
        assert_type(live(1), ExperimentRequest[str])
        live("invalid")  # pyright: ignore[reportArgumentType]
        live()  # pyright: ignore[reportCallIssue]
        refreshed("invalid")  # pyright: ignore[reportArgumentType]
        loaded()  # pyright: ignore[reportCallIssue]


def test_rebinding_rejects_another_projects_author_modules(
    project_files: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = imported_experiment()
    module = sys.modules["rebind_lab.authored.signal"]
    monkeypatch.setattr(
        module, "__file__", str(project_files.parent / "other/signal.py")
    )
    bundle = capture_sources(load_project(project_files / "scopecat.toml"))
    with pytest.raises(ValueError, match="shadowed by another project"):
        load_revision_experiment(
            original,
            bundle,
            project_root=project_files,
            cache=project_files / "cache",
            expected_fingerprint="unused",
        )
    assert imported_experiment() is original
