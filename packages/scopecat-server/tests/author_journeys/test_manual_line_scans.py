"""Finite lines use the normal authored preview, procedure and exact child receipts."""

import time
from dataclasses import dataclass
from uuid import uuid4

import httpx2
import pytest
from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.api.lab import LabClient
from scopecat.application.author_procedures import AuthorProcedure
from scopecat.application.author_project import AuthorProject
from scopecat.daemon.client import DaemonClient
from scopecat.kernel.quantity import Quantity
from scopecat.records.control_edit import ControlEdit
from scopecat.records.manual_line import ManualLineSelection, PhysicalLine
from scopecat.records.manual_line_scan import ManualLineLabels, ManualLineScanCreate
from scopecat.records.parameter_revision import ParameterRevision
from scopecat_testkit.authoring import source_workspace_id

from scopecat_server.services.manual_lines import ManualLineScanView


@dataclass
class Wiring:
    confirmed: bool


def settled(task: AuthorProcedure, state: str) -> None:
    deadline = time.monotonic() + 45
    while True:
        view = task.progress()
        if view.procedure.state == state and not view.dispatch.worker_running:
            return
        assert time.monotonic() < deadline, view
        assert view.dispatch.failure is None, view.dispatch.failure
        time.sleep(0.05)


def test_ordered_lines_remeasurement_and_changed_ordinary_inputs_keep_exact_receipts(
    independent_lab_daemon: str,
    independent_parameters: ParameterRevision,
) -> None:
    endpoint = independent_lab_daemon
    with (
        AuthorProject(endpoint, workspace_id=source_workspace_id(endpoint)) as author,
        LabClient(DaemonClient(endpoint)) as lab,
        httpx2.Client(
            base_url=endpoint, timeout=60, headers={"content-type": "application/json"}
        ) as http,
    ):
        author.use(
            parameters=independent_parameters, setup=lab.setup.get("initial").ref
        )
        prepared = author.prepare("ui_signal.signal", actor="measure-operator")
        plan = prepared.save_plan("S21 template", saved_by="list-operator")
        command = ManualLineScanCreate(
            scan_id=f"lines_{uuid4().hex}",
            saved_by="list-operator",
            name="Reading lines",
            template=plan.definition,
            lines=tuple(
                PhysicalLine(line_id=f"line_{uuid4().hex}", label=f"Line {i}")
                for i in [1, 2]
            ),
        )
        response = http.post(
            "/api/v1/manual-line-scans", content=command.model_dump_json()
        )
        assert response.status_code == 200, response.text
        assert (
            http.post(
                "/api/v1/manual-line-scans", content=command.model_dump_json()
            ).json()
            == response.json()
        )
        scan_id = command.scan_id

        def view() -> ManualLineScanView:
            return ManualLineScanView.model_validate_json(
                http.get(f"/api/v1/manual-line-scans/{scan_id}").content
            )

        def submit(index: int, *, changed: bool = False):
            current = view().scan
            line = current.lines[index]
            request = prepared.request.model_copy(
                update={
                    "manual_line": ManualLineSelection(
                        scan_id=scan_id, line_id=line.line_id, label=line.label
                    ),
                    "reviewed": None,
                    "control_edits": {
                        "amplitude": ControlEdit(
                            mode="fixed", value=Quantity(0.08, "V")
                        )
                    }
                    if changed
                    else prepared.request.control_edits,
                }
            )
            preview = author.preview(request)
            checked = request.model_copy(
                update={
                    "reviewed": preview.reviewed,
                    "manual_state": preview.manual_state,
                }
            )
            checked = checked.model_copy(
                update={
                    "action": "submit",
                    "request_key": uuid4().hex,
                    "expected_request_hash": checked.request_hash,
                }
            )
            return author.submit(checked)

        assert [item.state for item in view().lines] == ["not_started", "not_started"]
        with pytest.raises(httpx2.HTTPStatusError):
            submit(1)
        first = author.procedures.get(submit(0).procedure_id)
        settled(first, "waiting_for_input")
        assert view().active_procedure_id == first.id
        assert lab.runs().items == ()
        with pytest.raises(httpx2.HTTPStatusError):
            submit(1)
        first.pending_input(
            schema=AnalysisFactSchema("scopecat.manual-line-wiring.v1", Wiring)
        ).respond(Wiring(True), actor="wire-first")
        first.resume()
        settled(first, "closed")
        assert (
            first.snapshot.closure is not None
            and first.snapshot.closure.status == "succeeded"
        )
        original = view().lines[0].attempts[0]
        assert original.run is not None
        assert original.run.control.admission.manual_line_confirmation is not None
        assert (
            original.run.control.admission.manual_line_confirmation.line_id
            == command.lines[0].line_id
        )
        renamed = http.put(
            f"/api/v1/manual-line-scans/{scan_id}/labels",
            content=ManualLineLabels(
                expected_revision=1,
                name=command.name,
                saved_by="rename-operator",
                lines=(
                    command.lines[0].model_copy(update={"label": "Renamed readout"}),
                    command.lines[1],
                ),
            ).model_dump_json(),
        )
        assert renamed.status_code == 200, renamed.text
        assert view().lines[0].attempts[0].selection.label == "Line 1"
        for index in [1, 0]:
            task = author.procedures.get(submit(index, changed=True).procedure_id)
            settled(task, "waiting_for_input")
            task.pending_input(
                schema=AnalysisFactSchema("scopecat.manual-line-wiring.v1", Wiring)
            ).respond(Wiring(True), actor="wire-next")
            task.resume()
            settled(task, "closed")
            assert (
                task.snapshot.closure is not None
                and task.snapshot.closure.status == "succeeded"
            )
        retained = view()
        assert [item.state for item in retained.lines] == ["completed", "completed"]
        assert [len(item.attempts) for item in retained.lines] == [2, 1]
        assert retained.lines[0].attempts[0] == original
        assert retained.lines[0].attempts[1].selection.label == "Renamed readout"
        ids = {
            item.run.snapshot.run_id
            for line in retained.lines
            for item in line.attempts
            if item.run
        }
        assert len(ids) == 3
        assert (
            retained.lines[0].attempts[0].procedure.intent["edits"]
            != retained.lines[0].attempts[1].procedure.intent["edits"]
        )
