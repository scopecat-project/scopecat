"""Installed laboratory declarations preserve ownership and execution identity."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from scopecat_testkit.project_loading import isolated_project_imports

from scopecat.project import (
    ProjectCodeLoadError,
    ProjectManifestError,
    load_project,
    open_project,
)
from scopecat.project_sources import capture_sources, require_environment


@pytest.fixture(autouse=True)
def isolated() -> Iterator[None]:
    with isolated_project_imports():
        yield


@pytest.fixture
def adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    site = tmp_path / "site"
    module = site / "test_lab_adapter"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text(
        "raise AssertionError('discovery imported adapter')\n"
    )
    (module / "bootstrap.py").write_text(
        "from scopecat.application import LabBootstrap\n"
        "def create(root):\n    return LabBootstrap()\n"
    )
    manifest = module / "adapter.toml"
    manifest.write_text(
        '[lab]\nbootstrap="test_lab_adapter.bootstrap:create"\n'
        '[lab.capabilities]\nauthor_modules=["test_lab_adapter.authored"]\n'
        '[authors.packages]\ntest_lab_adapter="test-lab-adapter"\n'
    )
    (module / "authored.py").write_text("value = 1\n")
    metadata = site / "test_lab_adapter-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: test-lab-adapter\nVersion: 1.0\n")
    (metadata / "RECORD").write_text(
        "\n".join(
            f"test_lab_adapter/{name},,"
            for name in ("__init__.py", "bootstrap.py", "authored.py", "adapter.toml")
        )
    )
    monkeypatch.setattr(sys, "path", [str(site), *sys.path])
    project = tmp_path / "project"
    project.mkdir()
    (project / "src").mkdir()
    (project / "src" / "experiments.py").write_text("value = 2\n")
    (project / "scopecat.toml").write_text(
        '[lab.adapter]\ndistribution="test-lab-adapter"\nmanifest="test_lab_adapter/adapter.toml"\n'
        '[authors]\nmodules=["experiments"]\nsource_roots=["src"]\nrefresh_roots=["src"]\ndependencies=[]\n'
    )
    return project, module


def test_discovery_reads_owned_resources_without_importing(
    adapter: tuple[Path, Path],
) -> None:
    root, _ = adapter
    project = open_project(root)
    assert project.bootstrap_spec == "test_lab_adapter.bootstrap:create"
    assert project.adapter_packages == (("test_lab_adapter", "test-lab-adapter"),)
    assert project.installed_packages == project.adapter_packages
    assert project.capabilities is not None
    assert project.capabilities.author_modules == (
        "test_lab_adapter.authored",
        "experiments",
    )


def test_bootstrap_and_local_authors_share_declared_composition(
    adapter: tuple[Path, Path],
) -> None:
    root, module = adapter
    (module / "__init__.py").write_text("")
    project = open_project(root)
    assert project.load_bootstrap().bootstrap_config is None
    assert project.load_application().authors is not None
    assert sys.modules["test_lab_adapter.authored"].__file__ == str(
        module / "authored.py"
    )
    assert sys.modules["experiments"].__file__ == str(root / "src" / "experiments.py")


def test_capture_detects_same_version_adapter_replacement(
    adapter: tuple[Path, Path],
) -> None:
    root, module = adapter
    project = open_project(root)
    bundle = capture_sources(project)
    assert "test-lab-adapter" in bundle.manifest.packages
    require_environment(bundle.manifest)
    (module / "adapter.toml").write_text(
        (module / "adapter.toml").read_text() + "\n# changed\n"
    )
    with pytest.raises(ValueError, match="installed author package content changed"):
        require_environment(bundle.manifest)


def test_local_shadow_is_rejected_before_import(
    adapter: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, _ = adapter
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "test_lab_adapter.py").write_text(
        "raise AssertionError('shadow executed')\n"
    )
    monkeypatch.setattr(sys, "path", [str(shadow), *sys.path])
    with pytest.raises(ProjectCodeLoadError, match="declared distribution"):
        open_project(root).load_bootstrap()


def test_stop_discovery_does_not_require_installed_adapter(tmp_path: Path) -> None:
    manifest = tmp_path / "scopecat.toml"
    manifest.write_text(
        '[lab.adapter]\ndistribution="missing-adapter"\nmanifest="missing/adapter.toml"\n'
    )
    assert load_project(manifest, resolve_adapter=False).adapter_packages == ()
    assert open_project(tmp_path, resolve_adapter=False).bootstrap_spec is None
    with pytest.raises(ProjectManifestError, match="invalid lab adapter"):
        open_project(tmp_path)


def test_project_cannot_override_adapter_capabilities(
    adapter: tuple[Path, Path],
) -> None:
    root, _ = adapter
    manifest = root / "scopecat.toml"
    manifest.write_text(
        manifest.read_text().replace(
            "[authors]",
            '[lab.capabilities]\nexperiment_system="experiments:build"\n[authors]',
        )
    )
    with pytest.raises(ProjectManifestError, match="only add"):
        open_project(root)


def test_adapter_cannot_declare_unowned_implementation(
    adapter: tuple[Path, Path],
) -> None:
    root, module = adapter
    manifest = module / "adapter.toml"
    manifest.write_text(
        manifest.read_text().replace(
            "test_lab_adapter.bootstrap:create", "other.bootstrap:create"
        )
    )
    with pytest.raises(ProjectManifestError, match="no declared package owner"):
        open_project(root)


def test_manifest_must_belong_to_declared_distribution(
    adapter: tuple[Path, Path],
) -> None:
    root, module = adapter
    record = module.parent / "test_lab_adapter-1.0.dist-info" / "RECORD"
    record.write_text(record.read_text().replace("test_lab_adapter/adapter.toml,,", ""))
    with pytest.raises(ProjectManifestError, match="manifest is not owned"):
        open_project(root)


def test_author_only_capture_pins_laboratory_without_copying_live_manifest(
    adapter: tuple[Path, Path],
) -> None:
    import json

    from scopecat.author_workspaces import (
        LocalAuthorWorkspace,
        LocalAuthorWorkspaces,
        author_bindings_path,
    )
    from scopecat.project import load_captured_project
    from scopecat.project_sources import materialize_sources

    owner, _ = adapter
    (owner / "scopecat.toml").write_text(
        '[lab.adapter]\ndistribution="test-lab-adapter"\n'
        'manifest="test_lab_adapter/adapter.toml"\n'
    )
    source = owner.parent / "author"
    (source / "src").mkdir(parents=True)
    (source / "src/experiment.py").write_text("value = 1\n")
    manifest = source / "scopecat.toml"
    manifest.write_text(
        '[authors]\nmodules=["experiment"]\nsource_roots=["src"]\n'
        'refresh_roots=["src"]\ndependencies=[]\n'
    )
    assert open_project(source, resolve_adapter=False).author_only
    with pytest.raises(ValueError, match="not registered"):
        open_project(source)
    binding = owner / ".scopecat"
    (source / "scopecat.runtime.toml").write_text(
        f"[runtime]\ndata_root={json.dumps(str(binding))}\n"
        f"deployment_root={json.dumps(str(binding))}\n"
    )
    binding.mkdir()
    author_bindings_path(owner).write_text(
        LocalAuthorWorkspaces(
            service_root=owner,
            items=(
                LocalAuthorWorkspace(
                    id="author",
                    name="Author",
                    root=source,
                    python=Path(sys.executable),
                ),
            ),
        ).model_dump_json()
    )
    project = open_project(source)
    bundle = capture_sources(project)
    archive = materialize_sources(bundle, source.parent / "archive")
    # Retained composition does not follow later edits to the live owner.
    (owner / "scopecat.toml").write_text("[lab]\n")
    with pytest.raises(ValueError, match=r"only \[lab.adapter\]"):
        open_project(source)
    retained = load_captured_project(archive)
    assert retained.author_only and retained.lab_adapter == project.lab_adapter
    assert retained.bootstrap_spec == "test_lab_adapter.bootstrap:create"
    assert (archive / "scopecat.toml").read_bytes() == manifest.read_bytes()
    (archive / "scopecat.laboratory.toml").unlink()
    with pytest.raises(ValueError, match="missing its laboratory declaration"):
        load_captured_project(archive)
