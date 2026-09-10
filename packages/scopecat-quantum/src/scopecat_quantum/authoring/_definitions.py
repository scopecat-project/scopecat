# pyright: reportPrivateUsage=false
"""Reusable gate, fragment, and pulse-template definitions."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from collections.abc import Sequence as SequenceCollection
from dataclasses import dataclass
from typing import (
    cast,
)

from scopecat import Quantity
from scopecat.program.value_types import ValueValidationError, coerce_literal

from scopecat_quantum._ids import (
    CouplerId,
    PulseProgramId,
    QubitId,
)
from scopecat_quantum.gates import (
    GateDefinition,
    GateParameterDefinition,
)
from scopecat_quantum.pulses import (
    AcquireSignal,
    AnalyticEnvelope,
    DriveSignal,
    FluxSignal,
    FrameSignal,
    LogicalSignal,
    PlaySignal,
    ReadoutSignal,
)

from ._analysis import (
    _argument_matches_kind,
    _element_ir_id,
    _program_input_matches_kind,
    _program_input_type,
    _pulse_envelope,
    _pulse_envelope_parts,
    _summarize_fragment,
)
from ._expressions import (
    QuantityExpression,
    constrain_expression,
    substitute_expression,
)
from ._ir import (
    CircuitArgument,
    CircuitFragment,
    Coupler,
    CouplerSet,
    EntitySetPort,
    ProgramInput,
    ProgramPort,
    PulseElement,
    PulseEnvelope,
    PulseFragment,
    QuantumFragment,
    QuantumQuantity,
    Qubit,
    QubitPairSet,
    QubitSet,
    RepeatCount,
    _DelayFragment,
    _FragmentCall,
    _GateFragment,
    _ImplementedGateFragment,
    _PlayFragment,
    _PulseTemplateCallFragment,
    _QuantumParallelFragment,
    _QuantumRepeatFragment,
    _QuantumSequenceFragment,
    _ShiftPhaseFragment,
)

type _PulseTemplateArgument = Quantity | int | float | ProgramInput | QuantityExpression

type Gate = SingleQubitGate | TwoQubitGate


@dataclass(frozen=True, slots=True)
class _QuantumFunctionContract:
    """One decorator function's ordered symbolic port contract."""

    signature: inspect.Signature
    parameters: tuple[ProgramPort, ...]

    @property
    def arguments(self) -> dict[str, ProgramPort]:
        return {
            parameter.name: value
            for parameter, value in zip(
                self.signature.parameters.values(),
                self.parameters,
                strict=True,
            )
        }

    @property
    def elements(self) -> tuple[PulseElement, ...]:
        return tuple(
            parameter
            for parameter in self.parameters
            if isinstance(parameter, Qubit | Coupler)
        )

    @property
    def inputs(self) -> tuple[ProgramInput, ...]:
        return tuple(
            parameter
            for parameter in self.parameters
            if isinstance(parameter, ProgramInput)
        )

    @property
    def entity_sets(self) -> tuple[EntitySetPort, ...]:
        return tuple(
            parameter
            for parameter in self.parameters
            if isinstance(parameter, QubitSet | CouplerSet | QubitPairSet)
        )


@dataclass(frozen=True, slots=True, repr=False)
class SingleQubitGate:
    """A reusable symbolic gate with exactly one logical-qubit operand."""

    definition: GateDefinition

    @property
    def id(self) -> str:
        """Return the gate semantic identity."""

        return self.definition.id.value

    @property
    def parameters(self) -> tuple[GateParameterDefinition, ...]:
        """Return the ordered scalar parameter contract."""

        return self.definition.parameters

    def __call__(
        self,
        qubit: Qubit,
        /,
        **arguments: CircuitArgument,
    ) -> CircuitFragment:
        """Author one occurrence of this gate on ``qubit``."""

        return _author_gate_call(self, (qubit,), arguments)


@dataclass(frozen=True, slots=True, repr=False)
class TwoQubitGate:
    """A reusable symbolic gate with exactly two logical-qubit operands."""

    definition: GateDefinition

    @property
    def id(self) -> str:
        """Return the gate semantic identity."""

        return self.definition.id.value

    @property
    def parameters(self) -> tuple[GateParameterDefinition, ...]:
        """Return the ordered scalar parameter contract."""

        return self.definition.parameters

    def __call__(
        self,
        first: Qubit,
        second: Qubit,
        /,
        **arguments: CircuitArgument,
    ) -> CircuitFragment:
        """Author one occurrence of this gate on two ordered qubits."""

        return _author_gate_call(self, (first, second), arguments)


def _author_gate_call(
    gate_handle: Gate,
    qubits: tuple[Qubit, ...],
    arguments: Mapping[str, CircuitArgument],
) -> CircuitFragment:
    definition = gate_handle.definition
    if len(qubits) != definition.qubit_arity:
        msg = (
            f"gate {gate_handle.id!r} requires {definition.qubit_arity} qubits, "
            f"got {len(qubits)}"
        )
        raise ValueError(msg)
    qubit_ids = tuple(qubit.ir_id for qubit in qubits)
    if len(set(qubit_ids)) != len(qubit_ids):
        msg = f"gate {gate_handle.id!r} operands must be unique"
        raise ValueError(msg)

    expected = {parameter.id: parameter for parameter in gate_handle.parameters}
    supplied = set(arguments)
    missing = sorted(set(expected) - supplied)
    unknown = sorted(supplied - set(expected))
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(repr(item) for item in missing))
        if unknown:
            details.append("unknown " + ", ".join(repr(item) for item in unknown))
        msg = f"gate {gate_handle.id!r} arguments are invalid: " + "; ".join(details)
        raise ValueError(msg)
    ordered_arguments: list[tuple[str, CircuitArgument]] = []
    for parameter in gate_handle.parameters:
        value = arguments[parameter.id]
        if isinstance(value, ProgramInput | QuantityExpression):
            if not _program_input_matches_kind(value, parameter.kind):
                msg = (
                    f"gate {gate_handle.id!r} parameter {parameter.id!r} requires "
                    f"{parameter.kind.value!r}, but input {value.id!r} declares "
                    f"{value.value_type!r}"
                )
                raise TypeError(msg)
        elif not _argument_matches_kind(value, parameter.kind):
            msg = (
                f"gate {gate_handle.id!r} parameter {parameter.id!r} requires "
                f"{parameter.kind.value!r}"
            )
            raise TypeError(msg)
        ordered_arguments.append((parameter.id, value))
    return _GateFragment(
        gate=gate_handle,
        qubits=qubits,
        arguments=tuple(ordered_arguments),
    )


@dataclass(frozen=True, slots=True, repr=False)
class _PulseTemplateSource:
    """Closed pulse-template source retained by a function definition."""

    ir_id: PulseProgramId
    body: QuantumFragment
    elements: tuple[PulseElement, ...]
    inputs: tuple[ProgramInput, ...]

    @property
    def id(self) -> str:
        """Return the stable pulse-template identity."""

        return self.ir_id.value


class PulseTemplateDefinition[**P](_PulseTemplateSource):
    """A function-authored pulse template with an inspectable call signature."""

    __slots__ = ("_contract", "_definition")

    _contract: _QuantumFunctionContract
    _definition: Callable[P, QuantumFragment]

    def __init__(
        self,
        declaration: _PulseTemplateSource,
        definition: Callable[P, QuantumFragment],
        contract: _QuantumFunctionContract,
    ) -> None:
        super().__init__(
            ir_id=declaration.ir_id,
            body=declaration.body,
            elements=declaration.elements,
            inputs=declaration.inputs,
        )
        self._definition = definition
        self._contract = contract

    @property
    def parameters(self) -> tuple[ProgramPort, ...]:
        """Return ports in their declared Python order."""

        return self._contract.parameters

    @property
    def __wrapped__(self) -> Callable[P, QuantumFragment]:
        return self._definition

    @property
    def __name__(self) -> str:
        return self._definition.__name__

    @property
    def __signature__(self) -> inspect.Signature:
        return self._contract.signature.replace(return_annotation=PulseFragment)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> PulseFragment:
        """Instantiate the template in its declared Python parameter order."""

        bound = self._contract.signature.bind(*args, **kwargs)
        return _instantiate_bound_pulse_template(self, bound)


@dataclass(frozen=True, slots=True)
class _GateImplementationContract:
    """Names that attach pulse parameters to their gate-level roles."""

    signature: inspect.Signature
    operands: tuple[str, ...]
    resources: tuple[str, ...]
    arguments: tuple[str, ...]


class GateImplementationDefinition[**P]:
    """A function-authored fixed gate implementation backed by pulse structure."""

    __slots__ = ("_contract", "candidate", "gate", "template")

    gate: Gate
    candidate: str | None
    template: PulseTemplateDefinition[P]
    _contract: _GateImplementationContract

    def __init__(
        self,
        template: PulseTemplateDefinition[P],
        *,
        gate: Gate,
        candidate: str | None,
        contract: _GateImplementationContract,
    ) -> None:
        self.template = template
        self.gate = gate
        self.candidate = candidate
        self._contract = contract

    @property
    def id(self) -> str:
        """Return the stable pulse implementation identity."""

        return self.template.id

    @property
    def parameters(self) -> tuple[PulseElement | ProgramInput, ...]:
        """Return the implementation's typed operands, resources, and inputs."""

        return cast(
            "tuple[PulseElement | ProgramInput, ...]",
            self.template.parameters,
        )

    @property
    def __wrapped__(self) -> Callable[P, QuantumFragment]:
        return self.template.__wrapped__

    @property
    def __name__(self) -> str:
        return self.template.__name__

    @property
    def __signature__(self) -> inspect.Signature:
        return self._contract.signature.replace(return_annotation=QuantumFragment)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> QuantumFragment:
        """Instantiate the pulse and attach its declared gate semantics."""

        bound = self._contract.signature.bind(*args, **kwargs)
        pulse = _instantiate_bound_pulse_template(self.template, bound)
        operands = tuple(
            cast("Qubit", bound.arguments[name]) for name in self._contract.operands
        )
        resources = tuple(
            cast("Coupler", bound.arguments[name]) for name in self._contract.resources
        )
        arguments = {
            name: cast("CircuitArgument", bound.arguments[name])
            for name in self._contract.arguments
        }
        gate_call: CircuitFragment
        if isinstance(self.gate, SingleQubitGate):
            gate_call = self.gate(operands[0], **arguments)
        else:
            gate_call = self.gate(operands[0], operands[1], **arguments)
        return _implement_gate(
            gate_call,
            pulse,
            resources=resources,
            candidate=self.candidate,
        )


@dataclass(frozen=True, slots=True)
class ProgramFamilyEnvelope:
    """Static gate and size bounds for one point-bound program family."""

    allowed_gates: tuple[Gate, ...]
    max_operations: int
    max_depth: int

    def __post_init__(self) -> None:
        gate_ids = tuple(gate.id for gate in self.allowed_gates)
        duplicate_gate_ids = sorted(
            gate_id for gate_id in set(gate_ids) if gate_ids.count(gate_id) > 1
        )
        if duplicate_gate_ids:
            rendered = ", ".join(repr(gate_id) for gate_id in duplicate_gate_ids)
            raise ValueError(
                f"program family envelope has duplicate allowed gates: {rendered}"
            )
        for name, value in (
            ("max_operations", self.max_operations),
            ("max_depth", self.max_depth),
        ):
            if type(value) is not int or value < 0:
                msg = f"program family envelope {name} must be a non-negative integer"
                raise ValueError(msg)

    @property
    def gate_definitions(self) -> tuple[GateDefinition, ...]:
        """Return the declared semantic gate catalog."""

        return tuple(gate.definition for gate in self.allowed_gates)


@dataclass(frozen=True, slots=True, repr=False)
class FragmentDefinition[**P]:
    """A bounded, typed program family expanded after point inputs bind."""

    id: str
    envelope: ProgramFamilyEnvelope
    _definition: Callable[P, QuantumFragment]
    _contract: _QuantumFunctionContract

    @property
    def parameters(self) -> tuple[ProgramPort, ...]:
        """Return ports in their declared Python order."""

        return self._contract.parameters

    @property
    def allowed_elements(self) -> tuple[PulseElement, ...]:
        """Return the only qubit and coupler handles the family may use."""

        return self._contract.elements

    @property
    def __wrapped__(self) -> Callable[P, QuantumFragment]:
        return self._definition

    @property
    def __name__(self) -> str:
        return self._definition.__name__

    @property
    def __signature__(self) -> inspect.Signature:
        return self._contract.signature

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> QuantumFragment:
        """Record one typed call for expansion during program binding."""

        bound = self._contract.signature.bind(*args, **kwargs)
        arguments = tuple(bound.arguments.items())
        _validate_fragment_call_arguments(self, arguments)
        return _FragmentCall(definition=self, arguments=arguments)


def _implement_gate(
    gate_call: CircuitFragment,
    pulse: QuantumFragment,
    /,
    *,
    resources: SequenceCollection[Coupler] = (),
    candidate: str | None = None,
) -> QuantumFragment:
    """Attach validated pulse structure to one logical gate occurrence."""

    if not isinstance(gate_call, _GateFragment):
        msg = "gate implementation requires one authored gate call"
        raise TypeError(msg)
    facts = _summarize_fragment(pulse)
    if not facts.pulse_only:
        msg = "gate implementation must contain only pulse statements"
        raise TypeError(msg)
    if facts.results:
        msg = "gate implementation cannot acquire results"
        raise ValueError(msg)
    selected_resources = tuple(resources)
    resource_ids = tuple(resource.ir_id for resource in selected_resources)
    if len(set(resource_ids)) != len(resource_ids):
        msg = "gate implementation resources must be unique"
        raise ValueError(msg)
    operand_ids = {qubit.ir_id for qubit in gate_call.qubits}
    allowed_owners = {*operand_ids, *resource_ids}
    pulse_owners = set(facts.pulse_owners)
    foreign_owners = pulse_owners - allowed_owners
    if foreign_owners:
        rendered = ", ".join(
            repr(owner.value)
            for owner in sorted(foreign_owners, key=lambda item: item.value)
        )
        msg = f"gate implementation contains unauthorized signal owners: {rendered}"
        raise ValueError(msg)
    used_resources = {owner for owner in pulse_owners if isinstance(owner, CouplerId)}
    unused_resources = set(resource_ids) - used_resources
    if unused_resources:
        rendered = ", ".join(
            repr(owner.value)
            for owner in sorted(unused_resources, key=lambda item: item.value)
        )
        msg = f"gate implementation declares unused coupler resources: {rendered}"
        raise ValueError(msg)
    if candidate is not None and not candidate.strip():
        msg = "gate implementation candidate must be a non-empty string"
        raise ValueError(msg)
    return _ImplementedGateFragment(
        gate=gate_call,
        pulse=pulse,
        candidate_id=candidate,
    )


def _validate_fragment_call_arguments(
    definition: FragmentDefinition[...],
    arguments: tuple[tuple[str, object], ...],
) -> None:
    if tuple(name for name, _value in arguments) != tuple(
        parameter.id for parameter in definition.parameters
    ):
        raise AssertionError("bound fragment arguments must follow declared ports")
    for (name, actual), formal in zip(
        arguments,
        definition.parameters,
        strict=True,
    ):
        if isinstance(formal, Qubit | Coupler):
            if type(actual) is not type(formal):
                msg = (
                    f"quantum fragment {definition.id!r} port {name!r} requires "
                    f"{type(formal).__name__}"
                )
                raise TypeError(msg)
            continue
        if isinstance(formal, QubitSet | CouplerSet | QubitPairSet):
            raise TypeError("quantum fragments cannot declare entity-set ports")
        if isinstance(actual, Qubit | Coupler):
            msg = f"quantum fragment {definition.id!r} port {name!r} requires a value"
            raise TypeError(msg)
        if isinstance(actual, QuantityExpression):
            # Fragment expansion already coerces its concrete arguments.
            constrain_expression(actual, formal.value_type, port=name)
            continue
        if isinstance(actual, ProgramInput):
            expected = _program_input_type(formal, non_negative=False)
            supplied = _program_input_type(actual, non_negative=False)
            if supplied != expected:
                msg = (
                    f"quantum fragment {definition.id!r} port {name!r} requires "
                    f"{expected!r}, but input {actual.id!r} declares {supplied!r}"
                )
                raise TypeError(msg)
            continue
        try:
            coerce_literal(
                _program_input_type(formal, non_negative=False),
                actual,
                path=("fragment", definition.id, name),
            )
        except ValueValidationError as error:
            raise TypeError(str(error)) from error


def _instantiate_bound_pulse_template(
    template: PulseTemplateDefinition[...],
    bound: inspect.BoundArguments,
) -> PulseFragment:
    elements: list[PulseElement] = []
    inputs: dict[str, _PulseTemplateArgument] = {}
    for formal in template.parameters:
        actual = cast("object", bound.arguments[formal.id])
        if isinstance(formal, Qubit | Coupler):
            elements.append(cast("PulseElement", actual))
        else:
            inputs[formal.id] = cast("_PulseTemplateArgument", actual)
    return _instantiate_pulse_template(template, tuple(elements), inputs)


def _instantiate_pulse_template(
    template: PulseTemplateDefinition[...],
    elements: tuple[PulseElement, ...],
    inputs: Mapping[str, _PulseTemplateArgument],
) -> PulseFragment:
    for index, (formal, actual) in enumerate(
        zip(template.elements, elements, strict=True)
    ):
        if type(formal) is not type(actual):
            msg = (
                f"pulse template {template.id!r} element {index} requires "
                f"{type(formal).__name__}, got {type(actual).__name__}"
            )
            raise TypeError(msg)
    actual_ids = tuple(_element_ir_id(element) for element in elements)
    if len(set(actual_ids)) != len(actual_ids):
        msg = f"pulse template {template.id!r} elements must be unique"
        raise ValueError(msg)

    expected = {input_handle.id: input_handle for input_handle in template.inputs}
    input_bindings: dict[
        ProgramInput, Quantity | int | float | ProgramInput | QuantityExpression
    ] = {}
    for input_id, formal in expected.items():
        selected = inputs[input_id]
        if isinstance(selected, QuantityExpression):
            input_bindings[formal] = constrain_expression(
                selected, formal.value_type, port=input_id
            )
            continue
        if isinstance(selected, ProgramInput):
            if selected.value_type != formal.value_type:
                msg = (
                    f"pulse template input {input_id!r} requires "
                    f"{formal.value_type!r}, but outer input {selected.id!r} "
                    "declares an incompatible type"
                )
                raise TypeError(msg)
            input_bindings[formal] = selected
            continue
        try:
            coerced = coerce_literal(formal.value_type, selected)
        except ValueValidationError as error:
            msg = f"invalid pulse template input {input_id!r}: {error}"
            raise TypeError(msg) from error
        if not isinstance(coerced, Quantity | int | float):
            msg = f"pulse template input {input_id!r} is not a scalar pulse value"
            raise TypeError(msg)
        input_bindings[formal] = coerced

    element_bindings = {
        _element_ir_id(formal): _element_ir_id(actual)
        for formal, actual in zip(template.elements, elements, strict=True)
    }
    instantiated = _substitute_pulse_fragment(
        template.body,
        element_bindings=element_bindings,
        input_bindings=input_bindings,
    )
    return _PulseTemplateCallFragment(
        template=template,
        body=instantiated,
    )


def _substitute_pulse_fragment(
    fragment: QuantumFragment,
    *,
    element_bindings: Mapping[QubitId | CouplerId, QubitId | CouplerId],
    input_bindings: Mapping[
        ProgramInput, Quantity | int | float | ProgramInput | QuantityExpression
    ],
) -> QuantumFragment:
    if isinstance(fragment, _PlayFragment):
        return _PlayFragment(
            signal=cast(
                "PlaySignal", _substitute_signal(fragment.signal, element_bindings)
            ),
            envelope=_substitute_envelope(fragment.envelope, input_bindings),
        )
    if isinstance(fragment, _DelayFragment):
        return _DelayFragment(
            signal=cast(
                "PlaySignal", _substitute_signal(fragment.signal, element_bindings)
            ),
            duration=cast(
                "QuantumQuantity",
                _substitute_template_value(fragment.duration, input_bindings),
            ),
        )
    if isinstance(fragment, _ShiftPhaseFragment):
        return _ShiftPhaseFragment(
            signal=cast(
                "FrameSignal", _substitute_signal(fragment.signal, element_bindings)
            ),
            phase=cast(
                "QuantumQuantity",
                _substitute_template_value(fragment.phase, input_bindings),
            ),
        )
    if isinstance(fragment, _PulseTemplateCallFragment):
        return _PulseTemplateCallFragment(
            template=fragment.template,
            body=_substitute_pulse_fragment(
                fragment.body,
                element_bindings=element_bindings,
                input_bindings=input_bindings,
            ),
        )
    if isinstance(fragment, _QuantumSequenceFragment):
        return _QuantumSequenceFragment(
            operations=tuple(
                _substitute_pulse_fragment(
                    child,
                    element_bindings=element_bindings,
                    input_bindings=input_bindings,
                )
                for child in fragment.operations
            )
        )
    if isinstance(fragment, _QuantumParallelFragment):
        return _QuantumParallelFragment(
            branches=tuple(
                _substitute_pulse_fragment(
                    child,
                    element_bindings=element_bindings,
                    input_bindings=input_bindings,
                )
                for child in fragment.branches
            ),
            alignment=fragment.alignment,
        )
    if isinstance(fragment, _QuantumRepeatFragment):
        count = _substitute_template_value(fragment.count, input_bindings)
        return _QuantumRepeatFragment(
            operation=_substitute_pulse_fragment(
                fragment.operation,
                element_bindings=element_bindings,
                input_bindings=input_bindings,
            ),
            count=cast("RepeatCount", count),
        )
    raise AssertionError("verified pulse templates contain only pulse fragments")


def _substitute_envelope(
    envelope: PulseEnvelope | AnalyticEnvelope,
    bindings: Mapping[
        ProgramInput, Quantity | int | float | ProgramInput | QuantityExpression
    ],
) -> PulseEnvelope | AnalyticEnvelope:
    if not isinstance(envelope, PulseEnvelope):
        return envelope
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
    return _pulse_envelope(
        kind,
        duration=cast(
            "QuantumQuantity",
            _substitute_template_value(duration, bindings),
        ),
        amplitude=cast(
            "QuantumQuantity",
            _substitute_template_value(amplitude, bindings),
        ),
        sigma=(
            cast("QuantumQuantity", _substitute_template_value(sigma, bindings))
            if sigma is not None
            else None
        ),
        derivative_beta=(
            cast(
                "QuantumQuantity",
                _substitute_template_value(derivative_beta, bindings),
            )
            if derivative_beta is not None
            else None
        ),
        rise_duration=(
            cast(
                "QuantumQuantity",
                _substitute_template_value(rise_duration, bindings),
            )
            if rise_duration is not None
            else None
        ),
        fall_duration=(
            cast(
                "QuantumQuantity",
                _substitute_template_value(fall_duration, bindings),
            )
            if fall_duration is not None
            else None
        ),
        phase=cast(
            "QuantumQuantity",
            _substitute_template_value(phase, bindings),
        ),
        frequency_offset=(
            cast(
                "QuantumQuantity",
                _substitute_template_value(frequency_offset, bindings),
            )
            if frequency_offset is not None
            else None
        ),
        frequency_reference=frequency_reference,
    )


def _substitute_template_value(
    value: object,
    bindings: Mapping[
        ProgramInput, Quantity | int | float | ProgramInput | QuantityExpression
    ],
) -> object:
    return substitute_expression(value, bindings)


def _substitute_signal(
    signal: LogicalSignal,
    bindings: Mapping[QubitId | CouplerId, QubitId | CouplerId],
) -> LogicalSignal:
    if isinstance(signal, DriveSignal):
        owner = bindings.get(signal.qubit, signal.qubit)
        assert isinstance(owner, QubitId)
        return DriveSignal(owner)
    if isinstance(signal, ReadoutSignal):
        owner = bindings.get(signal.qubit, signal.qubit)
        assert isinstance(owner, QubitId)
        return ReadoutSignal(owner)
    if isinstance(signal, AcquireSignal):
        owner = bindings.get(signal.qubit, signal.qubit)
        assert isinstance(owner, QubitId)
        return AcquireSignal(owner)
    owner = signal.owner
    return FluxSignal(bindings.get(owner, owner))
