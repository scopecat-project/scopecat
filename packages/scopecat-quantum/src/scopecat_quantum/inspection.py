# pyright: reportPrivateUsage=false
"""Structured, bounded inspection of quantum compilation stages."""

from __future__ import annotations

from dataclasses import dataclass

from scopecat.inspection import (
    CompiledInspectionFact,
    CompiledProgramInspection,
    CompiledProgramInspectionInvertedIndexBuilder,
    CompiledProgramInspectionLayer,
    CompiledProgramInspectionLayerIndex,
    CompiledProgramInspectionLink,
    CompiledProgramInspectionLinkIndex,
    CompiledProgramInspectionNode,
    CompiledProgramInspectionNodeIndex,
    CompiledProgramInspectionQuery,
)

from scopecat_quantum.authoring import (
    QUANTUM_PROGRAM_DIALECT_ID,
    BoundProgram,
    Program,
)
from scopecat_quantum.authoring._inspection import _inspection_node, _InspectionNode
from scopecat_quantum.circuits import Measure
from scopecat_quantum.gates import GateCall
from scopecat_quantum.programs import (
    Conditional,
    ImplementedGate,
    Parallel,
    ParallelEach,
    QuantumNode,
    Repeat,
    estimate_quantum_program_workload,
)
from scopecat_quantum.programs import (
    Sequence as QuantumSequence,
)
from scopecat_quantum.pulses import (
    Acquire,
    DriveSignal,
    FluxSignal,
    Play,
    ReadoutSignal,
    ScheduledPulseEvent,
    ScheduledPulseProgram,
    ShiftPhase,
    pulse_leaf_owners,
)


@dataclass(frozen=True, slots=True)
class QuantumInspectionBounds:
    """Hard response budget for each structured program layer."""

    max_nodes_per_layer: int = 128

    def __post_init__(self) -> None:
        if self.max_nodes_per_layer <= 0:
            raise ValueError("quantum inspection node limits must be positive")


@dataclass(frozen=True, slots=True)
class QuantumProgramInspectionSnapshot:
    """Reusable structural indices for one immutable compiled-program variant."""

    program_id: str
    snapshot_id: str
    bounds: QuantumInspectionBounds
    layers: tuple[CompiledProgramInspectionLayerIndex, ...]
    lineage: CompiledProgramInspectionLinkIndex

    def project(
        self,
        query: CompiledProgramInspectionQuery | None = None,
    ) -> CompiledProgramInspection:
        """Project one bounded response without rebuilding layer node sources."""

        projected_layers: list[CompiledProgramInspectionLayer] = []
        for layer_index in self.layers:
            layer, _selection = layer_index.project(
                query=query,
                default_limit=self.bounds.max_nodes_per_layer,
                snapshot_id=self.snapshot_id,
            )
            projected_layers.append(layer)
        lineage = self.lineage.project(
            projected_layers,
            max_links=self.bounds.max_nodes_per_layer,
        )
        return CompiledProgramInspection(
            dialect_id=QUANTUM_PROGRAM_DIALECT_ID,
            program_id=self.program_id,
            snapshot_id=self.snapshot_id,
            layers=tuple(projected_layers),
            links=lineage.links,
            query=query,
            warnings=(
                ("lineage links were truncated to the inspection response budget",)
                if lineage.truncated
                else ()
            ),
        )


def build_quantum_program_inspection_snapshot(
    program: Program,
    *,
    bound: BoundProgram | None = None,
    scheduled: ScheduledPulseProgram | None = None,
    bounds: QuantumInspectionBounds | None = None,
    snapshot_id: str | None = None,
) -> QuantumProgramInspectionSnapshot:
    """Build stable layer indices once for repeated artifact-scoped queries."""

    selected_bounds = bounds or QuantumInspectionBounds()
    selected_snapshot_id = snapshot_id or f"quantum-program:{program.id}"
    layers = [_authored_layer_index(program)]
    logical_operation_nodes: dict[str, str] = {}
    if bound is not None:
        logical, logical_operation_nodes = _logical_layer_index(bound)
        layers.append(logical)
    if scheduled is not None:
        layers.append(_scheduled_layer_index(scheduled))
    lineage = _quantum_lineage_index(
        scheduled,
        logical_operation_nodes=logical_operation_nodes,
    )
    return QuantumProgramInspectionSnapshot(
        program_id=program.id,
        snapshot_id=selected_snapshot_id,
        bounds=selected_bounds,
        layers=tuple(layers),
        lineage=lineage,
    )


def _quantum_lineage_index(
    scheduled: ScheduledPulseProgram | None,
    *,
    logical_operation_nodes: dict[str, str],
) -> CompiledProgramInspectionLinkIndex:
    links: list[CompiledProgramInspectionLink] = []
    if scheduled is not None:
        for event in scheduled.events:
            source_operation_id = _source_operation_id(event)
            source_node_id = (
                _logical_operation_node(
                    source_operation_id,
                    logical_operation_nodes,
                )
                if source_operation_id is not None
                else None
            )
            if source_node_id is not None:
                links.append(
                    CompiledProgramInspectionLink(
                        source_layer_id="logical",
                        source_node_id=source_node_id,
                        target_layer_id="scheduled",
                        target_node_id=f"scheduled:event:{event.id.value}",
                        relation="lowers_to",
                    )
                )
    return CompiledProgramInspectionLinkIndex.from_links(links)


def _static_layer_index(
    *,
    id: str,
    label: str,
    kind: str,
    nodes: tuple[CompiledProgramInspectionNode, ...],
    root_ids: tuple[str, ...],
    facts: tuple[CompiledInspectionFact, ...] = (),
) -> CompiledProgramInspectionLayerIndex:
    return CompiledProgramInspectionLayerIndex(
        id=id,
        label=label,
        kind=kind,
        root_ids=root_ids,
        nodes=CompiledProgramInspectionNodeIndex.from_nodes(nodes),
        facts=facts,
    )


def _authored_layer_index(
    program: Program,
) -> CompiledProgramInspectionLayerIndex:
    root_id = "authored:program"
    tree = _inspection_node(program.body)
    nodes = [
        CompiledProgramInspectionNode(
            id=root_id,
            kind="program",
            label=f"program {program.id}",
            child_count=1,
            result_ids=tuple(result.id for result in program.results),
            facts=(
                CompiledInspectionFact("port_count", len(program.ports)),
                CompiledInspectionFact("result_count", len(program.results)),
            ),
        )
    ]

    def visit(
        inspection_node: _InspectionNode,
        *,
        parent_id: str,
        path: tuple[int, ...],
    ) -> None:
        node_id = "authored:" + "/".join(str(index) for index in path)
        label = inspection_node.label
        nodes.append(
            CompiledProgramInspectionNode(
                id=node_id,
                kind=label.split(" ", maxsplit=1)[0],
                label=label,
                parent_id=parent_id,
                child_count=len(inspection_node.children),
            )
        )
        for index, child in enumerate(inspection_node.children):
            visit(child, parent_id=node_id, path=(*path, index))

    visit(tree, parent_id=root_id, path=(0,))
    return _static_layer_index(
        id="authored",
        label="Authored program",
        kind="authored",
        nodes=tuple(nodes),
        root_ids=(root_id,),
    )


def _logical_layer_index(
    bound: BoundProgram,
) -> tuple[CompiledProgramInspectionLayerIndex, dict[str, str]]:
    root_id = "logical:program"
    workload = estimate_quantum_program_workload(bound.verified)
    nodes = [
        CompiledProgramInspectionNode(
            id=root_id,
            kind="program",
            label=f"bound {bound.program.id.value}",
            child_count=1,
            result_ids=tuple(result.id for result in bound.results),
            facts=(
                CompiledInspectionFact(
                    "operation_count",
                    workload.structural_operation_count,
                ),
                CompiledInspectionFact(
                    "unresolved_operation_count",
                    len(bound.verified.unresolved.operations),
                ),
                CompiledInspectionFact(
                    "expanded_operation_count",
                    workload.expanded_operation_count,
                ),
                CompiledInspectionFact(
                    "selected_entity_count",
                    workload.selected_entity_count,
                ),
                CompiledInspectionFact(
                    "max_parallel_width",
                    workload.max_parallel_width,
                ),
            ),
        )
    ]
    operation_nodes: dict[str, str] = {}

    def visit(node: QuantumNode, *, parent_id: str, path: tuple[int, ...]) -> None:
        structural_id = "logical:" + "/".join(str(index) for index in path)
        children: tuple[QuantumNode, ...]
        facts: tuple[CompiledInspectionFact, ...] = ()
        result_ids: tuple[str, ...] = ()
        if isinstance(node, QuantumSequence):
            kind, label, children = "sequence", "sequence", node.operations
            entity_ids: tuple[str, ...] = ()
        elif isinstance(node, Parallel):
            kind, label, children = "parallel", "parallel", node.branches
            entity_ids = ()
        elif isinstance(node, ParallelEach):
            kind, label, children = (
                "parallel_each",
                (
                    f"parallel_each ${node.entity_set_id} "
                    f"({len(node.entity_ids)} entities)"
                ),
                (node.operation,),
            )
            entity_ids = tuple(entity.value for entity in node.entity_ids)
            facts = (
                CompiledInspectionFact("entity_set_id", node.entity_set_id),
                CompiledInspectionFact("entity_count", len(node.entity_ids)),
            )
        elif isinstance(node, Repeat):
            dimension_suffix = (
                ""
                if node.result_dimension_id is None
                else f" result_dimension={node.result_dimension_id!r}"
            )
            kind, label, children = (
                "repeat",
                f"repeat x{node.count}{dimension_suffix}",
                (node.operation,),
            )
            entity_ids = ()
            facts = (
                CompiledInspectionFact("count", node.count),
                *(
                    ()
                    if node.result_dimension_id is None
                    else (
                        CompiledInspectionFact(
                            "result_dimension_id",
                            node.result_dimension_id,
                        ),
                    )
                ),
            )
        elif isinstance(node, Conditional):
            case_bodies = tuple(body for _state, body in node.cases)
            children = (
                case_bodies if node.default is None else (*case_bodies, node.default)
            )
            kind = "switch"
            label = f"switch ${node.predicate.local_id}"
            entity_ids = ()
            result_ids = (node.predicate.local_id,)
            facts = (
                CompiledInspectionFact(
                    "case_states",
                    tuple(state for state, _body in node.cases),
                ),
                CompiledInspectionFact(
                    "has_default",
                    node.default is not None,
                ),
            )
        else:
            children = ()
            operation_id = node.id.value
            structural_id = f"logical:operation:{operation_id}"
            operation_nodes[operation_id] = structural_id
            if isinstance(node, GateCall):
                kind = "gate"
                entity_ids = tuple(qubit.value for qubit in node.qubits)
                label = f"{node.gate_id.value}({', '.join(entity_ids)})"
                if node.recipe_scope is not None:
                    facts = (CompiledInspectionFact("recipe_scope", node.recipe_scope),)
            elif isinstance(node, Measure):
                kind = "measure"
                entity_ids = (node.qubit.value,)
                result_ids = (node.acquisition_slot_id.local_id,)
                label = f"measure {node.qubit.value} → {result_ids[0]}"
            elif isinstance(node, ImplementedGate):
                kind = "implemented_gate"
                entity_ids = tuple(qubit.value for qubit in node.call.qubits)
                label = f"{node.call.gate_id.value} implementation"
                facts = (
                    CompiledInspectionFact(
                        "candidate_id",
                        node.candidate_id or "inline",
                    ),
                )
            else:
                kind = "pulse_block"
                entity_ids = tuple(
                    owner.value for owner in pulse_leaf_owners(node.pulse_template.body)
                )
                label = f"pulse {node.pulse_template.id.value}"
                result_ids = tuple(
                    output.local_id
                    for _source, output in node.acquisition_slot_bindings
                )
        nodes.append(
            CompiledProgramInspectionNode(
                id=structural_id,
                kind=kind,
                label=label,
                parent_id=parent_id,
                child_count=len(children),
                entity_ids=tuple(dict.fromkeys(entity_ids)),
                result_ids=result_ids,
                facts=facts,
            )
        )
        for index, child in enumerate(children):
            visit(child, parent_id=structural_id, path=(*path, index))

    visit(bound.program.body, parent_id=root_id, path=(0,))
    layer = _static_layer_index(
        id="logical",
        label="Bound logical program",
        kind="logical",
        nodes=tuple(nodes),
        root_ids=(root_id,),
    )
    return layer, operation_nodes


def _scheduled_layer_index(
    scheduled: ScheduledPulseProgram,
) -> CompiledProgramInspectionLayerIndex:
    root_id = "scheduled:program"
    root = CompiledProgramInspectionNode(
        id=root_id,
        kind="program",
        label=f"schedule {scheduled.id.value}",
        child_count=len(scheduled.events),
        duration_seconds=str(scheduled.duration_seconds),
        result_ids=tuple(
            dict.fromkeys(slot.id.local_id for slot in scheduled.acquisition_slots)
        ),
        facts=(
            CompiledInspectionFact("event_count", len(scheduled.events)),
            CompiledInspectionFact(
                "acquisition_count", len(scheduled.acquisition_slots)
            ),
        ),
    )
    ordinal_by_id = {
        root_id: 0,
        **{
            f"scheduled:event:{event.id.value}": ordinal
            for ordinal, event in enumerate(scheduled.events, start=1)
        },
    }
    inverted_index = CompiledProgramInspectionInvertedIndexBuilder()
    inverted_index.add(
        0,
        parent_id=None,
        kind=root.kind,
        result_ids=root.result_ids,
    )
    for ordinal, event in enumerate(scheduled.events, start=1):
        kind, entity_ids, result_ids, _signal_label = _scheduled_event_identity(event)
        inverted_index.add(
            ordinal,
            parent_id=root_id,
            kind=kind,
            entity_ids=entity_ids,
            result_ids=result_ids,
        )

    def node_at(
        ordinal: int,
        _query: CompiledProgramInspectionQuery | None,
    ) -> CompiledProgramInspectionNode:
        if ordinal == 0:
            return root
        return _scheduled_event_node(
            scheduled.events[ordinal - 1],
            parent_id=root_id,
        )

    return CompiledProgramInspectionLayerIndex(
        id="scheduled",
        label="Scheduled pulse events",
        kind="scheduled",
        root_ids=(root_id,),
        nodes=CompiledProgramInspectionNodeIndex(
            node_count=1 + len(scheduled.events),
            node_at=node_at,
            ordinal_by_id=ordinal_by_id.get,
            inverted_index=inverted_index.build(),
        ),
        facts=(
            CompiledInspectionFact(
                "duration_seconds", str(scheduled.duration_seconds), unit="s"
            ),
        ),
    )


def _logical_operation_node(
    source_operation_id: str,
    operation_nodes: dict[str, str],
) -> str | None:
    exact = operation_nodes.get(source_operation_id)
    if exact is not None:
        return exact
    template_ids = tuple(
        operation_id
        for operation_id in operation_nodes
        if source_operation_id.endswith(f"/{operation_id}")
    )
    if not template_ids:
        return None
    template_id = max(template_ids, key=len)
    return operation_nodes[template_id]


def _scheduled_event_node(
    event: ScheduledPulseEvent,
    *,
    parent_id: str,
) -> CompiledProgramInspectionNode:
    instruction = event.instruction
    kind, entity_ids, result_ids, signal_label = _scheduled_event_identity(event)
    facts: list[CompiledInspectionFact] = [
        CompiledInspectionFact("signal", signal_label),
    ]
    if isinstance(instruction, Play):
        facts.append(
            CompiledInspectionFact(
                "envelope",
                type(instruction.envelope).__name__,
            )
        )
    elif isinstance(instruction, ShiftPhase):
        facts.append(
            CompiledInspectionFact(
                "phase", instruction.phase.value, unit=instruction.phase.unit
            )
        )
    return CompiledProgramInspectionNode(
        id=f"scheduled:event:{event.id.value}",
        kind=kind,
        label=f"{kind} {signal_label}",
        parent_id=parent_id,
        entity_ids=entity_ids,
        result_ids=result_ids,
        start_seconds=str(event.start_seconds),
        duration_seconds=str(event.duration_seconds),
        facts=tuple(facts),
    )


def _scheduled_event_identity(
    event: ScheduledPulseEvent,
) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
    instruction = event.instruction
    signal = instruction.signal
    if isinstance(signal, FluxSignal):
        entity_ids = (signal.owner.value,)
        signal_label = f"flux({signal.owner.value})"
    else:
        entity_ids = (signal.qubit.value,)
        signal_kind = (
            "drive"
            if isinstance(signal, DriveSignal)
            else "readout"
            if isinstance(signal, ReadoutSignal)
            else "acquire"
        )
        signal_label = f"{signal_kind}({signal.qubit.value})"
    if isinstance(instruction, Play):
        kind = "play"
    elif isinstance(instruction, Acquire):
        kind = "acquire"
    elif isinstance(instruction, ShiftPhase):
        kind = "shift_phase"
    else:
        kind = "delay"
    result_ids = (
        (instruction.slot_id.local_id,) if isinstance(instruction, Acquire) else ()
    )
    return kind, entity_ids, result_ids, signal_label


def _source_operation_id(event: ScheduledPulseEvent) -> str | None:
    scope = event.id.scope
    for index in range(len(scope) - 1, -1, -1):
        if scope[index] == "operations" and index + 1 < len(scope):
            return scope[index + 1]
    return None


__all__ = [
    "QuantumInspectionBounds",
    "QuantumProgramInspectionSnapshot",
    "build_quantum_program_inspection_snapshot",
]
