"""Vendor-neutral bench AWG, digitizer and trigger capability declarations."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from scopecat.kernel.quantity import Quantity
from scopecat.sdk.instruments.declarations import (
    Member,
    acquisition,
    argument,
    array_result,
    axis,
    compile_interface,
    declared_acquisition_ref,
    declared_argument_ref,
    declared_operation_ref,
    declared_property_ref,
    instrument_interface,
    member,
    operation,
    result_schema,
)

from reference_lab.payloads import (
    AWG_PROGRAM_SCHEMA_ID,
    DIGITIZER_PROGRAM_SCHEMA_ID,
    TRIGGER_PROGRAM_SCHEMA_ID,
)

type AwgRunMode = Literal["once", "continuous"]
type Coupling = Literal["dc", "ac"]
type DigitizerTriggerSource = Literal["external", "software"]


@instrument_interface(
    "reference_lab.awg_sequencer/v1",
    label="AWG sequencer",
    description="Instrument-wide sampling and sequence execution configuration.",
)
class AwgSequencerInterface(Protocol):
    sample_rate: Member[Quantity] = member(
        access="read_write",
        unit="Hz",
        minimum=1.0,
        label="Sample rate",
    )
    run_mode: Member[AwgRunMode] = member(
        access="read_write",
        label="Run mode",
    )

    @operation(label="Load a synchronized multi-channel program")
    def load_program(
        self,
        *,
        program: Annotated[
            object,
            argument(payload_schema_id=AWG_PROGRAM_SCHEMA_ID, label="AWG program"),
        ],
    ) -> None: ...

    @operation(label="Arm the complete loaded list program")
    def arm_program(self) -> None: ...


@instrument_interface(
    "reference_lab.analog_waveform_output/v1",
    label="Analog waveform output",
    description="One real-valued DAC output driven by normalized samples.",
)
class AnalogWaveformOutputInterface(Protocol):
    amplitude: Member[Quantity] = member(
        access="read_write",
        unit="V",
        minimum=0.0,
        label="Peak amplitude",
    )
    offset: Member[Quantity] = member(
        access="read_write",
        unit="V",
        label="DC offset",
    )
    output_enabled: Member[bool] = member(
        access="read_write",
        label="Output enabled",
    )

    @operation(
        label="Reset output settings",
        invalidates=(amplitude, offset, output_enabled),
    )
    def reset(self) -> None: ...


@instrument_interface(
    "reference_lab.digitizer_control/v1",
    label="Digitizer acquisition control",
    description="Instrument-wide sample clock, trigger, and record memory.",
)
class DigitizerControlInterface(Protocol):
    sample_rate: Member[Quantity] = member(
        access="read_write",
        unit="Hz",
        minimum=1.0,
        label="Sample rate",
    )
    record_length: Member[int] = member(
        access="read_write",
        minimum=1,
        label="Record length",
    )
    trigger_source: Member[DigitizerTriggerSource] = member(
        access="read_write",
        label="Trigger source",
    )

    @operation(label="Load a segmented acquisition program")
    def load_program(
        self,
        *,
        program: Annotated[
            object,
            argument(
                payload_schema_id=DIGITIZER_PROGRAM_SCHEMA_ID,
                label="Digitizer program",
            ),
        ],
    ) -> None: ...

    @operation(label="Arm the complete segmented program")
    def arm_program(self) -> None: ...


@result_schema
class DigitizerProgramResults:
    value = array_result(
        dtype="float64",
        unit="V",
        axes=("sample",),
    )


@result_schema
class DigitizerProgramIqResults:
    value = array_result(
        dtype="complex128",
        unit="V",
        axes=("demodulator",),
    )


@instrument_interface(
    "reference_lab.digitizer_input/v1",
    label="Digitizer analog input",
    description=(
        "One physical ADC input. Target-owned demodulation slots are named "
        "by routing channel IDs rather than mounted as separate instruments."
    ),
)
class DigitizerInputInterface(Protocol):
    input_enabled: Member[bool] = member(
        access="read_write",
        label="Input enabled",
    )
    input_range: Member[Quantity] = member(
        access="read_write",
        unit="V",
        minimum=1e-6,
        label="Input range",
    )
    coupling: Member[Coupling] = member(access="read_write", label="Coupling")

    @acquisition(
        results=DigitizerProgramResults,
        label="Fetch one flattened segmented raw-capture block",
        axes={"sample": axis(kind="time", unit="s")},
    )
    def fetch_program(self) -> None: ...

    @acquisition(
        results=DigitizerProgramIqResults,
        label="Fetch one flattened segmented integrated-IQ block",
        axes={"demodulator": axis(kind="index")},
    )
    def fetch_program_iq(self) -> None: ...


@instrument_interface(
    "reference_lab.trigger_coordinator/v2",
    label="Trigger coordinator",
    description="Execute a shared multi-entry trigger program for armed devices.",
)
class TriggerCoordinatorInterface(Protocol):
    @operation(label="Load a multi-entry trigger program")
    def load_program(
        self,
        *,
        program: Annotated[
            object,
            argument(
                payload_schema_id=TRIGGER_PROGRAM_SCHEMA_ID,
                label="Trigger program",
            ),
        ],
    ) -> None: ...

    @operation(label="Start one non-idempotent loaded trigger program")
    def start_program(self) -> None: ...

    @operation(label="Start one session-idempotent loaded trigger program")
    def start_program_idempotent(self) -> None: ...


_COMPILED_AWG_SEQUENCER = compile_interface(AwgSequencerInterface)
AWG_SEQUENCER_SPEC = _COMPILED_AWG_SEQUENCER.spec
AWG_SEQUENCER = _COMPILED_AWG_SEQUENCER.ref
AWG_SAMPLE_RATE = declared_property_ref(AwgSequencerInterface, "sample_rate")
AWG_RUN_MODE = declared_property_ref(AwgSequencerInterface, "run_mode")
AWG_LOAD_PROGRAM = declared_operation_ref(AwgSequencerInterface, "load_program")
AWG_PROGRAM = declared_argument_ref(AwgSequencerInterface, "load_program", "program")
AWG_ARM_PROGRAM = declared_operation_ref(AwgSequencerInterface, "arm_program")

_COMPILED_ANALOG_WAVEFORM_OUTPUT = compile_interface(AnalogWaveformOutputInterface)
ANALOG_WAVEFORM_OUTPUT_SPEC = _COMPILED_ANALOG_WAVEFORM_OUTPUT.spec
ANALOG_WAVEFORM_OUTPUT = _COMPILED_ANALOG_WAVEFORM_OUTPUT.ref
ANALOG_WAVEFORM_OUTPUT_AMPLITUDE = declared_property_ref(
    AnalogWaveformOutputInterface, "amplitude"
)
ANALOG_WAVEFORM_OUTPUT_OFFSET = declared_property_ref(
    AnalogWaveformOutputInterface, "offset"
)
ANALOG_WAVEFORM_OUTPUT_ENABLED = declared_property_ref(
    AnalogWaveformOutputInterface, "output_enabled"
)
ANALOG_WAVEFORM_OUTPUT_RESET = declared_operation_ref(
    AnalogWaveformOutputInterface, "reset"
)

_COMPILED_DIGITIZER_CONTROL = compile_interface(DigitizerControlInterface)
DIGITIZER_CONTROL_SPEC = _COMPILED_DIGITIZER_CONTROL.spec
DIGITIZER_CONTROL = _COMPILED_DIGITIZER_CONTROL.ref
DIGITIZER_SAMPLE_RATE = declared_property_ref(DigitizerControlInterface, "sample_rate")
DIGITIZER_RECORD_LENGTH = declared_property_ref(
    DigitizerControlInterface, "record_length"
)
DIGITIZER_TRIGGER_SOURCE = declared_property_ref(
    DigitizerControlInterface, "trigger_source"
)
DIGITIZER_LOAD_PROGRAM = declared_operation_ref(
    DigitizerControlInterface, "load_program"
)
DIGITIZER_PROGRAM = declared_argument_ref(
    DigitizerControlInterface, "load_program", "program"
)
DIGITIZER_ARM_PROGRAM = declared_operation_ref(DigitizerControlInterface, "arm_program")

_COMPILED_DIGITIZER_INPUT = compile_interface(DigitizerInputInterface)
DIGITIZER_INPUT_SPEC = _COMPILED_DIGITIZER_INPUT.spec
DIGITIZER_INPUT = _COMPILED_DIGITIZER_INPUT.ref
DIGITIZER_INPUT_ENABLED = declared_property_ref(
    DigitizerInputInterface, "input_enabled"
)
DIGITIZER_INPUT_RANGE = declared_property_ref(DigitizerInputInterface, "input_range")
DIGITIZER_INPUT_COUPLING = declared_property_ref(DigitizerInputInterface, "coupling")
DIGITIZER_FETCH_PROGRAM = declared_acquisition_ref(
    DigitizerInputInterface, "fetch_program"
)
DIGITIZER_FETCH_PROGRAM_VALUE = DIGITIZER_FETCH_PROGRAM.result("value")
DIGITIZER_FETCH_PROGRAM_IQ = declared_acquisition_ref(
    DigitizerInputInterface, "fetch_program_iq"
)
DIGITIZER_FETCH_PROGRAM_IQ_VALUE = DIGITIZER_FETCH_PROGRAM_IQ.result("value")

_COMPILED_TRIGGER_COORDINATOR = compile_interface(TriggerCoordinatorInterface)
TRIGGER_COORDINATOR_SPEC = _COMPILED_TRIGGER_COORDINATOR.spec
TRIGGER_COORDINATOR = _COMPILED_TRIGGER_COORDINATOR.ref
TRIGGER_LOAD_PROGRAM = declared_operation_ref(
    TriggerCoordinatorInterface, "load_program"
)
TRIGGER_PROGRAM = declared_argument_ref(
    TriggerCoordinatorInterface, "load_program", "program"
)
TRIGGER_START_PROGRAM = declared_operation_ref(
    TriggerCoordinatorInterface, "start_program"
)
TRIGGER_START_PROGRAM_IDEMPOTENT = declared_operation_ref(
    TriggerCoordinatorInterface, "start_program_idempotent"
)
