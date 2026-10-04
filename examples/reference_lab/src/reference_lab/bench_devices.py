"""Virtual bare AWGs, digitizer, and timing controller for the reference lab."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

import numpy as np
import scopecat as sc
from numpy.typing import NDArray
from scopecat.program.measurement_types import MeasurementDType
from scopecat.records.measurement import (
    MeasurementAcquisitionValue,
    MeasurementArray,
)
from scopecat.sdk.instruments import (
    AcquisitionResultRef,
    DriverAcquisition,
    DriverConnectionSpec,
    DriverOperation,
    DriverOutcome,
    DriverPayload,
    DriverReadback,
    DriverScalar,
    DriverSpec,
    DriverStatePatch,
    DriverStateReadback,
    DriverStateReadRequest,
    DriverSuccess,
    InstrumentDescription,
    ObjectInstrumentDriver,
    PropertyRef,
    implements,
    instrument_component,
    instrument_driver,
    interface_mount,
    state_readback,
)
from scopecat.sdk.instruments.declarations import compile_interface
from scopecat_instruments.interface_declarations import ReferenceClockInterface
from scopecat_instruments.members import REFERENCE_CLOCK_REFERENCE_SOURCE

from reference_lab.bench_interfaces import (
    ANALOG_WAVEFORM_OUTPUT_AMPLITUDE,
    ANALOG_WAVEFORM_OUTPUT_ENABLED,
    ANALOG_WAVEFORM_OUTPUT_OFFSET,
    ANALOG_WAVEFORM_OUTPUT_PLAY,
    ANALOG_WAVEFORM_OUTPUT_RESET,
    ANALOG_WAVEFORM_OUTPUT_SPEC,
    ANALOG_WAVEFORM_OUTPUT_WAVEFORM,
    AWG_ARM_PROGRAM,
    AWG_LOAD_PROGRAM,
    AWG_PROGRAM,
    AWG_RUN_MODE,
    AWG_SAMPLE_RATE,
    AWG_SEQUENCER_SPEC,
    DIGITIZER_ARM_PROGRAM,
    DIGITIZER_CONTROL_SPEC,
    DIGITIZER_FETCH_PROGRAM_IQ,
    DIGITIZER_INPUT_COUPLING,
    DIGITIZER_INPUT_ENABLED,
    DIGITIZER_INPUT_RANGE,
    DIGITIZER_INPUT_SPEC,
    DIGITIZER_LOAD_PROGRAM,
    DIGITIZER_PROGRAM,
    DIGITIZER_RECORD_LENGTH,
    DIGITIZER_SAMPLE_RATE,
    DIGITIZER_TRIGGER_SOURCE,
    TriggerCoordinatorInterface,
)
from reference_lab.interfaces import (
    CLOCK_TIMING_FREQUENCY,
    CLOCK_TIMING_LOCKED,
    CLOCK_TIMING_SPEC,
)
from reference_lab.payloads import (
    AwgEntryDocument,
    AwgProgramDocument,
    DigitizerProgramDocument,
    MaterializedAwgProgramDocument,
    SampledWaveformDocument,
    TriggerProgramDocument,
    materialize_awg_program,
)
from reference_lab.targets.list_mode.iq_semantics import (
    integrate_rectangular_iq,
)
from reference_lab.virtual_lab.capture_payload import VirtualCaptureQueueDocument
from reference_lab.virtual_lab.capture_plant import VirtualCaptureSourceInterface

AWG_OUTPUT_COMPONENT_IDS = tuple(f"ch{index}" for index in range(1, 9))
DIGITIZER_INPUT_COMPONENT_IDS = ("ch1", "ch2")
type BenchSamples = NDArray[np.float64]
VIRTUAL_AWG_DRIVER_ID = "reference_lab.virtual.awg"
VIRTUAL_DIGITIZER_DRIVER_ID = "reference_lab.virtual.digitizer"
VIRTUAL_TIMING_CONTROLLER_DRIVER_ID = "reference_lab.virtual.timing_controller"
REFERENCE_CLOCK_SPEC = compile_interface(ReferenceClockInterface).spec


def _virtual_driver_spec(
    driver_id: str,
    label: str,
    *,
    channel_option: str,
) -> DriverSpec:
    return DriverSpec(
        driver_id=driver_id,
        implementation_version="v1",
        label=label,
        connections=(
            DriverConnectionSpec(
                kind="virtual",
                options_schema={
                    "type": "object",
                    "properties": {
                        channel_option: {"type": "integer", "minimum": 1},
                    },
                    "additionalProperties": False,
                },
            ),
        ),
    )


VIRTUAL_AWG_DRIVER_SPEC = _virtual_driver_spec(
    VIRTUAL_AWG_DRIVER_ID,
    "Virtual configurable-channel AWG",
    channel_option="output_count",
)
VIRTUAL_DIGITIZER_DRIVER_SPEC = _virtual_driver_spec(
    VIRTUAL_DIGITIZER_DRIVER_ID,
    "Virtual configurable-channel digitizer",
    channel_option="input_count",
)
VIRTUAL_TIMING_CONTROLLER_DRIVER_SPEC = DriverSpec(
    driver_id=VIRTUAL_TIMING_CONTROLLER_DRIVER_ID,
    implementation_version="v1",
    label="Virtual timing controller",
    connections=(
        DriverConnectionSpec(
            kind="virtual",
            options_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
    ),
)


@dataclass(slots=True)
class BenchSignalWorld:
    """Shared trigger and analog-signal world for the virtual bench."""

    armed_awg_programs: dict[str, tuple[AwgEntryDocument, ...]] = field(
        default_factory=dict
    )
    armed_digitizer_programs: dict[str, DigitizerProgramDocument] = field(
        default_factory=dict
    )
    digitizer_program_captures: dict[
        tuple[str, tuple[str, ...]], tuple[tuple[int, BenchSamples], ...]
    ] = field(default_factory=dict)
    capture_queue: list[dict[tuple[str, tuple[str, ...]], BenchSamples]] = field(
        default_factory=list
    )
    trigger_count: int = 0

    def arm_awg_program(
        self,
        instrument_id: str,
        entries: tuple[AwgEntryDocument, ...],
    ) -> None:
        self.armed_awg_programs[instrument_id] = entries

    def is_awg_program_armed(self, instrument_id: str) -> bool:
        return instrument_id in self.armed_awg_programs

    def arm_digitizer_program(
        self,
        instrument_id: str,
        program: DigitizerProgramDocument,
    ) -> None:
        self.armed_digitizer_programs[instrument_id] = program

    def is_digitizer_program_armed(self, instrument_id: str) -> bool:
        return instrument_id in self.armed_digitizer_programs

    def load_capture_queue(self, queue: VirtualCaptureQueueDocument) -> None:
        self.capture_queue = [
            {
                (trace.instrument_id, trace.component_path): trace.samples
                for trace in capture.traces
            }
            for capture in queue.captures
        ]

    def run_program(
        self,
        program: TriggerProgramDocument,
    ) -> tuple[int, int]:
        expected_awgs = tuple(
            sorted(
                {
                    instrument_id
                    for entry in program.entries
                    for instrument_id in entry.awg_instrument_ids
                }
            )
        )
        expected_digitizers = tuple(
            sorted(
                {
                    instrument_id
                    for entry in program.entries
                    for instrument_id in entry.digitizer_instrument_ids
                }
            )
        )
        if tuple(sorted(self.armed_awg_programs)) != expected_awgs:
            raise ValueError("armed AWG programs do not match trigger participants")
        if tuple(sorted(self.armed_digitizer_programs)) != expected_digitizers:
            raise ValueError(
                "armed digitizer programs do not match trigger participants"
            )
        if any(
            len(entries) != len(program.entries)
            for entries in self.armed_awg_programs.values()
        ) or any(
            len(digitizer.entries) != len(program.entries)
            for digitizer in self.armed_digitizer_programs.values()
        ):
            raise ValueError("armed device programs do not match trigger entry count")

        captures: dict[tuple[str, tuple[str, ...]], list[tuple[int, BenchSamples]]] = {}
        for _shot_index in range(program.repetitions):
            for entry_index, trigger_entry in enumerate(program.entries):
                awg_ids = tuple(
                    sorted(
                        instrument_id
                        for instrument_id, entries in self.armed_awg_programs.items()
                        if entries[entry_index].waveforms
                    )
                )
                digitizer_ids = tuple(
                    sorted(
                        instrument_id
                        for instrument_id, digitizer in (
                            self.armed_digitizer_programs.items()
                        )
                        if digitizer.entries[entry_index].input_component_paths
                    )
                )
                if awg_ids != trigger_entry.awg_instrument_ids:
                    raise ValueError(
                        "AWG program entry does not match trigger participants"
                    )
                if digitizer_ids != trigger_entry.digitizer_instrument_ids:
                    raise ValueError(
                        "digitizer program entry does not match trigger participants"
                    )

                selected_capture = (
                    self.capture_queue.pop(0) if self.capture_queue else {}
                )
                for instrument_id in trigger_entry.digitizer_instrument_ids:
                    digitizer_entry = self.armed_digitizer_programs[
                        instrument_id
                    ].entries[entry_index]
                    for component_path in digitizer_entry.input_component_paths:
                        capture = selected_capture.get((instrument_id, component_path))
                        if capture is None:
                            capture = np.zeros(
                                digitizer_entry.sample_count,
                                dtype=np.float64,
                            )
                        captures.setdefault((instrument_id, component_path), []).append(
                            (
                                entry_index,
                                capture,
                            )
                        )
                self.trigger_count += 1

        self.digitizer_program_captures = {
            key: tuple(value) for key, value in captures.items()
        }
        awg_count = len(self.armed_awg_programs)
        digitizer_count = len(self.armed_digitizer_programs)
        self.armed_awg_programs.clear()
        self.armed_digitizer_programs.clear()
        return awg_count, digitizer_count

    def digitizer_program_segments(
        self,
        instrument_id: str,
        component_path: tuple[str, ...],
    ) -> tuple[tuple[int, BenchSamples], ...]:
        return self.digitizer_program_captures.get(
            (instrument_id, component_path),
            (),
        )

    def abort_instrument(self, instrument_id: str) -> None:
        self.armed_awg_programs.pop(instrument_id, None)
        self.armed_digitizer_programs.pop(instrument_id, None)
        self.digitizer_program_captures = {
            key: value
            for key, value in self.digitizer_program_captures.items()
            if key[0] != instrument_id
        }


class VirtualAwg:
    """Real-valued waveform outputs backed by one shared sample clock."""

    implementation_id = VIRTUAL_AWG_DRIVER_ID
    implementation_version = "v1"

    def __init__(
        self,
        instrument_id: str,
        world: BenchSignalWorld,
        *,
        output_count: int = 8,
    ) -> None:
        self.instrument_id = instrument_id
        self._world = world
        self._output_component_ids = tuple(
            f"ch{index}" for index in range(1, output_count + 1)
        )
        self._loaded_program: MaterializedAwgProgramDocument | None = None
        self._state: dict[PropertyRef, DriverScalar] = {
            AWG_SAMPLE_RATE: sc.Quantity(1.0e9, "Hz"),
            AWG_RUN_MODE: "once",
            REFERENCE_CLOCK_REFERENCE_SOURCE: "external",
            CLOCK_TIMING_FREQUENCY: sc.Quantity(10.0e6, "Hz"),
            CLOCK_TIMING_LOCKED: True,
        }
        for channel_id in self._output_component_ids:
            component_path = ("outputs", channel_id)
            self._state.update(
                {
                    _mount_property(
                        ANALOG_WAVEFORM_OUTPUT_AMPLITUDE,
                        component_path,
                    ): sc.Quantity(0.25, "V"),
                    _mount_property(
                        ANALOG_WAVEFORM_OUTPUT_OFFSET,
                        component_path,
                    ): sc.Quantity(0.0, "V"),
                    _mount_property(
                        ANALOG_WAVEFORM_OUTPUT_ENABLED,
                        component_path,
                    ): False,
                }
            )

    def describe(self) -> InstrumentDescription:
        output = ANALOG_WAVEFORM_OUTPUT_SPEC
        return InstrumentDescription(
            instrument_id=self.instrument_id,
            implementation_id=self.implementation_id,
            implementation_version=self.implementation_version,
            label=f"Virtual {len(self._output_component_ids)}-channel AWG",
            description=(
                "A modular AWG model with an instrument-wide sample and reference "
                "clock and independently mounted real-valued DAC outputs."
            ),
            components=[
                instrument_component(
                    "outputs",
                    components=tuple(
                        instrument_component(channel_id)
                        for channel_id in self._output_component_ids
                    ),
                ),
            ],
            interfaces=[
                AWG_SEQUENCER_SPEC,
                REFERENCE_CLOCK_SPEC,
                CLOCK_TIMING_SPEC,
                output,
            ],
            interface_mounts=[
                interface_mount(output.id, "outputs", channel_id)
                for channel_id in self._output_component_ids
            ],
        )

    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        return state_readback(
            request,
            self._state,
            evidence={
                "mode": "virtual",
                "loaded_entry_count": (
                    0
                    if self._loaded_program is None
                    else len(self._loaded_program.entries)
                ),
                "program_armed": self._world.is_awg_program_armed(self.instrument_id),
            },
        )

    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        clock_changed = any(
            entry.target in {REFERENCE_CLOCK_REFERENCE_SOURCE, CLOCK_TIMING_FREQUENCY}
            for entry in request.entries
        )
        if clock_changed:
            self._state[CLOCK_TIMING_LOCKED] = False
        for entry in request.entries:
            if not isinstance(entry.target, PropertyRef):
                raise ValueError("virtual AWG has no model-specific state members")
            value = entry.value
            if isinstance(value, sc.Quantity):
                unit = (
                    "Hz"
                    if entry.target.property_id
                    in {
                        AWG_SAMPLE_RATE.property_id,
                        CLOCK_TIMING_FREQUENCY.property_id,
                    }
                    else "V"
                )
                value = value.to(unit)
            self._state[entry.target] = value
        if clock_changed:
            self._state[CLOCK_TIMING_LOCKED] = True
        return DriverSuccess(
            None,
            metadata={"clock_settled": bool(self._state[CLOCK_TIMING_LOCKED])},
        )

    def invoke(
        self,
        request: DriverOperation,
    ) -> DriverOutcome[DriverStateReadback | None]:
        if request.target.operation_id == AWG_LOAD_PROGRAM.operation_id:
            decoded = cast(
                "AwgProgramDocument",
                cast("DriverPayload", request.arguments[AWG_PROGRAM.argument_id]).value,
            )
            self._loaded_program = materialize_awg_program(decoded)
            for channel_id in self._output_component_ids:
                self._state[
                    _mount_property(
                        ANALOG_WAVEFORM_OUTPUT_OFFSET,
                        ("outputs", channel_id),
                    )
                ] = sc.Quantity(0.0, "V")
            return DriverSuccess(
                None,
                metadata={
                    "operation_id": AWG_LOAD_PROGRAM.operation_id,
                    "entry_count": len(self._loaded_program.entries),
                },
            )

        if request.target.operation_id == AWG_ARM_PROGRAM.operation_id:
            if self._loaded_program is None:
                raise ValueError("AWG has no loaded program")
            self._world.arm_awg_program(
                self.instrument_id,
                self._loaded_program.entries,
            )
            return DriverSuccess(
                None,
                metadata={
                    "operation_id": AWG_ARM_PROGRAM.operation_id,
                    "entry_count": len(self._loaded_program.entries),
                },
            )

        component_path = request.target.component_path
        if request.target.operation_id == ANALOG_WAVEFORM_OUTPUT_RESET.operation_id:
            self._state.update(
                {
                    _mount_property(
                        ANALOG_WAVEFORM_OUTPUT_AMPLITUDE,
                        component_path,
                    ): sc.Quantity(0.25, "V"),
                    _mount_property(
                        ANALOG_WAVEFORM_OUTPUT_OFFSET,
                        component_path,
                    ): sc.Quantity(0.0, "V"),
                    _mount_property(
                        ANALOG_WAVEFORM_OUTPUT_ENABLED,
                        component_path,
                    ): False,
                }
            )
            return DriverSuccess(
                None,
                metadata={"operation_id": ANALOG_WAVEFORM_OUTPUT_RESET.operation_id},
            )
        waveform = cast(
            "SampledWaveformDocument",
            cast(
                "DriverPayload",
                request.arguments[ANALOG_WAVEFORM_OUTPUT_WAVEFORM.argument_id],
            ).value,
        )
        emitted = cast(
            "bool",
            self._state[
                _mount_property(ANALOG_WAVEFORM_OUTPUT_ENABLED, component_path)
            ],
        )
        sample_rate = _quantity_value(self._state[AWG_SAMPLE_RATE], "Hz")
        run_mode = cast("str", self._state[AWG_RUN_MODE])
        return DriverSuccess(
            None,
            metadata={
                "component_path": list(component_path),
                "operation_id": ANALOG_WAVEFORM_OUTPUT_PLAY.operation_id,
                "sample_count": len(waveform.samples),
                "sample_rate_hz": sample_rate,
                "output_enabled": emitted,
                "run_mode": run_mode,
                "signal_emitted": emitted,
            },
        )

    def collect(self, request: DriverAcquisition) -> DriverOutcome[DriverReadback]:
        del request
        raise NotImplementedError

    def disconnect(self) -> None:
        return None

    def abort(self) -> None:
        self._world.abort_instrument(self.instrument_id)
        for channel_id in self._output_component_ids:
            self._state[
                _mount_property(
                    ANALOG_WAVEFORM_OUTPUT_ENABLED,
                    ("outputs", channel_id),
                )
            ] = False


class VirtualDigitizer:
    """Two physical ADC inputs sharing one acquisition engine."""

    implementation_id = VIRTUAL_DIGITIZER_DRIVER_ID
    implementation_version = "v1"

    def __init__(
        self,
        instrument_id: str,
        world: BenchSignalWorld,
        *,
        input_count: int = 2,
    ) -> None:
        self.instrument_id = instrument_id
        self._world = world
        self._loaded_program: DigitizerProgramDocument | None = None
        self._input_component_ids = tuple(
            f"ch{index}" for index in range(1, input_count + 1)
        )
        self._state: dict[PropertyRef, DriverScalar] = {
            DIGITIZER_SAMPLE_RATE: sc.Quantity(1.0e9, "Hz"),
            DIGITIZER_RECORD_LENGTH: 1024,
            DIGITIZER_TRIGGER_SOURCE: "external",
        }
        for channel_id in self._input_component_ids:
            component_path = ("inputs", channel_id)
            self._state.update(
                {
                    _mount_property(
                        DIGITIZER_INPUT_ENABLED,
                        component_path,
                    ): channel_id == "ch1",
                    _mount_property(
                        DIGITIZER_INPUT_RANGE,
                        component_path,
                    ): sc.Quantity(0.5, "V"),
                    _mount_property(
                        DIGITIZER_INPUT_COUPLING,
                        component_path,
                    ): "dc",
                }
            )

    def describe(self) -> InstrumentDescription:
        input_interface = DIGITIZER_INPUT_SPEC
        return InstrumentDescription(
            instrument_id=self.instrument_id,
            implementation_id=self.implementation_id,
            implementation_version=self.implementation_version,
            label=f"Virtual {len(self._input_component_ids)}-channel digitizer",
            description=(
                "A bare ADC model; list-mode demodulation windows belong to the "
                "quantum target and reference physical inputs by route."
            ),
            components=[
                instrument_component(
                    "inputs",
                    components=tuple(
                        instrument_component(channel_id)
                        for channel_id in self._input_component_ids
                    ),
                )
            ],
            interfaces=[
                DIGITIZER_CONTROL_SPEC,
                input_interface,
            ],
            interface_mounts=[
                interface_mount(input_interface.id, "inputs", channel_id)
                for channel_id in self._input_component_ids
            ],
        )

    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        return state_readback(
            request,
            self._state,
            evidence={
                "mode": "virtual",
                "program_armed": self._world.is_digitizer_program_armed(
                    self.instrument_id
                ),
                "loaded_entry_count": (
                    0
                    if self._loaded_program is None
                    else len(self._loaded_program.entries)
                ),
            },
        )

    def apply_state(
        self,
        request: DriverStatePatch,
    ) -> DriverOutcome[DriverStateReadback | None]:
        for entry in request.entries:
            if not isinstance(entry.target, PropertyRef):
                raise ValueError("digitizer has no model-specific state members")
            value = entry.value
            if isinstance(value, sc.Quantity):
                unit = (
                    "Hz"
                    if entry.target.property_id == DIGITIZER_SAMPLE_RATE.property_id
                    else "V"
                )
                value = value.to(unit)
            self._state[entry.target] = value
        return DriverSuccess(None)

    def invoke(
        self,
        request: DriverOperation,
    ) -> DriverOutcome[DriverStateReadback | None]:
        if request.target.operation_id == DIGITIZER_LOAD_PROGRAM.operation_id:
            self._loaded_program = cast(
                "DigitizerProgramDocument",
                cast(
                    "DriverPayload",
                    request.arguments[DIGITIZER_PROGRAM.argument_id],
                ).value,
            )
            return DriverSuccess(
                None,
                metadata={"entry_count": len(self._loaded_program.entries)},
            )
        if request.target.operation_id == DIGITIZER_ARM_PROGRAM.operation_id:
            if self._loaded_program is None:
                raise ValueError("digitizer has no loaded program")
            self._world.arm_digitizer_program(
                self.instrument_id,
                self._loaded_program,
            )
            return DriverSuccess(
                None,
                metadata={
                    "program_armed": True,
                    "entry_count": len(self._loaded_program.entries),
                },
            )
        raise ValueError(
            f"unsupported digitizer operation {request.target.operation_id!r}"
        )

    def collect(self, request: DriverAcquisition) -> DriverOutcome[DriverReadback]:
        sample_rate = _quantity_value(self._state[DIGITIZER_SAMPLE_RATE], "Hz")
        values: dict[AcquisitionResultRef, MeasurementAcquisitionValue] = {}
        assert self._loaded_program is not None
        segments = self._world.digitizer_program_segments(
            self.instrument_id,
            request.target.component_path,
        )
        if request.target.acquisition_id == DIGITIZER_FETCH_PROGRAM_IQ.acquisition_id:
            block: NDArray[np.float64] | NDArray[np.complex128] = np.fromiter(
                (
                    integrate_rectangular_iq(
                        trace,
                        start_sample=window.start_sample,
                        sample_count=window.sample_count,
                        sample_rate_hz=sample_rate,
                        demodulation_frequency_hz=(window.demodulation_frequency_hz),
                    )
                    for entry_index, trace in segments
                    for window in self._loaded_program.entries[entry_index].windows
                    if window.component_path == request.target.component_path
                ),
                dtype=np.complex128,
            )
            dtype: MeasurementDType = "complex128"
        else:
            traces = tuple(trace for _entry_index, trace in segments)
            block = (
                np.empty(0, dtype=np.float64)
                if not traces
                else traces[0]
                if len(traces) == 1
                else np.concatenate(traces)
            )
            dtype = "float64"
        if request.results:
            result = next(iter(request.results))
            dimensions = request.dimensions.get(result, ())
            if dimensions:
                [dimension] = dimensions
                if dimension.size is not None:
                    block = block[
                        (dimension.offset or 0) : (dimension.offset or 0)
                        + dimension.size
                    ]
        if request.results:
            measurement = MeasurementArray.create(
                dtype=dtype,
                unit="V",
                values=block,
            )
            values.update(dict.fromkeys(request.results, measurement))
        return DriverSuccess(
            DriverReadback(
                values=values,
                metadata={
                    "triggered": True,
                    "mode": "virtual",
                    "segment_count": len(segments),
                },
            )
        )

    def disconnect(self) -> None:
        return None

    def abort(self) -> None:
        self._world.abort_instrument(self.instrument_id)


@instrument_driver(
    VIRTUAL_TIMING_CONTROLLER_DRIVER_ID,
    "v1",
    interfaces=(TriggerCoordinatorInterface, VirtualCaptureSourceInterface),
    label="Virtual timing controller",
    description=(
        "A single shared trigger edge for armed AWGs and digitizers. "
        "The virtual capture interface is a test-plant input."
    ),
)
class VirtualTimingController(ObjectInstrumentDriver):
    """A programmable shared-trigger source plus a test-only virtual plant input."""

    def __init__(self, instrument_id: str, world: BenchSignalWorld) -> None:
        self.instrument_id = instrument_id
        self._world = world
        self._loaded_program: TriggerProgramDocument | None = None
        self._started_programs: dict[str, TriggerProgramDocument] = {}

    @implements(VirtualCaptureSourceInterface.load)
    def load_captures(self, *, captures: DriverPayload) -> DriverSuccess[None]:
        queue = cast("VirtualCaptureQueueDocument", captures.value)
        self._world.load_capture_queue(queue)
        return DriverSuccess(None, metadata={"capture_count": len(queue.captures)})

    @implements(TriggerCoordinatorInterface.load_program)
    def load_trigger_program(self, *, program: DriverPayload) -> DriverSuccess[None]:
        self._loaded_program = cast("TriggerProgramDocument", program.value)
        return DriverSuccess(
            None,
            metadata={
                "program_id": self._loaded_program.program_id,
                "entry_count": len(self._loaded_program.entries),
                "repetitions": self._loaded_program.repetitions,
            },
        )

    @implements(TriggerCoordinatorInterface.start_program)
    def start_program(self) -> DriverSuccess[None]:
        return self._start_program(idempotent=False)

    @implements(TriggerCoordinatorInterface.start_program_idempotent)
    def start_program_idempotent(self) -> DriverSuccess[None]:
        return self._start_program(idempotent=True)

    def _start_program(self, *, idempotent: bool) -> DriverSuccess[None]:
        if self._loaded_program is None:
            raise ValueError("timing controller has no loaded program")
        cached = self._started_programs.get(self._loaded_program.program_id)
        if idempotent and cached is not None:
            if cached != self._loaded_program:
                raise ValueError(
                    "trigger program id was reused with different contents"
                )
            awg_count, digitizer_count, replayed = 0, 0, True
        else:
            awg_count, digitizer_count = self._world.run_program(self._loaded_program)
            replayed = False
            if idempotent:
                self._started_programs[self._loaded_program.program_id] = (
                    self._loaded_program
                )
        return DriverSuccess(
            None,
            metadata={
                "armed_awg_count": awg_count,
                "armed_digitizer_count": digitizer_count,
                "trigger_count": self._world.trigger_count,
                "trigger_program_id": self._loaded_program.program_id,
                "replayed": replayed,
            },
        )


def _mount_property(
    target: PropertyRef,
    component_path: tuple[str, ...],
) -> PropertyRef:
    return PropertyRef(target.interface_id, component_path, target.property_id)


def _quantity_value(value: DriverScalar, unit: str) -> float:
    return cast("sc.Quantity", value).to(unit).value


__all__ = [
    "AWG_OUTPUT_COMPONENT_IDS",
    "DIGITIZER_INPUT_COMPONENT_IDS",
    "VIRTUAL_AWG_DRIVER_ID",
    "VIRTUAL_AWG_DRIVER_SPEC",
    "VIRTUAL_DIGITIZER_DRIVER_ID",
    "VIRTUAL_DIGITIZER_DRIVER_SPEC",
    "VIRTUAL_TIMING_CONTROLLER_DRIVER_ID",
    "VIRTUAL_TIMING_CONTROLLER_DRIVER_SPEC",
    "BenchSignalWorld",
    "VirtualAwg",
    "VirtualDigitizer",
    "VirtualTimingController",
]
