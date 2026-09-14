"""Installed module identity and local refresh boundaries without code imports."""

from pathlib import Path

import pytest
from scopecat.project import load_project
from scopecat.project_sources import capture_sources, require_environment

from scopecat_server.author_worker import author_module_path


def test_installed_sources_are_retained_and_local_edits_remain_refreshable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The same metadata and module layout produced by a regular wheel installer.
    site = tmp_path / "site"
    package = site / "lab_methods"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("raise RuntimeError('must not import')\n")
    method = package / "rabi.py"
    method.write_text("value = 1\n")
    metadata = site / "lab_methods-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: lab-methods\nVersion: 1.0\n")
    (metadata / "RECORD").write_text(
        "lab_methods/__init__.py,,\nlab_methods/rabi.py,,\n"
        "lab_methods-1.0.dist-info/METADATA,,\n"
    )
    monkeypatch.syspath_prepend(str(site))
    project_root = tmp_path / "project"
    local = project_root / "src/user_experiments"
    local.mkdir(parents=True)
    wrapper = local / "rabi.py"
    wrapper.write_text("from lab_methods.rabi import value\n")
    manifest = project_root / "scopecat.toml"
    manifest.write_text(
        '[lab]\n[authors]\nsource_roots=["src"]\n'
        'refresh_roots=["src/user_experiments"]\n'
        '[authors.packages]\nlab_methods="lab-methods"\n'
    )
    project = load_project(manifest)
    original = capture_sources(project).manifest
    require_environment(original)
    assert author_module_path(project, "lab_methods.rabi") == method
    assert author_module_path(project, "user_experiments.rabi") == wrapper
    with pytest.raises(ValueError, match="configured author refresh root"):
        author_module_path(project, "os")
    wrapper.write_text("from lab_methods.rabi import value\nvalue += 1\n")
    updated = capture_sources(project).manifest
    assert original.ref != updated.ref
    assert original.maintenance_hash == updated.maintenance_hash
    # Same version, different bytes: reject recovery and prevent local refresh.
    method.write_text("value = 2\n")
    with pytest.raises(ValueError, match="package content changed"):
        require_environment(original)
    assert (
        capture_sources(project).manifest.maintenance_hash != original.maintenance_hash
    )
    method.write_text("value = 1\n")
    require_environment(original)
    # A new, unrecorded helper or resource is also an identity change.
    (package / "calibration.json").write_text("{}")
    with pytest.raises(ValueError, match="package content changed"):
        require_environment(original)
    (metadata / "direct_url.json").write_text('{"dir_info":{"editable":true}}')
    with pytest.raises(ValueError, match="is editable"):
        capture_sources(project)


def test_installed_experiments_are_discovered_alongside_local_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import subprocess
    import sys

    from scopecat_server.scaffold import write_project_scaffold

    root = tmp_path / "project"
    write_project_scaffold(root)
    source = (root / "src/scopecat_lab/authored/signal.py").read_text()
    site = tmp_path / "site"
    package = site / "lab_methods"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "signal.py").write_text(
        source.replace('id="signal"', 'id="shared_signal"')
    )
    metadata = site / "lab_methods-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: lab-methods\nVersion: 1.0\n")
    (metadata / "RECORD").write_text(
        "lab_methods/__init__.py,,\nlab_methods/signal.py,,\n"
    )
    manifest = root / "scopecat.toml"
    manifest.write_text(
        manifest.read_text() + '\n[authors.packages]\nlab_methods="lab-methods"\n'
    )
    application = root / "src/scopecat_lab/application.py"
    application.write_text(
        application.read_text().replace(
            'author_modules=("scopecat_lab.authored",)',
            'author_modules=("lab_methods", "scopecat_lab.authored")',
        )
    )
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join((str(site), os.environ.get("PYTHONPATH", "")))
    )
    # A fresh process tests real import ownership without polluting other projects.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "from scopecat_server.author_worker import validate; "
                "app = validate(Path.cwd(), Path.cwd()); "
                "assert app.authors is not None; "
                "assert len(app.authors.experiments) == 2"
            ),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
