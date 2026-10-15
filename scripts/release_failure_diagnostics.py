"""Qualification-only bounded fixture diagnostics; no environment dump."""
import json
import subprocess
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def retain_timeout_output(monkeypatch):
    original = subprocess.run
    def run(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except subprocess.TimeoutExpired as error:
            for name in ("stdout", "stderr"):
                value = getattr(error, name, None)
                if value:
                    print("DIAG timeout " + name, repr(value[-16000:]), flush=True)
            raise
    monkeypatch.setattr(subprocess, "run", run)

@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.failed and "tmp_path" in item.funcargs:
        root = item.funcargs["tmp_path"]
        for path in root.rglob("verified-*.ipynb"):
            print("DIAG notebook", path.name, flush=True)
            for cell in json.loads(path.read_text(encoding="utf-8"))["cells"]:
                for output in cell.get("outputs", []):
                    value = output.get("text", output.get("traceback", []))
                    print(str(value)[-20000:], flush=True)
        for path in root.rglob("daemon.log"):
            print("DIAG daemon log", path.read_bytes()[-16000:].decode("utf-8", errors="replace"), flush=True)
    return report
