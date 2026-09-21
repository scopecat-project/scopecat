from __future__ import annotations

from scopecat.authoring import (
    ExperimentInvocation,
    QuantityType,
    ScalarType,
    axis,
    parameter,
)
from scopecat.kernel.quantity import Quantity
from scopecat.records.config import ConfigProfileSnapshot

from scopecat_testkit.authoring import (
    DRIVE_FREQUENCY_POINT,
    bind_invocation,
    simple_experiment,
)
from scopecat_testkit.bound_program import ProgramFixture
from scopecat_testkit.config_fixtures import simple_scan_config


def load_config() -> ConfigProfileSnapshot:
    return simple_scan_config()


def load_experiment() -> ProgramFixture:
    """Compile the simple-scan DSL fixture into a transient typed program."""

    bound = bind_invocation(
        load_invocation(),
        config_profile=load_config(),
    )
    return ProgramFixture(logical=bound.program, bindings=bound.bindings)


def load_invocation() -> ExperimentInvocation:
    return (
        simple_experiment(id="test.workflow_scan")
        .bind(subject="q0")
        .grid(
            axis(
                DRIVE_FREQUENCY_POINT,
                center=parameter(
                    "drive_frequency",
                    ScalarType(QuantityType()),
                ),
                span=Quantity(value=200.0, unit="MHz"),
                points=3,
            )
        )
    )


def config_with_instrument_id(instrument_id: str) -> ConfigProfileSnapshot:
    config = load_config()
    instrument = config.instrument_registry.instruments[0].model_copy(
        update={"id": instrument_id}
    )
    system = config.system.model_copy(
        update={
            "instrument_registry": config.instrument_registry.model_copy(
                update={"instruments": [instrument]}
            ),
            "routing": config.routing.model_copy(
                update={
                    "routes": [
                        route.model_copy(update={"instrument_id": instrument_id})
                        for route in config.routing.routes
                    ],
                }
            ),
        }
    )
    return config.model_copy(update={"system": system})
