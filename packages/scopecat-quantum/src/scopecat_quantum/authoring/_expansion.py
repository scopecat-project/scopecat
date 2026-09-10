# pyright: reportPrivateUsage=false
"""Expansion and validation of authored fragment calls."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from scopecat.program.value_types import ValueValidationError, coerce_literal

from ._analysis import (
    _expanded_fragment_shape,
    _program_input_type,
    _summarize_fragment,
    _unique_gate_definitions,
)
from ._expressions import resolve_expression
from ._ir import (
    Coupler,
    CouplerSet,
    ProgramBindingError,
    QuantumFragment,
    Qubit,
    QubitPairSet,
    QubitSet,
    _ConditionalFragment,
    _ExpandedFragment,
    _FragmentCall,
    _FragmentHandle,
    _ParallelCouplerEachFragment,
    _ParallelEachFragment,
    _ParallelQubitPairEachFragment,
    _QuantumParallelFragment,
    _QuantumRepeatFragment,
    _QuantumSequenceFragment,
)


def _expand_fragment_calls(
    value: QuantumFragment,
    bindings: Mapping[str, object],
    *,
    stack: tuple[_FragmentHandle, ...] = (),
) -> QuantumFragment:
    if isinstance(value, _FragmentCall):
        definition = value.definition
        if any(definition is active for active in stack):
            chain = " -> ".join((*tuple(item.id for item in stack), definition.id))
            raise ProgramBindingError(f"quantum fragment expansion cycle: {chain}")
        body = _evaluate_fragment_call(value, bindings)
        expanded = _expand_fragment_calls(
            body,
            bindings,
            stack=(*stack, definition),
        )
        _validate_expanded_fragment(value, expanded)
        return _ExpandedFragment(definition_id=definition.id, body=expanded)
    if isinstance(value, _ExpandedFragment):
        return replace(
            value,
            body=_expand_fragment_calls(value.body, bindings, stack=stack),
        )
    if isinstance(value, _ConditionalFragment):
        return replace(
            value,
            cases=tuple(
                (
                    state,
                    _expand_fragment_calls(branch, bindings, stack=stack),
                )
                for state, branch in value.cases
            ),
            default=(
                None
                if value.default is None
                else _expand_fragment_calls(value.default, bindings, stack=stack)
            ),
        )
    if isinstance(value, _QuantumSequenceFragment):
        return replace(
            value,
            operations=tuple(
                _expand_fragment_calls(operation, bindings, stack=stack)
                for operation in value.operations
            ),
        )
    if isinstance(value, _QuantumParallelFragment):
        return replace(
            value,
            branches=tuple(
                _expand_fragment_calls(branch, bindings, stack=stack)
                for branch in value.branches
            ),
        )
    if isinstance(
        value,
        _ParallelEachFragment
        | _ParallelCouplerEachFragment
        | _ParallelQubitPairEachFragment,
    ):
        return replace(
            value,
            operation=_expand_fragment_calls(value.operation, bindings, stack=stack),
        )
    if isinstance(value, _QuantumRepeatFragment):
        return replace(
            value,
            operation=_expand_fragment_calls(value.operation, bindings, stack=stack),
        )
    return value


def _evaluate_fragment_call(
    call: _FragmentCall,
    bindings: Mapping[str, object],
) -> QuantumFragment:
    resolved: dict[str, object] = {}
    for (name, actual), formal in zip(
        call.arguments,
        call.definition.parameters,
        strict=True,
    ):
        if isinstance(formal, Qubit | Coupler):
            resolved[name] = actual
            continue
        if isinstance(formal, QubitSet | CouplerSet | QubitPairSet):
            raise AssertionError("quantum fragments cannot declare entity-set ports")
        selected = resolve_expression(actual, bindings)
        try:
            resolved[name] = coerce_literal(
                _program_input_type(formal, non_negative=False),
                selected,
                path=("fragment", call.definition.id, name),
            )
        except ValueValidationError as error:
            raise ProgramBindingError(str(error)) from error
    return call.definition.__wrapped__(**resolved)


def _validate_expanded_fragment(
    call: _FragmentCall,
    body: QuantumFragment,
) -> None:
    facts = _summarize_fragment(body)
    if facts.has_realtime:
        msg = (
            f"quantum fragment {call.definition.id!r} cannot contain real-time control"
        )
        raise ValueError(msg)
    if facts.results:
        msg = f"quantum fragment {call.definition.id!r} cannot produce results"
        raise ValueError(msg)
    if facts.inputs:
        rendered = ", ".join(repr(value.id) for value in facts.inputs)
        msg = (
            f"quantum fragment {call.definition.id!r} captures unbound inputs: "
            f"{rendered}"
        )
        raise ValueError(msg)
    if facts.entity_sets:
        rendered = ", ".join(repr(entity_set.id) for entity_set in facts.entity_sets)
        msg = (
            f"quantum fragment {call.definition.id!r} captures entity sets: {rendered}"
        )
        raise ValueError(msg)
    allowed_elements = {
        (type(value), value.id)
        for _name, value in call.arguments
        if isinstance(value, Qubit | Coupler)
    }
    foreign_elements = {
        (type(value), value.id) for value in facts.element_uses
    } - allowed_elements
    if foreign_elements:
        rendered = ", ".join(
            repr(element_id)
            for _element_type, element_id in sorted(
                foreign_elements,
                key=lambda item: (item[0].__name__, item[1]),
            )
        )
        msg = (
            f"quantum fragment {call.definition.id!r} captures undeclared "
            f"elements: {rendered}"
        )
        raise ValueError(msg)
    envelope = call.definition.envelope
    actual_gates = _unique_gate_definitions(facts.gate_definitions)
    allowed_gates = {
        definition.id.value: definition for definition in envelope.gate_definitions
    }
    foreign_gates = sorted(
        definition.id.value
        for definition in actual_gates
        if definition.id.value not in allowed_gates
    )
    if foreign_gates:
        rendered = ", ".join(repr(gate_id) for gate_id in foreign_gates)
        msg = (
            f"quantum fragment {call.definition.id!r} uses gates outside its "
            f"program family envelope: {rendered}"
        )
        raise ValueError(msg)
    conflicting_gates = sorted(
        definition.id.value
        for definition in actual_gates
        if definition.id.value in allowed_gates
        and allowed_gates[definition.id.value] != definition
    )
    if conflicting_gates:
        rendered = ", ".join(repr(gate_id) for gate_id in conflicting_gates)
        msg = (
            f"quantum fragment {call.definition.id!r} uses gate definitions "
            f"that conflict with its program family envelope: {rendered}"
        )
        raise ValueError(msg)
    shape = _expanded_fragment_shape(body)
    if shape.operation_count > envelope.max_operations:
        msg = (
            f"quantum fragment {call.definition.id!r} expands to "
            f"{shape.operation_count} operations, exceeding its program family "
            f"envelope maximum of {envelope.max_operations}"
        )
        raise ValueError(msg)
    if shape.depth > envelope.max_depth:
        msg = (
            f"quantum fragment {call.definition.id!r} expands to depth "
            f"{shape.depth}, exceeding its program family envelope maximum of "
            f"{envelope.max_depth}"
        )
        raise ValueError(msg)
