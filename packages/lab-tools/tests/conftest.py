"""Shared isolation for local teaching Notebook author imports."""

import sys

import pytest


@pytest.fixture
def notebook_imports(monkeypatch):
    previous = {
        name: module
        for name, module in sys.modules.copy().items()
        if name == "my_experiment" or name.startswith("my_experiment.")
    }
    finders = list(sys.meta_path)
    try:
        yield monkeypatch
    finally:
        sys.meta_path[:] = finders
        for name in tuple(sys.modules):
            if name == "my_experiment" or name.startswith("my_experiment."):
                del sys.modules[name]
        sys.modules.update(previous)
