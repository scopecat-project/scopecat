"""A first ordinary experiment survives leaving and reopening the application."""

import json
import os
import subprocess
import time
from pathlib import Path

import httpx2

from lab_tools import application
from lab_tools.first_run import SetupRequest
from lab_tools.host_client import ensure_host
from lab_tools.host_operations import Command, Operations, launch
from lab_tools.services import Services
from scopecat.project import open_project
from scopecat_server.lifecycle import inspect_daemon, stop_project


def test_create_external_data_run_and_reopen_primary_workbench(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "application home"
    root = tmp_path / "实验代码"
    data = tmp_path / "实验数据"
    source = tmp_path / "public-source"
    gui = source / "apps" / "scopecat-ui" / "dist"
    gui.mkdir(parents=True)
    (gui / "index.html").write_text("<html>experiment workbench</html>")
    command = Command(
        action="setup",
        setup=SetupRequest(
            mode="create", project=str(root), data_root=str(data), name="实验台"
        ),
    )
    operation = launch(home, source, command)
    operations = Operations(home)
    deadline = time.monotonic() + 60
    while operation.status in ("starting", "running"):
        assert time.monotonic() < deadline, operation
        time.sleep(0.05)
        operation = operations.get(command.id)
    assert operation.status == "succeeded", operation.detail
    assert operation.service is not None and operation.workspace is None
    assert launch(home, source, command) == operation
    service = Services(home).get(operation.service)
    project = open_project(root)
    assert project.runtime_binding.data_root == data
    assert project.capabilities is not None
    assert (root / "src/scopecat_lab/authored/signal.py").is_file()
    assert inspect_daemon(project).state == "running"
    store = Services(home)
    opened: list[str] = []
    monkeypatch.setattr(application.webbrowser, "open", opened.append)
    # The project module namespace belongs to the experiment process, not pytest.
    script = """
import json, sys
from pathlib import Path
import scopecat as sc
from scopecat.application import LabApplication
project = sc.open_project(Path(sys.argv[1]))
project.load_application()
from scopecat_lab.authored.signal import Summary, signal
request = signal(center=0.0)
request.values['position'] = sc.Scan([-1.0, 0.0, 1.0])
from scopecat_server.lifecycle import inspect_daemon
record = inspect_daemon(project).record
assert record is not None
with LabApplication().connect(record.base_url) as lab, project.authoring() as author:
    original_default = lab.config.active()
    original_setup = lab.setup.active()
    templates = lab.setup.templates()
    assert len(templates) == 1 and templates[0].id == 'starter-software'
    imported = lab.setup.import_template(templates[0], name='fresh-software-parameters')
    assert lab.setup.import_template(
        templates[0], name='fresh-software-parameters'
    ) == imported
    assert lab.config.active() == original_default
    assert lab.setup.active() == original_setup
    lab.setup.activate(
        imported.setup, expected_generation=original_setup.activation.generation
    )
    author.use(selection=imported.selection)
    job = author.prepare(request).run()
    run = job.wait(timeout=60).result()
    scenario = run.snapshot.scientific_binding.scenario
    assert scenario is not None and scenario.id == 'starter-software'
    assert scenario.model_id == 'scopecat.starter.responses'
    assert run.snapshot.config_source.entry_id == imported.configuration.entry.id
    assert lab.config.active() == original_default
    report = author.analyze_as(
        run.id, 'scopecat_lab.authored.signal:summarize', Summary
    )
    assert author.reopen(job.receipt).wait(timeout=60).result().id == run.id
    print(json.dumps({'run': run.id, 'points': report.value.points,
                      'mean': report.value.mean, 'analysis': report.publication.id}))
"""
    environment = dict(os.environ)
    environment.pop("SCOPECAT_DAEMON_URL", None)
    try:
        assert store.preferred() == service
        completed = subprocess.run(  # noqa: S603 - fixed virtual experiment script
            [service.python, "-c", script, str(root)],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        assert result["points"] == 3
        assert result["mean"] == 2 / 3
        assert result["analysis"]
        first = inspect_daemon(project)
        assert first.record is not None
        with httpx2.Client(trust_env=False) as http:
            before = http.get(first.record.base_url + "/api/v1/runs")
            before.raise_for_status()
            records = before.json()["items"]
            assert len(records) == 1
            assert records[0]["snapshot"]["run_id"] == result["run"]
            scenario = records[0]["snapshot"]["scientific_binding"]["scenario"]
            assert scenario["id"] == "starter-software"
            assert scenario["limitations"]
        store.stop(service.id)
        # Daily entry has no project path, teaching topic, or management step.
        application.main(["--home", str(home)])
        reopened = inspect_daemon(project)
        assert reopened.state == "running" and reopened.record is not None
        assert reopened.record != first.record
        assert opened == [reopened.record.base_url]
        assert store.preferred() == service
        with httpx2.Client(trust_env=False) as http:
            after = http.get(reopened.record.base_url + "/api/v1/runs")
            after.raise_for_status()
            assert after.json()["items"] == records
    finally:
        stop_project(project)
        ensure_host(home, None).shutdown()
