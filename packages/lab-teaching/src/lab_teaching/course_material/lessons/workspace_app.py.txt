"""Minimal compute-only composition; no instrument backend or lab deployment."""

from pathlib import Path

import scopecat as sc
from scopecat.application import LabBootstrap
from scopecat.records.config import (
    InstrumentRegistry,
    RoutingGraph,
    Topology,
)
from scopecat.records.setup import ExecutableSetupSnapshot


def initial_setup() -> ExecutableSetupSnapshot:
    return ExecutableSetupSnapshot(
        primary_entity_id="q0",
        topology=Topology(entities=[sc.EntityRef(id="q0", kind="logical_qubit")]),
        instrument_registry=InstrumentRegistry(instruments=[]),
        routing=RoutingGraph(routes=[]),
        domain_target=None,
    )


def create_bootstrap(_project_root: Path) -> LabBootstrap:
    return LabBootstrap(setup=initial_setup)
