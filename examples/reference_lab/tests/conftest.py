from __future__ import annotations

import shutil
import sys
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest
from scopecat.api.lab import LabClient
from scopecat.daemon.client import DaemonClient
from scopecat.project import load_project
from scopecat.records.parameter_revision import ParameterRevision
from scopecat_server.lifecycle import start_project, stop_project
from scopecat_testkit.project_loading import isolated_project_imports

from reference_lab.configuration import bootstrap_config

EXAMPLE_ROOT = Path(__file__).parents[1]


@dataclass(frozen=True, slots=True)
class ReferenceLabDaemon:
    url: str
    root: Path


@pytest.fixture(scope="session")
def reference_collection_modules() -> dict[str, ModuleType]:
    # Test modules retain collection-time classes and declarations. Restore their
    # matching imports even when a module-scoped fixture loaded a cloned project
    # before function-scoped isolation started.
    return {
        name: module
        for name, module in tuple(sys.modules.items())
        if name.partition(".")[0] in {"reference_lab", "reference_lab_authors"}
    }


@pytest.fixture(autouse=True)
def isolate_project_loader(
    reference_collection_modules: dict[str, ModuleType],
) -> Generator[None]:
    def restore() -> None:
        for name in tuple(sys.modules):
            if name.partition(".")[0] in {"reference_lab", "reference_lab_authors"}:
                del sys.modules[name]
        sys.modules.update(reference_collection_modules)
        for name, module in reference_collection_modules.items():
            parent, _, child = name.rpartition(".")
            if parent in reference_collection_modules:
                vars(reference_collection_modules[parent])[child] = module

    try:
        with isolated_project_imports():
            restore()
            yield
    finally:
        restore()


@pytest.fixture
def reference_lab_author_imports() -> Generator[None]:
    """Cloned author workspaces must not reuse collection-time repository imports."""
    prefix = "reference_lab_authors.authored"
    parent = sys.modules.get("reference_lab_authors")
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


@pytest.fixture
def select_reference_source(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Path], None]:
    """Match notebook imports to a cloned source tree before its first refresh."""

    def select(root: Path) -> None:
        for name in tuple(sys.modules):
            if name.partition(".")[0] in {"reference_lab", "reference_lab_authors"}:
                del sys.modules[name]
        monkeypatch.setattr(sys, "path", [str(root / "src"), *sys.path])

    return select


@pytest.fixture(scope="session")
def independent_lab_daemon(tmp_path_factory: pytest.TempPathFactory) -> Generator[str]:
    """Share equipment without notebooks, defaults or endpoint env mutation."""
    root = tmp_path_factory.mktemp("independent-reference-lab")
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
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
