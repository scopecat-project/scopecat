"""An ordinary author folder shares a persistent application across reopen."""

import json
import os
import subprocess
from pathlib import Path

import httpx2

from lab_tools import application
from lab_tools.application_runtime import ApplicationRuntime
from scopecat.daemon.views import RunSummaryPage
from scopecat.project import open_project
from scopecat_server.scaffold import write_author_scaffold


def test_author_source_run_and_reopen_direct_workbench(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = ApplicationRuntime(tmp_path / "application home")
    source = tmp_path / "实验代码"
    write_author_scaffold(source)
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>workbench</html>")
    selected = runtime.configure(static_dir=gui)
    identity = runtime.register_source(source)
    assert open_project(source).runtime_binding.data_root == runtime.root / ".scopecat"
    opened = []
    monkeypatch.setattr(application.webbrowser, "open", opened.append)
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "SCOPECAT_DAEMON_URL"):
        environment.pop(name, None)
    try:
        application.main(["--home", str(runtime.home), "--action", "start"])
        assert opened == []
        record = runtime.status().record
        assert record is not None
        result = subprocess.run(  # noqa: S603 - fixed author acceptance cells
            [
                str(selected.python),
                "-I",
                "-c",
                """
import json, sys
import scopecat as sc
project = sc.open_project(sys.argv[1])
_ = project.load_application()
with project.authoring() as session:
    session.refresh()
    from scopecat_lab.authored.parameters import open_parameters
    from scopecat_lab.authored.signal import signal
    params = open_parameters(session)
    prepared = session.prepare(
        signal().sweep(position=[-1.0, 0.0, 1.0]), parameters=params
    )
    job = prepared.run()
    run = job.wait(timeout=60).result()
    assert list(run.measurements()["result"].require_values()) == [0.5, 1.0, 0.5]
    print(json.dumps({"run": run.id}))
""",
                str(source),
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        run_id = json.loads(result.stdout.strip().splitlines()[-1])["run"]
        runtime.stop()
        application.main(
            [
                "--home",
                str(runtime.home),
                "--action",
                "open",
                "--workspace",
                str(source),
            ]
        )
        assert len(opened) == 1 and identity in opened[0]
        reopened = runtime.status().record
        assert reopened is not None and reopened.data_root == record.data_root
        with httpx2.Client(base_url=reopened.base_url, trust_env=False) as client:
            assert client.get("/").content == (gui / "index.html").read_bytes()
            runs = RunSummaryPage.model_validate(client.get("/api/v1/runs").json())
            assert [item.run_id for item in runs.items] == [run_id]
        assert not (runtime.home / "host/services.sqlite").exists()
    finally:
        runtime.stop()
