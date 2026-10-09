from __future__ import annotations

import sys
from collections.abc import Callable, Generator
from pathlib import Path
from types import ModuleType

import pytest
from scopecat.records.parameter_revision import ParameterRevision
from scopecat_testkit.project_loading import isolated_project_imports

FIXTURE_ROOT = Path(__file__).resolve().parents[4] / "testing/fixtures/retained-signal"
sys.path.insert(0, str(FIXTURE_ROOT / "src"))


@pytest.fixture(scope="session")
def author_collection_modules() -> dict[str, ModuleType]:
    return {
        name: module
        for name, module in tuple(sys.modules.items())
        if name == "ui_signal" or name.startswith("ui_signal.")
    }


@pytest.fixture(autouse=True)
def isolate_author_imports(
    author_collection_modules: dict[str, ModuleType],
) -> Generator[None]:
    original_finders = list(sys.meta_path)

    def restore() -> None:
        for name in tuple(sys.modules):
            if name == "ui_signal" or name.startswith("ui_signal."):
                del sys.modules[name]
        sys.modules.update(author_collection_modules)
        for name, module in author_collection_modules.items():
            parent, _, child = name.rpartition(".")
            if parent in author_collection_modules:
                vars(author_collection_modules[parent])[child] = module

    try:
        with isolated_project_imports():
            restore()
            yield
    finally:
        sys.meta_path[:] = original_finders
        restore()


@pytest.fixture
def select_author_source(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path], None]:
    def select(root: Path) -> None:
        for name in tuple(sys.modules):
            if name == "ui_signal" or name.startswith("ui_signal."):
                del sys.modules[name]
        monkeypatch.setattr(sys, "path", [str(root / "src"), *sys.path])

    return select


@pytest.fixture
def independent_lab_daemon(
    tmp_path: Path, select_author_source: Callable[[Path], None]
) -> Generator[str]:
    import shutil

    from scopecat.project import load_project

    from scopecat_server.lifecycle import start_project, stop_project

    root = tmp_path / "author-project"
    shutil.copytree(FIXTURE_ROOT, root)
    select_author_source(root)
    project = load_project(root / "scopecat.toml")
    endpoint = start_project(project)
    try:
        yield endpoint.base_url
    finally:
        stop_project(project)


@pytest.fixture
def independent_parameters(independent_lab_daemon: str) -> ParameterRevision:
    from scopecat.api.lab import LabClient
    from scopecat.daemon.client import DaemonClient
    from ui_signal.application import initial_parameters

    content = initial_parameters()
    with LabClient(DaemonClient(independent_lab_daemon)) as lab:
        return lab.parameters.save(
            name="author-inputs", catalog=content.catalog, parameters=content.parameters
        )
