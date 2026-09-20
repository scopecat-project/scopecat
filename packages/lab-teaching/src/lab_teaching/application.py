"""Minimal compute-only composition; no instrument backend or lab deployment."""

from pathlib import Path

import scopecat as sc
from scopecat.application import LabBootstrap
from scopecat.records.config import (
    ConfigProfileSnapshot,
    InstrumentRegistry,
    RoutingGraph,
    SystemSpec,
    Topology,
    snapshot_config_profile,
)

from .parameters import Drive


def bootstrap_config() -> ConfigProfileSnapshot:
    return snapshot_config_profile(
        profile_id="teaching",
        system=SystemSpec(
            id="synthetic-teaching",
            primary_entity_id="q0",
            topology=Topology(entities=[sc.EntityRef(id="q0", kind="logical_qubit")]),
            instrument_registry=InstrumentRegistry(instruments=[]),
            routing=RoutingGraph(routes=[]),
            domain_target=None,
            parameter_catalog=sc.parameter_catalog("teaching", Drive),
        ),
        parameter_snapshot=sc.parameter_snapshot(
            "teaching-inputs",
            tables={Drive: [Drive(id="q0", frequency=5.15)]},
        ),
    )


def create_bootstrap(_project_root: Path) -> LabBootstrap:
    return LabBootstrap(bootstrap_config=bootstrap_config)
