"""Compile the installed verifier's actual author without starting a daemon."""

import importlib.util
import sys
from pathlib import Path
from typing import cast

import pytest
from scripts.verify_scalar_author_result import SOURCE

from scopecat.authoring.experiments import Experiment
from scopecat.compiler.frontend.resolution import compile_invocation


@pytest.mark.parametrize("gain", [1.0, 2.0])
def test_installed_scalar_author_compiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gain: float
) -> None:
    # Reuse the installed journey's bytes: a separate example would miss drift.
    name = "installed_scalar_contract"
    path = tmp_path / f"{name}.py"
    path.write_text(SOURCE, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    author = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, author)
    spec.loader.exec_module(author)
    declaration = cast("Experiment[..., object]", author.mean_iq_experiment)
    assert compile_invocation(declaration.build(gain=gain)) is not None
