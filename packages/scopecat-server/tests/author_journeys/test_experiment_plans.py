"""Retained analysis → named plan → checked child, through the real HTTP APIs."""

from __future__ import annotations

import time
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue, TypeAdapter
from scopecat.analysis.repository import AnalysisPublicationPage
from scopecat.api.lab import LabClient
from scopecat.application.author_project import AuthorPreparedLaunch, AuthorProject
from scopecat.application.comparison import ComparisonHandoff
from scopecat.automation import RunOutputRef
from scopecat.automation.wire import ProcedureSubmitCommand
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import DecisionRunPage, RunAnalysisView
from scopecat.kernel.quantity import Quantity
from scopecat.records.comparison import (
    ComparisonInspection,
    ComparisonPublication,
    ComparisonRequest,
    ComparisonSelection,
)
from scopecat.records.control_edit import ControlEdit
from scopecat.records.experiment_plan import ExperimentPlanSave
from scopecat.records.parameter_revision import ParameterRevision
from scopecat.records.plan_ref import PlanAnalysisSource
from scopecat.records.run import ParameterRunConfigSource
from scopecat.records.run_request import AxisValuesSourceRecord
from scopecat_testkit.authoring import source_workspace_id

from scopecat_server.runtime import LocalDaemonRuntime


def test_retained_analysis_plan_copy_revalidate_and_child_origin(
    independent_lab_daemon: str,
    independent_parameters: ParameterRevision,
    tmp_path: Path,
) -> None:
    endpoint = independent_lab_daemon
    key = uuid4().hex
    with (
        AuthorProject(endpoint, workspace_id=source_workspace_id(endpoint)) as author,
        LabClient(DaemonClient(endpoint)) as lab,
        httpx2.Client(
            base_url=endpoint, timeout=60, headers={"content-type": "application/json"}
        ) as http,
    ):
        setup = lab.setup.get("initial")
        branch = lab.parameters.create_branch(
            f"plan-{key}", revision=independent_parameters
        )
        author.use(parameter_branch=branch.name, setup=setup.ref)
        original_hash = lab.parameters.resolve(
            independent_parameters, setup=setup.ref
        ).config_source.content_hash

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
            output = handle.step("experiment").output
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
                "ui_signal.signal",
                control_edits={"frequency": frequencies},
                actor="source-author",
            )
            .submit(request_key=f"{key}-source1")
            .procedure_id
        )
        secondary = child(
            author.prepare(
                "ui_signal.signal",
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
            workspace_id=author.workspace_id,
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
        decision_source = PlanAnalysisSource(
            run_id=handoff.source_run,
            analysis_id=handoff.source_analysis,
            publication_hash=handoff.source_hash,
        )
        # A direct Start has no saved plan. Edited inputs and its decision source
        # are retained independently, under the same idempotent admission.
        direct = author.prepare(
            handoff.request.experiment,
            inputs=handoff.request.inputs,
            selection=handoff.request.selection,
            code_revision=handoff.request.code_revision,
            fixed={"frequency": Quantity(4.87, "GHz")},
            decision_source=decision_source,
        )
        direct_request = direct.request
        direct_submission = direct.submit(request_key=f"{key}-direct")
        direct_run = child(direct_submission.procedure_id)
        admitted_direct = author.get_procedure(direct_submission.procedure_id)
        assert admitted_direct.decision_source == decision_source
        with (
            LocalDaemonRuntime(tmp_path / "foreign-space") as other,
            TestClient(other.app()) as foreign,
        ):
            foreign_command = ProcedureSubmitCommand(
                request_key=f"{key}-foreign",
                definition=admitted_direct.definition,
                intent=admitted_direct.intent,
                decision_source=decision_source,
            )
            wrong_space = foreign.post(
                "/api/v1/procedures", json=foreign_command.model_dump(mode="json")
            )
            assert wrong_space.status_code == 409, wrong_space.text
            assert "decision source" in wrong_space.text

        retained_request = author.run_request(direct_run.id).request
        assert retained_request.decision_source == decision_source
        assert retained_request.plan_ref is None
        assert (
            direct.submit(request_key=f"{key}-direct").procedure_id
            == direct_submission.procedure_id
        )
        related_url = (
            f"/api/v1/runs/{decision_source.run_id}/analyses/"
            f"{decision_source.analysis_id}/decision-runs"
        )
        found = http.get(
            related_url,
            params={"publication_hash": decision_source.publication_hash, "limit": 1},
        )
        assert found.status_code == 200, found.text
        assert DecisionRunPage.model_validate_json(found.text).run_ids == (
            direct_run.id,
        )
        assert (
            http.get(
                related_url, params={"publication_hash": "sha256:" + "0" * 64}
            ).status_code
            == 409
        )
        # Neither a wrong hash nor an absent foreign-space source may admit work.
        for invalid in (
            decision_source.model_copy(
                update={"publication_hash": "sha256:" + "0" * 64}
            ),
            decision_source.model_copy(update={"run_id": str(uuid4())}),
        ):
            request_with_invalid_source = direct_request.model_copy(
                update={"decision_source": invalid}
            )
            rejected = AuthorPreparedLaunch(
                author,
                request_with_invalid_source,
                author.preview(request_with_invalid_source),
            )
            with pytest.raises(httpx2.HTTPStatusError) as error:
                rejected.submit(
                    request_key=f"{key}-{invalid.run_id}-{invalid.publication_hash}"
                )
            assert error.value.response.status_code in (409, 422)
        assert run_count() == original_count + 1
        original_count += 1
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
        assert (
            not saved.definition.scientific_binding.samples
        )  # no synthetic working point required
        assert run_count() == original_count

        # A new client proves the plan survives the original author session.
        with AuthorProject(
            endpoint, workspace_id=source_workspace_id(endpoint)
        ) as reopened:
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
            previewed = reopened.prepare_plan(copied.ref, actor="carol")
            lab.parameters.checkout(branch.name).save(
                catalog=independent_parameters.catalog,
                parameters=independent_parameters.parameters,
                note="branch changes after the plan preview",
            )
            assert previewed.preview.plan_ref == copied.ref
            assert (
                previewed.preview.reviewed.config_source.content_hash == original_hash
            )
            submitted = previewed.submit(request_key=f"{key}-target")
            target = child(submitted.procedure_id)
            assert isinstance(target.snapshot.config_source, ParameterRunConfigSource)
            assert (
                target.snapshot.config_source.parameters == independent_parameters.ref
            )
            assert target.snapshot.config_source.setup == setup.ref
            assert author.get_procedure(submitted.procedure_id).plan_ref == copied.ref
            assert author.run_request(target.id).request.plan_ref == copied.ref
            assert (
                author.run_request(target.id).request.decision_source == decision_source
            )
            assert (
                author.get_procedure(submitted.procedure_id).decision_source
                == decision_source
            )
            assert target.snapshot.config_content_hash == original_hash
            assert copied.definition.source == saved.definition.source
            first_page = DecisionRunPage.model_validate_json(
                http.get(
                    related_url,
                    params={
                        "publication_hash": decision_source.publication_hash,
                        "limit": 1,
                    },
                ).text
            )
            assert first_page.run_ids == (target.id,)
            second_page = DecisionRunPage.model_validate_json(
                http.get(
                    related_url,
                    params={
                        "publication_hash": decision_source.publication_hash,
                        "limit": 1,
                        "before": first_page.next_cursor,
                    },
                ).text
            )
            assert second_page.run_ids == (direct_run.id,)
            assert second_page.next_cursor is None
            original_publication = RunAnalysisView.model_validate_json(
                http.get(
                    f"/api/v1/runs/{decision_source.run_id}/analyses/{decision_source.analysis_id}"
                ).text
            ).analysis
            assert original_publication.key is not None
            newer = (
                primary.analysis("Newer conclusion", key=original_publication.key)
                .result()
                .fact("note", "a later revision")
                .save()
            )
            assert newer.id != decision_source.analysis_id
            assert (
                author.run_request(target.id).request.decision_source == decision_source
            )
            assert DecisionRunPage.model_validate_json(
                http.get(
                    related_url,
                    params={"publication_hash": decision_source.publication_hash},
                ).text
            ).run_ids == (target.id, direct_run.id)
            assert (
                DecisionRunPage.model_validate_json(
                    http.get(
                        f"/api/v1/runs/{primary.id}/analyses/{newer.id}/decision-runs",
                        params={"publication_hash": newer.publication_hash},
                    ).text
                ).run_ids
                == ()
            )

            assert (
                AnalysisPublicationPage.model_validate_json(
                    http.get(
                        "/api/v1/analysis-publications",
                        params={"input_run_id": direct_run.id},
                    ).text
                ).items
                == ()
            )
            # After lease closure and another lab change, exact replay
            # still returns the original id.
            lab.parameters.checkout(branch.name).save(
                catalog=independent_parameters.catalog,
                parameters=independent_parameters.parameters,
                note="second independent branch edit",
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
            assert lab.setup.get("initial") == setup
            assert lab.config.registry().entries == ()
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


def test_plan_freezes_sample_parameters_and_setup_without_activation(
    independent_lab_daemon: str,
    independent_parameters: ParameterRevision,
) -> None:
    from scopecat.records.sample import SampleRevisionDraft
    from scopecat.records.scientific_selection import ParameterConfiguration

    key = uuid4().hex
    with (
        AuthorProject(
            independent_lab_daemon,
            workspace_id=source_workspace_id(independent_lab_daemon),
        ) as author,
        LabClient(DaemonClient(independent_lab_daemon)) as lab,
    ):
        setup = lab.setup.get("initial")
        sample = lab.samples.create(
            f"plan-sample-{key}",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Plan sample revision one"),
        )
        branch = lab.parameters.create_branch(
            f"sample-{key}", revision=independent_parameters
        )
        author.use(sample=sample.id, parameter_branch=branch.name, setup=setup.ref)
        saved = author.prepare("ui_signal.signal").save_plan(
            "Exact sample and parameters",
            saved_by="alice",
        )
        lab.samples.revise(
            sample.id,
            SampleRevisionDraft(display_name="Plan sample revision two"),
            expected_revision=1,
        )
        lab.parameters.checkout(branch.name).save(
            catalog=independent_parameters.catalog,
            parameters=independent_parameters.parameters,
        )
        reopened = author.prepare_plan(saved.ref, actor="bob")
        assert reopened.preview.reviewed.binding == saved.definition.scientific_binding
        assert reopened.preview.reviewed.binding.samples[0].revision == 1
        selection = saved.definition.selection.configuration
        assert isinstance(selection, ParameterConfiguration)
        assert (
            selection.ref == independent_parameters.ref and selection.setup == setup.ref
        )
        assert lab.setup.get("initial") == setup
        assert lab.config.registry().entries == ()


def test_authored_plan_freezes_default_structural_input_and_explicit_copy(
    independent_lab_daemon: str, independent_parameters: ParameterRevision
) -> None:
    endpoint = independent_lab_daemon
    key = uuid4().hex
    with (
        AuthorProject(endpoint, workspace_id=source_workspace_id(endpoint)) as author,
        LabClient(DaemonClient(endpoint)) as lab,
    ):
        author.use(
            parameters=independent_parameters.ref, setup=lab.setup.get("initial").ref
        )
        collection = author.create_record_collection("Plan runs")
        prepared = author.prepare("signal", actor="alice")
        assert prepared.request.inputs == {"polarity": "positive"}
        saved = prepared.save_plan("Positive signal", saved_by="alice")
        assert saved.definition.inputs == {"polarity": "positive"}
        assert saved.definition.code_revision == prepared.preview.code_revision
        copied = lab.plans.save(
            ExperimentPlanSave(
                name="Negative signal",
                saved_by="bob",
                copied_from=saved.ref,
                definition=saved.definition.model_copy(
                    update={"inputs": {"polarity": "negative"}}
                ),
            )
        )
        for plan, polarity in ((saved, "positive"), (copied, "negative")):
            reopened = author.prepare_plan(
                plan.ref, actor="carol", record_collection=collection.id
            )
            assert reopened.request.inputs == {"polarity": polarity}
            admitted = reopened.submit(request_key=f"{key}-{polarity}")
            handle = lab.procedures.get(admitted.procedure_id)
            deadline = time.monotonic() + 30
            while handle.snapshot.closure is None:
                assert time.monotonic() < deadline, handle.snapshot
                time.sleep(0.05)
            assert handle.snapshot.closure.status == "succeeded"
            assert handle.snapshot.plan_ref == plan.ref
            assert handle.snapshot.intent["inputs"] == {"polarity": polarity}
            output = handle.step("experiment").output
            assert isinstance(output, RunOutputRef)
            request = author.run_request(output.run_id).request
            assert request.plan_ref == plan.ref
            assert request.record_collection == collection.id
            address = author.get_run(output.run_id).address
            assert address is not None and address.collection_id == collection.id
        assert lab.plans.get(saved.ref).definition.inputs == {"polarity": "positive"}
