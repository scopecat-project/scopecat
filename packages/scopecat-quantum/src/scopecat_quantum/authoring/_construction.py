# pyright: reportPrivateUsage=false
"""Public factories for composing symbolic quantum programs."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from collections.abc import Sequence as SequenceCollection
from dataclasses import replace
from typing import (
    Literal,
    cast,
    get_type_hints,
    overload,
)

from scopecat.authoring import (
    QuantityType,
    ScalarType,
)

from scopecat_quantum._ids import (
    CouplerId,
    GateId,
    PulseProgramId,
    QuantumProgramId,
    QubitId,
)
from scopecat_quantum.acquisitions import (
    INTEGRATED_IQ_RESULT,
    AcquisitionKind,
    QuantumResultContract,
)
from scopecat_quantum.gates import (
    GateDefinition,
    GateParameterDefinition,
    GateParameterKind,
    gate_parameter_label,
)
from scopecat_quantum.pulses import (
    AcquireSignal,
    AnalyticEnvelope,
    DriveSignal,
    EnvelopePhaseReference,
    FluxSignal,
    FrameSignal,
    PlaySignal,
    ReadoutSignal,
)

from ._analysis import (
    _core_input_type,
    _duplicates,
    _element_ir_id,
    _is_integer_input,
    _program_function_argument,
    _program_input_matches_kind,
    _program_input_type,
    _pulse_envelope,
    _require_quantity_expression,
    _summarize_fragment,
    _unique_gate_definitions,
    _validate_realtime_structure,
    _validate_result_repeat_contract,
)
from ._definitions import (
    FragmentDefinition,
    Gate,
    GateImplementationDefinition,
    ProgramFamilyEnvelope,
    PulseTemplateDefinition,
    SingleQubitGate,
    TwoQubitGate,
    _GateImplementationContract,
    _PulseTemplateSource,
    _QuantumFunctionContract,
)
from ._ir import (
    _RESERVED_PROGRAM_PORT_IDS,
    _RESERVED_RESULT_IDS,
    Acquisition,
    CircuitFragment,
    Coupler,
    CouplerSet,
    EntitySetPort,
    Measurement,
    MeasurementResult,
    ProgramFunction,
    ProgramInput,
    ProgramPort,
    ProgramResults,
    PulseElement,
    PulseEnvelope,
    PulseFragment,
    PulseTemplateFunction,
    QuantumFragment,
    QuantumQuantity,
    Qubit,
    QubitPair,
    QubitPairSet,
    QubitSet,
    RepeatCount,
    _ConditionalFragment,
    _DelayFragment,
    _ParallelCouplerEachFragment,
    _ParallelEachFragment,
    _ParallelFragment,
    _ParallelQubitPairEachFragment,
    _PlayFragment,
    _QuantumParallelFragment,
    _QuantumRepeatFragment,
    _QuantumSequenceFragment,
    _RecipeScopeFragment,
    _RepeatFragment,
    _SequenceFragment,
    _ShiftPhaseFragment,
)
from ._programs import (
    Program,
    ProgramDefinition,
)


def qubit(id: str) -> Qubit:
    """Declare one logical qubit handle."""

    return Qubit(ir_id=QubitId(id))


def coupler(id: str) -> Coupler:
    """Declare one logical coupler handle."""

    return Coupler(ir_id=CouplerId(id))


def scalar_input(id: str, kind: GateParameterKind) -> ProgramInput:
    """Declare one typed scalar input port for a symbolic circuit."""

    if not id.strip():
        msg = "circuit input id must be a non-empty string"
        raise ValueError(msg)
    return ProgramInput(_id=id, value_type=_core_input_type(kind))


def input(id: str, value_type: ScalarType) -> ProgramInput:
    """Declare one core-typed scalar input for gate-and-pulse authoring."""

    if not id.strip():
        msg = "quantum input id must be a non-empty string"
        raise ValueError(msg)
    return ProgramInput(
        _id=id,
        value_type=value_type,
    )


def single_qubit_gate(
    id: str,
    *,
    parameters: Mapping[str, GateParameterKind | QuantityType] | None = None,
) -> SingleQubitGate:
    """Declare one hardware-independent single-qubit gate semantic."""

    selected = gate(id, arity=1, parameters=parameters)
    assert isinstance(selected, SingleQubitGate)
    return selected


def two_qubit_gate(
    id: str,
    *,
    parameters: Mapping[str, GateParameterKind | QuantityType] | None = None,
) -> TwoQubitGate:
    """Declare one hardware-independent two-qubit gate semantic."""

    selected = gate(id, arity=2, parameters=parameters)
    assert isinstance(selected, TwoQubitGate)
    return selected


@overload
def gate(
    id: str,
    *,
    arity: Literal[1],
    parameters: Mapping[str, GateParameterKind | QuantityType] | None = None,
) -> SingleQubitGate: ...


@overload
def gate(
    id: str,
    *,
    arity: Literal[2],
    parameters: Mapping[str, GateParameterKind | QuantityType] | None = None,
) -> TwoQubitGate: ...


def gate(
    id: str,
    *,
    arity: Literal[1, 2],
    parameters: Mapping[str, GateParameterKind | QuantityType] | None = None,
) -> Gate:
    """Declare one hardware-independent one- or two-qubit gate semantic."""

    selected: Mapping[str, GateParameterKind | QuantityType] = (
        {} if parameters is None else parameters
    )
    if any(not name.strip() for name in selected):
        msg = "gate parameter ids must be non-empty strings"
        raise ValueError(msg)
    definition = GateDefinition(
        id=GateId(id),
        qubit_arity=arity,
        parameters=tuple(
            GateParameterDefinition(name, kind) for name, kind in selected.items()
        ),
    )
    handle_type = SingleQubitGate if arity == 1 else TwoQubitGate
    return handle_type(definition=definition)


def measure(
    qubit: Qubit,
    /,
    *,
    result: str,
    contract: QuantumResultContract = INTEGRATED_IQ_RESULT,
) -> Measurement:
    """Author one measurement and its acquisition result."""

    if not result.strip():
        msg = "measurement result id must be a non-empty string"
        raise ValueError(msg)
    result_handle = MeasurementResult(
        _id=result,
        _qubit=qubit,
        contract=contract,
    )
    return Measurement(result=result_handle)


def acquire(
    qubit: Qubit,
    /,
    *,
    duration: QuantumQuantity,
    result: str,
    contract: QuantumResultContract = INTEGRATED_IQ_RESULT,
) -> Acquisition:
    """Acquire one physical signal and expose its typed result port."""

    _require_quantity_expression(duration, field="duration", kind="time")
    if not result.strip():
        msg = "acquisition result id must be a non-empty string"
        raise ValueError(msg)
    result_handle = MeasurementResult(
        _id=result,
        _qubit=qubit,
        contract=contract,
    )
    return Acquisition(
        signal=AcquireSignal(qubit.ir_id),
        duration=duration,
        result=result_handle,
    )


def drive(qubit: Qubit, /) -> DriveSignal:
    """Select the logical drive signal for one authored qubit."""

    return DriveSignal(qubit.ir_id)


def flux(element: PulseElement, /) -> FluxSignal:
    """Select the logical flux signal for one authored qubit or coupler."""

    return FluxSignal(_element_ir_id(element))


def readout(qubit: Qubit, /) -> ReadoutSignal:
    """Select the logical readout-stimulus signal for one authored qubit."""

    return ReadoutSignal(qubit.ir_id)


def shift_phase(signal: FrameSignal, phase: QuantumQuantity, /) -> PulseFragment:
    """Advance a drive or readout frame without consuming timeline duration."""

    _require_quantity_expression(phase, field="phase shift", kind="phase")
    return _ShiftPhaseFragment(
        signal=signal,
        phase=phase,
    )


def _close_pulse_template(
    id: str,
    body: QuantumFragment,
    /,
    *,
    elements: SequenceCollection[PulseElement],
    formal_inputs: SequenceCollection[ProgramInput] | None = None,
) -> _PulseTemplateSource:
    facts = _summarize_fragment(body)
    if not facts.pulse_only:
        msg = "pulse_template body must contain only pulse statements"
        raise TypeError(msg)
    if facts.results:
        msg = "pulse templates cannot capture acquisition results"
        raise ValueError(msg)
    raw_elements = tuple(elements)
    element_ids = tuple(_element_ir_id(item) for item in raw_elements)
    if len(set(element_ids)) != len(element_ids):
        msg = "pulse template elements must be unique"
        raise ValueError(msg)

    inputs_by_id: dict[str, ProgramInput] = {}
    for input_handle in facts.inputs:
        existing = inputs_by_id.get(input_handle.id)
        if existing is not None and existing is not input_handle:
            msg = (
                f"pulse template input {input_handle.id!r} is declared by multiple "
                "distinct handles"
            )
            raise ValueError(msg)
        inputs_by_id.setdefault(input_handle.id, input_handle)

    selected_inputs = tuple(inputs_by_id.values())
    if formal_inputs is not None:
        declared_inputs = tuple(formal_inputs)
        unused_inputs = tuple(
            input_handle.id
            for input_handle in declared_inputs
            if all(input_handle is not used for used in selected_inputs)
        )
        if unused_inputs:
            rendered = ", ".join(repr(item) for item in unused_inputs)
            raise ValueError(f"pulse template has unused scalar ports: {rendered}")
        foreign_inputs = tuple(
            input_handle.id
            for input_handle in selected_inputs
            if all(input_handle is not declared for declared in declared_inputs)
        )
        if foreign_inputs:
            rendered = ", ".join(repr(item) for item in foreign_inputs)
            raise ValueError(
                f"pulse template captures undeclared scalar ports: {rendered}"
            )
        selected_inputs = declared_inputs

    formal_ids = set(element_ids)
    foreign_owners = {owner for owner in facts.pulse_owners if owner not in formal_ids}
    if foreign_owners:
        rendered = ", ".join(
            repr(owner.value)
            for owner in sorted(foreign_owners, key=lambda item: item.value)
        )
        msg = f"pulse template contains undeclared formal elements: {rendered}"
        raise ValueError(msg)

    return _PulseTemplateSource(
        ir_id=PulseProgramId(id),
        body=body,
        elements=raw_elements,
        inputs=selected_inputs,
    )


def constant(
    *,
    duration: QuantumQuantity,
    amplitude: QuantumQuantity,
    phase: QuantumQuantity | None = None,
) -> PulseEnvelope:
    """Author a constant analytic envelope with bindable quantities."""

    return _pulse_envelope(
        "constant",
        duration=duration,
        amplitude=amplitude,
        phase=phase,
    )


def gaussian(
    *,
    duration: QuantumQuantity,
    amplitude: QuantumQuantity,
    sigma: QuantumQuantity,
    phase: QuantumQuantity | None = None,
) -> PulseEnvelope:
    """Author a truncated Gaussian envelope with bindable quantities."""

    return _pulse_envelope(
        "gaussian",
        duration=duration,
        amplitude=amplitude,
        sigma=sigma,
        phase=phase,
    )


def cosine_flat_top(
    *,
    duration: QuantumQuantity,
    amplitude: QuantumQuantity,
    rise_duration: QuantumQuantity,
    fall_duration: QuantumQuantity,
    phase: QuantumQuantity | None = None,
) -> PulseEnvelope:
    """Author a flat-top envelope with bindable half-cosine edges."""

    return _pulse_envelope(
        "cosine_flat_top",
        duration=duration,
        amplitude=amplitude,
        rise_duration=rise_duration,
        fall_duration=fall_duration,
        phase=phase,
    )


def drag(
    *,
    duration: QuantumQuantity,
    amplitude: QuantumQuantity,
    sigma: QuantumQuantity,
    beta: QuantumQuantity,
    phase: QuantumQuantity | None = None,
) -> PulseEnvelope:
    """Author Gaussian DRAG as a convenience composition."""

    return derivative_quadrature(
        gaussian(
            duration=duration,
            amplitude=amplitude,
            sigma=sigma,
            phase=phase,
        ),
        beta=beta,
    )


def derivative_quadrature(
    envelope: PulseEnvelope,
    /,
    *,
    beta: QuantumQuantity,
) -> PulseEnvelope:
    """Add ``i * beta * d(envelope) / dt`` to a smooth base envelope."""

    if envelope.kind not in {"gaussian", "cosine_flat_top"}:
        raise TypeError(
            "derivative quadrature requires a Gaussian or cosine-flat-top envelope"
        )
    if envelope.derivative_beta is not None:
        raise ValueError("envelope already has a derivative-quadrature correction")
    if envelope.frequency_offset is not None:
        raise ValueError(
            "apply derivative quadrature before a pulse-local frequency shift"
        )
    _require_quantity_expression(beta, field="beta", kind="time")
    return replace(envelope, derivative_beta=beta)


def frequency_shift(
    envelope: PulseEnvelope,
    /,
    *,
    offset: QuantumQuantity,
    phase_reference: EnvelopePhaseReference = "center",
) -> PulseEnvelope:
    """Apply a pulse-local frequency offset with a reset phase ramp."""

    if envelope.frequency_offset is not None:
        raise ValueError("envelope already has a pulse-local frequency shift")
    if phase_reference not in {"start", "center"}:
        raise ValueError("phase_reference must be either 'start' or 'center'")
    _require_quantity_expression(offset, field="offset", kind="frequency")
    return replace(
        envelope,
        frequency_offset=offset,
        frequency_reference=phase_reference,
    )


def play(
    signal: PlaySignal,
    envelope: PulseEnvelope | AnalyticEnvelope,
    /,
) -> PulseFragment:
    """Play one concrete or symbolic envelope on a logical signal."""

    return _PlayFragment(
        signal=signal,
        envelope=envelope,
    )


def delay(signal: PlaySignal, duration: QuantumQuantity, /) -> PulseFragment:
    """Reserve time on one logical signal."""

    _require_quantity_expression(duration, field="duration", kind="time")
    return _DelayFragment(signal=signal, duration=duration)


def recipe_scope(scope: str, operation: QuantumFragment) -> QuantumFragment:
    """Select recipe parameters for unresolved gates in one subtree.

    Nested scopes take precedence. Measurements and gates with explicit pulse
    implementations retain their existing behavior.
    """
    if not scope.strip():
        raise ValueError("recipe scope must be a non-empty string")
    return _RecipeScopeFragment(scope=scope, body=operation)


@overload
def sequence(
    *operations: CircuitFragment,
) -> CircuitFragment: ...


@overload
def sequence(
    *operations: QuantumFragment,
) -> QuantumFragment: ...


def sequence(
    *operations: QuantumFragment,
) -> QuantumFragment:
    """Compose gate, measurement, and pulse fragments in order."""

    if not operations:
        msg = "sequence requires at least one quantum fragment"
        raise ValueError(msg)
    if all(isinstance(operation, CircuitFragment) for operation in operations):
        return _SequenceFragment(
            operations=cast("tuple[CircuitFragment, ...]", operations),
        )
    return _QuantumSequenceFragment(operations=operations)


@overload
def parallel(
    *branches: CircuitFragment,
    alignment: Literal["start"] = "start",
) -> CircuitFragment: ...


@overload
def parallel(
    *branches: QuantumFragment,
    alignment: Literal["start", "end"] = "start",
) -> QuantumFragment: ...


def parallel(
    *branches: QuantumFragment,
    alignment: Literal["start", "end"] = "start",
) -> QuantumFragment:
    """Compose static branches with common starts or common ends.

    End alignment translates each entire branch after pulse realization; it
    never extends an authored delay. Sequence parallel blocks for aligned layers.
    """

    if alignment not in ("start", "end"):
        raise ValueError("parallel alignment must be start or end")
    if len(branches) < 2:
        msg = "parallel requires at least two quantum branches"
        raise ValueError(msg)
    if any(_summarize_fragment(branch).has_realtime for branch in branches):
        raise ValueError("real-time control is not supported under parallel")
    if alignment == "start" and all(
        isinstance(branch, CircuitFragment) for branch in branches
    ):
        return _ParallelFragment(
            branches=cast("tuple[CircuitFragment, ...]", branches),
        )
    return _QuantumParallelFragment(branches=branches, alignment=alignment)


@overload
def parallel_each(
    entity_set: QubitSet,
    operation: Callable[[Qubit], QuantumFragment],
    /,
) -> QuantumFragment: ...


@overload
def parallel_each(
    entity_set: CouplerSet,
    operation: Callable[[Coupler], QuantumFragment],
    /,
) -> QuantumFragment: ...


@overload
def parallel_each(
    entity_set: QubitPairSet,
    operation: Callable[[QubitPair], QuantumFragment],
    /,
) -> QuantumFragment: ...


def parallel_each(
    entity_set: QubitSet | CouplerSet | QubitPairSet,
    operation: Callable[..., QuantumFragment],
    /,
) -> QuantumFragment:
    """Retain one parallel operation over a variable-size entity set."""

    body = operation(entity_set.item)
    if isinstance(entity_set, QubitSet):
        return _ParallelEachFragment(entity_set=entity_set, operation=body)
    if _summarize_fragment(body).has_realtime:
        raise ValueError("real-time control is supported only for qubit parallel_each")
    if isinstance(entity_set, CouplerSet):
        return _ParallelCouplerEachFragment(entity_set=entity_set, operation=body)
    return _ParallelQubitPairEachFragment(entity_set=entity_set, operation=body)


def switch(
    result: MeasurementResult,
    cases: Mapping[int, QuantumFragment],
    *,
    default: QuantumFragment | None = None,
) -> QuantumFragment:
    """Select one finite result-free branch from an earlier classified result."""

    if result.contract.acquisition_kind is not AcquisitionKind.CLASSIFIED_STATE:
        raise ValueError("switch predicates require classified-state results")
    if not cases:
        raise ValueError("switch requires at least one classified-state case")
    if any(type(state) is not int for state in cases):
        raise TypeError("switch case states must be integers")
    selected_cases = tuple(sorted(cases.items(), key=lambda item: item[0]))
    branches = tuple(branch for _state, branch in selected_cases)
    if default is not None:
        branches = (*branches, default)
    if any(_summarize_fragment(branch).results for branch in branches):
        raise ValueError(
            "switch branches cannot produce acquisition results; place "
            "measurement before or after the switch"
        )
    return _ConditionalFragment(
        predicate=result,
        cases=selected_cases,
        default=default,
    )


@overload
def repeat(
    operation: CircuitFragment,
    count: int | ProgramInput,
    *,
    result_dimension: None = None,
) -> CircuitFragment: ...


@overload
def repeat(
    operation: QuantumFragment,
    count: RepeatCount,
    *,
    result_dimension: str | None = None,
) -> QuantumFragment: ...


def repeat(
    operation: QuantumFragment,
    count: RepeatCount,
    *,
    result_dimension: str | None = None,
) -> QuantumFragment:
    """Repeat a static fragment or retain one bounded result-producing loop."""

    if isinstance(count, ProgramInput):
        if not _is_integer_input(count):
            msg = "repeat count inputs must have integer kind"
            raise TypeError(msg)
    elif type(count) is not int or count < 0:
        msg = "repeat count must be a non-negative integer or integer input"
        raise ValueError(msg)
    if result_dimension is not None and not result_dimension.strip():
        raise ValueError("repeat result dimension must be a non-empty string")

    facts = _summarize_fragment(operation)
    if facts.results:
        if result_dimension is None:
            raise ValueError(
                "result-producing repeats require result_dimension and matching "
                "result contracts"
            )
        if isinstance(count, int) and count == 0:
            raise ValueError("result-producing repeat count must be positive")
        if facts.result_repeat_dimension_ids:
            raise ValueError(
                "result-producing repeats cannot be nested; combine repeated "
                "rounds into one declared result dimension"
            )
        _validate_result_repeat_contract(
            operation,
            count=count,
            result_dimension_id=result_dimension,
        )
        return _QuantumRepeatFragment(
            operation=operation,
            count=count,
            result_dimension_id=result_dimension,
        )
    if result_dimension is not None:
        raise ValueError("result-free repeats cannot declare a result dimension")
    if isinstance(operation, CircuitFragment):
        return _RepeatFragment(
            operation=operation,
            count=count,
        )
    return _QuantumRepeatFragment(
        operation=operation,
        count=count,
        result_dimension_id=None,
    )


@overload
def fragment[**P](
    definition: Callable[P, QuantumFragment],
    /,
    *,
    envelope: ProgramFamilyEnvelope,
    id: str | None = None,
) -> FragmentDefinition[P]: ...


@overload
def fragment[**P](
    definition: None = None,
    /,
    *,
    envelope: ProgramFamilyEnvelope,
    id: str | None = None,
) -> Callable[[Callable[P, QuantumFragment]], FragmentDefinition[P]]: ...


def fragment[**P](
    definition: Callable[P, QuantumFragment] | None = None,
    /,
    *,
    envelope: ProgramFamilyEnvelope,
    id: str | None = None,
) -> (
    FragmentDefinition[P]
    | Callable[[Callable[P, QuantumFragment]], FragmentDefinition[P]]
):
    """Define a bounded result-free family expanded from concrete point inputs."""

    def decorate(fn: Callable[P, QuantumFragment]) -> FragmentDefinition[P]:
        return _fragment_from_function(fn, envelope=envelope, id=id)

    return decorate(definition) if definition is not None else decorate


@overload
def pulse_template[**P](
    definition: Callable[P, QuantumFragment],
    /,
    *,
    id: str | None = None,
) -> PulseTemplateDefinition[P]: ...


@overload
def pulse_template[**P](
    definition: None = None,
    /,
    *,
    id: str | None = None,
) -> Callable[[Callable[P, QuantumFragment]], PulseTemplateDefinition[P]]: ...


def pulse_template[**P](
    definition: Callable[P, QuantumFragment] | None = None,
    /,
    *,
    id: str | None = None,
) -> (
    PulseTemplateDefinition[P]
    | Callable[[Callable[P, QuantumFragment]], PulseTemplateDefinition[P]]
):
    """Define a reusable pulse fragment from a symbolic Python function."""

    def decorate(fn: Callable[P, QuantumFragment]) -> PulseTemplateDefinition[P]:
        return _pulse_template_from_function(fn, id=id)

    return decorate(definition) if definition is not None else decorate


def implementation[**P](
    *,
    of: Gate,
    candidate: str | None = None,
    id: str | None = None,
) -> Callable[
    [Callable[P, QuantumFragment]],
    GateImplementationDefinition[P],
]:
    """Define a fixed semantic gate and its pulse implementation together."""

    def decorate(
        fn: Callable[P, QuantumFragment],
    ) -> GateImplementationDefinition[P]:
        return _gate_implementation_from_function(
            fn,
            gate=of,
            candidate=candidate,
            id=id,
        )

    return decorate


@overload
def program(
    definition: ProgramFunction,
    /,
    *,
    id: str | None = None,
) -> ProgramDefinition: ...


@overload
def program(
    definition: None = None,
    /,
    *,
    id: str | None = None,
) -> Callable[[ProgramFunction], ProgramDefinition]: ...


def program(
    definition: ProgramFunction | None = None,
    /,
    *,
    id: str | None = None,
) -> ProgramDefinition | Callable[[ProgramFunction], ProgramDefinition]:
    """Define a quantum program from a symbolic Python function."""

    def decorate(fn: ProgramFunction) -> ProgramDefinition:
        return _program_from_function(fn, id=id)

    return decorate(definition) if definition is not None else decorate


def _close_program(
    id: str,
    body: QuantumFragment,
    *,
    elements: SequenceCollection[PulseElement] = (),
    entity_sets: SequenceCollection[EntitySetPort] = (),
    formal_inputs: SequenceCollection[ProgramInput] | None = None,
    description: str | None = None,
) -> Program:
    ir_id = QuantumProgramId(id)
    _validate_realtime_structure(body)
    facts = _summarize_fragment(body)
    formal_elements = tuple(elements)
    formal_entity_sets = tuple(entity_sets)
    element_ids = tuple(element.id for element in formal_elements)
    if len(element_ids) != len(set(element_ids)):
        raise ValueError("quantum program element ids must be unique")
    used_elements = {(type(element), element.id) for element in facts.element_uses}
    unused_elements = tuple(
        element.id
        for element in formal_elements
        if (type(element), element.id) not in used_elements
    )
    if unused_elements:
        rendered = ", ".join(repr(item) for item in unused_elements)
        raise ValueError(f"quantum program has unused formal elements: {rendered}")
    used_entity_sets = {entity_set.id for entity_set in facts.entity_sets}
    unused_entity_sets = tuple(
        entity_set.id
        for entity_set in formal_entity_sets
        if entity_set.id not in used_entity_sets
    )
    if unused_entity_sets:
        rendered = ", ".join(repr(item) for item in unused_entity_sets)
        raise ValueError(f"quantum program has unused entity-set ports: {rendered}")
    foreign_entity_sets = sorted(
        used_entity_sets - {entity_set.id for entity_set in formal_entity_sets}
    )
    if foreign_entity_sets:
        rendered = ", ".join(repr(item) for item in foreign_entity_sets)
        raise ValueError(
            f"quantum program captures undeclared entity-set ports: {rendered}"
        )
    if formal_inputs is not None:
        formal_element_keys = {
            (type(element), element.id) for element in formal_elements
        }
        foreign_elements = sorted(
            {
                (type(element), element.id)
                for element in facts.element_uses
                if (type(element), element.id) not in formal_element_keys
            },
            key=lambda item: (item[0].__name__, item[1]),
        )
        if foreign_elements:
            rendered = ", ".join(
                repr(element_id) for _type, element_id in foreign_elements
            )
            raise ValueError(
                f"quantum program captures undeclared formal elements: {rendered}"
            )
    collected_inputs = facts.inputs
    inputs_by_id: dict[str, ProgramInput] = {}
    contracts_by_id: dict[str, ScalarType] = {}
    repeat_input_ids = {input_handle.id for input_handle in facts.repeat_inputs}
    for input_handle in collected_inputs:
        existing_handle = inputs_by_id.get(input_handle.id)
        if existing_handle is not None and existing_handle is not input_handle:
            msg = (
                f"quantum input {input_handle.id!r} is declared by multiple "
                "distinct handles"
            )
            raise ValueError(msg)
        contract = _program_input_type(
            input_handle,
            non_negative=input_handle.id in repeat_input_ids,
        )
        existing_contract = contracts_by_id.get(input_handle.id)
        if existing_contract is not None and existing_contract != contract:
            msg = f"quantum input {input_handle.id!r} has conflicting value types"
            raise ValueError(msg)
        inputs_by_id.setdefault(input_handle.id, input_handle)
        contracts_by_id.setdefault(input_handle.id, contract)

    selected_inputs = tuple(inputs_by_id.values())
    if formal_inputs is not None:
        declared_inputs = tuple(formal_inputs)
        unused_inputs = tuple(
            input_handle.id
            for input_handle in declared_inputs
            if all(input_handle is not used for used in selected_inputs)
        )
        if unused_inputs:
            rendered = ", ".join(repr(item) for item in unused_inputs)
            raise ValueError(f"quantum program has unused scalar ports: {rendered}")
        foreign_inputs = tuple(
            input_handle.id
            for input_handle in selected_inputs
            if all(input_handle is not declared for declared in declared_inputs)
        )
        if foreign_inputs:
            rendered = ", ".join(repr(item) for item in foreign_inputs)
            raise ValueError(
                f"quantum program captures undeclared scalar ports: {rendered}"
            )
        selected_inputs = declared_inputs

    selected_input_ids = {input_handle.id for input_handle in selected_inputs}
    entity_set_ids = {entity_set.id for entity_set in formal_entity_sets}
    conflicting_ports = sorted(
        (set(element_ids) & selected_input_ids)
        | (set(element_ids) & entity_set_ids)
        | (selected_input_ids & entity_set_ids)
    )
    if conflicting_ports:
        rendered = ", ".join(repr(item) for item in conflicting_ports)
        raise ValueError(f"quantum program has conflicting port ids: {rendered}")
    reserved_ports = sorted(
        (set(element_ids) | entity_set_ids | selected_input_ids)
        & _RESERVED_PROGRAM_PORT_IDS
    )
    if reserved_ports:
        rendered = ", ".join(repr(item) for item in reserved_ports)
        raise ValueError(f"quantum program uses reserved port ids: {rendered}")

    results = facts.results
    duplicate_results = _duplicates(result.id for result in results)
    if duplicate_results:
        rendered = ", ".join(repr(item) for item in duplicate_results)
        msg = f"quantum program has duplicate result ids: {rendered}"
        raise ValueError(msg)
    reserved_results = sorted({result.id for result in results} & _RESERVED_RESULT_IDS)
    if reserved_results:
        rendered = ", ".join(repr(item) for item in reserved_results)
        raise ValueError(f"quantum program uses reserved result ids: {rendered}")

    _unique_gate_definitions(facts.gate_definitions)

    return Program(
        ir_id=ir_id,
        body=body,
        elements=formal_elements,
        entity_sets=formal_entity_sets,
        inputs=selected_inputs,
        results=ProgramResults(results),
        description=description,
    )


def _quantum_function_contract(
    fn: Callable[..., QuantumFragment],
    *,
    kind: str,
    allow_entity_sets: bool = False,
) -> _QuantumFunctionContract:
    signature = inspect.signature(fn)
    hints = cast("Mapping[str, object]", get_type_hints(fn, include_extras=True))
    parameters: list[ProgramPort] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise TypeError(f"{kind} functions require named parameters")
        if cast("object", parameter.default) is not inspect.Parameter.empty:
            raise TypeError(f"{kind} ports cannot declare Python defaults")
        annotation = hints.get(
            parameter.name,
            cast("object", parameter.annotation),
        )
        argument = _program_function_argument(parameter.name, annotation)
        if (
            isinstance(argument, QubitSet | CouplerSet | QubitPairSet)
            and not allow_entity_sets
        ):
            raise TypeError(f"{kind} functions cannot declare entity-set ports")
        parameters.append(argument)
    return _QuantumFunctionContract(signature, tuple(parameters))


def _fragment_from_function[**P](
    fn: Callable[P, QuantumFragment],
    *,
    envelope: ProgramFamilyEnvelope,
    id: str | None,
) -> FragmentDefinition[P]:
    contract = _quantum_function_contract(fn, kind="quantum fragment")
    selected_id = id or f"{fn.__module__}.{fn.__qualname__}"
    if not selected_id.strip():
        raise ValueError("quantum fragment id must be non-empty")
    return FragmentDefinition(
        id=selected_id,
        envelope=envelope,
        _definition=fn,
        _contract=contract,
    )


def _pulse_template_from_function[**P](
    fn: Callable[P, QuantumFragment],
    *,
    id: str | None,
) -> PulseTemplateDefinition[P]:
    contract = _quantum_function_contract(fn, kind="pulse template")
    arguments = contract.arguments
    result = cast("PulseTemplateFunction", fn)(**arguments)
    selected_id = id or f"{fn.__module__}.{fn.__qualname__}"
    declaration = _close_pulse_template(
        selected_id,
        result,
        elements=contract.elements,
        formal_inputs=contract.inputs,
    )
    return PulseTemplateDefinition(
        declaration,
        fn,
        contract,
    )


def _gate_implementation_from_function[**P](
    fn: Callable[P, QuantumFragment],
    *,
    gate: Gate,
    candidate: str | None,
    id: str | None,
) -> GateImplementationDefinition[P]:
    if candidate is not None and not candidate.strip():
        raise ValueError("implementation candidate must be a non-empty string")
    template = _pulse_template_from_function(fn, id=id)
    contract = _gate_implementation_contract(template, gate)
    return GateImplementationDefinition(
        template,
        gate=gate,
        candidate=candidate,
        contract=contract,
    )


def _gate_implementation_contract[**P](
    template: PulseTemplateDefinition[P],
    gate: Gate,
) -> _GateImplementationContract:
    operand_count = 1 if isinstance(gate, SingleQubitGate) else 2
    operands = template.parameters[:operand_count]
    if len(operands) != operand_count or any(
        not isinstance(operand, Qubit) for operand in operands
    ):
        msg = (
            f"implementation for gate {gate.id!r} requires "
            f"{operand_count} leading qubits"
        )
        raise TypeError(msg)
    remaining = template.parameters[operand_count:]
    if any(isinstance(parameter, Qubit) for parameter in remaining):
        msg = "implementation resources after gate operands must be couplers"
        raise TypeError(msg)
    inputs = {
        parameter.id: parameter
        for parameter in remaining
        if isinstance(parameter, ProgramInput)
    }
    missing = tuple(
        parameter.id for parameter in gate.parameters if parameter.id not in inputs
    )
    if missing:
        rendered = ", ".join(repr(item) for item in missing)
        raise TypeError(
            f"implementation for gate {gate.id!r} is missing parameters: {rendered}"
        )
    for parameter in gate.parameters:
        input_handle = inputs[parameter.id]
        if not _program_input_matches_kind(input_handle, parameter.kind):
            raise TypeError(
                f"implementation for gate {gate.id!r} parameter "
                f"{parameter.id!r} requires {gate_parameter_label(parameter.kind)!r}"
            )
    return _GateImplementationContract(
        signature=inspect.signature(template),
        operands=tuple(cast("Qubit", operand).id for operand in operands),
        resources=tuple(
            parameter.id for parameter in remaining if isinstance(parameter, Coupler)
        ),
        arguments=tuple(parameter.id for parameter in gate.parameters),
    )


def _program_from_function(
    fn: ProgramFunction,
    *,
    id: str | None,
) -> ProgramDefinition:
    contract = _quantum_function_contract(
        fn,
        kind="quantum program",
        allow_entity_sets=True,
    )
    arguments = contract.arguments
    result = fn(**arguments)
    declaration = _close_program(
        id or f"{fn.__module__}.{fn.__qualname__}",
        result,
        elements=contract.elements,
        entity_sets=contract.entity_sets,
        formal_inputs=contract.inputs,
        description=inspect.getdoc(fn),
    )
    return ProgramDefinition(
        declaration,
        fn,
        contract,
    )
