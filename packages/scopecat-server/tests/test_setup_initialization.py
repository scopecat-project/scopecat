"""Setup can be selected before any parameter configuration exists."""

from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.api.lab import LabClient
from scopecat.config.registry.records import ParameterConfigRegistrySource
from scopecat.config.resolution import compose_configuration
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.records.config import (
    InstrumentRegistry,
    RoutingGraph,
    Topology,
    config_content_hash,
)
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.setup import ExecutableSetupSnapshot

from scopecat_server import LocalDaemonRuntime


def test_setup_first_then_parameters_preserves_independent_authority(
    tmp_path: Path,
) -> None:
    setup = ExecutableSetupSnapshot(
        topology=Topology(),
        instrument_registry=InstrumentRegistry(instruments=[]),
        routing=RoutingGraph(),
        domain_target=None,
    )
    for first_start in (True, False):
        with (
            LocalDaemonRuntime(tmp_path) as runtime,
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

            with DaemonClient(
                "http://testserver", transport=httpx2.MockTransport(send)
            ) as client:
                lab = LabClient(client)
                if first_start:
                    assert not lab.config.registry().entries
                    revision = lab.setup.save(setup, name="initial-setup")
                    assert not lab.config.registry().entries
                    with pytest.raises(DaemonConflictError):
                        lab.config.set_parameter_default(
                            name="initial-parameters",
                            system_id="lab",
                            setup=revision,
                            catalog=ParameterCatalog(id="author-schema"),
                            parameters=ParameterSnapshot(id="reviewed-values"),
                        )
                    assert not lab.config.registry().entries
                    selected = lab.setup.activate(revision)
                    assert selected.activation.generation == 1
                    assert not lab.config.registry().entries
                    with pytest.raises(DaemonConflictError, match="exact saved setup"):
                        lab.config.set_parameter_default(
                            name="stale-reference",
                            system_id="lab",
                            setup=revision.ref.model_copy(
                                update={"content_hash": "sha256:" + "0" * 64}
                            ),
                            catalog=ParameterCatalog(id="author-schema"),
                            parameters=ParameterSnapshot(id="reviewed-values"),
                        )
                    assert not lab.config.registry().entries
                    assert lab.setup.active() == selected
                    config = compose_configuration(
                        setup,
                        id="initial-parameters",
                        system_id="lab",
                        catalog=ParameterCatalog(id="author-schema"),
                        parameters=ParameterSnapshot(id="reviewed-values"),
                    )
                    _ = lab.config.set_parameter_default(
                        name=config.id,
                        system_id=config.system.id,
                        setup=revision,
                        catalog=config.parameter_catalog,
                        parameters=config.parameter_snapshot,
                    )
                    assert lab.config.registry().entries[
                        0
                    ].source == ParameterConfigRegistrySource(setup=revision.ref)
                    assert lab.setup.active() == selected
                    assert config_content_hash(
                        lab.config.active().config
                    ) == config_content_hash(config)
                else:
                    assert lab.setup.active().revision.id == "initial-setup"
                    assert lab.setup.active().activation.generation == 1
                    assert (
                        lab.config.active().config.parameter_catalog.id
                        == "author-schema"
                    )
                    assert (
                        lab.config.active().config.parameter_snapshot.id
                        == "reviewed-values"
                    )
