# pyright: reportPrivateUsage=false
"""Dependency-free inspection for symbolic quantum programs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Protocol,
)

from scopecat import Quantity
from scopecat.program.value_types import (
    Entity as EntityAtomType,
)
from scopecat.program.value_types import (
    Payload as PayloadType,
)
from scopecat.program.value_types import (
    Quantity as QuantityAtomType,
)

from scopecat_quantum.acquisitions import QuantumResultDimension
from scopecat_quantum.pulses import (
    AcquireSignal,
    AnalyticEnvelope,
    Constant,
    DerivativeQuadrature,
    DriveSignal,
    FrequencyShift,
    Gaussian,
    LogicalSignal,
    ReadoutSignal,
)

from ._analysis import (
    _pulse_envelope_parts,
)
from ._expressions import QuantityExpression
from ._ir import (
    Acquisition,
    Coupler,
    CouplerSet,
    Measurement,
    ProgramInput,
    ProgramPort,
    ProgramResult,
    ProgramResults,
    PulseEnvelope,
    QuantumFragment,
    QuantumQuantity,
    Qubit,
    QubitPairSet,
    QubitSet,
    _ConditionalFragment,
    _DelayFragment,
    _ExpandedFragment,
    _FragmentCall,
    _GateFragment,
    _ImplementedGateFragment,
    _ParallelCouplerEachFragment,
    _ParallelEachFragment,
    _ParallelFragment,
    _ParallelQubitPairEachFragment,
    _PlayFragment,
    _ProgramFamilyEnvelope,
    _PulseTemplateCallFragment,
    _QuantumParallelFragment,
    _QuantumRepeatFragment,
    _QuantumSequenceFragment,
    _RepeatFragment,
    _SequenceFragment,
    _ShiftPhaseFragment,
)


class _InspectableProgram(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def description(self) -> str | None: ...

    @property
    def ports(self) -> tuple[ProgramPort, ...]: ...

    @property
    def results(self) -> ProgramResults: ...

    @property
    def body(self) -> QuantumFragment: ...


@dataclass(frozen=True, slots=True)
class _InspectionNode:
    label: str
    children: tuple[_InspectionNode, ...] = ()


def describe(program: _InspectableProgram, /) -> str:
    """Return a concise typed declaration summary for ``program``."""

    lines = [f"program {program.id}"]
    if program.description is not None:
        lines.append("description:")
        lines.extend(f"  {line}" for line in program.description.splitlines())
    lines.append("ports:")
    if not program.ports:
        lines.append("  (none)")
    for port in program.ports:
        if isinstance(port, Qubit):
            value_type = "qubit"
        elif isinstance(port, QubitSet):
            value_type = "qubit-set"
        elif isinstance(port, CouplerSet):
            value_type = "coupler-set"
        elif isinstance(port, QubitPairSet):
            value_type = "qubit-pair-set"
        elif isinstance(port, Coupler):
            value_type = "coupler"
        else:
            value_type = _describe_program_input(port)
        lines.append(f"  {port.id}: {value_type}")
    lines.append("results:")
    if not program.results:
        lines.append("  (none)")
    for result in program.results:
        lines.append(f"  {result.id}: {_describe_result(result)}")
    return "\n".join(lines)


def draw(program: _InspectableProgram, /) -> str:
    """Return a dependency-free text tree of ``program`` source structure."""

    root = _InspectionNode(
        label=f"program {program.id}",
        children=(_inspection_node(program.body),),
    )
    lines = [root.label]
    _draw_inspection_children(root.children, prefix="", lines=lines)
    return "\n".join(lines)


def _draw_inspection_children(
    children: tuple[_InspectionNode, ...],
    *,
    prefix: str,
    lines: list[str],
) -> None:
    for index, child in enumerate(children):
        last = index == len(children) - 1
        lines.append(f"{prefix}{'└─' if last else '├─'} {child.label}")
        _draw_inspection_children(
            child.children,
            prefix=prefix + ("   " if last else "│  "),
            lines=lines,
        )


def _inspection_node(fragment: QuantumFragment) -> _InspectionNode:
    if isinstance(fragment, _GateFragment):
        return _InspectionNode(f"gate {_inspection_gate_call(fragment)}")
    if isinstance(fragment, Measurement):
        result = fragment.result
        return _InspectionNode(
            f"measure {result.qubit.id} -> {result.id}"
            f"{_inspection_result_shape(result)}"
        )
    if isinstance(fragment, Acquisition):
        return _InspectionNode(
            f"acquire {fragment.result.qubit.id} "
            f"duration={_inspection_value(fragment.duration)} -> {fragment.result.id}"
            f"{_inspection_result_shape(fragment.result)}"
        )
    if isinstance(fragment, _PlayFragment):
        return _InspectionNode(
            f"play {_inspection_signal(fragment.signal)} "
            f"{_inspection_envelope(fragment.envelope)}"
        )
    if isinstance(fragment, _DelayFragment):
        return _InspectionNode(
            f"delay {_inspection_signal(fragment.signal)} "
            f"duration={_inspection_value(fragment.duration)}"
        )
    if isinstance(fragment, _ShiftPhaseFragment):
        return _InspectionNode(
            f"shift_phase {_inspection_signal(fragment.signal)} "
            f"phase={_inspection_value(fragment.phase)}"
        )
    if isinstance(fragment, _PulseTemplateCallFragment):
        return _InspectionNode(
            f"pulse {fragment.template.id}",
            (_inspection_node(fragment.body),),
        )
    if isinstance(fragment, _ImplementedGateFragment):
        candidate = (
            ""
            if fragment.candidate_id is None
            else f" candidate={fragment.candidate_id!r}"
        )
        return _InspectionNode(
            f"implementation {_inspection_gate_call(fragment.gate)}{candidate}",
            (_inspection_node(fragment.pulse),),
        )
    if isinstance(fragment, _FragmentCall):
        arguments = ", ".join(
            f"{name}={_inspection_value(value)}" for name, value in fragment.arguments
        )
        return _InspectionNode(
            f"fragment {fragment.definition.id}({arguments})",
            (_inspection_program_family_envelope(fragment.definition.envelope),),
        )
    if isinstance(fragment, _ExpandedFragment):
        return _InspectionNode(
            f"fragment {fragment.definition_id}",
            (_inspection_node(fragment.body),),
        )
    if isinstance(fragment, _SequenceFragment | _QuantumSequenceFragment):
        return _InspectionNode(
            "sequence",
            tuple(_inspection_node(item) for item in fragment.operations),
        )
    if isinstance(fragment, _ParallelFragment | _QuantumParallelFragment):
        return _InspectionNode(
            "parallel(end)"
            if isinstance(fragment, _QuantumParallelFragment)
            and fragment.alignment == "end"
            else "parallel",
            tuple(_inspection_node(item) for item in fragment.branches),
        )
    if isinstance(
        fragment,
        _ParallelEachFragment
        | _ParallelCouplerEachFragment
        | _ParallelQubitPairEachFragment,
    ):
        return _InspectionNode(
            f"parallel_each ${fragment.entity_set.id}",
            (_inspection_node(fragment.operation),),
        )
    if isinstance(fragment, _ConditionalFragment):
        cases = tuple(
            _InspectionNode(
                f"case {state}",
                (_inspection_node(branch),),
            )
            for state, branch in fragment.cases
        )
        default = (
            _InspectionNode("default (no-op)")
            if fragment.default is None
            else _InspectionNode("default", (_inspection_node(fragment.default),))
        )
        return _InspectionNode(
            f"switch ${fragment.predicate.id}",
            (*cases, default),
        )
    if isinstance(fragment, _RepeatFragment | _QuantumRepeatFragment):
        result_dimension = (
            fragment.result_dimension_id
            if isinstance(fragment, _QuantumRepeatFragment)
            else None
        )
        dimension_suffix = (
            ""
            if result_dimension is None
            else f" result_dimension={result_dimension!r}"
        )
        return _InspectionNode(
            f"repeat {_inspection_value(fragment.count)}{dimension_suffix}",
            (_inspection_node(fragment.operation),),
        )
    raise AssertionError(f"unsupported quantum fragment {type(fragment).__name__}")


def _inspection_program_family_envelope(
    envelope: _ProgramFamilyEnvelope,
) -> _InspectionNode:
    allowed_gates = ", ".join(
        definition.id.value for definition in envelope.gate_definitions
    )
    return _InspectionNode(
        f"envelope allowed_gates=[{allowed_gates}] "
        f"max_operations={envelope.max_operations} max_depth={envelope.max_depth}"
    )


def _inspection_gate_call(fragment: _GateFragment) -> str:
    arguments = [qubit.id for qubit in fragment.qubits]
    arguments.extend(
        f"{name}={_inspection_value(value)}" for name, value in fragment.arguments
    )
    return f"{fragment.gate.id}({', '.join(arguments)})"


def _inspection_signal(signal: LogicalSignal) -> str:
    if isinstance(signal, DriveSignal):
        return f"drive({signal.qubit.value})"
    if isinstance(signal, ReadoutSignal):
        return f"readout({signal.qubit.value})"
    if isinstance(signal, AcquireSignal):
        return f"acquire({signal.qubit.value})"
    return f"flux({signal.owner.value})"


def _inspection_envelope(envelope: PulseEnvelope | AnalyticEnvelope) -> str:
    if isinstance(envelope, PulseEnvelope):
        (
            kind,
            duration,
            amplitude,
            sigma,
            derivative_beta,
            rise_duration,
            fall_duration,
            phase,
            frequency_offset,
            frequency_reference,
        ) = _pulse_envelope_parts(envelope)
    else:
        frequency_offset = None
        frequency_reference = "center"
        if isinstance(envelope, FrequencyShift):
            frequency_offset = envelope.frequency_offset
            frequency_reference = envelope.phase_reference
            envelope = envelope.envelope
        derivative_beta = None
        if isinstance(envelope, DerivativeQuadrature):
            derivative_beta = envelope.beta
            envelope = envelope.envelope
        if isinstance(envelope, Constant):
            kind = "constant"
            duration, amplitude = envelope.duration, envelope.amplitude
            sigma = None
            rise_duration, fall_duration, phase = None, None, envelope.phase
        elif isinstance(envelope, Gaussian):
            kind = "gaussian"
            duration, amplitude, sigma = (
                envelope.duration,
                envelope.amplitude,
                envelope.sigma,
            )
            rise_duration, fall_duration, phase = None, None, envelope.phase
        else:
            kind = "cosine_flat_top"
            duration, amplitude = envelope.duration, envelope.amplitude
            sigma = None
            rise_duration, fall_duration, phase = (
                envelope.rise_duration,
                envelope.fall_duration,
                envelope.phase,
            )
    fields = [
        f"duration={_inspection_value(duration)}",
        f"amplitude={_inspection_value(amplitude)}",
    ]
    if sigma is not None:
        fields.append(f"sigma={_inspection_value(sigma)}")
    if derivative_beta is not None:
        fields.append(f"derivative_beta={_inspection_value(derivative_beta)}")
    if rise_duration is not None:
        fields.append(f"rise_duration={_inspection_value(rise_duration)}")
    if fall_duration is not None:
        fields.append(f"fall_duration={_inspection_value(fall_duration)}")
    if not _is_zero_phase(phase):
        fields.append(f"phase={_inspection_value(phase)}")
    if frequency_offset is not None:
        fields.append(f"frequency_offset={_inspection_value(frequency_offset)}")
        if frequency_reference != "center":
            fields.append(f"frequency_reference={frequency_reference!r}")
    return f"{kind}({', '.join(fields)})"


def _is_zero_phase(value: QuantumQuantity) -> bool:
    return isinstance(value, Quantity) and value.to("rad").value == 0


def _inspection_value(value: object) -> str:
    if isinstance(value, Qubit | QubitSet | Coupler | CouplerSet | QubitPairSet):
        return value.id
    if isinstance(value, ProgramInput):
        return f"${value.id}"
    if isinstance(value, QuantityExpression):
        if value.binding_type is not None:
            return _inspection_value(value.left)
        return (
            f"({_inspection_value(value.left)} {value.operator} "
            f"{_inspection_value(value.right)})"
        )
    if isinstance(value, Quantity):
        return f"{value.value:g} {value.unit}"
    return repr(value)


def _describe_program_input(value: ProgramInput) -> str:
    atom = value.value_type.atom
    detail: str | None = None
    if isinstance(atom, QuantityAtomType):
        detail = atom.unit or atom.dimension
    elif isinstance(atom, EntityAtomType):
        detail = atom.entity_kind
    elif isinstance(atom, PayloadType):
        detail = atom.schema_id
    atom_name = type(atom).__name__
    return f"{atom_name}[{detail}]" if detail is not None else atom_name


def _describe_result(result: ProgramResult) -> str:
    contract = result.contract
    unit = "" if contract.unit is None else f" {contract.unit}"
    entity_axes = ("entity",) if result.entity_set is not None else ()
    axes = ",".join(
        (
            *entity_axes,
            "shot",
            *(
                _describe_result_dimension(dimension)
                for dimension in contract.dimensions
            ),
        )
    )
    return (
        f"{result.acquisition_kind.value} {contract.dtype}{unit} "
        f"on {result.qubit.id}; axes={axes}"
    )


def _describe_result_dimension(dimension: QuantumResultDimension) -> str:
    input_id = dimension.size_input_id
    if input_id is None:
        return dimension.id
    return f"{dimension.id}=${input_id}(max={dimension.maximum_size})"


def _inspection_result_shape(result: ProgramResult) -> str:
    dimensions = result.contract.dimensions
    if not dimensions:
        return ""
    return " dimensions=" + ",".join(
        _describe_result_dimension(dimension) for dimension in dimensions
    )
