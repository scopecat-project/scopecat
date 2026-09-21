"""Mixed gate-and-pulse quantum programs and their pulse refinement.

The source tree in this module is deliberately heterogeneous: logical gate and
measurement operations may be composed with authored pulse blocks, while an
``ImplementedGate`` retains both a gate's semantic identity and a local pulse
implementation. Pulse planning pairs that retained tree with the exact
point-effective implementation catalog; target preparation later produces a
bounded target program of scheduled pulse regions and retained control flow.

Pulse resolution retains finite maps, repeats, and classified-state switches
with an exact implementation catalog. Target preparation schedules maximal
static regions while preserving bounded real-time control for the compiler.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter
from collections.abc import Iterator
from collections.abc import Sequence as SequenceCollection
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from scopecat import Quantity

from scopecat_quantum._ids import (
    AcquisitionSlotId,
    CircuitOperationId,
    PulseEventId,
    PulseProgramId,
    QuantumProgramId,
    QubitId,
)
from scopecat_quantum.acquisitions import AcquisitionKind, QuantumResultContract
from scopecat_quantum.circuits import (
    CircuitIssue,
    CircuitVerificationError,
    Measure,
    VerifiedCircuitOperations,
    verify_circuit_operations,
)
from scopecat_quantum.gates import GateCall, GateDefinition
from scopecat_quantum.measurement_implementations import (
    MeasurementPulseImplementationBinding,
)
from scopecat_quantum.pulse_implementations import (
    GatePulseImplementationBinding,
    PulseImplementationIndex,
    ResolvedPulseImplementations,
)
from scopecat_quantum.pulses import (
    Acquire,
    AcquireSignal,
    AcquisitionSlot,
    CosineFlatTop,
    DriveSignal,
    FluxSignal,
    LogicalSignal,
    Play,
    PlaySignal,
    PulseInstruction,
    PulseProgram,
    PulseValidationError,
    ReadoutSignal,
    TimeShift,
    iter_pulse_leaves,
    pulse_leaf_owners,
    schedule,
)
from scopecat_quantum.pulses import Parallel as PulseParallel
from scopecat_quantum.pulses import Sequence as PulseSequence
from scopecat_quantum.realtime import (
    ClassifiedStatePredicate,
    RealtimeCase,
    RealtimeConditional,
    RealtimeInstruction,
    RealtimeNoOp,
    RealtimeParallel,
    RealtimeRepeat,
    RealtimeSequence,
    ScheduledBlock,
    TargetProgram,
)


@dataclass(frozen=True, slots=True)
class PulseBlock:
    """One authored occurrence of a reusable or inline pulse template."""

    id: CircuitOperationId
    pulse_template: PulseProgram
    acquisition_slot_bindings: tuple[
        tuple[AcquisitionSlotId, AcquisitionSlotId], ...
    ] = ()


@dataclass(frozen=True, slots=True)
class ImplementedGate:
    """A logical gate occurrence with an explicit local pulse implementation."""

    call: GateCall
    pulse_template: PulseProgram
    candidate_id: str | None = None

    @property
    def id(self) -> CircuitOperationId:
        return self.call.id


@dataclass(frozen=True, slots=True)
class Sequence:
    """Mixed quantum nodes executed in order."""

    operations: tuple[QuantumNode, ...]


@dataclass(frozen=True, slots=True)
class Parallel:
    """Compose branches with common start or end times."""

    branches: tuple[QuantumNode, ...]
    alignment: Literal["start", "end"] = "start"


@dataclass(frozen=True, slots=True)
class FlatTopWindow:
    """A smooth window whose plateau follows a static lowered body's duration."""

    id: CircuitOperationId
    signal: PlaySignal
    operation: QuantumNode
    amplitude: Quantity
    rise_duration: Quantity
    fall_duration: Quantity
    settle_duration: Quantity

    def __post_init__(self) -> None:
        for name, quantity in (
            ("rise_duration", self.rise_duration),
            ("fall_duration", self.fall_duration),
            ("settle_duration", self.settle_duration),
        ):
            value = quantity.to("s").value
            if not math.isfinite(value) or value < 0:
                raise ValueError(
                    f"flat_top_window {name} must be finite and non-negative"
                )
        if _node_requires_realtime_target(self.operation):
            raise ValueError(
                "flat_top_window requires a static body without real-time control"
            )


@dataclass(frozen=True, slots=True)
class ParallelEach:
    """One retained entity-set map with a single bound body template."""

    entity_set_id: str
    item_id: QubitId
    entity_ids: tuple[QubitId, ...]
    operation: QuantumNode

    def __post_init__(self) -> None:
        if not self.entity_set_id:
            raise ValueError("quantum parallel_each entity-set id must be non-empty")
        if not self.entity_ids:
            raise ValueError("quantum parallel_each entity set must not be empty")
        if len(set(self.entity_ids)) != len(self.entity_ids):
            raise ValueError("quantum parallel_each entity ids must be unique")


@dataclass(frozen=True, slots=True)
class Repeat:
    """A finite subtree, optionally indexing results by one local dimension."""

    operation: QuantumNode
    count: int
    result_dimension_id: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or self.count < 0:
            raise ValueError("quantum repeat count must be a non-negative integer")
        if (
            self.result_dimension_id is not None
            and not self.result_dimension_id.strip()
        ):
            raise ValueError("quantum repeat result dimension id must be non-empty")


@dataclass(frozen=True, slots=True)
class Conditional:
    """A finite classified-state switch retained for target compilation."""

    predicate: AcquisitionSlotId
    cases: tuple[tuple[int, QuantumNode], ...]
    default: QuantumNode | None = None

    def __post_init__(self) -> None:
        if not self.cases:
            raise ValueError("quantum conditionals require at least one case")
        states = tuple(state for state, _body in self.cases)
        if any(type(state) is not int for state in states):
            raise ValueError("quantum conditional case states must be integers")
        if len(set(states)) != len(states):
            raise ValueError("quantum conditional case states must be unique")


type QuantumOperation = GateCall | Measure | PulseBlock | ImplementedGate
type QuantumNode = (
    QuantumOperation
    | Sequence
    | Parallel
    | ParallelEach
    | Repeat
    | Conditional
    | FlatTopWindow
)


@dataclass(frozen=True, slots=True)
class QuantumProgramIR:
    """One concrete mixed quantum source tree before pulse refinement."""

    id: QuantumProgramId
    body: QuantumNode


type QuantumIssuePathItem = str | int


@dataclass(frozen=True, slots=True)
class QuantumProgramIssue:
    """One source-level mixed-program verification problem."""

    code: str
    message: str
    path: tuple[QuantumIssuePathItem, ...] = ()
    operation_id: CircuitOperationId | None = None


class QuantumProgramVerificationError(ValueError):
    """Aggregate of independently discoverable mixed-program issues."""

    __slots__ = ("issues",)

    def __init__(self, issues: SequenceCollection[QuantumProgramIssue]) -> None:
        selected = tuple(issues)
        if not selected:
            msg = "quantum program verification errors require at least one issue"
            raise ValueError(msg)
        self.issues = tuple(
            sorted(
                selected,
                key=lambda issue: (
                    tuple(str(item) for item in issue.path),
                    issue.code,
                    issue.message,
                ),
            )
        )
        super().__init__(
            "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
        )


class QuantumProgramExpansionError(ValueError):
    """A retained program exceeds the concrete lowering budget."""

    def __init__(self, *, expanded_operation_count: int, limit: int) -> None:
        self.expanded_operation_count = expanded_operation_count
        self.limit = limit
        super().__init__(
            "quantum program requires "
            f"{expanded_operation_count} expanded operations; lowering budget "
            f"is {limit}"
        )


@dataclass(frozen=True, slots=True)
class VerifiedQuantumProgram:
    """Mixed source plus template-level logical and unresolved proofs."""

    program: QuantumProgramIR
    logical_operations: tuple[GateCall | Measure, ...]
    unresolved: VerifiedCircuitOperations

    @property
    def operations(self) -> tuple[QuantumOperation, ...]:
        return tuple(iter_quantum_operations(self.program.body))

    def require_expansion_budget(
        self,
        max_expanded_operations: int | None,
    ) -> QuantumProgramWorkload:
        """Check finite lowering work without instantiating retained control flow."""

        workload = estimate_quantum_program_workload(self)
        if (
            max_expanded_operations is not None
            and workload.expanded_operation_count > max_expanded_operations
        ):
            raise QuantumProgramExpansionError(
                expanded_operation_count=workload.expanded_operation_count,
                limit=max_expanded_operations,
            )
        return workload

    def iter_expanded_unresolved_operations(
        self,
        *,
        max_expanded_operations: int | None = None,
    ) -> Iterator[GateCall | Measure]:
        """Stream concrete unresolved leaves after a constant-memory preflight."""

        if max_expanded_operations is not None:
            self.require_expansion_budget(max_expanded_operations)
        return (
            operation
            for operation in iter_expanded_quantum_operations(self.program.body)
            if isinstance(operation, GateCall | Measure)
        )


@dataclass(frozen=True, slots=True)
class QuantumProgramWorkload:
    """Logical expansion estimate without lowering pulses or rendering buffers."""

    structural_operation_count: int
    expanded_operation_count: int
    selected_entity_count: int
    max_parallel_width: int


def _window_qubits(node: FlatTopWindow) -> tuple[QubitId, ...]:
    owner = (
        node.signal.owner if isinstance(node.signal, FluxSignal) else node.signal.qubit
    )
    return (owner,) if isinstance(owner, QubitId) else ()


def estimate_quantum_program_workload(
    program: VerifiedQuantumProgram,
) -> QuantumProgramWorkload:
    """Summarize retained control flow and its finite logical expansion."""

    selected_entities: set[QubitId] = set()

    def estimate(
        node: QuantumNode,
        template_items: frozenset[QubitId] | None = None,
    ) -> tuple[int, int]:
        selected_template_items: frozenset[QubitId] = (
            frozenset() if template_items is None else template_items
        )
        if isinstance(node, GateCall):
            selected_entities.update(
                qubit for qubit in node.qubits if qubit not in selected_template_items
            )
            return 1, 1
        if isinstance(node, Measure):
            if node.qubit not in selected_template_items:
                selected_entities.add(node.qubit)
            return 1, 1
        if isinstance(node, ImplementedGate):
            selected_entities.update(
                qubit
                for qubit in node.call.qubits
                if qubit not in selected_template_items
            )
            return 1, 1
        if isinstance(node, PulseBlock):
            selected_entities.update(
                owner
                for owner in pulse_leaf_owners(node.pulse_template.body)
                if isinstance(owner, QubitId) and owner not in selected_template_items
            )
            return 1, 1
        if isinstance(node, FlatTopWindow):
            structural, expanded = estimate(node.operation, selected_template_items)
            selected_entities.update(
                owner
                for owner in _window_qubits(node)
                if owner not in selected_template_items
            )
            return structural + 1, expanded + 1
        if isinstance(node, Repeat):
            structural, expanded = estimate(node.operation, selected_template_items)
            return structural, expanded * node.count
        if isinstance(node, ParallelEach):
            selected_entities.update(node.entity_ids)
            structural, expanded = estimate(
                node.operation,
                selected_template_items | {node.item_id},
            )
            return structural, expanded * len(node.entity_ids)
        if isinstance(node, Conditional):
            children = tuple(body for _state, body in node.cases)
            if node.default is not None:
                children = (*children, node.default)
        else:
            children = node.operations if isinstance(node, Sequence) else node.branches
        estimates = tuple(
            estimate(child, selected_template_items) for child in children
        )
        return (
            sum(item[0] for item in estimates),
            sum(item[1] for item in estimates),
        )

    def parallel_width(node: QuantumNode) -> int:
        if isinstance(node, GateCall | Measure | ImplementedGate | PulseBlock):
            return 1
        if isinstance(node, FlatTopWindow):
            return 1 + parallel_width(node.operation)
        if isinstance(node, Repeat):
            return parallel_width(node.operation)
        if isinstance(node, ParallelEach):
            return len(node.entity_ids) * parallel_width(node.operation)
        if isinstance(node, Conditional):
            children = tuple(body for _state, body in node.cases)
            if node.default is not None:
                children = (*children, node.default)
            return max((parallel_width(child) for child in children), default=0)
        children = node.operations if isinstance(node, Sequence) else node.branches
        child_widths = tuple(parallel_width(child) for child in children)
        if isinstance(node, Parallel):
            return sum(child_widths)
        return max(child_widths, default=0)

    structural, expanded = estimate(program.program.body)
    return QuantumProgramWorkload(
        structural_operation_count=structural,
        expanded_operation_count=expanded,
        selected_entity_count=len(selected_entities),
        max_parallel_width=parallel_width(program.program.body),
    )


def iter_quantum_operations(node: QuantumNode) -> Iterator[QuantumOperation]:
    """Yield mixed leaves in deterministic structural order."""

    if isinstance(
        node,
        GateCall | Measure | PulseBlock | ImplementedGate,
    ):
        yield node
        return
    if isinstance(node, FlatTopWindow):
        yield from iter_quantum_operations(node.operation)
        return
    if isinstance(node, Repeat):
        if node.count:
            yield from iter_quantum_operations(node.operation)
        return
    if isinstance(node, ParallelEach):
        for entity_id in node.entity_ids:
            yield from iter_quantum_operations(
                instantiate_parallel_each_operation(node, entity_id)
            )
        return
    if isinstance(node, Conditional):
        for _state, body in node.cases:
            yield from iter_quantum_operations(body)
        if node.default is not None:
            yield from iter_quantum_operations(node.default)
        return
    children = node.operations if isinstance(node, Sequence) else node.branches
    for child in children:
        yield from iter_quantum_operations(child)


def iter_expanded_quantum_operations(node: QuantumNode) -> Iterator[QuantumOperation]:
    """Yield concrete leaves in deterministic occurrence order."""

    if isinstance(
        node,
        GateCall | Measure | PulseBlock | ImplementedGate,
    ):
        yield node
        return
    if isinstance(node, FlatTopWindow):
        yield from iter_expanded_quantum_operations(node.operation)
        return
    if isinstance(node, Repeat):
        for _ in range(node.count):
            yield from iter_expanded_quantum_operations(node.operation)
        return
    if isinstance(node, ParallelEach):
        for entity_id in node.entity_ids:
            yield from iter_expanded_quantum_operations(
                instantiate_parallel_each_operation(node, entity_id)
            )
        return
    if isinstance(node, Conditional):
        for _state, body in node.cases:
            yield from iter_expanded_quantum_operations(body)
        if node.default is not None:
            yield from iter_expanded_quantum_operations(node.default)
        return
    children = node.operations if isinstance(node, Sequence) else node.branches
    for child in children:
        yield from iter_expanded_quantum_operations(child)


def instantiate_parallel_each_operation(
    node: ParallelEach,
    entity_id: QubitId,
) -> QuantumNode:
    """Instantiate one retained map body with entity-keyed identities."""

    if entity_id not in node.entity_ids:
        raise KeyError(entity_id)
    scope = (node.entity_set_id, entity_id.value)
    return _substitute_quantum_node(
        node.operation,
        source=node.item_id,
        target=entity_id,
        scope=scope,
    )


def _scoped_operation_id(
    operation_id: CircuitOperationId,
    scope: tuple[str, ...],
) -> CircuitOperationId:
    return CircuitOperationId("/".join((*scope, operation_id.value)))


def _substitute_qubit(
    qubit: QubitId,
    *,
    source: QubitId,
    target: QubitId,
) -> QubitId:
    return target if qubit == source else qubit


def _substitute_pulse_template(
    template: PulseProgram,
    *,
    source: QubitId,
    target: QubitId,
) -> PulseProgram:
    def substitute_signal(signal: LogicalSignal) -> LogicalSignal:
        if isinstance(signal, DriveSignal | ReadoutSignal | AcquireSignal):
            return replace(
                signal,
                qubit=_substitute_qubit(
                    signal.qubit,
                    source=source,
                    target=target,
                ),
            )
        if isinstance(signal.owner, QubitId):
            return replace(
                signal,
                owner=_substitute_qubit(
                    signal.owner,
                    source=source,
                    target=target,
                ),
            )
        return signal

    def substitute_instruction(instruction: PulseInstruction) -> PulseInstruction:
        if isinstance(instruction, TimeShift):
            return replace(
                instruction, instruction=substitute_instruction(instruction.instruction)
            )
        if isinstance(instruction, PulseSequence):
            return PulseSequence(
                tuple(
                    substitute_instruction(child) for child in instruction.instructions
                )
            )
        if isinstance(instruction, PulseParallel):
            return PulseParallel(
                tuple(substitute_instruction(child) for child in instruction.branches),
                alignment=instruction.alignment,
            )
        return replace(instruction, signal=substitute_signal(instruction.signal))

    return PulseProgram(
        id=template.id,
        body=substitute_instruction(template.body),
        acquisition_slots=tuple(
            replace(slot, signal=substitute_signal(slot.signal))
            for slot in template.acquisition_slots
        ),
    )


def _substitute_quantum_node(
    node: QuantumNode,
    *,
    source: QubitId,
    target: QubitId,
    scope: tuple[str, ...],
) -> QuantumNode:
    if isinstance(node, GateCall):
        return replace(
            node,
            id=_scoped_operation_id(node.id, scope),
            qubits=tuple(
                _substitute_qubit(qubit, source=source, target=target)
                for qubit in node.qubits
            ),
        )
    if isinstance(node, Measure):
        return replace(
            node,
            id=_scoped_operation_id(node.id, scope),
            qubit=_substitute_qubit(node.qubit, source=source, target=target),
            acquisition_slot_id=node.acquisition_slot_id.prefixed(*scope),
        )
    if isinstance(node, PulseBlock):
        return replace(
            node,
            id=_scoped_operation_id(node.id, scope),
            pulse_template=_substitute_pulse_template(
                node.pulse_template,
                source=source,
                target=target,
            ),
            acquisition_slot_bindings=tuple(
                (template_id, output_id.prefixed(*scope))
                for template_id, output_id in node.acquisition_slot_bindings
            ),
        )
    if isinstance(node, ImplementedGate):
        call = _substitute_quantum_node(
            node.call,
            source=source,
            target=target,
            scope=scope,
        )
        assert isinstance(call, GateCall)
        return replace(
            node,
            call=call,
            pulse_template=_substitute_pulse_template(
                node.pulse_template,
                source=source,
                target=target,
            ),
        )
    if isinstance(node, Sequence):
        return Sequence(
            tuple(
                _substitute_quantum_node(
                    child,
                    source=source,
                    target=target,
                    scope=scope,
                )
                for child in node.operations
            )
        )
    if isinstance(node, Parallel):
        return Parallel(
            tuple(
                _substitute_quantum_node(
                    child,
                    source=source,
                    target=target,
                    scope=scope,
                )
                for child in node.branches
            ),
            alignment=node.alignment,
        )
    if isinstance(node, FlatTopWindow):
        signal = node.signal
        if isinstance(signal, FluxSignal):
            signal = replace(
                signal, owner=target if signal.owner == source else signal.owner
            )
        else:
            signal = replace(
                signal,
                qubit=_substitute_qubit(signal.qubit, source=source, target=target),
            )
        return replace(
            node,
            id=_scoped_operation_id(node.id, scope),
            signal=signal,
            operation=_substitute_quantum_node(
                node.operation, source=source, target=target, scope=scope
            ),
        )
    if isinstance(node, Repeat):
        return Repeat(
            operation=_substitute_quantum_node(
                node.operation,
                source=source,
                target=target,
                scope=scope,
            ),
            count=node.count,
            result_dimension_id=node.result_dimension_id,
        )
    if isinstance(node, Conditional):
        return Conditional(
            predicate=node.predicate.prefixed(*scope),
            cases=tuple(
                (
                    state,
                    _substitute_quantum_node(
                        body,
                        source=source,
                        target=target,
                        scope=scope,
                    ),
                )
                for state, body in node.cases
            ),
            default=(
                None
                if node.default is None
                else _substitute_quantum_node(
                    node.default,
                    source=source,
                    target=target,
                    scope=scope,
                )
            ),
        )
    return ParallelEach(
        entity_set_id=node.entity_set_id,
        item_id=_substitute_qubit(node.item_id, source=source, target=target),
        entity_ids=tuple(
            _substitute_qubit(entity, source=source, target=target)
            for entity in node.entity_ids
        ),
        operation=_substitute_quantum_node(
            node.operation,
            source=source,
            target=target,
            scope=scope,
        ),
    )


def verify_quantum_program(
    program: QuantumProgramIR,
    gate_definitions: SequenceCollection[GateDefinition],
) -> VerifiedQuantumProgram:
    """Verify the mixed source and its logical operations."""

    logical, unresolved = _verified_quantum_program_components(
        program,
        gate_definitions,
    )
    return VerifiedQuantumProgram(program, logical.operations, unresolved)


def _verified_quantum_program_components(
    program: QuantumProgramIR,
    gate_definitions: SequenceCollection[GateDefinition],
) -> tuple[
    VerifiedCircuitOperations,
    VerifiedCircuitOperations,
]:
    """Validate and canonicalize the fields stored by a verified program."""

    issues: list[QuantumProgramIssue] = []
    operation_entries = tuple(_iter_operations_with_paths(program.body, ("body",)))
    operation_ids = tuple(
        _operation_id(operation) for operation, _path in operation_entries
    )
    operation_id_counts = Counter(operation_ids)
    for operation_id, count in operation_id_counts.items():
        if count > 1:
            issues.append(
                QuantumProgramIssue(
                    code="quantum_operation_id_duplicate",
                    message=(
                        f"operation id {operation_id.value!r} is declared more than "
                        "once"
                    ),
                    operation_id=operation_id,
                )
            )

    acquisition_output_owners: dict[
        AcquisitionSlotId,
        tuple[CircuitOperationId, tuple[QuantumIssuePathItem, ...]],
    ] = {}
    for operation, path in operation_entries:
        if isinstance(operation, PulseBlock):
            _verify_pulse_template(
                operation.pulse_template,
                operation_id=operation.id,
                path=(*path, "pulse_template"),
                allow_acquisition=True,
                issues=issues,
            )
            effective_outputs = _verify_acquisition_slot_bindings(
                operation,
                source_program_id=program.id,
                path=(*path, "acquisition_slot_bindings"),
                issues=issues,
            )
            if effective_outputs is not None and operation_id_counts[operation.id] == 1:
                for output_id in effective_outputs:
                    existing = acquisition_output_owners.get(output_id)
                    if existing is not None:
                        existing_operation_id, _existing_path = existing
                        issues.append(
                            QuantumProgramIssue(
                                code="quantum_pulse_acquisition_output_duplicate",
                                message=(
                                    f"pulse acquisition output slot "
                                    f"{output_id.value!r} is produced by both "
                                    f"{existing_operation_id.value!r} and "
                                    f"{operation.id.value!r}"
                                ),
                                path=(*path, "acquisition_slot_bindings"),
                                operation_id=operation.id,
                            )
                        )
                    else:
                        acquisition_output_owners[output_id] = (operation.id, path)
        elif isinstance(operation, ImplementedGate):
            if (
                operation.candidate_id is not None
                and not operation.candidate_id.strip()
            ):
                issues.append(
                    QuantumProgramIssue(
                        code="quantum_candidate_id_invalid",
                        message="implemented gate candidate_id must be non-empty",
                        path=(*path, "candidate_id"),
                        operation_id=operation.id,
                    )
                )
            _verify_pulse_template(
                operation.pulse_template,
                operation_id=operation.id,
                path=(*path, "pulse_template"),
                allow_acquisition=False,
                issues=issues,
            )

    if issues:
        raise QuantumProgramVerificationError(issues)

    _verify_quantum_realtime_structure(
        program.body,
        source_program_id=program.id,
        issues=issues,
    )
    if issues:
        raise QuantumProgramVerificationError(issues)

    parallel_issues: list[CircuitIssue] = []
    _verify_parallel_qubits(program.body, ("body",), parallel_issues)

    definitions = tuple(gate_definitions)
    try:
        logical = verify_circuit_operations(
            tuple(
                operation.call if isinstance(operation, ImplementedGate) else operation
                for operation, _path in operation_entries
                if not isinstance(operation, PulseBlock)
            ),
            definitions,
        )
    except CircuitVerificationError as error:
        if parallel_issues:
            raise CircuitVerificationError((*parallel_issues, *error.issues)) from None
        raise
    if parallel_issues:
        raise CircuitVerificationError(parallel_issues)
    unresolved = verify_circuit_operations(
        tuple(
            operation
            for operation, _path in operation_entries
            if isinstance(operation, GateCall | Measure)
        ),
        definitions,
    )
    return logical, unresolved


def _verify_pulse_template(
    template: PulseProgram,
    *,
    operation_id: CircuitOperationId,
    path: tuple[QuantumIssuePathItem, ...],
    allow_acquisition: bool,
    issues: list[QuantumProgramIssue],
) -> None:
    if not allow_acquisition and (
        template.acquisition_slots
        or any(isinstance(leaf, Acquire) for leaf in iter_pulse_leaves(template.body))
    ):
        issues.append(
            QuantumProgramIssue(
                code="quantum_gate_implementation_acquisition_unsupported",
                message="gate implementations cannot declare or acquire results",
                path=path,
                operation_id=operation_id,
            )
        )
        return
    try:
        schedule(template)
    except PulseValidationError as error:
        issues.extend(
            QuantumProgramIssue(
                code="quantum_pulse_template_invalid",
                message=issue.message,
                path=(*path, *issue.path),
                operation_id=operation_id,
            )
            for issue in error.issues
        )


def _verify_acquisition_slot_bindings(
    block: PulseBlock,
    *,
    source_program_id: QuantumProgramId,
    path: tuple[QuantumIssuePathItem, ...],
    issues: list[QuantumProgramIssue],
) -> tuple[AcquisitionSlotId, ...] | None:
    bindings = block.acquisition_slot_bindings
    template_ids = tuple(binding[0] for binding in bindings)
    output_ids = tuple(binding[1] for binding in bindings)
    slots = block.pulse_template.acquisition_slots
    declared_ids = {slot.id for slot in slots}
    valid = True
    if len(set(template_ids)) != len(template_ids):
        valid = False
        issues.append(
            QuantumProgramIssue(
                code="quantum_pulse_acquisition_binding_duplicate",
                message="pulse acquisition template slots may be bound only once",
                path=path,
                operation_id=block.id,
            )
        )
    unknown = set(template_ids) - declared_ids
    if unknown:
        valid = False
        rendered = ", ".join(
            repr(slot.value) for slot in sorted(unknown, key=lambda item: item.value)
        )
        issues.append(
            QuantumProgramIssue(
                code="quantum_pulse_acquisition_binding_unknown",
                message=f"pulse acquisition bindings contain unknown slots: {rendered}",
                path=path,
                operation_id=block.id,
            )
        )
    if len(set(output_ids)) != len(output_ids):
        valid = False
        issues.append(
            QuantumProgramIssue(
                code="quantum_pulse_acquisition_output_duplicate",
                message="pulse acquisition output slot ids must be unique",
                path=path,
                operation_id=block.id,
            )
        )
    if not valid:
        return None

    substitutions = dict(bindings)
    prefix = (
        "programs",
        source_program_id.value,
        "operations",
        block.id.value,
    )
    effective_outputs = tuple(
        substitutions.get(slot.id, slot.id.prefixed(*prefix)) for slot in slots
    )
    if len(set(effective_outputs)) != len(effective_outputs):
        issues.append(
            QuantumProgramIssue(
                code="quantum_pulse_acquisition_output_duplicate",
                message=("effective pulse acquisition output slot ids must be unique"),
                path=path,
                operation_id=block.id,
            )
        )
        return None
    return effective_outputs


type _SourceAcquisition = tuple[AcquisitionSlotId, QuantumResultContract]


def _leaf_acquisitions(
    operation: QuantumOperation,
    *,
    source_program_id: QuantumProgramId,
) -> tuple[_SourceAcquisition, ...]:
    if isinstance(operation, Measure):
        return ((operation.acquisition_slot_id, operation.contract),)
    if not isinstance(operation, PulseBlock):
        return ()
    substitutions = dict(operation.acquisition_slot_bindings)
    prefix = (
        "programs",
        source_program_id.value,
        "operations",
        operation.id.value,
    )
    return tuple(
        (
            substitutions.get(slot.id, slot.id.prefixed(*prefix)),
            slot.contract,
        )
        for slot in operation.pulse_template.acquisition_slots
    )


def _node_has_acquisitions(node: QuantumNode) -> bool:
    if isinstance(node, Measure):
        return True
    if isinstance(node, PulseBlock):
        return bool(node.pulse_template.acquisition_slots)
    if isinstance(node, GateCall | ImplementedGate):
        return False
    if isinstance(node, FlatTopWindow):
        return _node_has_acquisitions(node.operation)
    if isinstance(node, Repeat):
        return node.count > 0 and _node_has_acquisitions(node.operation)
    if isinstance(node, ParallelEach):
        return _node_has_acquisitions(node.operation)
    if isinstance(node, Sequence):
        return any(_node_has_acquisitions(child) for child in node.operations)
    if isinstance(node, Parallel):
        return any(_node_has_acquisitions(child) for child in node.branches)
    return any(_node_has_acquisitions(body) for _state, body in node.cases) or (
        node.default is not None and _node_has_acquisitions(node.default)
    )


def _node_requires_realtime_target(node: QuantumNode) -> bool:
    if isinstance(node, GateCall | Measure | PulseBlock | ImplementedGate):
        return False
    if isinstance(node, Conditional):
        return True
    if isinstance(node, FlatTopWindow):
        return _node_requires_realtime_target(node.operation)
    if isinstance(node, Repeat):
        return node.result_dimension_id is not None or _node_requires_realtime_target(
            node.operation
        )
    if isinstance(node, ParallelEach):
        return _node_requires_realtime_target(node.operation)
    children = node.operations if isinstance(node, Sequence) else node.branches
    return any(_node_requires_realtime_target(child) for child in children)


def _node_needs_realtime_verification(node: QuantumNode) -> bool:
    if isinstance(node, GateCall | Measure | PulseBlock | ImplementedGate):
        return False
    if isinstance(node, Conditional):
        return True
    if isinstance(node, FlatTopWindow):
        return _node_needs_realtime_verification(node.operation)
    if isinstance(node, Repeat):
        return (
            node.result_dimension_id is not None
            or (node.count > 0 and _node_has_acquisitions(node.operation))
            or _node_needs_realtime_verification(node.operation)
        )
    if isinstance(node, ParallelEach):
        return _node_needs_realtime_verification(node.operation)
    children = node.operations if isinstance(node, Sequence) else node.branches
    return any(_node_needs_realtime_verification(child) for child in children)


def _collect_source_acquisitions(
    node: QuantumNode,
    *,
    source_program_id: QuantumProgramId,
) -> tuple[_SourceAcquisition, ...]:
    if isinstance(node, GateCall | Measure | PulseBlock | ImplementedGate):
        return _leaf_acquisitions(node, source_program_id=source_program_id)
    if isinstance(node, FlatTopWindow):
        return _collect_source_acquisitions(
            node.operation, source_program_id=source_program_id
        )
    if isinstance(node, Repeat):
        if node.count == 0:
            return ()
        return _collect_source_acquisitions(
            node.operation,
            source_program_id=source_program_id,
        )
    if isinstance(node, ParallelEach):
        if not _node_has_acquisitions(node.operation):
            return ()
        return tuple(
            acquisition
            for entity_id in node.entity_ids
            for acquisition in _collect_source_acquisitions(
                instantiate_parallel_each_operation(node, entity_id),
                source_program_id=source_program_id,
            )
        )
    if isinstance(node, Conditional):
        children = tuple(body for _state, body in node.cases)
        if node.default is not None:
            children = (*children, node.default)
    else:
        children = node.operations if isinstance(node, Sequence) else node.branches
    return tuple(
        acquisition
        for child in children
        for acquisition in _collect_source_acquisitions(
            child,
            source_program_id=source_program_id,
        )
    )


def _verify_quantum_realtime_structure(
    body: QuantumNode,
    *,
    source_program_id: QuantumProgramId,
    issues: list[QuantumProgramIssue],
) -> None:
    if not _node_needs_realtime_verification(body):
        return
    slot_index: dict[AcquisitionSlotId, QuantumResultContract] = {}
    for slot_id, contract in _collect_source_acquisitions(
        body,
        source_program_id=source_program_id,
    ):
        slot_index.setdefault(slot_id, contract)
    _verify_quantum_realtime_dataflow(
        body,
        source_program_id=source_program_id,
        path=("body",),
        available={},
        active_result_dimensions=frozenset(),
        slot_index=slot_index,
        issues=issues,
        inside_conditional_branch=False,
    )


def _verify_repeat_result_shape(
    repeat: Repeat,
    acquisitions: tuple[_SourceAcquisition, ...],
    *,
    path: tuple[QuantumIssuePathItem, ...],
    active_result_dimensions: frozenset[str],
    issues: list[QuantumProgramIssue],
) -> frozenset[str]:
    dimension_id = repeat.result_dimension_id
    if acquisitions and dimension_id is None:
        issues.append(
            QuantumProgramIssue(
                code="quantum_repeat_result_dimension_missing",
                message=(
                    "result-producing quantum repeats must name their local "
                    "result dimension"
                ),
                path=path,
            )
        )
    if not acquisitions and dimension_id is not None:
        issues.append(
            QuantumProgramIssue(
                code="quantum_repeat_result_dimension_unused",
                message="result-free quantum repeats cannot name a result dimension",
                path=path,
            )
        )
    if dimension_id is None:
        return active_result_dimensions
    for slot_id, contract in acquisitions:
        dimension = next(
            (
                dimension
                for dimension in contract.dimensions
                if dimension.id == dimension_id
            ),
            None,
        )
        if dimension is None or dimension.size != repeat.count:
            issues.append(
                QuantumProgramIssue(
                    code="quantum_repeat_result_dimension_mismatch",
                    message=(
                        f"acquisition slot {slot_id.qualified_name!r} must declare "
                        f"dimension {dimension_id!r} with size {repeat.count}"
                    ),
                    path=path,
                )
            )
    if dimension_id in active_result_dimensions:
        issues.append(
            QuantumProgramIssue(
                code="quantum_repeat_result_dimension_reentered",
                message=(
                    f"result dimension {dimension_id!r} cannot identify nested "
                    "quantum repeats"
                ),
                path=path,
            )
        )
    return active_result_dimensions | {dimension_id}


def _verify_conditional_predicate(
    predicate: AcquisitionSlotId,
    *,
    path: tuple[QuantumIssuePathItem, ...],
    available: dict[AcquisitionSlotId, frozenset[str]],
    active_result_dimensions: frozenset[str],
    slot_index: dict[AcquisitionSlotId, QuantumResultContract],
    issues: list[QuantumProgramIssue],
) -> None:
    contract = slot_index.get(predicate)
    if contract is None:
        issues.append(
            QuantumProgramIssue(
                code="quantum_conditional_predicate_unknown",
                message=(
                    "conditional predicate references undeclared acquisition slot "
                    f"{predicate.qualified_name!r}"
                ),
                path=path,
            )
        )
        return
    if predicate not in available:
        issues.append(
            QuantumProgramIssue(
                code="quantum_conditional_predicate_unavailable",
                message=(
                    f"conditional predicate slot {predicate.qualified_name!r} is "
                    "not available before the conditional"
                ),
                path=path,
            )
        )
    if contract.acquisition_kind is not AcquisitionKind.CLASSIFIED_STATE:
        issues.append(
            QuantumProgramIssue(
                code="quantum_conditional_predicate_not_classified",
                message=(
                    f"conditional predicate slot {predicate.qualified_name!r} must "
                    "produce a classified state"
                ),
                path=path,
            )
        )
    required_dimensions = frozenset(dimension.id for dimension in contract.dimensions)
    if not required_dimensions <= active_result_dimensions:
        issues.append(
            QuantumProgramIssue(
                code="quantum_conditional_predicate_dimensions_inactive",
                message=(
                    f"conditional predicate slot {predicate.qualified_name!r} has "
                    f"local dimensions {tuple(sorted(required_dimensions))!r} not "
                    "covered by active quantum repeats"
                ),
                path=path,
            )
        )
    acquisition_dimensions = available.get(predicate, frozenset())
    if not required_dimensions <= acquisition_dimensions:
        issues.append(
            QuantumProgramIssue(
                code="quantum_conditional_predicate_not_current_iteration",
                message=(
                    f"conditional predicate slot {predicate.qualified_name!r} was "
                    "not acquired in the current iterations of its local dimensions"
                ),
                path=path,
            )
        )


def _merge_parallel_availability(
    available: dict[AcquisitionSlotId, frozenset[str]],
    outputs: tuple[dict[AcquisitionSlotId, frozenset[str]], ...],
) -> dict[AcquisitionSlotId, frozenset[str]]:
    selected = dict(available)
    for output in outputs:
        selected.update(output)
    return selected


def _verify_quantum_realtime_dataflow(
    node: QuantumNode,
    *,
    source_program_id: QuantumProgramId,
    path: tuple[QuantumIssuePathItem, ...],
    available: dict[AcquisitionSlotId, frozenset[str]],
    active_result_dimensions: frozenset[str],
    slot_index: dict[AcquisitionSlotId, QuantumResultContract],
    issues: list[QuantumProgramIssue],
    inside_conditional_branch: bool,
) -> dict[AcquisitionSlotId, frozenset[str]]:
    if isinstance(node, GateCall | Measure | PulseBlock | ImplementedGate):
        acquisitions = _leaf_acquisitions(
            node,
            source_program_id=source_program_id,
        )
        if inside_conditional_branch and acquisitions:
            issues.append(
                QuantumProgramIssue(
                    code="quantum_conditional_branch_acquisition",
                    message=(
                        "conditional branches cannot contain acquisitions; place "
                        "measurement before or after the conditional"
                    ),
                    path=path,
                    operation_id=node.id,
                )
            )
        return {
            **available,
            **{
                slot_id: active_result_dimensions for slot_id, _contract in acquisitions
            },
        }
    if isinstance(node, FlatTopWindow):
        return _verify_quantum_realtime_dataflow(
            node.operation,
            source_program_id=source_program_id,
            path=(*path, "body"),
            available=available,
            active_result_dimensions=active_result_dimensions,
            slot_index=slot_index,
            issues=issues,
            inside_conditional_branch=inside_conditional_branch,
        )
    if isinstance(node, Sequence):
        selected = available
        for index, child in enumerate(node.operations):
            selected = _verify_quantum_realtime_dataflow(
                child,
                source_program_id=source_program_id,
                path=(*path, "operations", index),
                available=selected,
                active_result_dimensions=active_result_dimensions,
                slot_index=slot_index,
                issues=issues,
                inside_conditional_branch=inside_conditional_branch,
            )
        return selected
    if isinstance(node, Parallel):
        parallel_outputs: list[dict[AcquisitionSlotId, frozenset[str]]] = []
        for index, branch in enumerate(node.branches):
            if _node_requires_realtime_target(branch):
                issues.append(
                    QuantumProgramIssue(
                        code="quantum_realtime_parallel_unsupported",
                        message=(
                            "parallel branches cannot contain target-visible "
                            "real-time control"
                        ),
                        path=(*path, "branches", index),
                    )
                )
            parallel_outputs.append(
                _verify_quantum_realtime_dataflow(
                    branch,
                    source_program_id=source_program_id,
                    path=(*path, "branches", index),
                    available=available,
                    active_result_dimensions=active_result_dimensions,
                    slot_index=slot_index,
                    issues=issues,
                    inside_conditional_branch=inside_conditional_branch,
                )
            )
        return _merge_parallel_availability(available, tuple(parallel_outputs))
    if isinstance(node, ParallelEach):
        has_realtime = _node_requires_realtime_target(node.operation)
        if not has_realtime and not _node_has_acquisitions(node.operation):
            return available
        mapped_outputs = tuple(
            _verify_quantum_realtime_dataflow(
                instantiate_parallel_each_operation(node, entity_id),
                source_program_id=source_program_id,
                path=(*path, "entities", entity_id.value),
                available=available,
                active_result_dimensions=active_result_dimensions,
                slot_index=slot_index,
                issues=issues,
                inside_conditional_branch=inside_conditional_branch,
            )
            for entity_id in node.entity_ids
        )
        return _merge_parallel_availability(available, mapped_outputs)
    if isinstance(node, Repeat):
        if node.count == 0:
            return available
        acquisitions = _collect_source_acquisitions(
            node.operation,
            source_program_id=source_program_id,
        )
        nested_dimensions = _verify_repeat_result_shape(
            node,
            acquisitions,
            path=path,
            active_result_dimensions=active_result_dimensions,
            issues=issues,
        )
        return _verify_quantum_realtime_dataflow(
            node.operation,
            source_program_id=source_program_id,
            path=(*path, "operation"),
            available=available,
            active_result_dimensions=nested_dimensions,
            slot_index=slot_index,
            issues=issues,
            inside_conditional_branch=inside_conditional_branch,
        )

    _verify_conditional_predicate(
        node.predicate,
        path=(*path, "predicate"),
        available=available,
        active_result_dimensions=active_result_dimensions,
        slot_index=slot_index,
        issues=issues,
    )
    branch_outputs = tuple(
        _verify_quantum_realtime_dataflow(
            body,
            source_program_id=source_program_id,
            path=(*path, "cases", state),
            available=available,
            active_result_dimensions=active_result_dimensions,
            slot_index=slot_index,
            issues=issues,
            inside_conditional_branch=True,
        )
        for state, body in node.cases
    )
    default_output = (
        available
        if node.default is None
        else _verify_quantum_realtime_dataflow(
            node.default,
            source_program_id=source_program_id,
            path=(*path, "default"),
            available=available,
            active_result_dimensions=active_result_dimensions,
            slot_index=slot_index,
            issues=issues,
            inside_conditional_branch=True,
        )
    )
    conditional_outputs = (*branch_outputs, default_output)
    common_slots = set(available)
    for output in conditional_outputs:
        common_slots.intersection_update(output)
    selected: dict[AcquisitionSlotId, frozenset[str]] = {}
    for slot_id in common_slots:
        dimensions = available[slot_id]
        for output in conditional_outputs:
            dimensions &= output[slot_id]
        selected[slot_id] = dimensions
    return selected


def _operation_id(operation: QuantumOperation) -> CircuitOperationId:
    return operation.id


def _iter_operations_with_paths(
    node: QuantumNode,
    path: tuple[QuantumIssuePathItem, ...],
) -> Iterator[tuple[QuantumOperation, tuple[QuantumIssuePathItem, ...]]]:
    if isinstance(
        node,
        GateCall | Measure | PulseBlock | ImplementedGate,
    ):
        yield node, path
        return
    if isinstance(node, Sequence):
        for index, operation in enumerate(node.operations):
            yield from _iter_operations_with_paths(
                operation,
                (*path, "operations", index),
            )
        return
    if isinstance(node, Parallel):
        for index, branch in enumerate(node.branches):
            yield from _iter_operations_with_paths(
                branch,
                (*path, "branches", index),
            )
        return
    if isinstance(node, ParallelEach | FlatTopWindow):
        yield from _iter_operations_with_paths(
            node.operation,
            (*path, "operation"),
        )
        return
    if isinstance(node, Conditional):
        for state, body in node.cases:
            yield from _iter_operations_with_paths(
                body,
                (*path, "cases", state),
            )
        if node.default is not None:
            yield from _iter_operations_with_paths(
                node.default,
                (*path, "default"),
            )
        return
    if node.count:
        yield from _iter_operations_with_paths(
            node.operation,
            (*path, "operation"),
        )


def _verify_parallel_qubits(
    node: QuantumNode,
    path: tuple[QuantumIssuePathItem, ...],
    issues: list[CircuitIssue],
) -> set[QubitId]:
    if isinstance(node, GateCall | ImplementedGate):
        return set((node.call if isinstance(node, ImplementedGate) else node).qubits)
    if isinstance(node, Measure):
        return {node.qubit}
    if isinstance(node, PulseBlock):
        return set()
    if isinstance(node, FlatTopWindow):
        return _verify_parallel_qubits(node.operation, (*path, "body"), issues)
    if isinstance(node, Sequence):
        sequence_touched: set[QubitId] = set()
        for index, child in enumerate(node.operations):
            sequence_touched.update(
                _verify_parallel_qubits(
                    child,
                    (*path, "operations", index),
                    issues,
                )
            )
        return sequence_touched
    if isinstance(node, Parallel):
        branch_qubits = tuple(
            _verify_parallel_qubits(
                branch,
                (*path, "branches", index),
                issues,
            )
            for index, branch in enumerate(node.branches)
        )
        for right_index, right_qubits in enumerate(branch_qubits):
            for left_index in range(right_index):
                for qubit in sorted(
                    branch_qubits[left_index] & right_qubits,
                    key=lambda item: item.value,
                ):
                    issues.append(
                        CircuitIssue(
                            code="parallel_qubit_conflict",
                            message=(
                                f"parallel branches {left_index} and {right_index} "
                                f"both use qubit {qubit.value!r}"
                            ),
                            path=(*path, "branches", right_index),
                        )
                    )
        parallel_touched: set[QubitId] = set()
        for branch in branch_qubits:
            parallel_touched.update(branch)
        return parallel_touched
    if isinstance(node, ParallelEach):
        template_qubits = _verify_parallel_qubits(
            node.operation,
            (*path, "operation"),
            issues,
        )
        _verify_parallel_each_gate_substitutions(node, path, issues)
        owners: dict[QubitId, int] = {}
        for right_index, entity_id in enumerate(node.entity_ids):
            right_qubits = {
                entity_id if qubit == node.item_id else qubit
                for qubit in template_qubits
            }
            for qubit in sorted(right_qubits, key=lambda item: item.value):
                left_index = owners.get(qubit)
                if left_index is not None:
                    issues.append(
                        CircuitIssue(
                            code="parallel_qubit_conflict",
                            message=(
                                f"parallel_each entities {left_index} and "
                                f"{right_index} both use qubit {qubit.value!r}"
                            ),
                            path=(
                                *path,
                                "entities",
                                node.entity_ids[right_index].value,
                            ),
                        )
                    )
                else:
                    owners[qubit] = right_index
        return set(owners)
    if isinstance(node, Conditional):
        touched: set[QubitId] = set()
        for state, body in node.cases:
            touched.update(
                _verify_parallel_qubits(
                    body,
                    (*path, "cases", state),
                    issues,
                )
            )
        if node.default is not None:
            touched.update(
                _verify_parallel_qubits(
                    node.default,
                    (*path, "default"),
                    issues,
                )
            )
        return touched
    if node.count == 0:
        return set()
    return _verify_parallel_qubits(
        node.operation,
        (*path, "operation"),
        issues,
    )


def _verify_parallel_each_gate_substitutions(
    node: ParallelEach,
    path: tuple[QuantumIssuePathItem, ...],
    issues: list[CircuitIssue],
) -> None:
    """Reject entity substitutions that collapse distinct gate operands."""

    for operation, operation_path in _iter_operations_with_paths(
        node.operation,
        (*path, "operation"),
    ):
        call = operation.call if isinstance(operation, ImplementedGate) else operation
        if not isinstance(call, GateCall) or node.item_id not in call.qubits:
            continue
        fixed_qubits = set(call.qubits) - {node.item_id}
        for entity_id in node.entity_ids:
            if entity_id not in fixed_qubits:
                continue
            issues.append(
                CircuitIssue(
                    code="circuit_gate_qubit_duplicate",
                    message=(
                        f"gate call {call.id.value!r} uses qubit "
                        f"{entity_id.value!r} more than once after parallel_each "
                        "substitution"
                    ),
                    path=(*operation_path, "entities", entity_id.value, "qubits"),
                )
            )


@dataclass(frozen=True, slots=True)
class QuantumPulseLoweringPlan:
    """Implementation catalog and retained tree awaiting target materialization."""

    source_program_id: QuantumProgramId
    output_id: PulseProgramId
    body: QuantumNode
    implementations: ResolvedPulseImplementations
    expanded_operation_count: int


@dataclass(frozen=True, slots=True)
class _InstantiatedTemplate:
    body: PulseInstruction
    acquisition_slots: tuple[AcquisitionSlot, ...]


def plan_quantum_pulse_lowering(
    program: VerifiedQuantumProgram,
    implementations: ResolvedPulseImplementations,
    *,
    output_id: PulseProgramId,
    max_expanded_operations: int | None = None,
) -> QuantumPulseLoweringPlan:
    """Close quantum lowering without expanding retained control-flow nodes."""

    workload = program.require_expansion_budget(max_expanded_operations)
    return QuantumPulseLoweringPlan(
        source_program_id=program.program.id,
        output_id=output_id,
        body=program.program.body,
        implementations=implementations,
        expanded_operation_count=workload.expanded_operation_count,
    )


def materialize_quantum_pulse_program(
    plan: QuantumPulseLoweringPlan,
) -> PulseProgram:
    """Expand a static retained lowering at the pulse scheduling boundary."""

    if _node_requires_realtime_target(plan.body):
        raise ValueError(
            "target-visible real-time quantum programs must be materialized as "
            "TargetProgram"
        )

    bindings = PulseImplementationIndex.from_implementations(plan.implementations)
    return _materialize_static_quantum_node(
        plan.body,
        source_program_id=plan.source_program_id,
        output_id=plan.output_id,
        bindings=bindings,
    )


def materialize_quantum_target_program(
    plan: QuantumPulseLoweringPlan,
) -> TargetProgram:
    """Materialize static pulses and retain bounded target-visible control."""

    if not _node_requires_realtime_target(plan.body):
        return TargetProgram.from_scheduled(
            schedule(materialize_quantum_pulse_program(plan))
        )
    bindings = PulseImplementationIndex.from_implementations(plan.implementations)
    block_indices = itertools.count()
    body = _lower_target_node(
        plan.body,
        source_program_id=plan.source_program_id,
        output_id=plan.output_id,
        bindings=bindings,
        block_indices=block_indices,
    )
    return TargetProgram(id=plan.output_id, body=body)


def _materialize_static_quantum_node(
    node: QuantumNode,
    *,
    source_program_id: QuantumProgramId,
    output_id: PulseProgramId,
    bindings: PulseImplementationIndex,
) -> PulseProgram:
    acquisition_slots: list[AcquisitionSlot] = []
    body = _lower_node(
        node,
        source_program_id=source_program_id,
        bindings=bindings,
        acquisition_slots=acquisition_slots,
        occurrence_scope=(),
    )
    return PulseProgram(
        id=output_id,
        body=body,
        acquisition_slots=tuple(acquisition_slots),
    )


def _scheduled_quantum_block(
    nodes: tuple[QuantumNode, ...],
    *,
    source_program_id: QuantumProgramId,
    output_id: PulseProgramId,
    bindings: PulseImplementationIndex,
    block_indices: Iterator[int],
) -> ScheduledBlock:
    if not nodes:
        raise ValueError("scheduled quantum blocks require at least one source node")
    node: QuantumNode = nodes[0] if len(nodes) == 1 else Sequence(nodes)
    block_id = _target_block_id(output_id, next(block_indices))
    return ScheduledBlock(
        schedule(
            _materialize_static_quantum_node(
                node,
                source_program_id=source_program_id,
                output_id=block_id,
                bindings=bindings,
            )
        )
    )


def _target_block_id(output_id: PulseProgramId, index: int) -> PulseProgramId:
    """Identify one scheduled occurrence within a retained target program."""

    return PulseProgramId(f"{output_id.value}/blocks/{index}")


def _flatten_quantum_sequence(
    operations: tuple[QuantumNode, ...],
) -> Iterator[QuantumNode]:
    for operation in operations:
        if isinstance(operation, Sequence):
            yield from _flatten_quantum_sequence(operation.operations)
        else:
            yield operation


def _target_sequence(
    operations: tuple[QuantumNode, ...],
    *,
    source_program_id: QuantumProgramId,
    output_id: PulseProgramId,
    bindings: PulseImplementationIndex,
    block_indices: Iterator[int],
) -> RealtimeInstruction:
    instructions: list[RealtimeInstruction] = []
    static_nodes: list[QuantumNode] = []

    def flush_static_nodes() -> None:
        if not static_nodes:
            return
        instructions.append(
            _scheduled_quantum_block(
                tuple(static_nodes),
                source_program_id=source_program_id,
                output_id=output_id,
                bindings=bindings,
                block_indices=block_indices,
            )
        )
        static_nodes.clear()

    for operation in _flatten_quantum_sequence(operations):
        if _node_requires_realtime_target(operation):
            flush_static_nodes()
            instructions.append(
                _lower_target_node(
                    operation,
                    source_program_id=source_program_id,
                    output_id=output_id,
                    bindings=bindings,
                    block_indices=block_indices,
                )
            )
        else:
            static_nodes.append(operation)
    flush_static_nodes()
    if not instructions:
        return RealtimeNoOp()
    if len(instructions) == 1:
        return instructions[0]
    return RealtimeSequence(tuple(instructions))


def _lower_target_node(
    node: QuantumNode,
    *,
    source_program_id: QuantumProgramId,
    output_id: PulseProgramId,
    bindings: PulseImplementationIndex,
    block_indices: Iterator[int],
) -> RealtimeInstruction:
    if isinstance(node, Sequence):
        return _target_sequence(
            node.operations,
            source_program_id=source_program_id,
            output_id=output_id,
            bindings=bindings,
            block_indices=block_indices,
        )
    if isinstance(node, Conditional):
        return RealtimeConditional(
            predicate=ClassifiedStatePredicate(node.predicate),
            cases=tuple(
                RealtimeCase(
                    state,
                    _lower_target_node(
                        body,
                        source_program_id=source_program_id,
                        output_id=output_id,
                        bindings=bindings,
                        block_indices=block_indices,
                    ),
                )
                for state, body in node.cases
            ),
            default=(
                RealtimeNoOp()
                if node.default is None
                else _lower_target_node(
                    node.default,
                    source_program_id=source_program_id,
                    output_id=output_id,
                    bindings=bindings,
                    block_indices=block_indices,
                )
            ),
        )
    if isinstance(node, ParallelEach) and _node_requires_realtime_target(node):
        branches = tuple(
            _lower_target_node(
                instantiate_parallel_each_operation(node, entity_id),
                source_program_id=source_program_id,
                output_id=output_id,
                bindings=bindings,
                block_indices=block_indices,
            )
            for entity_id in node.entity_ids
        )
        return branches[0] if len(branches) == 1 else RealtimeParallel(branches)
    if isinstance(node, Repeat) and _node_requires_realtime_target(node):
        if node.count == 0:
            return RealtimeNoOp()
        return RealtimeRepeat(
            instruction=_lower_target_node(
                node.operation,
                source_program_id=source_program_id,
                output_id=output_id,
                bindings=bindings,
                block_indices=block_indices,
            ),
            count=node.count,
            result_dimension_id=node.result_dimension_id,
        )
    if _node_requires_realtime_target(node):
        raise AssertionError("parallel real-time source reached target lowering")
    return _scheduled_quantum_block(
        (node,),
        source_program_id=source_program_id,
        output_id=output_id,
        bindings=bindings,
        block_indices=block_indices,
    )


def _lower_node(
    node: QuantumNode,
    *,
    source_program_id: QuantumProgramId,
    bindings: PulseImplementationIndex,
    acquisition_slots: list[AcquisitionSlot],
    occurrence_scope: tuple[str, ...],
) -> PulseInstruction:
    if isinstance(node, FlatTopWindow):
        slot_start = len(acquisition_slots)
        body = _lower_node(
            node.operation,
            source_program_id=source_program_id,
            bindings=bindings,
            acquisition_slots=acquisition_slots,
            occurrence_scope=occurrence_scope,
        )
        prefix = (
            "programs",
            source_program_id.value,
            *occurrence_scope,
            "operations",
            node.id.value,
        )
        body_program = PulseProgram(
            PulseProgramId("/".join(prefix)),
            body,
            acquisition_slots=tuple(acquisition_slots[slot_start:]),
        )
        body_duration = schedule(body_program).duration_seconds
        rise = Decimal(str(node.rise_duration.to("s").value))
        settle = Decimal(str(node.settle_duration.to("s").value))
        fall = Decimal(str(node.fall_duration.to("s").value))
        envelope = CosineFlatTop(
            duration=Quantity(float(rise + settle + body_duration + fall), "s"),
            amplitude=node.amplitude,
            rise_duration=node.rise_duration,
            fall_duration=node.fall_duration,
        )
        shifted_body = TimeShift(body, Quantity(float(rise + settle), "s"))
        return PulseParallel(
            (
                Play(PulseEventId("window", scope=prefix), node.signal, envelope),
                shifted_body,
            )
        )
    if isinstance(node, Sequence):
        return PulseSequence(
            tuple(
                _lower_node(
                    child,
                    source_program_id=source_program_id,
                    bindings=bindings,
                    acquisition_slots=acquisition_slots,
                    occurrence_scope=occurrence_scope,
                )
                for child in node.operations
            )
        )
    if isinstance(node, Parallel):
        return PulseParallel(
            tuple(
                _lower_node(
                    child,
                    source_program_id=source_program_id,
                    bindings=bindings,
                    acquisition_slots=acquisition_slots,
                    occurrence_scope=occurrence_scope,
                )
                for child in node.branches
            ),
            alignment=node.alignment,
        )
    if isinstance(node, ParallelEach):
        return PulseParallel(
            tuple(
                _lower_node(
                    instantiate_parallel_each_operation(node, entity_id),
                    source_program_id=source_program_id,
                    bindings=bindings,
                    acquisition_slots=acquisition_slots,
                    occurrence_scope=occurrence_scope,
                )
                for entity_id in node.entity_ids
            )
        )
    if isinstance(node, Repeat):
        return PulseSequence(
            tuple(
                _lower_node(
                    node.operation,
                    source_program_id=source_program_id,
                    bindings=bindings,
                    acquisition_slots=acquisition_slots,
                    occurrence_scope=(*occurrence_scope, f"repeat[{index}]"),
                )
                for index in range(node.count)
            )
        )
    if isinstance(node, Conditional):
        raise AssertionError("real-time conditional reached static pulse lowering")
    return _lower_leaf(
        node,
        source_program_id=source_program_id,
        bindings=bindings,
        acquisition_slots=acquisition_slots,
        occurrence_scope=occurrence_scope,
    )


def _lower_leaf(
    node: QuantumOperation,
    *,
    source_program_id: QuantumProgramId,
    bindings: PulseImplementationIndex,
    acquisition_slots: list[AcquisitionSlot],
    occurrence_scope: tuple[str, ...],
) -> PulseInstruction:
    source_id = _operation_id(node)
    prefix = (
        "programs",
        source_program_id.value,
        *occurrence_scope,
        "operations",
        source_id.value,
    )
    if isinstance(node, PulseBlock):
        substitutions = {
            template: (
                output.prefixed(*occurrence_scope) if occurrence_scope else output
            )
            for template, output in node.acquisition_slot_bindings
        }
        instantiated = _instantiate_template(
            node.pulse_template,
            prefix=prefix,
            slot_substitutions=substitutions,
        )
        acquisition_slots.extend(instantiated.acquisition_slots)
        return instantiated.body

    if isinstance(node, ImplementedGate):
        return _instantiate_template(node.pulse_template, prefix=prefix).body

    binding = bindings.binding_for(node)
    if isinstance(node, GateCall):
        assert isinstance(binding, GatePulseImplementationBinding)
        return _instantiate_template(binding.pulse_template, prefix=prefix).body

    assert isinstance(node, Measure)
    assert isinstance(binding, MeasurementPulseImplementationBinding)
    template_slot = binding.pulse_template.acquisition_slots[0]
    output_slot_id = (
        node.acquisition_slot_id.prefixed(*occurrence_scope)
        if occurrence_scope
        else node.acquisition_slot_id
    )
    instantiated = _instantiate_template(
        binding.pulse_template,
        prefix=prefix,
        slot_substitutions={template_slot.id: output_slot_id},
    )
    [output_slot] = instantiated.acquisition_slots
    acquisition_slots.append(output_slot)
    return instantiated.body


def _instantiate_template(
    template: PulseProgram,
    *,
    prefix: tuple[str, ...],
    slot_substitutions: dict[AcquisitionSlotId, AcquisitionSlotId] | None = None,
) -> _InstantiatedTemplate:
    substitutions = slot_substitutions or {}
    slot_ids = {
        slot.id: substitutions.get(slot.id, slot.id.prefixed(*prefix))
        for slot in template.acquisition_slots
    }

    def instantiate(instruction: PulseInstruction) -> PulseInstruction:
        if isinstance(instruction, TimeShift):
            return replace(
                instruction, instruction=instantiate(instruction.instruction)
            )
        if isinstance(instruction, PulseSequence):
            return PulseSequence(
                tuple(instantiate(child) for child in instruction.instructions)
            )
        if isinstance(instruction, PulseParallel):
            return PulseParallel(
                tuple(instantiate(child) for child in instruction.branches),
                alignment=instruction.alignment,
            )
        event_id = instruction.id.prefixed(*prefix)
        selected = replace(instruction, id=event_id)
        if isinstance(selected, Acquire):
            selected = replace(selected, slot_id=slot_ids[selected.slot_id])
        return selected

    body = instantiate(template.body)
    return _InstantiatedTemplate(
        body=body,
        acquisition_slots=tuple(
            replace(slot, id=slot_ids[slot.id]) for slot in template.acquisition_slots
        ),
    )
