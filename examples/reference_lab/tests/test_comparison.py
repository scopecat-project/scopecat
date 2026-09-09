"""Independent retained-run analysis over real storage and the HTTP worker boundary."""

from __future__ import annotations

import os

import httpx2 as httpx
import pytest
import scopecat as sc
from scopecat.analysis.comparison import COMPARISON_REQUEST_SCHEMA
from scopecat.application.comparison import (
    ComparisonHandoff,
    ComparisonInspection,
    ComparisonPublication,
    ComparisonRequest,
    ComparisonSelection,
)
from scopecat.application.controls import ControlEdit
from scopecat.application.launch import LaunchPreview, LaunchRequest, LaunchSubmission
from scopecat.automation import RunOutputRef
from scopecat.records.analysis import MeasurementAnalysisRecordInput
from scopecat.records.run_request import AxisValuesSourceRecord

from reference_lab.application import create_application
from reference_lab.comparison import FIT_SCHEMA, NEXT_INPUT_SCHEMA, REVIEW_SCHEMA
from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.control_launch import control_launch
from reference_lab.workflows.authored.comparison import MODEL
from reference_lab.workflows.frequency_amplitude import (
    AMPLITUDE,
    FREQUENCY,
    frequency_amplitude,
)


def test_two_retained_runs_fit_candidate_rejection_and_handoff() -> None:
    url = os.environ["SCOPECAT_DAEMON_URL"]
    with (
        create_application(EXAMPLE_ROOT).connect(url) as lab,
        httpx.Client(
            base_url=url, timeout=60, headers={"content-type": "application/json"}
        ) as http,
    ):
        frequencies = [sc.Quantity(value, "GHz") for value in (4.6, 4.7, 4.8, 4.9, 5.0)]
        launch = LaunchRequest(
            action="preview",
            experiment="frequency-amplitude",
            version="1",
            control_edits={
                "frequency": ControlEdit(
                    mode="scan",
                    axis=AxisValuesSourceRecord(values=list(frequencies)),
                )
            },
        )
        preview = control_launch(lab, launch)
        assert isinstance(preview, LaunchPreview)
        admitted = control_launch(
            lab,
            launch.model_copy(
                update={
                    "action": "submit",
                    "request_key": "comparison-source",
                    "expected_request_hash": preview.request_hash,
                    "config_source": preview.config_source,
                }
            ),
        )
        assert isinstance(admitted, LaunchSubmission)
        source_procedure = lab.procedures.get(admitted.procedure_id)
        source_procedure.resume()
        closed = source_procedure.snapshot
        assert closed.closure is not None and closed.closure.status == "succeeded"
        output = source_procedure.step("signal").output
        assert isinstance(output, RunOutputRef)
        primary = lab.get_run(output.run_id)
        secondary = lab.run(
            frequency_amplitude()
            .with_axis(sc.axis(FREQUENCY.ref, frequencies))
            .with_axis(sc.axis(AMPLITUDE.ref, [sc.Quantity(0.08, "V")]))
        )
        runs = (primary, secondary)
        originals = tuple(
            run.measurements()["response"].require_values() for run in runs
        )
        original_requests = tuple(run.request for run in runs)
        active = lab.config.active()
        run_ids = {run.id for run in lab.runs().items}
        procedure_ids = {item.id for item in lab.procedures.list().items}
        base = ComparisonRequest(
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
        assert fit.primary_points == (4, 1, 2, 3)
        assert fit.center_ghz == pytest.approx(4.8, abs=0.02)
        assert {
            item.run_id
            for item in saved.inputs
            if isinstance(item, MeasurementAnalysisRecordInput)
        } == run_ids
        assert fit.primary_hash == inspected.primary.content_hash
        assert fit.secondary_hash == inspected.secondary.content_hash
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
        assert lab.config.active() == active
        assert tuple(run.request for run in runs) == original_requests
        assert (
            tuple(run.measurements()["response"].require_values() for run in runs)
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
