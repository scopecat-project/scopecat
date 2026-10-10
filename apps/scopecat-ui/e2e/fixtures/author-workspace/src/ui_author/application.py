"""Two editable objects, explicit parameter versions and distinct software setups."""

from pathlib import Path

import scopecat as sc
from scopecat.api.lab import LabClient
from scopecat.application import LabApplication, LabBootstrap
from scopecat.records.config import InstrumentRegistry, RoutingGraph, Topology
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.setup import ExecutableSetupSnapshot

from .signal import SignalParameters


def initial_setup() -> ExecutableSetupSnapshot:
    return ExecutableSetupSnapshot(
        topology=Topology(
            entities=[sc.EntityRef(id=id, kind="signal") for id in ("a", "b")]
        ),
        instrument_registry=InstrumentRegistry(instruments=[]),
        routing=RoutingGraph(),
        domain_target=None,
        scenario=SoftwareExecutionScenario(
            id="browser-a",
            label="Synthetic browser inputs",
            model_id="signal",
            model_version="1",
            capabilities=("analytic response",),
            limitations=("No physical devices or calibration",),
        ),
    )


def create_bootstrap(_root: Path) -> LabBootstrap:
    return LabBootstrap(setup=initial_setup)


def create_application(_root: Path) -> LabApplication:
    return LabApplication(author_modules=("ui_author.signal",))


def save_inputs(lab: LabClient) -> None:
    revision = lab.parameters.save(
        name="browser-values",
        catalog=sc.parameter_catalog("browser", SignalParameters),
        parameters=sc.parameter_snapshot(
            "browser-inputs",
            tables={
                SignalParameters: [
                    SignalParameters(
                        signal=sc.EntityRef(id="a", kind="signal"), center=4.8
                    ),
                    SignalParameters(
                        signal=sc.EntityRef(id="b", kind="signal"), center=5.1
                    ),
                ]
            },
        ),
    )
    lab.parameters.create_branch("browser", revision=revision)
    resolved = lab.setup.import_recipe(initial_setup(), name="browser-template")
    setup = lab.setup.definition(resolved.resolution.definition_id).definition
    lab.setup.save(setup, name="browser-bench-a")
    assert setup.scenario is not None
    lab.setup.save(
        setup.model_copy(
            update={"scenario": setup.scenario.model_copy(update={"id": "browser-b"})}
        ),
        name="browser-bench-b",
    )
    assert lab.config.registry().entries == ()
