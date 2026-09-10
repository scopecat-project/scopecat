# pyright: reportPrivateUsage=false
"""Binding and lowering from symbolic fragments to verified quantum IR."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import (
    cast,
)

from scopecat import Quantity
from scopecat.kernel.entity import EntityRef
from scopecat.program.value_types import ValueValidationError, coerce_literal

from scopecat_quantum._ids import (
    AcquisitionSlotId,
    CircuitOperationId,
    CouplerId,
    PulseEventId,
    PulseProgramId,
    QubitId,
)
from scopecat_quantum.acquisitions import (
    QuantumResultContract,
    QuantumResultDimension,
)
from scopecat_quantum.circuits import Measure
from scopecat_quantum.gates import (
    GateArgument,
    GateArgumentValue,
    GateCall,
    GateDefinition,
)
from scopecat_quantum.programs import (
    Conditional as IrQuantumConditional,
)
from scopecat_quantum.programs import (
    ImplementedGate,
    PulseBlock,
    QuantumNode,
    QuantumProgramIR,
    VerifiedQuantumProgram,
    verify_quantum_program,
)
from scopecat_quantum.programs import Parallel as IrQuantumParallel
from scopecat_quantum.programs import ParallelEach as IrQuantumParallelEach
from scopecat_quantum.programs import (
    Repeat as IrQuantumRepeat,
)
from scopecat_quantum.programs import Sequence as IrQuantumSequence
from scopecat_quantum.pulses import (
    Acquire,
    AcquireSignal,
    AcquisitionSlot,
    AnalyticEnvelope,
    Constant,
    CosineFlatTop,
    Delay,
    DerivativeQuadrature,
    FrameSignal,
    FrequencyShift,
    Gaussian,
    Play,
    PlaySignal,
    PulseInstruction,
    PulseProgram,
    ShiftPhase,
)
from scopecat_quantum.pulses import Parallel as IrPulseParallel
from scopecat_quantum.pulses import Sequence as IrPulseSequence

from ._analysis import (
    _operation_id,
    _program_input_type,
    _pulse_envelope_parts,
    _summarize_fragment,
    _unique_gate_definitions,
    _validate_realtime_structure,
    program_port_type,
)
from ._definitions import (
    _substitute_signal,
)
from ._expansion import (
    _expand_fragment_calls,
)
from ._expressions import resolve_expression
from ._ir import (
    Acquisition,
    CouplerSet,
    ElementBindings,
    Measurement,
    ProgramBindingError,
    ProgramInput,
    ProgramResults,
    PulseEnvelope,
    QuantumFragment,
    QuantumQuantity,
    Qubit,
    QubitPairSet,
    QubitSet,
    RepeatCount,
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
    _PulseTemplateCallFragment,
    _QuantumParallelFragment,
    _QuantumRepeatFragment,
    _QuantumSequenceFragment,
    _RepeatFragment,
    _SequenceFragment,
    _ShiftPhaseFragment,
)
from ._programs import (
    Program,
)


@dataclass(frozen=True, slots=True, repr=False)
class BoundProgram:
    """A declaration bound to concrete values and verified source IR."""

    declaration: Program
    verified: VerifiedQuantumProgram

    @property
    def program(self) -> QuantumProgramIR:
        """Return the concrete unified IR accepted by pulse refinement."""

        return self.verified.program

    @property
    def gate_definitions(self) -> tuple[GateDefinition, ...]:
        """Return the verified logical gate catalog."""

        return self.verified.unresolved.gate_definitions

    @property
    def results(self) -> ProgramResults:
        """Return declared measurement and acquisition results in source order."""

        return self.declaration.results


def materialize_pulse_recipe_body(
    id: str,
    body: QuantumFragment,
    /,
    *,
    measurement: tuple[QubitId, QuantumResultContract] | None = None,
) -> PulseProgram:
    """Close one concrete recipe body with framework-owned local identities."""

    expanded = _expand_fragment_calls(body, {})
    facts = _summarize_fragment(expanded)
    if facts.has_realtime:
        raise ValueError(
            "pulse recipe bodies cannot contain target-visible real-time control"
        )
    if not facts.pulse_only:
        msg = "pulse recipe bodies must contain only pulse statements"
        raise TypeError(msg)
    if facts.inputs:
        rendered = ", ".join(repr(value.id) for value in facts.inputs)
        msg = f"pulse recipe captures unbound inputs: {rendered}"
        raise ValueError(msg)

    slots: tuple[AcquisitionSlot, ...] = ()
    if measurement is None:
        if facts.results:
            msg = "gate pulse recipes cannot acquire results"
            raise ValueError(msg)
    else:
        qubit_id, result_contract = measurement
        if len(facts.results) != 1:
            msg = "measurement pulse recipes must acquire exactly one result"
            raise ValueError(msg)
        result = facts.results[0]
        if result.qubit.ir_id != qubit_id:
            msg = "measurement pulse recipe result must belong to its mapped qubit"
            raise ValueError(msg)
        if result.contract != result_contract:
            msg = "measurement pulse recipe result contract must match its declaration"
            raise ValueError(msg)
        slots = (
            AcquisitionSlot(
                id=result.acquisition_slot_id,
                contract=result_contract,
                signal=AcquireSignal(qubit_id),
            ),
        )

    return PulseProgram(
        id=PulseProgramId(id),
        body=_bind_pulse_fragment(
            expanded,
            {},
            element_bindings={},
            path=(),
        ),
        acquisition_slots=slots,
    )


def bind(
    declaration: Program,
    bindings: Mapping[str, object] | None = None,
) -> BoundProgram:
    """Bind all inputs and return verified unified quantum IR."""

    selected_bindings: Mapping[str, object] = {} if bindings is None else bindings
    expected = {port.id for port in declaration.ports}
    supplied = set(selected_bindings)
    missing = sorted(expected - supplied)
    unknown = sorted(supplied - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(repr(item) for item in missing))
        if unknown:
            details.append("unknown " + ", ".join(repr(item) for item in unknown))
        raise ProgramBindingError("invalid program bindings: " + "; ".join(details))

    repeat_input_ids = {
        input_handle.id
        for input_handle in _summarize_fragment(declaration.body).repeat_inputs
    }
    concrete_bindings: dict[str, object] = {}
    element_bindings: dict[QubitId | CouplerId, QubitId | CouplerId] = {}
    for element in declaration.elements:
        value_type = program_port_type(element)
        try:
            selected = coerce_literal(
                value_type,
                selected_bindings[element.id],
                path=("bindings", element.id),
            )
        except ValueValidationError as error:
            raise ProgramBindingError(str(error)) from error
        if not isinstance(selected, EntityRef):
            raise AssertionError("entity program ports normalize to EntityRef")
        concrete_bindings[element.id] = selected
        element_bindings[element.ir_id] = (
            QubitId(selected.id)
            if isinstance(element, Qubit)
            else CouplerId(selected.id)
        )
    for entity_set in declaration.entity_sets:
        selected = selected_bindings[entity_set.id]
        if isinstance(entity_set, QubitSet):
            bound_set = _bound_entity_set(entity_set, selected, column="qubit")
        elif isinstance(entity_set, CouplerSet):
            bound_set = _bound_entity_set(entity_set, selected, column="coupler")
        else:
            bound_set = _bound_qubit_pair_set(entity_set, selected)
        concrete_bindings[entity_set.id] = bound_set
    for input_handle in declaration.inputs:
        value_type = _program_input_type(
            input_handle,
            non_negative=input_handle.id in repeat_input_ids,
        )
        try:
            concrete_bindings[input_handle.id] = coerce_literal(
                value_type,
                selected_bindings[input_handle.id],
                path=("bindings", input_handle.id),
            )
        except ValueValidationError as error:
            raise ProgramBindingError(str(error)) from error

    expanded_body = _expand_fragment_calls(declaration.body, concrete_bindings)
    _validate_realtime_structure(expanded_body)
    gate_definitions = _bound_gate_definitions(expanded_body, concrete_bindings)
    concrete = QuantumProgramIR(
        id=declaration.ir_id,
        body=_bind_quantum_fragment(
            expanded_body,
            concrete_bindings,
            element_bindings=element_bindings,
            path=("body",),
        ),
    )
    verified = verify_quantum_program(concrete, gate_definitions)
    return BoundProgram(
        declaration=declaration,
        verified=verified,
    )


def _bind_circuit_operation(
    fragment: _GateFragment | Measurement,
    bindings: Mapping[str, object],
    *,
    element_bindings: ElementBindings,
    path: tuple[str, ...],
    acquisition_scope: tuple[str, ...],
) -> GateCall | Measure:
    if isinstance(fragment, _GateFragment):
        return GateCall(
            id=CircuitOperationId(_operation_id(path, "gate")),
            gate_id=fragment.gate.definition.id,
            qubits=tuple(
                _bound_qubit_id(qubit, element_bindings) for qubit in fragment.qubits
            ),
            arguments=tuple(
                GateArgument(
                    argument_id,
                    cast(
                        "GateArgumentValue",
                        resolve_expression(value, bindings),
                    ),
                )
                for argument_id, value in fragment.arguments
            ),
        )
    result = fragment.result
    return Measure(
        id=CircuitOperationId(_operation_id(path, "measure")),
        qubit=_bound_qubit_id(result.qubit, element_bindings),
        acquisition_slot_id=result.acquisition_slot_id.prefixed(*acquisition_scope),
        contract=_bind_result_contract(result.contract, bindings),
    )


def _bind_quantum_fragment(
    fragment: QuantumFragment,
    bindings: Mapping[str, object],
    *,
    element_bindings: ElementBindings,
    path: tuple[str, ...],
    acquisition_scope: tuple[str, ...] = (),
) -> QuantumNode:
    if isinstance(fragment, _ExpandedFragment):
        return _bind_quantum_fragment(
            fragment.body,
            bindings,
            element_bindings=element_bindings,
            path=(*path, f"fragment[{fragment.definition_id}]"),
            acquisition_scope=acquisition_scope,
        )
    if isinstance(fragment, _FragmentCall):
        raise AssertionError("quantum fragment calls must expand before binding")
    if isinstance(fragment, _GateFragment | Measurement):
        return _bind_circuit_operation(
            fragment,
            bindings,
            element_bindings=element_bindings,
            path=path,
            acquisition_scope=acquisition_scope,
        )
    if isinstance(fragment, _ImplementedGateFragment):
        call = cast(
            "GateCall",
            _bind_circuit_operation(
                fragment.gate,
                bindings,
                element_bindings=element_bindings,
                path=(*path, "logical"),
                acquisition_scope=acquisition_scope,
            ),
        )
        pulse_template_id = (
            fragment.pulse.template.ir_id
            if isinstance(fragment.pulse, _PulseTemplateCallFragment)
            else PulseProgramId(_operation_id(path, "implementation-template"))
        )
        pulse_body = (
            fragment.pulse.body
            if isinstance(fragment.pulse, _PulseTemplateCallFragment)
            else fragment.pulse
        )
        return ImplementedGate(
            call=call,
            pulse_template=PulseProgram(
                id=pulse_template_id,
                body=_bind_pulse_fragment(
                    pulse_body,
                    bindings,
                    element_bindings=element_bindings,
                    path=("implementation",),
                ),
            ),
            candidate_id=fragment.candidate_id,
        )
    if isinstance(fragment, Acquisition):
        slot_id = fragment.result.acquisition_slot_id.prefixed(*acquisition_scope)
        bound_acquire = _bind_pulse_fragment(
            fragment,
            bindings,
            element_bindings=element_bindings,
            path=(),
            acquisition_slot_id=slot_id,
        )
        if not isinstance(bound_acquire, Acquire):
            raise AssertionError("acquisition binding must produce Acquire")
        template = PulseProgram(
            id=PulseProgramId(_operation_id(path, "acquire-template")),
            body=bound_acquire,
            acquisition_slots=(
                AcquisitionSlot(
                    id=slot_id,
                    contract=_bind_result_contract(fragment.result.contract, bindings),
                    signal=bound_acquire.signal,
                ),
            ),
        )
        return PulseBlock(
            id=CircuitOperationId(_operation_id(path, "acquire")),
            pulse_template=template,
            acquisition_slot_bindings=((slot_id, slot_id),),
        )
    if isinstance(fragment, _PulseTemplateCallFragment):
        return PulseBlock(
            id=CircuitOperationId(_operation_id(path, "pulse-template-call")),
            pulse_template=PulseProgram(
                id=fragment.template.ir_id,
                body=_bind_pulse_fragment(
                    fragment.body,
                    bindings,
                    element_bindings=element_bindings,
                    path=(),
                ),
            ),
        )
    if isinstance(
        fragment,
        _PlayFragment | _DelayFragment | _ShiftPhaseFragment,
    ):
        return PulseBlock(
            id=CircuitOperationId(_operation_id(path, "pulse")),
            pulse_template=PulseProgram(
                id=PulseProgramId(_operation_id(path, "pulse-template")),
                body=_bind_pulse_fragment(
                    fragment,
                    bindings,
                    element_bindings=element_bindings,
                    path=(),
                ),
            ),
        )
    if isinstance(fragment, _ParallelEachFragment):
        entities = bindings[fragment.entity_set.id]
        if not isinstance(entities, tuple):
            raise AssertionError("verified qubit-set bindings must be tuples")
        entity_refs = cast("tuple[EntityRef, ...]", entities)
        return IrQuantumParallelEach(
            entity_set_id=fragment.entity_set.id,
            item_id=fragment.entity_set.item.ir_id,
            entity_ids=tuple(QubitId(entity.id) for entity in entity_refs),
            operation=_bind_quantum_fragment(
                fragment.operation,
                bindings,
                element_bindings={
                    **element_bindings,
                    fragment.entity_set.item.ir_id: fragment.entity_set.item.ir_id,
                },
                path=(*path, "parallel_each-body"),
                acquisition_scope=acquisition_scope,
            ),
        )
    if isinstance(fragment, _ParallelCouplerEachFragment):
        entities = cast("tuple[EntityRef, ...]", bindings[fragment.entity_set.id])
        return IrQuantumParallel(
            tuple(
                _bind_quantum_fragment(
                    fragment.operation,
                    bindings,
                    element_bindings={
                        **element_bindings,
                        fragment.entity_set.item.ir_id: CouplerId(entity.id),
                    },
                    path=(*path, f"parallel_each[{index}]"),
                    acquisition_scope=acquisition_scope,
                )
                for index, entity in enumerate(entities)
            )
        )
    if isinstance(fragment, _ParallelQubitPairEachFragment):
        pairs = cast(
            "tuple[tuple[EntityRef, EntityRef, EntityRef], ...]",
            bindings[fragment.entity_set.id],
        )
        item = fragment.entity_set.item
        return IrQuantumParallel(
            tuple(
                _bind_quantum_fragment(
                    fragment.operation,
                    bindings,
                    element_bindings={
                        **element_bindings,
                        item.left.ir_id: QubitId(left.id),
                        item.right.ir_id: QubitId(right.id),
                        item.coupler.ir_id: CouplerId(coupler.id),
                    },
                    path=(*path, f"parallel_each[{index}]"),
                    acquisition_scope=acquisition_scope,
                )
                for index, (left, right, coupler) in enumerate(pairs)
            )
        )
    if isinstance(fragment, _ConditionalFragment):
        return IrQuantumConditional(
            predicate=fragment.predicate.acquisition_slot_id.prefixed(
                *acquisition_scope
            ),
            cases=tuple(
                (
                    state,
                    _bind_quantum_fragment(
                        branch,
                        bindings,
                        element_bindings=element_bindings,
                        path=(*path, f"switch[{state}]"),
                        acquisition_scope=acquisition_scope,
                    ),
                )
                for state, branch in fragment.cases
            ),
            default=(
                None
                if fragment.default is None
                else _bind_quantum_fragment(
                    fragment.default,
                    bindings,
                    element_bindings=element_bindings,
                    path=(*path, "switch[default]"),
                    acquisition_scope=acquisition_scope,
                )
            ),
        )
    if isinstance(fragment, _SequenceFragment | _QuantumSequenceFragment):
        return IrQuantumSequence(
            tuple(
                _bind_quantum_fragment(
                    operation,
                    bindings,
                    element_bindings=element_bindings,
                    path=(*path, f"sequence[{index}]"),
                    acquisition_scope=acquisition_scope,
                )
                for index, operation in enumerate(fragment.operations)
            )
        )
    if isinstance(fragment, _ParallelFragment | _QuantumParallelFragment):
        return IrQuantumParallel(
            tuple(
                _bind_quantum_fragment(
                    branch,
                    bindings,
                    element_bindings=element_bindings,
                    path=(*path, f"parallel[{index}]"),
                    acquisition_scope=acquisition_scope,
                )
                for index, branch in enumerate(fragment.branches)
            ),
            alignment=fragment.alignment
            if isinstance(fragment, _QuantumParallelFragment)
            else "start",
        )
    if isinstance(fragment, _RepeatFragment | _QuantumRepeatFragment):
        count = _bound_repeat_count(fragment.count, bindings)
        return IrQuantumRepeat(
            operation=(
                IrQuantumSequence(())
                if count == 0
                else _bind_quantum_fragment(
                    fragment.operation,
                    bindings,
                    element_bindings=element_bindings,
                    path=(*path, "repeat-body"),
                    acquisition_scope=acquisition_scope,
                )
            ),
            count=count,
            result_dimension_id=(
                fragment.result_dimension_id
                if isinstance(fragment, _QuantumRepeatFragment)
                else None
            ),
        )
    msg = f"unsupported quantum fragment {type(fragment).__name__}"
    raise TypeError(msg)


def _bind_pulse_fragment(
    fragment: QuantumFragment,
    bindings: Mapping[str, object],
    *,
    element_bindings: ElementBindings,
    path: tuple[str, ...],
    acquisition_slot_id: AcquisitionSlotId | None = None,
) -> PulseInstruction:
    if isinstance(fragment, _ExpandedFragment):
        return _bind_pulse_fragment(
            fragment.body,
            bindings,
            element_bindings=element_bindings,
            path=(*path, f"fragment[{fragment.definition_id}]"),
            acquisition_slot_id=acquisition_slot_id,
        )
    if isinstance(fragment, _FragmentCall):
        raise AssertionError("quantum fragment calls must expand before binding")
    if isinstance(fragment, _PlayFragment):
        return Play(
            id=PulseEventId("play", scope=path),
            signal=cast(
                "PlaySignal",
                _substitute_signal(fragment.signal, element_bindings),
            ),
            envelope=_bind_envelope(fragment.envelope, bindings),
        )
    if isinstance(fragment, _DelayFragment):
        return Delay(
            id=PulseEventId("delay", scope=path),
            signal=cast(
                "PlaySignal",
                _substitute_signal(fragment.signal, element_bindings),
            ),
            duration=_bound_quantity(fragment.duration, bindings),
        )
    if isinstance(fragment, Acquisition):
        return Acquire(
            id=PulseEventId("acquire", scope=path),
            signal=cast(
                "AcquireSignal",
                _substitute_signal(fragment.signal, element_bindings),
            ),
            slot_id=(
                fragment.result.acquisition_slot_id
                if acquisition_slot_id is None
                else acquisition_slot_id
            ),
            duration=_bound_quantity(fragment.duration, bindings),
        )
    if isinstance(fragment, _ShiftPhaseFragment):
        return ShiftPhase(
            id=PulseEventId("shift-phase", scope=path),
            signal=cast(
                "FrameSignal",
                _substitute_signal(fragment.signal, element_bindings),
            ),
            phase=_bound_quantity(fragment.phase, bindings),
        )
    if isinstance(fragment, _PulseTemplateCallFragment):
        return _bind_pulse_fragment(
            fragment.body,
            bindings,
            element_bindings=element_bindings,
            path=path,
        )
    if isinstance(fragment, _QuantumSequenceFragment):
        return IrPulseSequence(
            tuple(
                _bind_pulse_fragment(
                    operation,
                    bindings,
                    element_bindings=element_bindings,
                    path=(*path, f"sequence[{index}]"),
                )
                for index, operation in enumerate(fragment.operations)
            )
        )
    if isinstance(fragment, _QuantumParallelFragment):
        return IrPulseParallel(
            tuple(
                _bind_pulse_fragment(
                    branch,
                    bindings,
                    element_bindings=element_bindings,
                    path=(*path, f"parallel[{index}]"),
                )
                for index, branch in enumerate(fragment.branches)
            ),
            alignment=fragment.alignment,
        )
    if isinstance(fragment, _QuantumRepeatFragment):
        if (
            fragment.result_dimension_id is not None
            or _summarize_fragment(fragment.operation).has_realtime
        ):
            raise TypeError(
                "real-time control cannot be materialized as a pulse-template body"
            )
        count = _bound_repeat_count(fragment.count, bindings)
        return IrPulseSequence(
            tuple(
                _bind_pulse_fragment(
                    fragment.operation,
                    bindings,
                    element_bindings=element_bindings,
                    path=(*path, f"repeat[{index}]"),
                )
                for index in range(count)
            )
        )
    msg = f"unsupported pulse fragment {type(fragment).__name__}"
    raise TypeError(msg)


def _bind_envelope(
    envelope: PulseEnvelope | AnalyticEnvelope,
    bindings: Mapping[str, object],
) -> AnalyticEnvelope:
    if isinstance(
        envelope,
        Constant | Gaussian | CosineFlatTop | DerivativeQuadrature | FrequencyShift,
    ):
        return envelope
    (
        kind,
        raw_duration,
        raw_amplitude,
        raw_sigma,
        raw_derivative_beta,
        raw_rise_duration,
        raw_fall_duration,
        raw_phase,
        raw_frequency_offset,
        raw_frequency_reference,
    ) = _pulse_envelope_parts(envelope)
    duration = _bound_quantity(raw_duration, bindings)
    amplitude = _bound_quantity(raw_amplitude, bindings)
    phase = _bound_quantity(raw_phase, bindings)
    if kind == "constant":
        base: Constant | Gaussian | CosineFlatTop = Constant(
            duration=duration,
            amplitude=amplitude,
            phase=phase,
        )
        if raw_derivative_beta is not None:
            raise AssertionError("verified constant envelope cannot have a derivative")
    elif kind == "cosine_flat_top":
        base = CosineFlatTop(
            duration=duration,
            amplitude=amplitude,
            rise_duration=_bound_quantity(
                cast("QuantumQuantity", raw_rise_duration),
                bindings,
            ),
            fall_duration=_bound_quantity(
                cast("QuantumQuantity", raw_fall_duration),
                bindings,
            ),
            phase=phase,
        )
    else:
        assert kind == "gaussian"
        sigma = _bound_quantity(cast("QuantumQuantity", raw_sigma), bindings)
        base = Gaussian(
            duration=duration,
            amplitude=amplitude,
            sigma=sigma,
            phase=phase,
        )
    if raw_derivative_beta is not None:
        assert isinstance(base, Gaussian | CosineFlatTop)
        corrected: Constant | Gaussian | CosineFlatTop | DerivativeQuadrature = (
            DerivativeQuadrature(
                envelope=base,
                beta=_bound_quantity(raw_derivative_beta, bindings),
            )
        )
    else:
        corrected = base
    if raw_frequency_offset is None:
        return corrected
    return FrequencyShift(
        envelope=corrected,
        frequency_offset=_bound_quantity(raw_frequency_offset, bindings),
        phase_reference=raw_frequency_reference,
    )


def _bound_quantity(
    value: QuantumQuantity,
    bindings: Mapping[str, object],
) -> Quantity:
    selected = resolve_expression(value, bindings)
    if not isinstance(selected, Quantity):
        raise AssertionError("verified quantity input must bind to Quantity")
    return selected


def _bound_repeat_count(
    count: RepeatCount,
    bindings: Mapping[str, object],
) -> int:
    selected = bindings[count.id] if isinstance(count, ProgramInput) else count
    if not isinstance(selected, int) or isinstance(selected, bool) or selected < 0:
        input_id = count.id if isinstance(count, ProgramInput) else None
        qualifier = f" input {input_id!r}" if input_id is not None else ""
        msg = f"repeat count{qualifier} must bind to a non-negative integer"
        raise ProgramBindingError(msg)
    return selected


def _bind_result_contract(
    contract: QuantumResultContract,
    bindings: Mapping[str, object],
) -> QuantumResultContract:
    """Resolve symbolic local extents for one concrete program point."""

    if contract.is_concrete:
        return contract
    dimensions: list[QuantumResultDimension] = []
    for dimension in contract.dimensions:
        input_id = dimension.size_input_id
        if input_id is None:
            dimensions.append(dimension)
            continue
        try:
            selected = bindings[input_id]
        except KeyError as error:
            raise ProgramBindingError(
                f"result dimension {dimension.id!r} references unbound "
                f"input {input_id!r}"
            ) from error
        if (
            not isinstance(selected, int)
            or isinstance(selected, bool)
            or selected <= 0
            or selected > dimension.maximum_size
        ):
            raise ProgramBindingError(
                f"result dimension {dimension.id!r} input {input_id!r} must bind "
                f"to an integer in [1, {dimension.maximum_size}]"
            )
        dimensions.append(replace(dimension, size=selected))
    return replace(contract, dimensions=tuple(dimensions))


def _bound_entity_set(
    entity_set: QubitSet | CouplerSet,
    value: object,
    *,
    column: str,
) -> tuple[EntityRef, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ProgramBindingError(
            f"bindings.{entity_set.id}: expected a sequence of logical entities"
        )
    rows = tuple(
        dict(cast("Mapping[str, object]", item))
        if isinstance(item, Mapping)
        else {column: item}
        for item in value
    )
    try:
        normalized = coerce_literal(
            entity_set.value_type,
            rows,
            path=("bindings", entity_set.id),
        )
    except ValueValidationError as error:
        raise ProgramBindingError(str(error)) from error
    entities = tuple(
        cast("EntityRef", row[column])
        for row in cast("tuple[dict[str, object], ...]", normalized)
    )
    if not entities:
        raise ProgramBindingError(
            f"bindings.{entity_set.id}: entity set must not be empty"
        )
    return entities


def _bound_qubit_pair_set(
    entity_set: QubitPairSet,
    value: object,
) -> tuple[tuple[EntityRef, EntityRef, EntityRef], ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ProgramBindingError(
            f"bindings.{entity_set.id}: expected a sequence of qubit-pair rows"
        )
    try:
        normalized = coerce_literal(
            entity_set.value_type,
            tuple(dict(cast("Mapping[str, object]", row)) for row in value),
            path=("bindings", entity_set.id),
        )
    except ValueValidationError as error:
        raise ProgramBindingError(str(error)) from error
    return tuple(
        (
            cast("EntityRef", row["left"]),
            cast("EntityRef", row["right"]),
            cast("EntityRef", row["coupler"]),
        )
        for row in cast("tuple[dict[str, object], ...]", normalized)
    )


def _bound_gate_definitions(
    fragment: QuantumFragment,
    bindings: Mapping[str, object],
) -> tuple[GateDefinition, ...]:
    """Derive the exact gate catalog from the point-bound fragment tree."""

    if isinstance(fragment, _ExpandedFragment):
        return _bound_gate_definitions(fragment.body, bindings)
    if isinstance(fragment, _FragmentCall):
        raise AssertionError("quantum fragment calls must expand before binding")
    if isinstance(
        fragment,
        _ParallelEachFragment
        | _ParallelCouplerEachFragment
        | _ParallelQubitPairEachFragment,
    ):
        return _bound_gate_definitions(fragment.operation, bindings)
    if isinstance(fragment, _ConditionalFragment):
        branches = tuple(body for _state, body in fragment.cases)
        if fragment.default is not None:
            branches = (*branches, fragment.default)
        return _unique_gate_definitions(
            tuple(
                definition
                for branch in branches
                for definition in _bound_gate_definitions(branch, bindings)
            )
        )
    if isinstance(fragment, _RepeatFragment | _QuantumRepeatFragment):
        if _bound_repeat_count(fragment.count, bindings) == 0:
            return ()
        return _bound_gate_definitions(fragment.operation, bindings)
    if isinstance(fragment, _SequenceFragment | _QuantumSequenceFragment):
        children = fragment.operations
    elif isinstance(fragment, _ParallelFragment | _QuantumParallelFragment):
        children = fragment.branches
    else:
        return _unique_gate_definitions(_summarize_fragment(fragment).gate_definitions)
    return _unique_gate_definitions(
        tuple(
            definition
            for child in children
            for definition in _bound_gate_definitions(child, bindings)
        )
    )


def _bound_qubit_id(qubit: Qubit, bindings: ElementBindings) -> QubitId:
    selected = bindings.get(qubit.ir_id, qubit.ir_id)
    if not isinstance(selected, QubitId):
        raise AssertionError("qubit ports must bind to logical qubits")
    return selected
