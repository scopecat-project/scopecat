from __future__ import annotations

from typing import Annotated

import scopecat.authoring as authoring
from scopecat.authoring import Experiment, axis
from scopecat.compiler.bind import BoundPlan, bind_program
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.config.environment import build_config_environment
from scopecat.config.parameter_resolution import resolve_config_parameters
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.quantity import Quantity
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.sdk.instruments import InterfaceRef

from scopecat_testkit.config_fixtures import simple_scan_config

_SET_FREQUENCY = InterfaceRef("test.set_frequency/v1")
_SET_FREQUENCY_VALUE = _SET_FREQUENCY.property("frequency")
_SCALAR_SIGNAL = InterfaceRef("test.scalar_signal/v1")
_SCALAR_SIGNAL_VALUE = _SCALAR_SIGNAL.acquisition("sample").result("signal")


def load_config() -> ConfigProfileSnapshot:
    return simple_scan_config()


def parameters():
    return resolve_config_parameters(load_config()).data


def bind_invocation(
    invocation: authoring.ExperimentInvocation,
    *,
    config_profile: ConfigProfileSnapshot,
) -> BoundPlan:
    """Compile and bind an authoring fixture against an explicit snapshot."""

    return bind_program(
        compile_invocation(invocation).program,
        build_config_environment(config_profile),
    )


DRIVE_FREQUENCY_POINT = authoring.coordinate(
    "drive_frequency",
    authoring.QuantityType(unit="GHz"),
)


@authoring.module(
    id="test.simple_scan",
    metadata={"assembled_by": "module"},
)
def SIMPLE_MODULE(
    module: authoring.ModuleContext,
    subject: Annotated[
        authoring.Input[EntityRef | str],
        authoring.ScalarType(authoring.EntityType()),
    ],
    drive_frequency: Annotated[
        authoring.Input[Quantity],
        authoring.ScalarType(authoring.QuantityType(unit="GHz")),
    ],
) -> authoring.ProductRef:
    source = module._resource(
        "source",
        requires=(_SET_FREQUENCY, _SCALAR_SIGNAL),
    )
    module._bind_property(
        source,
        _SET_FREQUENCY_VALUE,
        value=drive_frequency,
    )
    signal = module._product("signal", unit="ratio")
    module._acquire(
        "read-signal",
        resource=source,
        results={_SCALAR_SIGNAL_VALUE: signal},
    )
    return signal


def simple_experiment(
    *,
    id: str = "test.simple_scan",
    kind: str = "simple_scan",
) -> Experiment[...]:
    def definition(
        experiment: authoring.ExperimentContext,
        subject: Annotated[
            authoring.Input[EntityRef | str],
            authoring.ScalarType(authoring.EntityType()),
        ],
    ) -> None:
        signal = experiment.use(
            SIMPLE_MODULE(
                subject=subject,
                drive_frequency=DRIVE_FREQUENCY_POINT,
            )
        )
        experiment.grid(
            axis(
                DRIVE_FREQUENCY_POINT,
                center=authoring.parameter(
                    "drive_frequency",
                    authoring.ScalarType(authoring.QuantityType()),
                ),
                span=Quantity(value=200.0, unit="MHz"),
                points=5,
            ),
        )
        experiment.alias(signal, record_id="signal")

    return authoring.experiment(
        id=id,
        kind=kind,
        metadata={"assembled_by": "experiment"},
    )(definition)
