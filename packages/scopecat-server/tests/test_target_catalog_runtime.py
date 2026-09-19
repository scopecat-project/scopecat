"""Target registration freezes local sample evidence without enabling execution."""

from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.api.lab import LabClient
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.wire import SampleCreateCommand
from scopecat.kernel.entity import EntityRef
from scopecat.records.config import Topology
from scopecat.records.sample import SampleRevisionDraft
from scopecat.records.scientific_scope import (
    MeasurementTarget,
    TargetConnection,
    TargetEntity,
    TargetMember,
)
from scopecat.records.target_catalog import (
    TargetCreateCommand,
    TargetReviseCommand,
    TargetRevisionDraft,
)
from scopecat_testkit.config_registry import load_config

from scopecat_server import BackendConflict, BackendNotFound, LocalDaemonRuntime


def _client(transport: TestClient) -> DaemonClient:
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

    return DaemonClient("http://testserver", transport=httpx2.MockTransport(send))


def _members(runtime: LocalDaemonRuntime) -> tuple[TargetMember, ...]:
    members: list[TargetMember] = []
    for name in ("A", "B"):
        receipt = runtime.application.samples.create(
            SampleCreateCommand(
                operation_id=f"create:{name}",
                sample_id=name,
                kind="chip",
                actor="operator",
                content=SampleRevisionDraft(
                    display_name=name, topology=Topology(entities=[EntityRef(id="q0")])
                ),
            )
        )
        members.append(
            TargetMember(
                id=name,
                sample_id=name,
                revision=receipt.revision.revision,
                content_hash=receipt.revision.content_hash,
            )
        )
    return tuple(members)


def test_target_python_http_revision_cas_and_restart(tmp_path: Path) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=load_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):
        lab = LabClient(_client(transport))
        catalog_id = lab.health().project_id
        a, b = _members(runtime)
        single = lab.create_target(
            TargetCreateCommand(
                catalog_id=catalog_id,
                target_id="measurement",
                draft=TargetRevisionDraft(
                    name="A", content=MeasurementTarget(members=(a,)), actor="operator"
                ),
            )
        )
        link = TargetConnection(
            id="coupler",
            kind="interconnect",
            endpoints=(
                TargetEntity(member_id="A", entity_id="q0"),
                TargetEntity(member_id="B", entity_id="q0"),
            ),
        )
        joint = lab.revise_target(
            TargetReviseCommand(
                expected=single.ref,
                draft=TargetRevisionDraft(
                    name="A + B",
                    content=MeasurementTarget(members=(a, b), connections=(link,)),
                    actor="operator",
                ),
            )
        )
        assert joint.ref.revision == 2
        assert joint.ref.content_hash != single.ref.content_hash
        assert lab.resolve_target(single.ref) == single
        assert lab.target("measurement") == joint
        assert lab.target("measurement", revision=1) == single
        with pytest.raises(DaemonConflictError, match="reload before revising"):
            lab.revise_target(TargetReviseCommand(expected=single.ref, draft=single))
        foreign = single.ref.model_copy(update={"catalog_id": "local:elsewhere"})
        with pytest.raises(DaemonConflictError, match="another catalog"):
            lab.resolve_target(foreign)
        with pytest.raises(DaemonConflictError, match="another catalog"):
            lab.revise_target(TargetReviseCommand(expected=foreign, draft=single))
        renamed = lab.revise_target(
            TargetReviseCommand(
                expected=joint.ref,
                draft=TargetRevisionDraft(
                    name="new label", content=joint.content, actor="operator"
                ),
            )
        )
        assert renamed.ref.content_hash == joint.ref.content_hash
        other = lab.create_target(
            TargetCreateCommand(
                catalog_id=catalog_id,
                target_id="other",
                draft=TargetRevisionDraft(
                    name="other", content=single.content, actor="operator"
                ),
            )
        )
        page = lab.targets(limit=1)
        assert page.items == (other,)
        assert lab.targets(limit=1, before=page.next_cursor).items == (renamed,)
        assert (
            runtime.application.runs.list_runs(limit=100, before=None, state=None).items
            == ()
        )
    with LocalDaemonRuntime(tmp_path) as restarted:
        assert restarted.application.targets.catalog_id == catalog_id
        assert restarted.application.targets.resolve(single.ref) == single
        assert restarted.application.targets.get("measurement") == renamed


def test_target_rejects_missing_foreign_or_inconsistent_members_atomically(
    tmp_path: Path,
) -> None:
    with LocalDaemonRuntime(tmp_path, bootstrap_config=load_config()) as runtime:
        store = runtime.application.targets
        a, b = _members(runtime)
        draft = TargetRevisionDraft(
            name="invalid", content=MeasurementTarget(members=(a,)), actor="operator"
        )
        command = TargetCreateCommand(
            catalog_id=store.catalog_id, target_id="checked", draft=draft
        )
        with pytest.raises(BackendConflict, match="another catalog"):
            store.create(command.model_copy(update={"catalog_id": "local:foreign"}))
        for member, error in (
            (a.model_copy(update={"revision": 100}), BackendNotFound),
            (
                a.model_copy(update={"content_hash": "sha256:" + "0" * 64}),
                BackendConflict,
            ),
        ):
            with pytest.raises(error):
                store.create(
                    command.model_copy(
                        update={
                            "draft": draft.model_copy(
                                update={"content": MeasurementTarget(members=(member,))}
                            )
                        }
                    )
                )
        link = TargetConnection(
            id="link",
            kind="interconnect",
            endpoints=(
                TargetEntity(member_id="A", entity_id="unknown"),
                TargetEntity(member_id="B", entity_id="q0"),
            ),
        )
        with pytest.raises(BackendConflict, match="unknown entity"):
            store.create(
                command.model_copy(
                    update={
                        "draft": draft.model_copy(
                            update={
                                "content": MeasurementTarget(
                                    members=(a, b), connections=(link,)
                                )
                            }
                        )
                    }
                )
            )
        assert store.list().items == ()
        valid = store.create(command)
        assert valid.ref.revision == 1
        with pytest.raises(BackendConflict, match="does not match"):
            store.resolve(
                valid.ref.model_copy(update={"content_hash": "sha256:" + "0" * 64})
            )
