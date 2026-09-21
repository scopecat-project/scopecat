from __future__ import annotations

from scopecat.kernel.quantity import Quantity
from scopecat.kernel.state import StateValue
from scopecat.kernel.value_types import Payload as PayloadType
from scopecat.kernel.value_types import Scalar
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.measurement import (
    MeasurementAcquisitionValue,
    MeasurementScalar,
)
from scopecat.sdk.instruments import (
    AcquisitionResultRef,
    DriverAcquisition,
    DriverOperation,
    DriverOutcome,
    DriverReadback,
    DriverScalar,
    DriverStatePatch,
    DriverStateReadback,
    DriverStateReadRequest,
    DriverSuccess,
    InstrumentDescription,
    PropertyRef,
    acquisition,
    acquisition_result,
    float_property,
    interface,
    operation,
    operation_argument,
    quantity_property,
    state_readback,
)

from scopecat_testkit.config_fixtures import simple_scan_config


class SignalInstrumentDriver:
    def __init__(self, *, instrument_id: str = "source-0") -> None:
        self._instrument_id = instrument_id
        self.implementation_id = "tests.signal_driver"
        self.implementation_version = "v0"
        self._state: dict[tuple[str, str], DriverScalar] = {
            ("test.set_frequency/v1", "frequency"): Quantity(value=4.0, unit="GHz"),
            ("test.set_gain/v1", "gain"): 0.0,
        }
        self.applied: list[DriverStatePatch] = []
        self.invoked: list[DriverOperation] = []
        self.collect_requests: list[DriverAcquisition] = []

    @property
    def instrument_id(self) -> str:
        return self._instrument_id

    def describe(self) -> InstrumentDescription:
        return InstrumentDescription(
            instrument_id=self.instrument_id,
            implementation_id=self.implementation_id,
            implementation_version=self.implementation_version,
            interfaces=[
                interface(
                    "test.set_frequency/v1",
                    properties=[
                        quantity_property("frequency", unit="GHz", restore=True)
                    ],
                ),
                interface(
                    "test.set_gain/v1",
                    properties=[float_property("gain")],
                ),
                interface(
                    "test.play_program/v1",
                    operations=[
                        operation(
                            "play",
                            arguments=[
                                operation_argument(
                                    "program",
                                    value_type=Scalar(
                                        PayloadType(schema_id="pulse_program")
                                    ),
                                )
                            ],
                        )
                    ],
                ),
                interface(
                    "test.scalar_signal/v1",
                    acquisitions=[
                        acquisition(
                            "sample",
                            results=[acquisition_result("signal", unit="ratio")],
                        )
                    ],
                ),
            ],
        )

    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        return state_readback(
            request,
            {
                PropertyRef(interface_id, (), property_id): value
                for (interface_id, property_id), value in self._state.items()
            },
            evidence={"mode": "test_offline"},
        )

    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.applied.append(request)
        for entry in request.entries:
            if not isinstance(entry.target, PropertyRef):
                raise ValueError("test driver does not expose device state members")
            self._state[(entry.target.interface_id, entry.target.property_id)] = (
                entry.value
            )
        return DriverSuccess(None)

    def invoke(
        self,
        request: DriverOperation,
    ) -> DriverOutcome[DriverStateReadback | None]:
        self.invoked.append(request)
        return DriverSuccess(None)

    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        self.collect_requests.append(request)
        selected = {
            result for result in request.results if result.result_id == "signal"
        }
        if not selected:
            return DriverSuccess(DriverReadback(values={}))
        values: dict[AcquisitionResultRef, MeasurementAcquisitionValue] = {
            result: MeasurementScalar.create(
                dtype="float64",
                value=1.0,
                unit="ratio",
            )
            for result in selected
        }
        return DriverSuccess(
            DriverReadback(
                values=values,
                metadata={"implementation": self.implementation_id},
            ),
        )

    def disconnect(self) -> None:
        return None

    def abort(self) -> None:
        return None


def load_config() -> ConfigProfileSnapshot:
    return simple_scan_config()


def quantity_state(value: float, unit: str) -> StateValue:
    return StateValue(Quantity(value=value, unit=unit))


def number_state(value: float) -> StateValue:
    return StateValue(value)
