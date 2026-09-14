"""Declared installed dependency closure and unrelated author-tool isolation."""

import sys
from pathlib import Path

import pytest

from scopecat.execution_environment import execution_packages
from scopecat.project import load_project
from scopecat.project_sources import capture_sources, require_environment


def _distribution(
    site: Path, name: str, version: str = "1.0", requires: tuple[str, ...] = ()
) -> Path:
    metadata = site / f"{name}-1.0.dist-info"
    metadata.mkdir(exist_ok=True)
    path = metadata / "METADATA"
    path.write_text(
        f"Name: {name}\nVersion: {version}\n"
        + "".join(f"Requires-Dist: {item}\n" for item in requires)
    )
    return path


def test_dependency_closure_handles_extras_cycles_and_platform_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _distribution(
        tmp_path,
        "trial_methods",
        requires=(
            "trial_helper[fit]>=1",
            'trial_missing; python_version < "3"',
        ),
    )
    _distribution(
        tmp_path,
        "trial_helper",
        requires=(
            "trial_methods",
            'trial_fit; extra == "fit"',
            'trial_missing; extra == "unused"',
        ),
    )
    _distribution(tmp_path, "trial_fit")
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    selected = execution_packages(("trial-methods",))
    assert selected["trial-helper"] == selected["trial-fit"] == "1.0"
    assert "trial-missing" not in selected
    assert "pytest" not in selected
    assert "scopecat" in selected and "scopecat-server" in selected
    with pytest.raises(ValueError, match="not installed"):
        execution_packages(("trial-missing",))
    with pytest.raises(ValueError, match="does not accept"):
        execution_packages(("trial-methods>=2",))


def test_scoped_revision_ignores_tools_but_retains_execution_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = tmp_path / "site"
    site.mkdir()
    _distribution(site, "trial_methods", requires=("trial_helper",))
    helper = _distribution(site, "trial_helper")
    tool = _distribution(site, "trial_notebook")
    monkeypatch.setattr(sys, "path", [str(site), *sys.path])
    root = tmp_path / "project"
    (root / "src/authors").mkdir(parents=True)
    manifest = root / "scopecat.toml"
    manifest.write_text(
        '[lab]\n[authors]\nsource_roots=["src"]\nrefresh_roots=["src/authors"]\ndependencies=["trial-methods"]\n'
    )
    lock = root / "uv.lock"
    lock.write_text("notebook lock version 1")
    project = load_project(manifest)
    original = capture_sources(project).manifest
    tool.write_text("Name: trial_notebook\nVersion: 2.0\n")
    lock.write_text("notebook lock version 2")
    assert capture_sources(project).manifest == original
    require_environment(original)
    helper.write_text("Name: trial_helper\nVersion: 2.0\n")
    assert (
        capture_sources(project).manifest.maintenance_hash != original.maintenance_hash
    )
    with pytest.raises(ValueError, match=r"trial-helper==1\.0"):
        require_environment(original)
    helper.unlink()
    helper.parent.rmdir()
    with pytest.raises(ValueError, match="not installed"):
        require_environment(original)
