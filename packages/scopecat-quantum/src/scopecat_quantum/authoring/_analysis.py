# pyright: reportPrivateUsage=false, reportUnusedFunction=false
"""Structural analysis and value typing for quantum authoring."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import (
    Annotated,
    cast,
    get_args,
    get_origin,
)

from scopecat import Quantity
from scopecat.authoring import (
    EntityType,
    FloatType,
    IntType,
    QuantityType,
    ScalarType,
)
from scopecat.kernel.entity import EntityRef
from scopecat.program.value_types import (
    Bool as BoolType,
)
from scopecat.program.value_types import (
    Entity as EntityAtomType,
)
from scopecat.program.value_types import (
    Float as FloatAtomType,
)
from scopecat.program.value_types import (
    Payload as PayloadType,
)
from scopecat.program.value_types import (
    Quantity as QuantityAtomType,
)
from scopecat.program.value_types import (
    String as StringType,
)
from scopecat.program.value_types import ValueType

from scopecat_quantum._ids import (
    CouplerId,
    QubitId,
)
from scopecat_quantum.acquisitions import AcquisitionKind
from scopecat_quantum.gates import (
    GateDefinition,
    GateParameterKind,
)
from scopecat_quantum.pulses import (
    AnalyticEnvelope,
    EnvelopePhaseReference,
    FluxSignal,
    LogicalSignal,
)

from ._expressions import QuantityExpression, expression_inputs
from ._ir import (
    Acquisition,
    Coupler,
    CouplerSet,
    EntitySetPort,
    Measurement,
    ProgramInput,
    ProgramPort,
    ProgramResult,
    PulseElement,
    PulseEnvelope,
    QuantumFragment,
    QuantumQuantity,
    Qubit,
    QubitPair,
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


def _element_ir_id(value: PulseElement) -> QubitId | CouplerId:
    return value.ir_id


def _pulse_envelope_parts(
    value: PulseEnvelope,
) -> tuple[
    str,
    QuantumQuantity,
    QuantumQuantity,
    QuantumQuantity | None,
    QuantumQuantity | None,
    QuantumQuantity | None,
    QuantumQuantity | None,
    QuantumQuantity,
    QuantumQuantity | None,
    EnvelopePhaseReference,
]:
    return (
        value.kind,
        value.duration,
        value.amplitude,
        value.sigma,
        value.derivative_beta,
        value.rise_duration,
        value.fall_duration,
        value.phase,
        value.frequency_offset,
        value.frequency_reference,
    )


def _core_input_type(
    kind: GateParameterKind,
) -> ScalarType:
    if kind is GateParameterKind.INTEGER:
        return ScalarType(IntType())
    if kind is GateParameterKind.NUMBER:
        return ScalarType(FloatType())
    if kind is GateParameterKind.ANGLE:
        return ScalarType(QuantityType(dimension="phase", unit="rad"))
    raise AssertionError(f"unsupported gate parameter kind {kind!r}")


def program_port_type(
    value: ProgramPort,
    *,
    non_negative: bool = False,
) -> ValueType:
    """Return the core value contract for one quantum program port."""

    if isinstance(value, Qubit):
        return ScalarType(EntityType(entity_kind="logical_qubit"))
    if isinstance(value, Coupler):
        return ScalarType(EntityType(entity_kind="logical_coupler"))
    if isinstance(value, QubitSet | CouplerSet | QubitPairSet):
        return value.value_type
    return _program_input_type(
        value,
        non_negative=non_negative,
    )


def _unique_gate_definitions(
    definitions: Iterable[GateDefinition],
) -> tuple[GateDefinition, ...]:
    by_id: dict[str, GateDefinition] = {}
    for definition in definitions:
        existing = by_id.get(definition.id.value)
        if existing is not None and existing != definition:
            msg = (
                f"quantum program gate {definition.id.value!r} has "
                "conflicting definitions"
            )
            raise ValueError(msg)
        by_id.setdefault(definition.id.value, definition)
    return tuple(by_id.values())


def _pulse_envelope(
    kind: str,
    *,
    duration: QuantumQuantity,
    amplitude: QuantumQuantity,
    sigma: QuantumQuantity | None = None,
    derivative_beta: QuantumQuantity | None = None,
    rise_duration: QuantumQuantity | None = None,
    fall_duration: QuantumQuantity | None = None,
    phase: QuantumQuantity | None = None,
    frequency_offset: QuantumQuantity | None = None,
    frequency_reference: EnvelopePhaseReference = "center",
) -> PulseEnvelope:
    selected_phase = Quantity(0, "rad") if phase is None else phase
    if frequency_reference not in {"start", "center"}:
        raise ValueError("frequency_reference must be either 'start' or 'center'")
    _require_quantity_expression(duration, field="duration", kind="time")
    _require_quantity_expression(amplitude, field="amplitude", kind="amplitude")
    _require_quantity_expression(selected_phase, field="phase", kind="phase")
    if sigma is not None:
        _require_quantity_expression(sigma, field="sigma", kind="time")
    if derivative_beta is not None:
        _require_quantity_expression(
            derivative_beta,
            field="derivative_beta",
            kind="time",
        )
    if frequency_offset is not None:
        _require_quantity_expression(
            frequency_offset,
            field="frequency_offset",
            kind="frequency",
        )
    if rise_duration is not None:
        _require_quantity_expression(
            rise_duration,
            field="rise_duration",
            kind="time",
        )
    if fall_duration is not None:
        _require_quantity_expression(
            fall_duration,
            field="fall_duration",
            kind="time",
        )
    return PulseEnvelope(
        kind=kind,
        duration=duration,
        amplitude=amplitude,
        sigma=sigma,
        derivative_beta=derivative_beta,
        rise_duration=rise_duration,
        fall_duration=fall_duration,
        phase=selected_phase,
        frequency_offset=frequency_offset,
        frequency_reference=frequency_reference,
    )


def _require_quantity_expression(
    value: QuantumQuantity,
    *,
    field: str,
    kind: str,
) -> None:
    if isinstance(value, Quantity):
        accepted = False
        if kind == "time":
            accepted = _quantity_converts_to(value, "s")
        elif kind == "phase":
            accepted = _quantity_converts_to(value, "rad")
        elif kind == "frequency":
            accepted = _quantity_converts_to(value, "Hz")
        else:
            accepted = any(
                _quantity_converts_to(value, unit) for unit in ("arb", "ratio", "V")
            )
        if accepted:
            return
        msg = f"pulse {field} must use a {kind} quantity"
        raise TypeError(msg)
    atom = value.value_type.atom
    if not isinstance(atom, QuantityType):
        msg = f"pulse {field} input {value.id!r} must declare a quantity type"
        raise TypeError(msg)
    declared_kind = atom.dimension
    if declared_kind is None and atom.unit is not None:
        probe = Quantity(1, atom.unit)
        if _quantity_converts_to(probe, "s"):
            declared_kind = "time"
        elif _quantity_converts_to(probe, "rad"):
            declared_kind = "phase"
        elif _quantity_converts_to(probe, "Hz"):
            declared_kind = "frequency"
        elif any(_quantity_converts_to(probe, unit) for unit in ("arb", "ratio", "V")):
            declared_kind = "amplitude"
    accepted_kinds = (
        {"amplitude", "ratio", "voltage"} if kind == "amplitude" else {kind}
    )
    if declared_kind not in accepted_kinds:
        msg = f"pulse {field} input {value.id!r} must declare {kind!r} quantity units"
        raise TypeError(msg)


def _quantity_converts_to(value: Quantity, unit: str) -> bool:
    try:
        value.to(unit)
    except ValueError:
        return False
    return True


def _is_integer_input(value: ProgramInput) -> bool:
    return isinstance(value.value_type.atom, IntType)


def _program_input_type(
    value: ProgramInput | QuantityExpression,
    *,
    non_negative: bool,
) -> ScalarType:
    if not non_negative:
        return value.value_type
    atom = value.value_type.atom
    if not isinstance(atom, IntType):
        raise AssertionError("repeat inputs must have an integer type")
    minimum = 0 if atom.minimum is None else max(0, atom.minimum)
    return ScalarType(IntType(minimum=minimum, maximum=atom.maximum))


def _program_function_argument(
    name: str,
    annotation: object,
) -> ProgramPort:
    if annotation is Qubit:
        return Qubit(ir_id=QubitId(name))
    if annotation is Coupler:
        return Coupler(ir_id=CouplerId(name))
    if annotation is QubitSet:
        return QubitSet(
            _id=name,
            _item=Qubit(ir_id=QubitId(f"{name}[]")),
        )
    if annotation is CouplerSet:
        return CouplerSet(
            _id=name,
            _item=Coupler(ir_id=CouplerId(f"{name}[]")),
        )
    if annotation is QubitPairSet:
        return QubitPairSet(
            _id=name,
            _item=QubitPair(
                left=Qubit(ir_id=QubitId(f"{name}[].left")),
                right=Qubit(ir_id=QubitId(f"{name}[].right")),
                coupler=Coupler(ir_id=CouplerId(f"{name}[].coupler")),
            ),
        )
    if get_origin(annotation) is Annotated:
        python_type, *metadata = cast("tuple[object, ...]", get_args(annotation))
        gate_kinds = tuple(
            item for item in metadata if isinstance(item, GateParameterKind)
        )
        scalar_types = tuple(item for item in metadata if _is_quantum_scalar_type(item))
        if len(gate_kinds) + len(scalar_types) != 1:
            raise TypeError(
                f"quantum program port {name!r} requires one scalar type annotation"
            )
        if gate_kinds:
            if not _program_python_type_matches_gate_kind(python_type, gate_kinds[0]):
                raise TypeError(
                    f"quantum program port {name!r} Python annotation is "
                    f"incompatible with {gate_kinds[0].value!r}"
                )
            return ProgramInput(_id=name, value_type=_core_input_type(gate_kinds[0]))
        value_type = _quantum_scalar_type(scalar_types[0])
        if not _program_python_type_matches_scalar(python_type, value_type):
            raise TypeError(
                f"quantum program port {name!r} Python annotation is "
                f"incompatible with {value_type!r}"
            )
        return ProgramInput(_id=name, value_type=value_type)
    if annotation is int:
        return ProgramInput(
            _id=name,
            value_type=_core_input_type(GateParameterKind.INTEGER),
        )
    if annotation is float:
        return ProgramInput(
            _id=name,
            value_type=_core_input_type(GateParameterKind.NUMBER),
        )
    raise TypeError(
        f"quantum program port {name!r} needs Qubit, QubitSet, Coupler, "
        "CouplerSet, QubitPairSet, "
        "or Annotated scalar type"
    )


def _program_python_type_matches_gate_kind(
    annotation: object,
    kind: GateParameterKind,
) -> bool:
    expected: dict[GateParameterKind, tuple[object, ...]] = {
        GateParameterKind.INTEGER: (int,),
        GateParameterKind.NUMBER: (int, float),
        GateParameterKind.ANGLE: (Quantity, QuantumQuantity),
    }
    return annotation is object or annotation in expected[kind]


def _program_python_type_matches_scalar(
    annotation: object,
    value_type: ScalarType,
) -> bool:
    if annotation is object:
        return True
    atom = value_type.atom
    expected: dict[type[object], tuple[object, ...]] = {
        BoolType: (bool,),
        EntityAtomType: (str, EntityRef),
        FloatAtomType: (float,),
        IntType: (int,),
        PayloadType: (dict, Mapping),
        QuantityAtomType: (Quantity, QuantumQuantity),
        StringType: (str,),
    }
    return annotation in expected[type(atom)]


def _is_quantum_scalar_type(value: object) -> bool:
    return isinstance(
        value,
        ScalarType
        | BoolType
        | EntityAtomType
        | FloatAtomType
        | IntType
        | PayloadType
        | QuantityAtomType
        | StringType,
    )


def _quantum_scalar_type(value: object) -> ScalarType:
    if isinstance(value, ScalarType):
        return value
    if isinstance(
        value,
        BoolType
        | EntityAtomType
        | FloatAtomType
        | IntType
        | PayloadType
        | QuantityAtomType
        | StringType,
    ):
        return ScalarType(value)
    raise AssertionError("quantum scalar metadata was checked before normalization")


@dataclass(frozen=True, slots=True)
class _FragmentFacts:
    """One structural summary shared by quantum authoring closure checks."""

    pulse_only: bool = False
    pulse_owners: tuple[QubitId | CouplerId, ...] = ()
    element_uses: tuple[PulseElement, ...] = ()
    entity_sets: tuple[EntitySetPort, ...] = ()
    inputs: tuple[ProgramInput, ...] = ()
    repeat_inputs: tuple[ProgramInput, ...] = ()
    results: tuple[ProgramResult, ...] = ()
    gate_definitions: tuple[GateDefinition, ...] = ()
    has_realtime: bool = False
    result_repeat_dimension_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _ExpandedFragmentShape:
    """Exact executable size of one concrete point-expanded fragment."""

    operation_count: int
    depth: int


def _expanded_fragment_shape(fragment: QuantumFragment) -> _ExpandedFragmentShape:
    """Count executable leaves and longest sequential path after expansion."""

    if isinstance(
        fragment,
        _GateFragment
        | Measurement
        | Acquisition
        | _PlayFragment
        | _DelayFragment
        | _ShiftPhaseFragment
        | _ImplementedGateFragment,
    ):
        return _ExpandedFragmentShape(operation_count=1, depth=1)
    if isinstance(fragment, _PulseTemplateCallFragment | _ExpandedFragment):
        return _expanded_fragment_shape(fragment.body)
    if isinstance(fragment, _FragmentCall):
        raise AssertionError("fragment calls must expand before shape analysis")
    if isinstance(
        fragment,
        _ParallelEachFragment
        | _ParallelCouplerEachFragment
        | _ParallelQubitPairEachFragment,
    ):
        raise AssertionError("program family fragments cannot contain entity sets")
    if isinstance(fragment, _ConditionalFragment):
        branches = tuple(body for _state, body in fragment.cases)
        if fragment.default is not None:
            branches = (*branches, fragment.default)
        children = tuple(_expanded_fragment_shape(branch) for branch in branches)
        return _ExpandedFragmentShape(
            operation_count=1
            + max(
                (child.operation_count for child in children),
                default=0,
            ),
            depth=1 + max((child.depth for child in children), default=0),
        )
    if isinstance(fragment, _RepeatFragment | _QuantumRepeatFragment):
        if isinstance(fragment.count, ProgramInput):
            raise AssertionError("point-expanded repeat counts must be concrete")
        child = _expanded_fragment_shape(fragment.operation)
        return _ExpandedFragmentShape(
            operation_count=fragment.count * child.operation_count,
            depth=fragment.count * child.depth,
        )
    if isinstance(fragment, _SequenceFragment | _QuantumSequenceFragment):
        children = tuple(
            _expanded_fragment_shape(child) for child in fragment.operations
        )
        return _ExpandedFragmentShape(
            operation_count=sum(child.operation_count for child in children),
            depth=sum(child.depth for child in children),
        )
    if isinstance(fragment, _ParallelFragment | _QuantumParallelFragment):
        children = tuple(_expanded_fragment_shape(child) for child in fragment.branches)
        return _ExpandedFragmentShape(
            operation_count=sum(child.operation_count for child in children),
            depth=max(child.depth for child in children),
        )
    raise AssertionError(f"unsupported quantum fragment {type(fragment).__name__}")


def _summarize_entity_set_fragment(
    fragment: (
        _ParallelEachFragment
        | _ParallelCouplerEachFragment
        | _ParallelQubitPairEachFragment
    ),
) -> _FragmentFacts:
    operation = _summarize_fragment(fragment.operation)
    if isinstance(fragment, _ParallelEachFragment):
        items: tuple[Qubit | Coupler, ...] = (fragment.entity_set.item,)
        results = tuple(
            replace(result, _entity_set=fragment.entity_set)
            for result in operation.results
        )
    else:
        if operation.results:
            entity_kind = (
                "couplers"
                if isinstance(fragment, _ParallelCouplerEachFragment)
                else "qubit pairs"
            )
            raise ValueError(f"parallel_each over {entity_kind} cannot produce results")
        items = (
            (fragment.entity_set.item,)
            if isinstance(fragment, _ParallelCouplerEachFragment)
            else (
                fragment.entity_set.item.left,
                fragment.entity_set.item.right,
                fragment.entity_set.item.coupler,
            )
        )
        results = ()
    return _FragmentFacts(
        pulse_only=False,
        element_uses=tuple(
            element for element in operation.element_uses if element not in items
        ),
        entity_sets=(fragment.entity_set,),
        inputs=operation.inputs,
        repeat_inputs=operation.repeat_inputs,
        results=results,
        gate_definitions=operation.gate_definitions,
        has_realtime=operation.has_realtime,
        result_repeat_dimension_ids=operation.result_repeat_dimension_ids,
    )


def _summarize_fragment(fragment: QuantumFragment) -> _FragmentFacts:
    """Collect every closure fact in one structural fragment traversal."""

    if isinstance(fragment, _FragmentCall):
        return _FragmentFacts(
            element_uses=tuple(
                value
                for _name, value in fragment.arguments
                if isinstance(value, Qubit | Coupler)
            ),
            inputs=tuple(
                input_handle
                for _name, value in fragment.arguments
                for input_handle in expression_inputs(value)
            ),
            gate_definitions=fragment.definition.envelope.gate_definitions,
        )
    if isinstance(fragment, _ExpandedFragment):
        return _summarize_fragment(fragment.body)
    if isinstance(fragment, _GateFragment):
        return _FragmentFacts(
            element_uses=fragment.qubits,
            inputs=tuple(
                input_handle
                for _argument_id, value in fragment.arguments
                for input_handle in expression_inputs(value)
            ),
            gate_definitions=(fragment.gate.definition,),
        )
    if isinstance(fragment, Measurement):
        return _FragmentFacts(
            element_uses=(fragment.result.qubit,),
            inputs=_result_dimension_inputs(fragment.result),
            results=(fragment.result,),
        )
    if isinstance(fragment, Acquisition):
        return _FragmentFacts(
            pulse_only=True,
            pulse_owners=(_signal_owner(fragment.signal),),
            element_uses=(fragment.result.qubit,),
            inputs=(
                *expression_inputs(fragment.duration),
                *_result_dimension_inputs(fragment.result),
            ),
            results=(fragment.result,),
        )
    if isinstance(fragment, _PlayFragment):
        return _FragmentFacts(
            pulse_only=True,
            pulse_owners=(_signal_owner(fragment.signal),),
            element_uses=(_signal_element(fragment.signal),),
            inputs=_envelope_inputs(fragment.envelope),
        )
    if isinstance(fragment, _DelayFragment):
        return _FragmentFacts(
            pulse_only=True,
            pulse_owners=(_signal_owner(fragment.signal),),
            element_uses=(_signal_element(fragment.signal),),
            inputs=expression_inputs(fragment.duration),
        )
    if isinstance(fragment, _ShiftPhaseFragment):
        return _FragmentFacts(
            pulse_only=True,
            pulse_owners=(_signal_owner(fragment.signal),),
            element_uses=(_signal_element(fragment.signal),),
            inputs=expression_inputs(fragment.phase),
        )
    if isinstance(fragment, _PulseTemplateCallFragment):
        body = _summarize_fragment(fragment.body)
        return _FragmentFacts(
            # The template factory already proves this independently of its
            # instantiated-body facts.
            pulse_only=True,
            pulse_owners=body.pulse_owners,
            element_uses=body.element_uses,
            inputs=body.inputs,
            repeat_inputs=body.repeat_inputs,
            results=body.results,
            gate_definitions=body.gate_definitions,
            has_realtime=body.has_realtime,
            result_repeat_dimension_ids=body.result_repeat_dimension_ids,
        )
    if isinstance(fragment, _ImplementedGateFragment):
        gate = _summarize_fragment(fragment.gate)
        pulse = _summarize_fragment(fragment.pulse)
        return _FragmentFacts(
            element_uses=(*gate.element_uses, *pulse.element_uses),
            inputs=(*gate.inputs, *pulse.inputs),
            # Gate arguments are ordinary inputs; only repeat counts inside
            # the attached pulse acquire the non-negative contract.
            repeat_inputs=pulse.repeat_inputs,
            gate_definitions=(fragment.gate.gate.definition,),
            has_realtime=pulse.has_realtime,
            result_repeat_dimension_ids=pulse.result_repeat_dimension_ids,
        )
    if isinstance(
        fragment,
        _ParallelEachFragment
        | _ParallelCouplerEachFragment
        | _ParallelQubitPairEachFragment,
    ):
        return _summarize_entity_set_fragment(fragment)
    if isinstance(fragment, _ConditionalFragment):
        branches = tuple(body for _state, body in fragment.cases)
        if fragment.default is not None:
            branches = (*branches, fragment.default)
        merged = _merge_fragment_facts(
            tuple(_summarize_fragment(branch) for branch in branches),
            carries_pulse_structure=False,
        )
        return replace(merged, has_realtime=True)
    if isinstance(fragment, _RepeatFragment | _QuantumRepeatFragment):
        operation = _summarize_fragment(fragment.operation)
        carries_pulse_structure = isinstance(fragment, _QuantumRepeatFragment)
        result_dimension_id = (
            fragment.result_dimension_id
            if isinstance(fragment, _QuantumRepeatFragment)
            else None
        )
        has_realtime = operation.has_realtime or result_dimension_id is not None
        result_repeat_dimension_ids = (
            *operation.result_repeat_dimension_ids,
            *((result_dimension_id,) if result_dimension_id is not None else ()),
        )
        pulse_only = operation.pulse_only if carries_pulse_structure else False
        pulse_owners = operation.pulse_owners if carries_pulse_structure else ()
        if fragment.count == 0:
            # A zero repeat elides executable declarations but remains a pulse
            # fragment with the same authorized signal-owner surface.
            return _FragmentFacts(
                pulse_only=pulse_only,
                pulse_owners=pulse_owners,
                has_realtime=has_realtime,
                result_repeat_dimension_ids=result_repeat_dimension_ids,
            )
        count_inputs: tuple[ProgramInput, ...] = (
            (fragment.count,) if isinstance(fragment.count, ProgramInput) else ()
        )
        return _FragmentFacts(
            pulse_only=pulse_only,
            pulse_owners=pulse_owners,
            element_uses=operation.element_uses,
            inputs=(*count_inputs, *operation.inputs),
            repeat_inputs=(*count_inputs, *operation.repeat_inputs),
            results=operation.results,
            gate_definitions=operation.gate_definitions,
            has_realtime=has_realtime,
            result_repeat_dimension_ids=result_repeat_dimension_ids,
        )
    if isinstance(fragment, _SequenceFragment | _QuantumSequenceFragment):
        children = fragment.operations
    elif isinstance(fragment, _ParallelFragment | _QuantumParallelFragment):
        children = fragment.branches
    else:
        raise AssertionError(f"unsupported quantum fragment {type(fragment).__name__}")
    child_facts = tuple(_summarize_fragment(child) for child in children)
    return _merge_fragment_facts(
        child_facts,
        carries_pulse_structure=isinstance(
            fragment,
            _QuantumSequenceFragment | _QuantumParallelFragment,
        ),
    )


def _merge_fragment_facts(
    children: tuple[_FragmentFacts, ...],
    *,
    carries_pulse_structure: bool,
) -> _FragmentFacts:
    return _FragmentFacts(
        pulse_only=(
            carries_pulse_structure and all(child.pulse_only for child in children)
        ),
        pulse_owners=(
            tuple(owner for child in children for owner in child.pulse_owners)
            if carries_pulse_structure
            else ()
        ),
        element_uses=tuple(
            element for child in children for element in child.element_uses
        ),
        entity_sets=tuple(
            entity_set for child in children for entity_set in child.entity_sets
        ),
        inputs=tuple(value for child in children for value in child.inputs),
        repeat_inputs=tuple(
            value for child in children for value in child.repeat_inputs
        ),
        results=tuple(result for child in children for result in child.results),
        gate_definitions=tuple(
            definition for child in children for definition in child.gate_definitions
        ),
        has_realtime=any(child.has_realtime for child in children),
        result_repeat_dimension_ids=tuple(
            dimension_id
            for child in children
            for dimension_id in child.result_repeat_dimension_ids
        ),
    )


@dataclass(frozen=True, slots=True)
class _ResultAvailability:
    active_result_dimensions: frozenset[str]
    aggregate: bool


def _validate_realtime_structure(fragment: QuantumFragment) -> None:
    """Validate source-level feedback dataflow before target lowering."""

    _validate_realtime_node(
        fragment,
        available={},
        active_result_dimensions={},
        aggregate_results=False,
        inside_conditional_branch=False,
    )


def _validate_realtime_node(
    fragment: QuantumFragment,
    *,
    available: dict[ProgramResult, _ResultAvailability],
    active_result_dimensions: dict[str, RepeatCount],
    aggregate_results: bool,
    inside_conditional_branch: bool,
) -> dict[ProgramResult, _ResultAvailability]:
    if isinstance(fragment, _ExpandedFragment | _PulseTemplateCallFragment):
        return _validate_realtime_node(
            fragment.body,
            available=available,
            active_result_dimensions=active_result_dimensions,
            aggregate_results=aggregate_results,
            inside_conditional_branch=inside_conditional_branch,
        )
    if isinstance(fragment, _FragmentCall):
        # Calls are validated after point expansion, when their generated body
        # and captured values are visible.
        return available
    if isinstance(fragment, Measurement | Acquisition):
        if inside_conditional_branch:
            raise ValueError(
                "switch branches cannot produce acquisition results; place "
                "measurement before or after the switch"
            )
        return {
            **available,
            fragment.result: _ResultAvailability(
                active_result_dimensions=frozenset(active_result_dimensions),
                aggregate=aggregate_results,
            ),
        }
    if isinstance(fragment, _ConditionalFragment):
        return _validate_realtime_conditional(
            fragment,
            available=available,
            active_result_dimensions=active_result_dimensions,
            aggregate_results=aggregate_results,
        )
    if isinstance(
        fragment,
        _ParallelEachFragment
        | _ParallelCouplerEachFragment
        | _ParallelQubitPairEachFragment,
    ):
        return _validate_realtime_node(
            fragment.operation,
            available=available,
            active_result_dimensions=active_result_dimensions,
            aggregate_results=not isinstance(fragment, _ParallelEachFragment),
            inside_conditional_branch=inside_conditional_branch,
        )
    if isinstance(fragment, _ParallelFragment | _QuantumParallelFragment):
        outputs = dict(available)
        for branch in fragment.branches:
            if _summarize_fragment(branch).has_realtime:
                raise ValueError("real-time control is not supported under parallel")
            outputs.update(
                _validate_realtime_node(
                    branch,
                    available=dict(available),
                    active_result_dimensions=active_result_dimensions,
                    aggregate_results=aggregate_results,
                    inside_conditional_branch=inside_conditional_branch,
                )
            )
        return outputs
    if isinstance(fragment, _RepeatFragment | _QuantumRepeatFragment):
        return _validate_realtime_repeat(
            fragment,
            available=dict(available),
            active_result_dimensions=active_result_dimensions,
            aggregate_results=aggregate_results,
            inside_conditional_branch=inside_conditional_branch,
        )
    if isinstance(fragment, _SequenceFragment | _QuantumSequenceFragment):
        selected = available
        for operation in fragment.operations:
            selected = _validate_realtime_node(
                operation,
                available=selected,
                active_result_dimensions=active_result_dimensions,
                aggregate_results=aggregate_results,
                inside_conditional_branch=inside_conditional_branch,
            )
        return selected
    return available


def _validate_realtime_conditional(
    fragment: _ConditionalFragment,
    *,
    available: dict[ProgramResult, _ResultAvailability],
    active_result_dimensions: dict[str, RepeatCount],
    aggregate_results: bool,
) -> dict[ProgramResult, _ResultAvailability]:
    predicate = fragment.predicate
    if predicate.contract.acquisition_kind is not AcquisitionKind.CLASSIFIED_STATE:
        raise ValueError("switch predicates require classified-state results")
    predicate_availability = available.get(predicate)
    if predicate_availability is None:
        raise ValueError(
            f"switch predicate result {predicate.id!r} must be produced earlier "
            "in the same sequence"
        )
    if predicate_availability.aggregate:
        raise ValueError(
            f"switch predicate result {predicate.id!r} is aggregate and cannot "
            "be read as one classified state"
        )
    active_ids = frozenset(active_result_dimensions)
    required_ids = frozenset(
        dimension.id for dimension in predicate.contract.dimensions
    )
    if not (
        required_ids <= active_ids
        and predicate_availability.active_result_dimensions <= active_ids
    ):
        raise ValueError(
            f"switch predicate result {predicate.id!r} is only scalar in the "
            "current iteration of all its result dimensions"
        )
    branches = tuple(body for _state, body in fragment.cases)
    if fragment.default is not None:
        branches = (*branches, fragment.default)
    for branch in branches:
        if _summarize_fragment(branch).results:
            raise ValueError(
                "switch branches cannot produce acquisition results; place "
                "measurement before or after the switch"
            )
        _validate_realtime_node(
            branch,
            available=dict(available),
            active_result_dimensions=active_result_dimensions,
            aggregate_results=aggregate_results,
            inside_conditional_branch=True,
        )
    return available


def _validate_realtime_repeat(
    fragment: _RepeatFragment | _QuantumRepeatFragment,
    *,
    available: dict[ProgramResult, _ResultAvailability],
    active_result_dimensions: dict[str, RepeatCount],
    aggregate_results: bool,
    inside_conditional_branch: bool,
) -> dict[ProgramResult, _ResultAvailability]:
    dimension_id = (
        fragment.result_dimension_id
        if isinstance(fragment, _QuantumRepeatFragment)
        else None
    )
    nested_dimensions = active_result_dimensions
    if dimension_id is not None:
        if active_result_dimensions:
            raise ValueError(
                "result-producing repeats cannot be nested; combine repeated "
                "rounds into one declared result dimension"
            )
        _validate_result_repeat_contract(
            fragment.operation,
            count=fragment.count,
            result_dimension_id=dimension_id,
        )
        nested_dimensions = {dimension_id: fragment.count}
    output = _validate_realtime_node(
        fragment.operation,
        available=dict(available),
        active_result_dimensions=nested_dimensions,
        aggregate_results=aggregate_results,
        inside_conditional_branch=inside_conditional_branch,
    )
    if fragment.count == 0:
        return available
    return output


def _validate_result_repeat_contract(
    operation: QuantumFragment,
    *,
    count: RepeatCount,
    result_dimension_id: str,
) -> None:
    results = _summarize_fragment(operation).results
    if not results:
        raise ValueError("result-free repeats cannot declare a result dimension")
    for result in results:
        dimension = next(
            (
                dimension
                for dimension in result.contract.dimensions
                if dimension.id == result_dimension_id
            ),
            None,
        )
        if dimension is None or not _same_repeat_extent(dimension.size, count):
            raise ValueError(
                f"repeat result {result.id!r} must declare dimension "
                f"{result_dimension_id!r} with the same count"
            )


def _same_repeat_extent(left: object, right: RepeatCount) -> bool:
    if isinstance(left, int) and isinstance(right, int):
        return (
            not isinstance(left, bool) and not isinstance(right, bool) and left == right
        )
    return left is right


def _result_dimension_inputs(result: ProgramResult) -> tuple[ProgramInput, ...]:
    """Return authoring inputs used only by a result's local shape."""

    return tuple(
        dimension.size
        for dimension in result.contract.dimensions
        if isinstance(dimension.size, ProgramInput)
    )


def _signal_owner(signal: LogicalSignal) -> QubitId | CouplerId:
    if isinstance(signal, FluxSignal):
        return signal.owner
    return signal.qubit


def _signal_element(signal: LogicalSignal) -> PulseElement:
    owner = _signal_owner(signal)
    if isinstance(owner, CouplerId):
        return Coupler(owner)
    return Qubit(owner)


def _operation_id(path: tuple[str, ...], kind: str) -> str:
    return "/".join((*path, kind))


def _envelope_inputs(
    envelope: PulseEnvelope | AnalyticEnvelope,
) -> tuple[ProgramInput, ...]:
    if not isinstance(envelope, PulseEnvelope):
        return ()
    (
        _kind,
        duration,
        amplitude,
        sigma,
        derivative_beta,
        rise_duration,
        fall_duration,
        phase,
        frequency_offset,
        _frequency_reference,
    ) = _pulse_envelope_parts(envelope)
    return tuple(
        input_handle
        for value in (
            duration,
            amplitude,
            sigma,
            derivative_beta,
            rise_duration,
            fall_duration,
            phase,
            frequency_offset,
        )
        for input_handle in expression_inputs(value)
    )


def _argument_matches_kind(value: object, kind: GateParameterKind) -> bool:
    if kind is GateParameterKind.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind is GateParameterKind.NUMBER:
        if not isinstance(value, int | float) or isinstance(value, bool):
            return False
        try:
            return math.isfinite(float(value))
        except OverflowError:
            return False
    if kind is not GateParameterKind.ANGLE or not isinstance(value, Quantity):
        return False
    try:
        converted = value.to("rad")
    except ValueError:
        return False
    return math.isfinite(float(converted.value))


def _program_input_matches_kind(
    value: ProgramInput | QuantityExpression,
    kind: GateParameterKind,
) -> bool:
    atom = value.value_type.atom
    if kind is GateParameterKind.INTEGER:
        return isinstance(atom, IntType)
    if kind is GateParameterKind.NUMBER:
        return isinstance(atom, IntType | FloatType)
    if kind is not GateParameterKind.ANGLE or not isinstance(atom, QuantityType):
        return False
    if atom.dimension == "phase":
        return True
    if atom.unit is None:
        return False
    return _quantity_converts_to(Quantity(1, atom.unit), "rad")


def _duplicates(values: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(values)
    return tuple(sorted(value for value in set(selected) if selected.count(value) > 1))
