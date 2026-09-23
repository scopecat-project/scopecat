from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from scopecat.api.lab import LabClient
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import DAEMON_URL_ENV
from scopecat.project import load_project
from scopecat.records.parameter_revision import ParameterRevision
from scopecat_server.lifecycle import DaemonLifecycleError, start_project, stop_project
from scopecat_testkit.project_loading import isolated_project_imports

from reference_lab.configuration import bootstrap_config

EXAMPLE_ROOT = Path(__file__).parents[1]


@dataclass(frozen=True, slots=True)
class ReferenceLabDaemon:
    url: str
    root: Path


@pytest.fixture(autouse=True)
def isolate_project_loader() -> Generator[None]:
    with isolated_project_imports():
        yield


@pytest.fixture(scope="session")
def reference_lab_daemon(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[ReferenceLabDaemon]:
    """Run every notebook against one real HTTP daemon instance."""

    project_root = tmp_path_factory.mktemp("reference-lab-project")
    shutil.copytree(EXAMPLE_ROOT / "notebooks", project_root / "notebooks")
    shutil.copytree(EXAMPLE_ROOT / "config", project_root / "config")
    shutil.copytree(EXAMPLE_ROOT / "src", project_root / "src")
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", project_root / "scopecat.toml")
    project = load_project(project_root / "scopecat.toml")
    try:
        record = start_project(project)
    except DaemonLifecycleError as error:
        log = project_root / ".scopecat" / "daemon.log"
        if log.exists():
            error.add_note(
                "Reference fixture daemon log tail:\n"
                + log.read_bytes()[-8192:].decode("utf-8", errors="replace")
            )
        raise
    previous_url = os.environ.get(DAEMON_URL_ENV)
    os.environ[DAEMON_URL_ENV] = record.base_url
    try:
        yield ReferenceLabDaemon(url=record.base_url, root=project_root)
    finally:
        if previous_url is None:
            os.environ.pop(DAEMON_URL_ENV, None)
        else:
            os.environ[DAEMON_URL_ENV] = previous_url
        stop_project(project)


@pytest.fixture
def reference_lab_notebooks(
    reference_lab_daemon: ReferenceLabDaemon,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Path]:
    """Load gallery code from the same workspace that owns the test service."""
    retained = {
        name: module
        for name, module in tuple(sys.modules.items())
        if name == "reference_lab" or name.startswith("reference_lab.")
    }
    for name in retained:
        del sys.modules[name]
    monkeypatch.setattr(
        sys, "path", [str(reference_lab_daemon.root / "src"), *sys.path]
    )
    try:
        yield reference_lab_daemon.root / "notebooks"
    finally:
        for name in tuple(sys.modules):
            if name == "reference_lab" or name.startswith("reference_lab."):
                del sys.modules[name]
        sys.modules.update(retained)


@pytest.fixture
def reference_lab_author_imports() -> Generator[None]:
    """Cloned author workspaces must not reuse collection-time repository imports."""
    prefix = "reference_lab.workflows.authored"
    parent = sys.modules.get("reference_lab.workflows")
    missing = object()
    original_attribute = getattr(parent, "authored", missing)
    original_modules = {
        name: module
        for name, module in tuple(sys.modules.items())
        if name == prefix or name.startswith(prefix + ".")
    }
    original_finders = list(sys.meta_path)
    for name in original_modules:
        del sys.modules[name]
    if parent is not None and hasattr(parent, "authored"):
        delattr(parent, "authored")
    try:
        yield
    finally:
        sys.meta_path[:] = original_finders
        for name in tuple(sys.modules):
            if name == prefix or name.startswith(prefix + "."):
                del sys.modules[name]
        sys.modules.update(original_modules)
        if parent is not None:
            if original_attribute is missing:
                if hasattr(parent, "authored"):
                    delattr(parent, "authored")
            else:
                vars(parent)["authored"] = original_attribute


@pytest.fixture(scope="session")
def independent_lab_daemon(tmp_path_factory: pytest.TempPathFactory) -> Generator[str]:
    """Share equipment without notebooks, defaults or endpoint env mutation."""
    root = tmp_path_factory.mktemp("independent-reference-lab")
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(
        EXAMPLE_ROOT / "fixtures/equipment_bootstrap.py",
        root / "src/equipment_bootstrap.py",
    )
    (root / "scopecat.toml").write_text(
        (EXAMPLE_ROOT / "scopecat.toml")
        .read_text()
        .replace(
            "reference_lab.application:create_bootstrap",
            "equipment_bootstrap:create_bootstrap",
        )
    )
    project = load_project(root / "scopecat.toml")
    endpoint = start_project(project)
    try:
        yield endpoint.base_url
    finally:
        stop_project(project)


@pytest.fixture
def independent_parameters(independent_lab_daemon: str) -> ParameterRevision:
    """Give each consumer an explicit saved input without changing global state."""
    config = bootstrap_config()
    with LabClient(DaemonClient(independent_lab_daemon)) as lab:
        assert lab.config.registry().entries == ()
        return lab.parameters.save(
            name="plan-inputs-" + uuid4().hex,
            catalog=config.parameter_catalog,
            parameters=config.parameter_snapshot,
        )
