"""Execute the GUI's exact curated declaration through the public author API."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from scopecat_testkit.project_loading import isolated_project_imports

from scopecat_server.lifecycle import initialize_project, start_project, stop_project

ROOT = Path(__file__).resolve().parents[3]


def test_device_python_example_prepares_runs_and_reopens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user-data"))
    guide = (ROOT / "docs/how-to/device-to-python.md").read_text()
    setup_code = re.findall(r"```python\n(.*?)\n```", guide, re.DOTALL)[1]
    setup_code = setup_code.replace(
        'device_id = "paste-registered-virtual-vna-id"',
        "device_id = device.device.id",
    )
    component = (
        ROOT / "apps/scopecat-ui/src/features/instruments/DevicePythonGuide.tsx"
    ).read_text()
    match = re.search(r"experiment: `(.*?)`", component, re.DOTALL)
    assert match is not None
    with isolated_project_imports():
        project = initialize_project(tmp_path)
        (tmp_path / "src/scopecat_lab/authored/capture.py").write_text(match[1])
        environment = dict(os.environ)
        environment.pop("SCOPECAT_DAEMON_URL", None)
        connection = """
from pathlib import Path
import scopecat as sc
project = sc.open_project(".")
_ = project.load_application()
author = project.authoring()
"""
        exercise = (
            connection
            + """
from scopecat.records.device import DeviceConnection
from scopecat.records.config import VirtualInstrumentConnection
[driver] = [item for item in author.devices.drivers()
            if item.driver_id == "scopecat.virtual.vna"]
device = author.devices.register(label="Synthetic VNA", connection=DeviceConnection(
    driver=driver, connection=VirtualInstrumentConnection()),
    device_id="registered-vna")
"""
            + setup_code
            + """
author.refresh()
from scopecat_lab.authored.capture import capture
from scopecat_lab.authored.parameters import open_parameters
params = open_parameters(author)
author.use(setup=setup.ref)
request = capture(points=51)
for name, routes, code in (
    ("missing", [], "module_resource_route_not_found"),
    ("ambiguous", [routing.routes[0],
        routing.routes[0].model_copy(update={"id": "another-route"})],
        "multiple routes for the same role"),
):
    try:
        invalid = author.setup.save(definition.model_copy(update={
            "routing": RoutingGraph(roles=routing.roles, routes=routes),
        }), name=name)
        author.use(setup=invalid.ref)
        author.prepare(request, parameters=params)
    except ValueError as error:
        assert code in str(error), str(error)
    else:
        raise AssertionError("invalid routing unexpectedly prepared")
author.use(setup=setup.ref)
prepared = author.prepare(request, parameters=params)
request.values["points"] = 101
job = prepared.run()
run = job.wait(timeout=120).result()
assert len(run.measurements()["frequency"].require_magnitudes("GHz")[0]) == 51
Path("receipt.txt").write_text(str(job.receipt))
Path("run-id.txt").write_text(run.id)
second = author.prepare(request, parameters=params).run().wait(timeout=120).result()
assert second.id != run.id
assert len(second.measurements()["frequency"].require_magnitudes("GHz")[0]) == 101
assert len(run.measurements()["frequency"].require_magnitudes("GHz")[0]) == 51
author.close()
"""
        )
        reopen = (
            connection
            + """
receipt = Path(Path("receipt.txt").read_text())
run = author.reopen(receipt).wait(timeout=120).result()
assert run.id == Path("run-id.txt").read_text()
assert len(run.measurements()["frequency"].require_magnitudes("GHz")[0]) == 51
author.close()
"""
        )
        for script in (exercise, reopen):
            start_project(project)
            try:
                result = subprocess.run(  # noqa: S603 - local public example, virtual-only
                    [sys.executable, "-c", script],
                    cwd=tmp_path,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=240,
                    check=False,
                )
                assert result.returncode == 0, result.stdout + result.stderr
            finally:
                stop_project(project)
