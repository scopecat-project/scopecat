"""A device-free author workspace with one explicit parameter revision."""

from pathlib import Path

import scopecat as sc
from scopecat.api.lab import LabClient
from scopecat.application import LabApplication, LabBootstrap
from scopecat.daemon.views import ParameterResolution
from scopecat.records.config import InstrumentRegistry, RoutingGraph, Topology
from scopecat.records.setup import ExecutableSetupSnapshot

from .signal import SignalParameters


def create_bootstrap(_root: Path) -> LabBootstrap:
    return LabBootstrap(
        setup=lambda: ExecutableSetupSnapshot(
            topology=Topology(),
            instrument_registry=InstrumentRegistry(instruments=[]),
            routing=RoutingGraph(),
            domain_target=None,
        )
    )


def create_application(_root: Path) -> LabApplication:
    from .comparison import comparison_provider

    return LabApplication(
        author_modules=("ui_signal.signal",),
        comparison_provider=comparison_provider,
    )


def save_inputs(lab: LabClient) -> ParameterResolution:
    saved = lab.parameters.save(
        name="signal-inputs",
        catalog=sc.parameter_catalog("signal", SignalParameters),
        parameters=sc.parameter_snapshot(
            "signal-values",
            tables={SignalParameters: [SignalParameters(id="signal", center=4.8)]},
        ),
    )
    return lab.parameters.resolve(saved, setup=lab.setup.get("initial"))
