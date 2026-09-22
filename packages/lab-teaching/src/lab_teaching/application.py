"""Minimal compute-only composition; no instrument backend or lab deployment."""

from pathlib import Path

import scopecat as sc
from scopecat.application import LabBootstrap
from scopecat.records.config import (
    InstrumentRegistry,
    RoutingGraph,
    Topology,
)
from scopecat.records.parameter_revision import ParameterRevisionContent
from scopecat.records.setup import ExecutableSetupSnapshot

from .parameters import Drive


def initial_setup() -> ExecutableSetupSnapshot:
    return ExecutableSetupSnapshot(
        primary_entity_id="q0",
        topology=Topology(entities=[sc.EntityRef(id="q0", kind="logical_qubit")]),
        instrument_registry=InstrumentRegistry(instruments=[]),
        routing=RoutingGraph(routes=[]),
        domain_target=None,
    )


def initial_parameters() -> ParameterRevisionContent:
    return ParameterRevisionContent(
        id="teaching",
        system_id="synthetic-teaching",
        catalog=sc.parameter_catalog("teaching", Drive),
        parameters=sc.parameter_snapshot(
            "teaching-inputs",
            tables={Drive: [Drive(id="q0", frequency=5.15)]},
        ),
    )


def create_bootstrap(_project_root: Path) -> LabBootstrap:
    return LabBootstrap(setup=initial_setup, parameter_defaults=initial_parameters)
