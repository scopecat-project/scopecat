"""Config-free verification of logical circuit operations."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence as SequenceCollection
from dataclasses import dataclass, replace

from scopecat import Quantity
from scopecat.kernel.value_types import Quantity as QuantityType

from scopecat_quantum._ids import (
    AcquisitionSlotId,
    CircuitOperationId,
    GateId,
    QubitId,
)
from scopecat_quantum.acquisitions import (
    QuantumResultContract,
)
from scopecat_quantum.gates import (
    GateArgument,
    GateCall,
    GateDefinition,
    GateParameterDefinition,
    GateParameterKind,
    canonical_angle_value,
    canonical_gate_quantity,
    gate_parameter_label,
    quantity_argument_matches,
)


@dataclass(frozen=True, slots=True)
class Measure:
    """A logical measurement and its acquisition result."""

    id: CircuitOperationId
    qubit: QubitId
    acquisition_slot_id: AcquisitionSlotId
    contract: QuantumResultContract

    def __post_init__(self) -> None:
        if not self.contract.is_concrete:
            raise ValueError(
                "bound logical measurements require point-bound result dimensions"
            )


type CircuitOperation = GateCall | Measure


type CircuitIssuePathItem = str | int


@dataclass(frozen=True, slots=True)
class CircuitIssue:
    """One stable, machine-readable circuit verification issue."""

    code: str
    message: str
    path: tuple[CircuitIssuePathItem, ...] = ()


class CircuitVerificationError(ValueError):
    """Aggregate failure raised after all independent checks have run."""

    def __init__(self, issues: SequenceCollection[CircuitIssue]) -> None:
        self.issues = tuple(issues)
        summary = "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
        super().__init__(summary)


@dataclass(frozen=True, slots=True)
class VerifiedCircuitOperations:
    """Flat circuit facts safe for implementation materialization.

    Gate-call arguments are canonicalized into definition order so downstream
    implementation binding does not depend on authoring argument order.
    """

    operations: tuple[CircuitOperation, ...]
    gate_definitions: tuple[GateDefinition, ...]

    def __post_init__(self) -> None:
        definitions, operations = _verified_circuit_components(
            self.operations,
            self.gate_definitions,
        )
        object.__setattr__(self, "gate_definitions", definitions)
        object.__setattr__(self, "operations", operations)

    def gate_definition(self, gate_id: GateId) -> GateDefinition:
        """Return the proven unique definition for ``gate_id``."""

        for definition in self.gate_definitions:
            if definition.id == gate_id:
                return definition
        msg = f"verified circuit has no gate definition {gate_id.value!r}"
        raise KeyError(msg)


def verify_circuit_operations(
    operations: SequenceCollection[CircuitOperation],
    gate_definitions: SequenceCollection[GateDefinition],
) -> VerifiedCircuitOperations:
    """Verify ordered operations against an exact gate catalog.

    Catalog, identity, argument, and arity checks contribute to one aggregate
    error instead of stopping at the first malformed operation.
    """

    return VerifiedCircuitOperations(tuple(operations), tuple(gate_definitions))


def _verified_circuit_components(
    operations: SequenceCollection[CircuitOperation],
    gate_definitions: SequenceCollection[GateDefinition],
) -> tuple[tuple[GateDefinition, ...], tuple[CircuitOperation, ...]]:
    """Validate and canonicalize the fields stored by a verified circuit."""

    issues: list[CircuitIssue] = []
    catalog = _verify_gate_catalog(gate_definitions, issues)
    operation_entries = tuple(
        (operation, ("operations", index)) for index, operation in enumerate(operations)
    )
    _verify_operation_identities(operation_entries, issues)

    canonical_operations: list[CircuitOperation] = []
    for operation, path in operation_entries:
        if isinstance(operation, Measure):
            canonical_operations.append(operation)
            continue
        definition = catalog.get(operation.gate_id)
        if definition is None:
            duplicate_count = sum(
                candidate.id == operation.gate_id for candidate in gate_definitions
            )
            code = (
                "circuit_gate_ambiguous"
                if duplicate_count > 1
                else "circuit_gate_unknown"
            )
            qualifier = "ambiguous" if duplicate_count > 1 else "unknown"
            issues.append(
                CircuitIssue(
                    code=code,
                    message=(
                        f"gate call {operation.id.value!r} references {qualifier} "
                        f"gate {operation.gate_id.value!r}"
                    ),
                    path=(*path, "gate_id"),
                )
            )
            canonical_operations.append(operation)
            continue
        canonical_operations.append(
            _verify_gate_call(operation, definition, path, issues)
        )

    if issues:
        raise CircuitVerificationError(sorted(issues, key=_issue_sort_key))
    return (
        tuple(sorted(gate_definitions, key=lambda definition: definition.id.value)),
        tuple(canonical_operations),
    )


def _verify_gate_catalog(
    definitions: SequenceCollection[GateDefinition],
    issues: list[CircuitIssue],
) -> dict[GateId, GateDefinition]:
    grouped: dict[GateId, list[GateDefinition]] = defaultdict(list)
    for definition in definitions:
        grouped[definition.id].append(definition)

    catalog: dict[GateId, GateDefinition] = {}
    for gate_id in sorted(grouped, key=lambda item: item.value):
        candidates = grouped[gate_id]
        if len(candidates) > 1:
            issues.append(
                CircuitIssue(
                    code="gate_catalog_duplicate",
                    message=f"gate catalog defines {gate_id.value!r} more than once",
                    path=("gate_definitions", gate_id.value),
                )
            )
            continue
        definition = candidates[0]
        catalog[gate_id] = definition
        parameter_counts = Counter(parameter.id for parameter in definition.parameters)
        for parameter_id in sorted(parameter_counts):
            if parameter_counts[parameter_id] > 1:
                issues.append(
                    CircuitIssue(
                        code="gate_parameter_duplicate",
                        message=(
                            f"gate {gate_id.value!r} defines parameter "
                            f"{parameter_id!r} more than once"
                        ),
                        path=(
                            "gate_definitions",
                            gate_id.value,
                            "parameters",
                            parameter_id,
                        ),
                    )
                )
    return catalog


def _verify_operation_identities(
    entries: SequenceCollection[
        tuple[CircuitOperation, tuple[CircuitIssuePathItem, ...]]
    ],
    issues: list[CircuitIssue],
) -> None:
    operation_paths: dict[
        CircuitOperationId, list[tuple[CircuitIssuePathItem, ...]]
    ] = defaultdict(list)
    acquisition_paths: dict[
        AcquisitionSlotId, list[tuple[CircuitIssuePathItem, ...]]
    ] = defaultdict(list)
    for operation, path in entries:
        operation_paths[operation.id].append(path)
        if isinstance(operation, Measure):
            acquisition_paths[operation.acquisition_slot_id].append(path)

    for operation_id in sorted(operation_paths, key=lambda item: item.value):
        paths = operation_paths[operation_id]
        if len(paths) > 1:
            issues.append(
                CircuitIssue(
                    code="circuit_operation_duplicate",
                    message=(
                        f"circuit operation id {operation_id.value!r} is used "
                        "more than once"
                    ),
                    path=(*paths[0], "id"),
                )
            )
    for slot_id in sorted(
        acquisition_paths,
        key=lambda item: (item.scope, item.local_id),
    ):
        paths = acquisition_paths[slot_id]
        if len(paths) > 1:
            issues.append(
                CircuitIssue(
                    code="circuit_acquisition_slot_duplicate",
                    message=(
                        f"acquisition slot id {slot_id.value!r} is produced "
                        "more than once"
                    ),
                    path=(*paths[0], "acquisition_slot_id"),
                )
            )


def _verify_gate_call(
    call: GateCall,
    definition: GateDefinition,
    path: tuple[CircuitIssuePathItem, ...],
    issues: list[CircuitIssue],
) -> GateCall:
    if len(call.qubits) != definition.qubit_arity:
        issues.append(
            CircuitIssue(
                code="circuit_gate_arity_mismatch",
                message=(
                    f"gate call {call.id.value!r} supplies {len(call.qubits)} qubits; "
                    f"gate {definition.id.value!r} requires "
                    f"{definition.qubit_arity}"
                ),
                path=(*path, "qubits"),
            )
        )
    duplicate_qubits = _duplicates(call.qubits)
    for qubit in sorted(duplicate_qubits, key=lambda item: item.value):
        issues.append(
            CircuitIssue(
                code="circuit_gate_qubit_duplicate",
                message=(
                    f"gate call {call.id.value!r} uses qubit {qubit.value!r} "
                    "more than once"
                ),
                path=(*path, "qubits"),
            )
        )

    supplied: dict[str, GateArgument] = {}
    argument_counts = Counter(argument.id for argument in call.arguments)
    for argument in call.arguments:
        supplied.setdefault(argument.id, argument)
    for argument_id in sorted(argument_counts):
        if argument_counts[argument_id] > 1:
            issues.append(
                CircuitIssue(
                    code="circuit_gate_argument_duplicate",
                    message=(
                        f"gate call {call.id.value!r} supplies argument "
                        f"{argument_id!r} more than once"
                    ),
                    path=(*path, "arguments", argument_id),
                )
            )

    expected = {parameter.id: parameter for parameter in definition.parameters}
    for parameter_id in sorted(expected.keys() - supplied.keys()):
        issues.append(
            CircuitIssue(
                code="circuit_gate_argument_missing",
                message=(
                    f"gate call {call.id.value!r} is missing argument {parameter_id!r}"
                ),
                path=(*path, "arguments", parameter_id),
            )
        )
    for argument_id in sorted(supplied.keys() - expected.keys()):
        issues.append(
            CircuitIssue(
                code="circuit_gate_argument_unknown",
                message=(
                    f"gate call {call.id.value!r} supplies unknown argument "
                    f"{argument_id!r}"
                ),
                path=(*path, "arguments", argument_id),
            )
        )
    for parameter in definition.parameters:
        argument = supplied.get(parameter.id)
        if argument is not None and not _argument_matches(parameter, argument):
            issues.append(
                CircuitIssue(
                    code="circuit_gate_argument_type_mismatch",
                    message=(
                        f"gate call {call.id.value!r} argument {parameter.id!r} "
                        f"does not satisfy {gate_parameter_label(parameter.kind)!r}"
                    ),
                    path=(*path, "arguments", parameter.id),
                )
            )

    if set(supplied) != set(expected) or any(
        count > 1 for count in argument_counts.values()
    ):
        return call
    return replace(
        call,
        arguments=tuple(
            _canonical_gate_argument(parameter, supplied[parameter.id])
            for parameter in definition.parameters
        ),
    )


def _canonical_gate_argument(
    parameter: GateParameterDefinition,
    argument: GateArgument,
) -> GateArgument:
    """Normalize equivalent values before exact implementation binding."""

    if isinstance(parameter.kind, QuantityType) and isinstance(
        argument.value, Quantity
    ):
        if quantity_argument_matches(argument.value, parameter.kind):
            return replace(argument, value=canonical_gate_quantity(argument.value))
        return argument
    if parameter.kind is not GateParameterKind.ANGLE:
        return argument
    if not _argument_matches(parameter, argument):
        return argument
    value = argument.value
    if not isinstance(value, Quantity):
        return argument
    try:
        return replace(argument, value=canonical_angle_value(value))
    except ValueError:
        return argument


def _argument_matches(
    parameter: GateParameterDefinition,
    argument: GateArgument,
) -> bool:
    value = argument.value
    if isinstance(parameter.kind, QuantityType):
        return quantity_argument_matches(value, parameter.kind)
    if parameter.kind is GateParameterKind.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if parameter.kind is GateParameterKind.NUMBER:
        if not isinstance(value, int | float) or isinstance(value, bool):
            return False
        try:
            return math.isfinite(float(value))
        except OverflowError:
            return False
    if parameter.kind is GateParameterKind.ANGLE:
        if not isinstance(value, Quantity):
            return False
        if not math.isfinite(value.value):
            return False
        try:
            value.to("rad")
        except ValueError:
            return False
        return True
    return False


def _duplicates[T](values: SequenceCollection[T]) -> set[T]:
    seen: set[T] = set()
    duplicates: set[T] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _issue_sort_key(issue: CircuitIssue) -> tuple[tuple[str, ...], str, str]:
    return (
        tuple(f"{type(item).__name__}:{item}" for item in issue.path),
        issue.code,
        issue.message,
    )
