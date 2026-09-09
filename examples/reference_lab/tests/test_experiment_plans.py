"""Retained analysis → named plan → checked child, through the real HTTP APIs."""

from __future__ import annotations

import os
import time
from uuid import uuid4

import httpx2
import pytest
from pydantic import JsonValue, TypeAdapter
from scopecat.api.lab import LabClient
from scopecat.application.author_project import AuthorPreparedLaunch, AuthorProject
from scopecat.application.comparison import ComparisonHandoff
from scopecat.automation import RunOutputRef
from scopecat.automation.wire import ProcedureSubmitCommand
from scopecat.daemon.client import DaemonClient
from scopecat.kernel.quantity import Quantity
from scopecat.records.comparison import (
    ComparisonInspection,
    ComparisonPublication,
    ComparisonRequest,
    ComparisonSelection,
)
from scopecat.records.control_edit import ControlEdit
from scopecat.records.experiment_plan import ExperimentPlanSave
from scopecat.records.plan_ref import PlanAnalysisSource
from scopecat.records.run_request import AxisValuesSourceRecord


def test_retained_analysis_plan_copy_revalidate_and_child_origin() -> None:
    endpoint = os.environ["SCOPECAT_DAEMON_URL"]
    key = uuid4().hex
    with (
        AuthorProject(endpoint) as author,
        LabClient(DaemonClient(endpoint)) as lab,
        httpx2.Client(
            base_url=endpoint, timeout=60, headers={"content-type": "application/json"}
        ) as http,
    ):
        original_active = lab.config.active()

        def run_count() -> int:
            body = TypeAdapter(dict[str, JsonValue]).validate_json(
                http.get("/api/v1/runs", params={"limit": 100}).content
            )
            items = body["items"]
            assert isinstance(items, list)
            return len(items)

        def child(procedure_id: str):
            handle = lab.procedures.get(procedure_id)
            deadline = time.monotonic() + 30
            while handle.snapshot.closure is None:
                assert time.monotonic() < deadline, handle.snapshot
                time.sleep(0.05)
            assert handle.snapshot.closure is not None
            assert handle.snapshot.closure.status == "succeeded"
            output = handle.step("signal").output
            assert isinstance(output, RunOutputRef)
            return lab.get_run(output.run_id)

        frequencies = ControlEdit(
            mode="scan",
            axis=AxisValuesSourceRecord(
                values=[Quantity(value, "GHz") for value in (4.6, 4.7, 4.8, 4.9, 5.0)]
            ),
        )
        primary = child(
            author.prepare(
                "frequency-amplitude",
                control_edits={"frequency": frequencies},
                actor="source-author",
            )
            .submit(request_key=f"{key}-source1")
            .procedure_id
        )
        secondary = child(
            author.prepare(
                "frequency-amplitude",
                control_edits={
                    "frequency": frequencies,
                    "amplitude": ControlEdit(mode="fixed", value=Quantity(0.08, "V")),
                },
                actor="source-author",
            )
            .submit(request_key=f"{key}-source2")
            .procedure_id
        )
        old_request = author.run_request(primary.id)
        original_count = run_count()
        request = ComparisonRequest(
            action="inspect",
            primary_run=primary.id,
            secondary_run=secondary.id,
            model_id="signal-quadratic",
            model_version="1",
        )
        response = http.post(
            "/api/v1/run-comparison", content=request.model_dump_json()
        )
        assert response.status_code == 200, response.text
        inspection = ComparisonInspection.model_validate_json(response.text)
        request = request.model_copy(
            update={
                "action": "fit",
                "code_revision": inspection.code_revision,
                "primary": ComparisonSelection(
                    run_id=primary.id,
                    content_hash=inspection.primary.content_hash,
                    points=(0, 1, 2, 3, 4),
                ),
                "secondary": ComparisonSelection(
                    run_id=secondary.id,
                    content_hash=inspection.secondary.content_hash,
                    points=(0, 1, 2, 3, 4),
                ),
            }
        )
        response = http.post(
            "/api/v1/run-comparison", content=request.model_dump_json()
        )
        assert response.status_code == 200, response.text
        fit = ComparisonPublication.model_validate_json(response.text)
        response = http.post(
            "/api/v1/run-comparison",
            content=request.model_copy(
                update={
                    "action": "handoff",
                    "analysis_id": fit.analysis_id,
                    "analysis_hash": fit.publication_hash,
                }
            ).model_dump_json(),
        )
        assert response.status_code == 200, response.text
        handoff = ComparisonHandoff.model_validate_json(response.text)
        suggested = AuthorPreparedLaunch(
            author, handoff.request, author.preview(handoff.request)
        )
        saved = suggested.save_plan(
            "Signal follow-up",
            saved_by="alice",
            source=PlanAnalysisSource(
                run_id=handoff.source_run,
                analysis_id=handoff.source_analysis,
                publication_hash=handoff.source_hash,
            ),
        )
        frozen = saved.model_dump_json()
        assert saved.definition.sample is None  # no synthetic working point required
        assert run_count() == original_count

        # A new client proves the plan survives the original author session.
        with AuthorProject(endpoint) as reopened:
            assert reopened.experiment_plan(saved.ref).model_dump_json() == frozen
            copied = reopened.save_experiment_plan(
                ExperimentPlanSave(
                    name="Signal follow-up copy",
                    saved_by="bob",
                    copied_from=saved.ref,
                    definition=saved.definition.model_copy(
                        update={
                            "control_edits": {
                                "frequency": ControlEdit(
                                    mode="fixed", value=Quantity(4.85, "GHz")
                                )
                            }
                        }
                    ),
                )
            )
            assert copied.ref.plan_id != saved.ref.plan_id
            stale = reopened.prepare_plan(copied.ref, actor="carol")
            try:
                lab.config.set_default(
                    original_active.config.model_copy(
                        update={"id": f"plan-lab-default-{key}"}
                    ),
                    entry_id=f"plan-default-{key}",
                )
                with pytest.raises(httpx2.HTTPStatusError) as rejected:
                    stale.submit(request_key=f"{key}-stale")
                assert rejected.value.response.status_code in (409, 422)
                assert (
                    "configuration changed since preview"
                    in rejected.value.response.text
                ), rejected.value.response.text
                assert run_count() == original_count
                previewed = reopened.prepare_plan(copied.ref, actor="carol")
                assert previewed.preview.plan_ref == copied.ref
                assert (
                    previewed.preview.config_source.content_hash
                    == original_active.entry.content_hash
                )
                submitted = previewed.submit(request_key=f"{key}-target")
                target = child(submitted.procedure_id)
                assert (
                    author.get_procedure(submitted.procedure_id).plan_ref == copied.ref
                )
                assert author.run_request(target.id).request.plan_ref == copied.ref
                assert (
                    target.snapshot.config_content_hash
                    == original_active.entry.content_hash
                )
                assert copied.definition.source == saved.definition.source
                # After lease closure and another lab change, exact replay
                # still returns the original id.
                lab.config.activate_entry(
                    original_active.entry.id,
                    operation_id=f"{key}-restore",
                    expected_generation=lab.config.active().activation.generation,
                )
                assert (
                    previewed.submit(request_key=f"{key}-target").procedure_id
                    == submitted.procedure_id
                )
                admitted = author.get_procedure(submitted.procedure_id)
                forged = ProcedureSubmitCommand(
                    request_key=f"{key}-forged",
                    definition=admitted.definition,
                    intent=admitted.intent,
                    samples=admitted.samples,
                    plan_ref=copied.ref,
                )
                rejection = http.post(
                    "/api/v1/procedures", content=forged.model_dump_json()
                )
                assert rejection.status_code in (409, 422), rejection.text
                assert "checked launch request" in rejection.text
                assert run_count() == original_count + 1
            finally:
                active = lab.config.active()
                if active.entry.id != original_active.entry.id:
                    lab.config.activate_entry(
                        original_active.entry.id,
                        operation_id=f"{key}-finally",
                        expected_generation=active.activation.generation,
                    )
        lab.plans.delete(saved.ref)
        assert lab.plans.get(saved.ref).model_dump_json() == frozen
        assert author.run_request(primary.id) == old_request
        changed = (
            saved.definition.model_copy(
                update={
                    "source": saved.definition.source.model_copy(
                        update={"publication_hash": "sha256:" + "0" * 64}
                    )
                }
            )
            if saved.definition.source
            else None
        )
        assert changed is not None
        response = http.post(
            "/api/v1/experiment-plans",
            content=ExperimentPlanSave(
                name="wrong source", saved_by="alice", definition=changed
            ).model_dump_json(),
        )
        assert response.status_code == 409, response.text
