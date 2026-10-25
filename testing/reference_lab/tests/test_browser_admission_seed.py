"""Run the browser's actual admission-only seed against an isolated daemon."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from scopecat.api.lab import LabClient
from scopecat.daemon.client import DaemonClient
from scopecat.project import load_project
from scopecat_server.lifecycle import start_project, stop_project

from reference_lab.configuration import initial_parameters, initial_setup

ROOT = Path(__file__).resolve().parents[3]
SEED = ROOT / "apps/scopecat-ui/e2e/fixtures/admit_procedure.py"


def test_browser_seed_retains_an_undispatched_procedure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("SCOPECAT_LOCATOR_HOME", str(tmp_path / "locator"))
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    for name in ("src", "config", "scopecat.toml"):
        source = ROOT / "testing/reference_lab" / name
        if source.is_dir():
            shutil.copytree(source, tmp_path / name)
        else:
            shutil.copy2(source, tmp_path / name)
    project = load_project(tmp_path / "scopecat.toml")
    endpoint = start_project(project)
    try:
        with LabClient(DaemonClient(endpoint.base_url)) as lab:
            values = initial_parameters()
            revision = lab.parameters.save(
                name="browser-values",
                catalog=values.catalog,
                parameters=values.parameters,
            )
            lab.parameters.create_branch("browser", revision=revision)
            resolved = lab.setup.import_recipe(initial_setup(), name="browser-template")
            setup = lab.setup.definition(resolved.resolution.definition_id).definition
            lab.setup.save(setup, name="browser-bench-a")
        env = dict(os.environ)
        env.pop("SCOPECAT_DAEMON_URL", None)
        result = subprocess.run(  # noqa: S603 - exact repository seed, isolated project
            [sys.executable, str(SEED), str(tmp_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        procedure_id = result.stdout.strip()
        assert procedure_id and len(result.stdout.splitlines()) == 1
        with LabClient(DaemonClient(endpoint.base_url)) as lab:
            handle = lab.procedures.get(procedure_id)
            assert handle.steps().items == ()
            retained = handle.snapshot
            assert retained.state == "ready"
            assert retained.source is not None
            assert (
                retained.definition.id
                == "scopecat.author:reference_lab.temperature_diagnostic"
            )
    finally:
        stop_project(project)
