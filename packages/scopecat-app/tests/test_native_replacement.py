"""Replacement checks use the candidate's production layout resolver."""

import os
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/verify_native_replacement.py"
spec = spec_from_file_location("verify_native_replacement", SCRIPT)
probe = module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize("legacy", [False, True])
def test_selection_uses_candidate_runtime(tmp_path, legacy):
    home = tmp_path / "isolated home"
    data = home / "data"
    state = data if legacy else data / "state"
    state.mkdir(parents=True)
    if legacy:
        (data / "runtime").mkdir()
    selection = state / "installation.json"
    selection.write_text("retained selection")
    observed = probe.installation_selection(
        Path(sys.executable), home, dict(os.environ)
    )
    assert observed == selection
    assert observed.read_text() == "retained selection"


def test_selection_rejects_mixed_layout_without_mutation(tmp_path):
    data = tmp_path / "data"
    (data / "runtime").mkdir(parents=True)
    (data / "state").mkdir()
    for path in (data / "installation.json", data / "state/installation.json"):
        path.write_text("original")
    before = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    with pytest.raises(subprocess.CalledProcessError) as error:
        probe.installation_selection(Path(sys.executable), tmp_path, dict(os.environ))
    assert "同时发现新旧应用布局" in error.value.stderr
    assert {p: p.read_bytes() for p in data.rglob("*") if p.is_file()} == before
