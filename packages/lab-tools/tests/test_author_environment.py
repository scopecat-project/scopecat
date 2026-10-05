"""Execution receipts preserve full identity while native paths stay compact."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lab_tools import author_environment as environments


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text('[project]\nname = "example"\n')
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / environments.MANIFEST).write_text("delivery identity")
    runtime = Mock(home=tmp_path / "data", lock=threading.Lock())
    runtime.installation.return_value = SimpleNamespace(
        python=Path("application-python"),
        environment={"scopecat": "1", "server": "1"},
    )
    monkeypatch.setattr(environments, "_bundle", lambda _: bundle)
    monkeypatch.setattr(environments, "find_uv_bin", lambda: "uv")
    monkeypatch.setattr(environments, "_independent_python", lambda *_: Path("base"))
    calls = []

    def run(command):
        calls.append(command)
        if "compile" in command:
            Path(command[command.index("--output-file") + 1]).write_text("locked==1\n")

    monkeypatch.setattr(environments, "_run", run)
    capture = Mock()
    monkeypatch.setattr("scopecat_server.author_environment.capture", capture)
    return runtime, source, calls, capture


def test_short_candidates_reuse_full_identity_receipts(preparation):
    runtime, source, calls, capture = preparation
    python = environments.prepare_execution_environment(runtime, source)
    receipts = list((runtime.home / "environments").glob("*/environment.json"))
    assert len(receipts) == 1
    receipt = receipts[0]
    assert len(receipt.parent.name) == 64
    assert python.parent.parent.parent.parent == runtime.home / "environments"
    assert python.parent.parent.parent.name.startswith("e-")
    assert len(python.parent.parent.parent.name) < 16
    assert json.loads(receipt.read_text()) == {"python": str(python)}
    capture.assert_called_once_with(source.resolve(), python)
    assert environments.prepare_execution_environment(runtime, source) == python
    assert sum("install" in command for command in calls) == 1
    # Existing receipts may point at the previous nested layout. Keep using it.
    previous = receipt.parent / "retained-attempt/runtime/bin/python"
    receipt.write_text(json.dumps({"python": str(previous)}))
    assert environments.prepare_execution_environment(runtime, source) == previous
    assert sum("install" in command for command in calls) == 1


def test_failed_candidate_preserves_other_environments(preparation):
    runtime, source, _, capture = preparation
    retained = runtime.home / "environments/e-retained/runtime"
    retained.mkdir(parents=True)
    (retained / "keep").write_text("existing environment")
    capture.side_effect = ValueError("capture failed")
    with pytest.raises(ValueError, match="capture failed"):
        environments.prepare_execution_environment(runtime, source)
    assert (retained / "keep").read_text() == "existing environment"
    assert list((runtime.home / "environments").glob("e-*")) == [retained.parent]
    assert not list((runtime.home / "environments").glob("*/environment.json"))
    capture.side_effect = None
    python = environments.prepare_execution_environment(runtime, source)
    assert python != capture.call_args_list[0].args[1]
    assert retained.is_dir()


def test_concurrent_preparation_publishes_one_candidate_per_identity(preparation):
    runtime, source, calls, capture = preparation
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(
            workers.map(
                lambda _: environments.prepare_execution_environment(runtime, source),
                range(2),
            )
        )
    assert results[0] == results[1]
    capture.assert_called_once()
    assert sum("install" in command for command in calls) == 1
    previous = results[0]
    bundle = source.parent / "bundle"
    (bundle / environments.MANIFEST).write_text("different delivery identity")
    current = environments.prepare_execution_environment(runtime, source)
    assert current != previous
    receipts = list((runtime.home / "environments").glob("*/environment.json"))
    assert len(receipts) == 2
    assert {json.loads(receipt.read_text())["python"] for receipt in receipts} == {
        str(previous),
        str(current),
    }
    assert previous.parent.parent.parent.is_dir()
