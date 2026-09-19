from __future__ import annotations

from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.api.lab import LabClient
from scopecat.config.documents import load_config_snapshot_document
from scopecat.control.models import RunPlanSummary, RunResourceRequirement
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.wire import (
    RunSubmission,
    SampleCreateCommand,
    SampleReviseCommand,
)
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import (
    SampleRelation,
    SampleRevisionDraft,
    SampleSelector,
)

from scopecat_server import BackendNotFound, LocalDaemonRuntime
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane

_FIXTURE = (
    Path(__file__).parents[3]
    / "fixtures"
    / "core"
    / "simple_scan"
    / "config-snapshot.json"
)


def _config() -> ConfigProfileSnapshot:
    return load_config_snapshot_document(_FIXTURE)


def _submission(sample_id: str) -> RunSubmission:
    return RunSubmission(
        submission_id="sample-run-submission",
        config=_config(),
        request=RunRequest(
            experiment_id="sample-scan",
            samples=(SampleSelector(sample_id=sample_id, context_id="cooldown-1"),),
        ),
        plan=RunPlanSummary(
            experiment_id="sample-scan",
            experiment_kind="sample-scan",
            point_plan_fingerprint="a" * 64,
            measurement_contract_fingerprint="b" * 64,
            point_count=1,
            initial_point_count=1,
            point_limit=1,
            run_resource_requirements=(
                RunResourceRequirement(id="source-0", kind="instrument"),
            ),
        ),
    )


def _daemon_client(transport: TestClient) -> DaemonClient:
    def send(request: httpx2.Request) -> httpx2.Response:
        response = transport.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx2.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    return DaemonClient(
        "http://testserver",
        transport=httpx2.MockTransport(send),
    )


def test_sample_revision_and_run_binding_survive_restart(tmp_path: Path) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        parent_response = transport.post(
            "/api/v1/samples",
            json=SampleCreateCommand(
                operation_id="create:wafer-1",
                sample_id="wafer-1",
                kind="wafer",
                actor="operator",
                content=SampleRevisionDraft(
                    display_name="Wafer 1",
                    tags=("lot-a",),
                ),
            ).model_dump(mode="json"),
        )
        assert parent_response.status_code == 201

        create_response = transport.post(
            "/api/v1/samples",
            json=SampleCreateCommand(
                operation_id="create:die-1",
                sample_id="die-1",
                kind="die",
                actor="operator",
                content=SampleRevisionDraft(
                    display_name="Die 1",
                    relations=(SampleRelation(kind="cut_from", sample_id="wafer-1"),),
                ),
            ).model_dump(mode="json"),
        )
        assert create_response.status_code == 201

        revise_response = transport.post(
            "/api/v1/samples/die-1/revisions",
            json=SampleReviseCommand(
                operation_id="revise:die-1:2",
                expected_revision=1,
                actor="operator",
                note="mounted for cooldown",
                content=SampleRevisionDraft(
                    display_name="Die 1 / device A",
                    status="mounted",
                    tags=("lot-a", "device-a"),
                    relations=(SampleRelation(kind="cut_from", sample_id="wafer-1"),),
                ),
            ).model_dump(mode="json"),
        )
        assert revise_response.status_code == 200
        assert revise_response.json()["revision"]["revision"] == 2

        admission = runtime.application.submit_run(_submission("die-1"))
        binding = admission.snapshot.samples[0]
        assert binding.sample_id == "die-1"
        assert binding.revision == 2
        assert binding.display_name == "Die 1 / device A"
        assert binding.context_id == "cooldown-1"

        page_response = transport.get("/api/v1/samples")
        assert page_response.status_code == 200
        die_summary = next(
            item
            for item in page_response.json()["items"]
            if item["record"]["id"] == "die-1"
        )
        assert die_summary["run_count"] == 1

        detail_response = transport.get("/api/v1/samples/die-1")
        assert detail_response.status_code == 200
        assert "revisions" not in detail_response.json()

        first_revision_page = transport.get(
            "/api/v1/samples/die-1/revisions",
            params={"limit": 1},
        )
        assert first_revision_page.status_code == 200
        assert [
            revision["revision"] for revision in first_revision_page.json()["items"]
        ] == [2]
        second_revision_page = transport.get(
            "/api/v1/samples/die-1/revisions",
            params={"limit": 1, "before": first_revision_page.json()["next_cursor"]},
        )
        assert [
            revision["revision"] for revision in second_revision_page.json()["items"]
        ] == [1]
        exact_revision = transport.get("/api/v1/samples/die-1/revisions/1")
        assert exact_revision.status_code == 200
        assert exact_revision.json()["content"]["display_name"] == "Die 1"

        events = transport.get("/api/v1/events", params={"limit": 100}).json()["items"]
        sample_events = [
            event
            for event in events
            if event["kind"] in {"sample_created", "sample_revision_activated"}
        ]
        assert [event["kind"] for event in sample_events] == [
            "sample_created",
            "sample_created",
            "sample_revision_activated",
        ]
        assert sample_events[-1]["payload"] == {
            "operation_id": "revise:die-1:2",
            "sample_id": "die-1",
            "revision": 2,
            "content_hash": revise_response.json()["revision"]["content_hash"],
            "status": "mounted",
        }

        sample_runs_response = transport.get(
            "/api/v1/runs",
            params={"sample_id": "die-1"},
        )
        assert sample_runs_response.status_code == 200
        assert [
            item["snapshot"]["run_id"] for item in sample_runs_response.json()["items"]
        ] == [admission.run_id]
        assert (
            transport.get(
                "/api/v1/runs",
                params={"sample_id": "wafer-1"},
            ).json()["items"]
            == []
        )

        run_id = admission.run_id

    with LocalDaemonRuntime(tmp_path) as restarted:
        sample = restarted.application.samples.get("die-1")
        snapshot = restarted.application.runs.get_run(run_id).snapshot

    assert sample.record.active_revision == 2
    assert sample.run_count == 1
    assert snapshot.samples == (binding,)


def test_sample_ids_are_url_safe_stable_identifiers(tmp_path: Path) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        response = transport.post(
            "/api/v1/samples",
            json={
                "operation_id": "invalid-sample-id",
                "sample_id": "lot/chip-1",
                "kind": "chip",
                "actor": "operator",
                "content": {"display_name": "Chip 1"},
            },
        )

    assert response.status_code == 422


def test_lab_sample_facade_and_run_filter_use_stable_handles(tmp_path: Path) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        lab = LabClient(_daemon_client(transport), operator="notebook-operator")
        sample = lab.samples.create(
            "chip-1",
            kind="chip",
            content=SampleRevisionDraft(display_name="Chip 1"),
        )
        sample.revise(
            SampleRevisionDraft(display_name="Chip 1 mounted", status="mounted"),
            note="ready for measurement",
        )
        admission = runtime.application.submit_run(_submission(sample.id))

        page = lab.runs(sample=sample)

        assert tuple(run.id for run in page.items) == (admission.run_id,)
        assert sample.view.record.active_revision == 2
        assert tuple(item.revision for item in sample.revisions().items) == (2, 1)
        assert sample.revision(1).content.display_name == "Chip 1"


def test_sample_mutation_rolls_back_when_its_event_cannot_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime:

        def reject_event(*_args: object) -> None:
            raise RuntimeError("event append failed")

        monkeypatch.setattr(
            SQLiteControlPlane,
            "append_event_in_transaction",
            reject_event,
        )
        with pytest.raises(RuntimeError, match="event append failed"):
            runtime.application.samples.create(
                SampleCreateCommand(
                    operation_id="atomic-sample-create",
                    sample_id="atomic-chip",
                    kind="chip",
                    actor="operator",
                    content=SampleRevisionDraft(display_name="Atomic chip"),
                )
            )
        with pytest.raises(BackendNotFound, match="unknown sample"):
            runtime.application.samples.get("atomic-chip")


def test_research_history_associations_and_bench_survive_restart(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime, timedelta

    from scopecat.records.research_project import ResearchProjectEdit, RunHistoryFilter

    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        for sample in ("a", "b"):
            runtime.application.samples.create(
                SampleCreateCommand(
                    operation_id=f"create-{sample}",
                    sample_id=sample,
                    kind="chip",
                    actor="operator",
                    content=SampleRevisionDraft(display_name=sample),
                )
            )
        first = runtime.application.submit_run(_submission("a"))
        second = runtime.application.submit_run(
            _submission("b").model_copy(update={"submission_id": "second"})
        )
        original = client.get_run(first.run_id)
        lab = LabClient(client, operator="research-test")
        retained = (
            lab.get_run(first.run_id)
            .analysis("Original")
            .result()
            .fact("value", 1)
            .save()
        )
        assert original.deployment_id == runtime.application.deployment_id
        alpha = client.save_research_project("alpha", ResearchProjectEdit(name="Alpha"))
        client.save_research_project("beta", ResearchProjectEdit(name="Beta"))
        for project in ("alpha", "beta"):
            client.associate_research_member(project, "samples", "a")
            client.associate_research_member(project, "runs", first.run_id)
        client.associate_research_member("beta", "samples", "b")
        client.associate_research_member("beta", "runs", second.run_id)
        client.associate_research_member("beta", "runs", second.run_id)
        page = client.research_members("beta", "runs", limit=1)
        assert page.next_cursor is not None
        other = client.research_members("beta", "runs", limit=1, after=page.next_cursor)
        assert {*page.ids, *other.ids} == {first.run_id, second.run_id}
        filtered = client.list_runs(
            sample_id="a",
            history=RunHistoryFilter(
                research_project="beta",
                working_point="cooldown-1",
                deployment_id=original.deployment_id,
                created_after=datetime.now(UTC) - timedelta(days=1),
                created_before=datetime.now(UTC) + timedelta(days=1),
            ),
        )
        assert [run.run_id for run in filtered.items] == [first.run_id]
        assert not client.list_runs(
            history=RunHistoryFilter(deployment_id="another-bench")
        ).items
        renamed = client.save_research_project(
            "alpha",
            ResearchProjectEdit(name="Renamed", expected_revision=alpha.revision),
        )
        assert renamed.id == alpha.id and renamed.revision == 2
        with pytest.raises(DaemonConflictError) as stale:
            client.save_research_project(
                "alpha",
                ResearchProjectEdit(name="Stale", expected_revision=alpha.revision),
            )
        assert "reload" in str(stale.value)
        client.associate_research_member("alpha", "runs", first.run_id, present=False)
        assert not client.list_runs(
            history=RunHistoryFilter(research_project="alpha")
        ).items
        assert client.get_run(first.run_id) == original
        assert (
            lab.get_run(first.run_id)
            .published_analysis(retained.id)
            .fact("value")
            .value
            == 1
        )
    with (
        LocalDaemonRuntime(tmp_path) as restarted,
        TestClient(restarted.app()) as transport,
        _daemon_client(transport) as client,
    ):
        lab = LabClient(client, operator="research-test")
        later = (
            lab.get_run(first.run_id).analysis("Later").result().fact("value", 2).save()
        )
        assert later.id != retained.id
        assert (
            lab.get_run(first.run_id)
            .published_analysis(retained.id)
            .fact("value")
            .value
            == 1
        )
        assert client.get_run(first.run_id).snapshot == original.snapshot
        assert client.get_run(first.run_id).deployment_id == original.deployment_id
        assert set(client.research_members("beta", "samples").ids) == {"a", "b"}
        assert (
            len(
                client.list_runs(
                    history=RunHistoryFilter(research_project="beta")
                ).items
            )
            == 2
        )
        assert client.research_projects(limit=1).next_cursor is not None
        assert {item.name for item in client.research_projects().items} == {
            "Renamed",
            "Beta",
        }


def test_collection_addresses_are_atomic_scoped_and_retained(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from scopecat.daemon.client import DaemonNotFoundError
    from scopecat.records.record_collection import RecordCollectionEdit
    from scopecat.records.research_project import RunHistoryFilter

    def submission(key: str, collection: str | None) -> RunSubmission:
        base = _submission("unused")
        return base.model_copy(
            update={
                "submission_id": key,
                "request": base.request.model_copy(
                    update={"samples": (), "record_collection": collection}
                ),
            }
        )

    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        default = client.record_collections().items[0]
        assert default.is_default
        alpha = client.create_record_collection("Chip A / cooldown September")
        beta = client.save_record_collection("beta", RecordCollectionEdit(name="Beta"))
        first = client.submit_run(submission("first", alpha.id))
        assert client.submit_run(submission("first", alpha.id)) == first
        address = client.resolve_run_number(alpha.id, 1)
        assert address.run_id == first.run_id
        assert client.get_run(first.run_id).address == address
        with pytest.raises(DaemonConflictError):
            client.submit_run(submission("first", beta.id))
        with pytest.raises(DaemonNotFoundError):
            client.submit_run(submission("missing", "unknown"))
        # A failed transaction must not consume this submission or a number.
        recovered = client.submit_run(submission("missing", beta.id))
        assert client.resolve_run_number(beta.id, 1).run_id == recovered.run_id
        legacy = client.submit_run(submission("legacy", None))
        legacy_detail = client.get_run(legacy.run_id)
        assert legacy_detail.address is not None
        assert legacy_detail.address.collection_id == default.id
        assert legacy_detail.address.number == legacy_detail.control.sequence
        with ThreadPoolExecutor(max_workers=4) as pool:
            admissions = tuple(
                pool.map(
                    runtime.application.submit_run,
                    (submission(f"parallel-{i}", alpha.id) for i in range(4)),
                )
            )
        numbers = {
            client.resolve_run_number(alpha.id, number).run_id for number in range(2, 6)
        }
        assert numbers == {item.run_id for item in admissions}
        renamed = client.save_record_collection(
            alpha.id, RecordCollectionEdit(name="Renamed", expected_revision=1)
        )
        assert renamed.revision == 2 and renamed.id == alpha.id
        with pytest.raises(DaemonConflictError):
            client.save_record_collection(alpha.id, RecordCollectionEdit(name="Stale"))
        assert client.resolve_run_number(alpha.id, 1) == address
        page = client.list_runs(
            limit=2, history=RunHistoryFilter(record_collection=alpha.id)
        )
        assert len(page.items) == 2 and page.next_cursor is not None
        rest = client.list_runs(
            before=page.next_cursor,
            history=RunHistoryFilter(record_collection=alpha.id),
        )
        assert {item.run_id for item in (*page.items, *rest.items)} == numbers | {
            first.run_id
        }
        assert all(item.address is not None for item in page.items)
        collections = client.record_collections(limit=1)
        assert collections.next_cursor is not None
        assert len(client.record_collections(before=collections.next_cursor).items) == 2
        assert (
            transport.put(
                "/api/v1/record-collections/invalid space", json={"name": "Bad"}
            ).status_code
            == 422
        )
        assert (
            transport.get(f"/api/v1/record-collections/{alpha.id}/runs/0").status_code
            == 422
        )
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        assert client.record_collection(alpha.id).name == "Renamed"
        assert client.resolve_run_number(alpha.id, 1) == address
        next_run = client.submit_run(submission("after-restart", alpha.id))
        assert client.resolve_run_number(alpha.id, 6).run_id == next_run.run_id
        assert client.submit_run(submission("first", alpha.id)) == first


def test_batch_catalog_and_bound_runs_survive_restart(tmp_path: Path) -> None:
    from scopecat.daemon.client import DaemonNotFoundError
    from scopecat.records.experimental_batch import ExperimentalBatchEdit
    from scopecat.records.research_project import RunHistoryFilter

    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=_config()) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        runtime.application.samples.create(
            SampleCreateCommand(
                operation_id="batch-chip",
                sample_id="batch-chip",
                kind="chip",
                actor="operator",
                content=SampleRevisionDraft(display_name="Batch chip"),
            )
        )
        batch = client.save_experimental_batch(
            "cooldown-a", ExperimentalBatchEdit(name="First")
        )
        other = client.create_experimental_batch("Second")
        page = client.experimental_batches(limit=1)
        assert page.next_cursor is not None
        assert client.experimental_batches(before=page.next_cursor).items == (batch,)
        base = _submission("batch-chip")
        requested = base.model_copy(
            update={
                "request": base.request.model_copy(
                    update={
                        "samples": (
                            SampleSelector(sample_id="batch-chip", batch_id="missing"),
                        ),
                    }
                )
            }
        )
        with pytest.raises(DaemonNotFoundError):
            client.submit_run(requested)
        requested = requested.model_copy(
            update={
                "request": requested.request.model_copy(
                    update={
                        "samples": (
                            SampleSelector(sample_id="batch-chip", batch_id=batch.id),
                        ),
                    }
                )
            }
        )
        run = client.submit_run(requested)
        assert client.submit_run(requested) == run
        assert run.snapshot.samples[0].batch_id == batch.id
        with pytest.raises(DaemonConflictError):
            client.submit_run(
                requested.model_copy(
                    update={
                        "request": requested.request.model_copy(
                            update={
                                "samples": (
                                    SampleSelector(
                                        sample_id="batch-chip", batch_id=other.id
                                    ),
                                ),
                            }
                        )
                    }
                )
            )
        client.save_experimental_batch(
            batch.id, ExperimentalBatchEdit(name="Renamed", expected_revision=1)
        )
        with pytest.raises(DaemonConflictError):
            client.save_experimental_batch(
                batch.id, ExperimentalBatchEdit(name="Stale")
            )
    with (
        LocalDaemonRuntime(tmp_path) as runtime,
        TestClient(runtime.app()) as transport,
        _daemon_client(transport) as client,
    ):
        assert client.experimental_batch(batch.id).name == "Renamed"
        assert client.get_run(run.run_id).snapshot == run.snapshot
        assert [
            item.run_id
            for item in client.list_runs(
                history=RunHistoryFilter(batch_id=batch.id)
            ).items
        ] == [run.run_id]
        assert not client.list_runs(history=RunHistoryFilter(batch_id=other.id)).items
