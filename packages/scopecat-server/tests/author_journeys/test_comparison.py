"""Independent retained-run analysis over real storage and the HTTP worker boundary."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

import httpx2 as httpx
import pytest
import scopecat as sc
from scopecat.api.comparison import COMPARISON_REQUEST_SCHEMA
from scopecat.api.lab import LabClient
from scopecat.api.procedures import ProcedureHandle
from scopecat.application.comparison import ComparisonHandoff
from scopecat.application.launch import LaunchCatalog, LaunchPreview, LaunchSubmission
from scopecat.automation import RunOutputRef
from scopecat.daemon.client import DaemonClient
from scopecat.project import load_project
from scopecat.records.analysis import MeasurementAnalysisRecordInput
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.comparison import (
    ComparisonInspection,
    ComparisonPublication,
    ComparisonRequest,
    ComparisonSelection,
)
from scopecat.records.control_edit import ControlEdit
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.run_request import AxisValuesSourceRecord
from scopecat.records.scientific_selection import (
    ParameterConfiguration,
    ScientificSelection,
)
from scopecat_testkit.authoring import source_workspace_id
from ui_signal.application import initial_parameters
from ui_signal.comparison import FIT_SCHEMA, NEXT_INPUT_SCHEMA, REVIEW_SCHEMA
from ui_signal.model import MODEL

from scopecat_server.lifecycle import start_project, stop_project

from .conftest import FIXTURE_ROOT


def test_two_retained_runs_fit_candidate_rejection_and_handoff(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    select_author_source: Callable[[Path], None],
) -> None:
    root = tmp_path / "comparison"
    shutil.copytree(FIXTURE_ROOT, root)
    select_author_source(root)
    project = load_project(root / "scopecat.toml")
    url = start_project(project).base_url
    request.addfinalizer(lambda: stop_project(project))
    with (
        LabClient(DaemonClient(url)) as lab,
        httpx.Client(
            base_url=url, timeout=60, headers={"content-type": "application/json"}
        ) as http,
    ):
        setup = lab.setup.get("initial")
        content = initial_parameters()
        independent_parameters = lab.parameters.save(
            name="comparison-inputs",
            catalog=content.catalog,
            parameters=content.parameters,
        )
        frequencies = [sc.Quantity(value, "GHz") for value in (4.6, 4.7, 4.8, 4.9, 5.0)]
        catalog = LaunchCatalog.model_validate(
            http.get(
                "/api/v1/experiment-launcher",
                headers={"X-Scopecat-Workspace": source_workspace_id(url)},
            ).json()
        )
        entry = next(item for item in catalog.entries if item.id == "ui_signal.signal")
        launch = LaunchRequest(
            workspace_id=source_workspace_id(url),
            action="preview",
            experiment=entry.id,
            version=entry.version,
            selection=ScientificSelection(
                configuration=ParameterConfiguration(
                    ref=independent_parameters.ref, setup=setup.ref
                )
            ),
            control_edits={
                "frequency": ControlEdit(
                    mode="scan",
                    axis=AxisValuesSourceRecord(values=list(frequencies)),
                )
            },
        )

        def acquire(request: LaunchRequest, key: str) -> ProcedureHandle:
            response = http.post(
                "/api/v1/experiment-launcher/preview", content=request.model_dump_json()
            )
            assert response.status_code == 200, response.text
            preview = LaunchPreview.model_validate_json(response.text)
            response = http.post(
                "/api/v1/experiment-launcher/submit",
                content=request.model_copy(
                    update={
                        "action": "submit",
                        "request_key": key,
                        "expected_request_hash": preview.request_hash,
                        "reviewed": preview.reviewed,
                        "manual_state": preview.manual_state,
                        "code_revision": preview.code_revision,
                    }
                ).model_dump_json(),
            )
            assert response.status_code == 200, response.text
            admitted = LaunchSubmission.model_validate_json(response.text)
            assert admitted.dispatch_error is None
            procedure = lab.procedures.get(admitted.procedure_id)
            deadline = time.monotonic() + 30
            while procedure.snapshot.closure is None:
                assert time.monotonic() < deadline, procedure.snapshot
                time.sleep(0.05)
            assert procedure.snapshot.closure is not None
            assert procedure.snapshot.closure.status == "succeeded"
            return procedure

        source_procedure = acquire(launch, "comparison-source")
        closed = source_procedure.snapshot
        output = source_procedure.step("experiment").output
        assert isinstance(output, RunOutputRef)
        primary = lab.get_run(output.run_id)
        secondary_procedure = acquire(
            launch.model_copy(
                update={
                    "control_edits": {
                        **launch.control_edits,
                        "amplitude": ControlEdit(
                            mode="fixed", value=sc.Quantity(0.08, "V")
                        ),
                    }
                }
            ),
            "comparison-secondary",
        )
        secondary_output = secondary_procedure.step("experiment").output
        assert isinstance(secondary_output, RunOutputRef)
        secondary = lab.get_run(secondary_output.run_id)
        runs = (primary, secondary)
        originals = tuple(run.measurements()["result"].require_values() for run in runs)
        original_requests = tuple(run.request for run in runs)
        assert lab.config.registry().entries == ()
        run_ids = {run.id for run in lab.runs().items}
        procedure_ids = {item.id for item in lab.procedures.list().items}
        base = ComparisonRequest(
            workspace_id=source_workspace_id(url),
            action="inspect",
            model_id=MODEL.id,
            model_version=MODEL.version,
            primary_run=primary.id,
            secondary_run=secondary.id,
        )

        def call(command: ComparisonRequest) -> str:
            response = http.post(
                "/api/v1/run-comparison",
                content=command.model_dump_json(),
                headers={"content-type": "application/json"},
            )
            assert response.status_code == 200, response.text
            return response.text

        inspected = ComparisonInspection.model_validate_json(call(base))
        command = base.model_copy(
            update={
                "action": "fit",
                "code_revision": inspected.code_revision,
                "primary": ComparisonSelection(
                    run_id=primary.id,
                    content_hash=inspected.primary.content_hash,
                    points=(4, 1, 2, 3),
                ),
                "secondary": ComparisonSelection(
                    run_id=secondary.id,
                    content_hash=inspected.secondary.content_hash,
                    points=(0, 1, 2, 3, 4),
                ),
                "parameters": {"offset_ghz": 0.0},
            }
        )
        first = ComparisonPublication.model_validate_json(call(command))
        saved = primary.published_analysis(first.analysis_id)
        assert saved.fact_as("comparison-request", COMPARISON_REQUEST_SCHEMA) == command
        fit = saved.fact_as("fit", FIT_SCHEMA)
        assert fit.center_ghz == pytest.approx(4.8, abs=0.02)
        assert {
            item.run_id
            for item in saved.inputs
            if isinstance(item, MeasurementAnalysisRecordInput)
        } == {run.id for run in runs}
        assert len(saved.executions) == 1
        assert (
            saved.fact_as("next-input", NEXT_INPUT_SCHEMA).frequency.value
            == fit.center_ghz
        )
        second = ComparisonPublication.model_validate_json(
            call(command.model_copy(update={"parameters": {"offset_ghz": 0.01}}))
        )
        assert second.analysis_id != first.analysis_id
        assert (
            primary.published_analysis(second.analysis_id).revision
            == saved.revision + 1
        )
        action = base.model_copy(
            update={
                "action": "candidate",
                "code_revision": AuthorRevisionRef(content_hash="sha256:" + "f" * 64),
                "model_id": "changed-after-fit",
                "model_version": "different",
                "secondary_run": "changed-after-fit",
                "parameters": {"offset_ghz": 99.0},
                "analysis_id": saved.id,
                "analysis_hash": saved.publication_hash,
            }
        )
        candidate_ref = ComparisonPublication.model_validate_json(call(action))
        candidate = primary.published_analysis(candidate_ref.analysis_id)
        retained_request = candidate.fact_as(
            "comparison-request", COMPARISON_REQUEST_SCHEMA
        )
        assert retained_request.code_revision == command.code_revision
        assert retained_request.primary == command.primary
        assert retained_request.secondary == command.secondary
        assert retained_request.parameters == command.parameters
        assert retained_request.model_id == command.model_id
        assert candidate.parameter_proposals[0].source_run_id == primary.id
        assert (
            candidate.parameter_proposals[0].base_config_content_hash
            == primary.snapshot.config_content_hash
        )
        rejected_ref = ComparisonPublication.model_validate_json(
            call(
                action.model_copy(
                    update={
                        "action": "reject",
                        "analysis_id": candidate.id,
                        "analysis_hash": candidate.publication_hash,
                        "reason": "Model requires more evidence",
                    }
                )
            )
        )
        review = primary.published_analysis(rejected_ref.analysis_id).fact_as(
            "review", REVIEW_SCHEMA
        )
        assert not review.accepted
        assert review.candidate_analysis == candidate.id
        assert review.candidate_publication_hash == candidate.publication_hash
        handoff = ComparisonHandoff.model_validate_json(
            call(action.model_copy(update={"action": "handoff"}))
        )
        assert handoff.source_hash == saved.publication_hash
        assert handoff.request.action == "preview"
        assert handoff.request.request_key == ""
        assert handoff.request.control_edits["frequency"].value == sc.Quantity(
            fit.center_ghz, "GHz"
        )
        stale = (
            command.model_copy(
                update={
                    "secondary": command.secondary.model_copy(
                        update={"content_hash": "sha256:wrong"}
                    )
                }
            )
            if command.secondary
            else command
        )
        response = http.post("/api/v1/run-comparison", content=stale.model_dump_json())
        assert response.status_code == 422
        assert "changed" in response.text
        missing = command.model_copy(update={"secondary_run": "removed-run"})
        response = http.post(
            "/api/v1/run-comparison", content=missing.model_dump_json()
        )
        assert response.status_code == 422
        assert {run.id for run in lab.runs().items} == run_ids
        assert {item.id for item in lab.procedures.list().items} == procedure_ids
        assert source_procedure.snapshot == closed
        assert lab.setup.get("initial") == setup
        assert lab.config.registry().entries == ()
        assert tuple(run.request for run in runs) == original_requests
        assert (
            tuple(run.measurements()["result"].require_values() for run in runs)
            == originals
        )
        assert (
            primary.published_analysis(first.analysis_id).publication_hash
            == first.publication_hash
        )
        assert (
            primary.published_analysis(candidate_ref.analysis_id).publication_hash
            == candidate_ref.publication_hash
        )
