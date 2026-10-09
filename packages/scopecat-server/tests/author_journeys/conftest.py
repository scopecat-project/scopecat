from __future__ import annotations

import sys
from collections.abc import Callable, Generator
from pathlib import Path

import pytest
from scopecat_testkit.project_loading import isolated_project_imports

FIXTURE_ROOT = Path(__file__).resolve().parents[4] / "testing/fixtures/retained-signal"
sys.path.insert(0, str(FIXTURE_ROOT / "src"))


@pytest.fixture(autouse=True)
def isolate_author_imports() -> Generator[None]:
    modules = {
        name: module
        for name, module in sys.modules.copy().items()
        if name == "ui_signal" or name.startswith("ui_signal.")
    }
    with isolated_project_imports():
        try:
            yield
        finally:
            for name in tuple(sys.modules):
                if name == "ui_signal" or name.startswith("ui_signal."):
                    del sys.modules[name]
            sys.modules.update(modules)


@pytest.fixture
def select_author_source(monkeypatch: pytest.MonkeyPatch) -> Callable[[Path], None]:
    def select(root: Path) -> None:
        for name in tuple(sys.modules):
            if name == "ui_signal" or name.startswith("ui_signal."):
                del sys.modules[name]
        monkeypatch.setattr(sys, "path", [str(root / "src"), *sys.path])

    return select
