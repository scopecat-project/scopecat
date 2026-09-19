"""Public setup maintenance never silently rewrites scientific inputs."""

from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.api.lab import LabClient
from scopecat.config.registry.records import (
    ContextConfigRegistrySource,
    SetupRebindRegistrySource,
)
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.wire import SampleCreateCommand
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.sample import SampleRevisionDraft, SampleSelector
from scopecat_testkit.config_registry import load_config

from scopecat_server import LocalDaemonRuntime


def test_setup_rebind_creates_explicit_unverified_branch(tmp_path: Path) -> None:
    with (
        LocalDaemonRuntime(tmp_path, bootstrap_config=load_config()) as runtime,
        TestClient(runtime.app()) as transport,
    ):

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

        lab = LabClient(
            DaemonClient("http://testserver", transport=httpx2.MockTransport(send))
        )
        default = lab.config.active()
        before = lab.setup.active()
        runtime.application.samples.create(
            SampleCreateCommand(
                operation_id="chip",
                sample_id="chip",
                kind="chip",
                actor="operator",
                content=SampleRevisionDraft(display_name="Chip"),
            )
        )
        original = lab.config.save_context(
            entry_id="parked",
            base=ConfigContextRef(
                entry_id=default.entry.id, content_hash=default.entry.content_hash
            ),
            sample=SampleSelector(sample_id="chip", revision=1),
            working_point_id="parked",
            label="Parked",
        )
        old_ref = ConfigContextRef(
            entry_id=original.entry.id, content_hash=original.entry.content_hash
        )
        target = before.revision.setup.domain_target
        assert target is not None
        revised = lab.setup.save(
            before.revision.setup.model_copy(
                update={
                    "domain_target": target.model_copy(
                        update={"id": "reconfigured-target"}
                    )
                }
            ),
            name="reconfigured",
        )
        assert lab.setup.active() == before
        preview = lab.config.preview_setup_rebind(base=old_ref, setup=revised)
        assert preview.parameter_snapshot == original.config.parameter_snapshot
        assert (
            preview.system.parameter_catalog == original.config.system.parameter_catalog
        )
        assert preview.domain_target == revised.setup.domain_target
        assert lab.config.latest_context(old_ref).entry == original.entry
        assert len(lab.config.registry().entries) == 2
        rebound = lab.config.rebind_setup(
            base=old_ref, setup=revised, name="parked-reconfigured"
        )
        assert lab.config.entry(rebound.entry.id).entry == rebound.entry
        assert rebound.config == preview
        source = rebound.entry.source
        assert isinstance(source, ContextConfigRegistrySource)
        assert source.context.workspace_id == rebound.entry.id
        assert source.context.workspace_id != original.entry.id
        assert source.context.base == old_ref
        assert source.publication is None
        assert source.rebind == SetupRebindRegistrySource(
            base=old_ref, setup=revised.ref
        )
        assert (
            lab.config.rebind_setup(base=old_ref, setup=revised, name=rebound.entry.id)
            == rebound
        )
        assert lab.config.latest_context(old_ref).entry == original.entry
        selected = lab.setup.activate(
            revised,
            expected_generation=before.activation.generation,
            operation_id="select-new",
        )
        assert lab.config.active() == default
        assert lab.config.entry(original.entry.id).config == original.config
        assert selected.revision == revised
        assert (
            lab.setup.activate(
                revised,
                expected_generation=before.activation.generation,
                operation_id="select-new",
            )
            == selected
        )
        with pytest.raises(DaemonConflictError, match="setup"):
            lab.config.activate_entry(
                default.entry.id,
                operation_id="old-default",
                expected_generation=default.activation.generation,
            )
        refreshed_default = lab.config.rebind_setup(
            base=default.entry.id, setup=revised, name="default-reconfigured"
        )
        assert isinstance(refreshed_default.entry.source, SetupRebindRegistrySource)
        lab.config.activate_entry(
            refreshed_default.entry.id,
            operation_id="new-default",
            expected_generation=default.activation.generation,
        )
        assert lab.setup.active() == selected
        assert lab.config.active().entry == refreshed_default.entry
