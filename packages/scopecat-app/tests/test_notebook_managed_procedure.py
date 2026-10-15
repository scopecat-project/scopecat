"""The public Notebook session submits ordinary work without a local executor."""

import importlib
from pathlib import Path

from IPython.core.interactiveshell import InteractiveShell

from scopecat_server.lifecycle import initialize_project, start_project, stop_project


def test_notebook_managed_procedure(tmp_path: Path, notebook_imports) -> None:
    shell = InteractiveShell()
    notebook_module = importlib.import_module("scopecat.notebook_workspace")
    notebook_imports.setattr(notebook_module, "_shell", lambda: shell)
    project = initialize_project(tmp_path / "notebook-procedure")
    manifest = project.root / "scopecat.toml"
    manifest.write_text(
        manifest.read_text()
        + '\n[lab.capabilities]\nprocedures = ["scopecat_lab.workflow:ordinary"]\n'
    )
    (project.root / "src/scopecat_lab/workflow.py").write_text("""\
from pydantic import BaseModel, ConfigDict
from scopecat.api.procedures import LabProcedureContext
from scopecat.automation import procedure

class Intent(BaseModel):
    model_config = ConfigDict(frozen=True)
    label: str

@procedure(id="ordinary", version="1", intent=Intent)
def ordinary(context: LabProcedureContext, intent: Intent) -> None:
    pass
""")
    start_project(project, timeout=60)
    shell.user_ns["root"] = project.root
    try:
        result = shell.run_cell("""
import time
import scopecat as sc
session = sc.notebook(root)
from scopecat_lab.workflow import ordinary, Intent
prepared = session.procedures.prepare(
    ordinary, Intent(label="Notebook"), request_key="notebook-procedure"
)
task = prepared.submit()
assert task.dispatch_error is None
identity = task.id
session.close()
session = sc.notebook(root)
task = session.procedures.get(identity)
deadline = time.monotonic() + 60
while task.snapshot.closure is None and time.monotonic() < deadline:
    time.sleep(0.1)
assert task.snapshot.closure.status == "succeeded"
assert prepared.reconnect(session).submit().id == identity
session.close()
""")
        assert result.error_before_exec is None
        assert result.error_in_exec is None
    finally:
        workspace = shell.user_ns.get("session")
        if workspace is not None:
            workspace.close()
        stop_project(project)
