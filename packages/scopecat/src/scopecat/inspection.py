"""Typed, target-neutral views of compiled physical realizations."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Literal

from scopecat.kernel.json_types import JsonValue
from scopecat.records.instrument import InstrumentStateSetting

PLANNED_INSTRUMENT_SETTING_LIMIT = 64


@dataclass(frozen=True, slots=True)
class PlannedInstrumentSetting:
    """A frozen point-plan assignment, not an observed or confirmed device value."""

    point_index: int | None
    proposal_fingerprint: str
    operation_index: int
    assignment_index: int
    operation_id: str
    instrument_id: str
    setting: InstrumentStateSetting


@dataclass(frozen=True, slots=True)
class CompiledInspectionFact:
    """One named target fact suitable for display and transport."""

    id: str
    value: JsonValue
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class CompiledProgramInspectionNode:
    """One bounded, identity-bearing node in a compiler inspection layer."""

    id: str
    kind: str
    label: str
    parent_id: str | None = None
    child_count: int = 0
    entity_ids: tuple[str, ...] = ()
    entity_count: int = 0
    entity_ids_truncated: bool = False
    resource_ids: tuple[str, ...] = ()
    resource_count: int = 0
    resource_ids_truncated: bool = False
    result_ids: tuple[str, ...] = ()
    result_count: int = 0
    result_ids_truncated: bool = False
    start_seconds: str | None = None
    duration_seconds: str | None = None
    facts: tuple[CompiledInspectionFact, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.entity_count == 0 and self.entity_ids:
            object.__setattr__(self, "entity_count", len(self.entity_ids))
        if self.resource_count == 0 and self.resource_ids:
            object.__setattr__(self, "resource_count", len(self.resource_ids))
        if self.result_count == 0 and self.result_ids:
            object.__setattr__(self, "result_count", len(self.result_ids))


@dataclass(frozen=True, slots=True)
class CompiledProgramInspectionQuery:
    """One bounded server-side node query for a program layer."""

    layer_id: str
    snapshot_id: str | None = None
    cursor: str | None = None
    offset: int = 0
    limit: int = 128
    node_id: str | None = None
    parent_id: str | None = None
    entity_id: str | None = None
    resource_id: str | None = None
    result_id: str | None = None
    kind: str | None = None
    text: str | None = None

    def __post_init__(self) -> None:
        if not self.layer_id:
            raise ValueError("inspection queries require a layer id")
        if self.offset < 0:
            raise ValueError("inspection query offsets must be non-negative")
        if self.cursor is not None and self.offset:
            raise ValueError("inspection queries select a cursor or offset, not both")
        if not 1 <= self.limit <= 512:
            raise ValueError("inspection query limits must be between 1 and 512")


@dataclass(frozen=True, slots=True)
class CompiledProgramInspectionPage:
    """Page metadata for one layer after server-side filtering."""

    offset: int
    limit: int
    matching_node_count: int
    returned_node_count: int
    snapshot_id: str
    next_offset: int | None = None
    next_cursor: str | None = None
    previous_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class CompiledProgramInspectionLayer:
    """A bounded semantic projection of one program compilation stage."""

    id: str
    label: str
    kind: str
    node_count: int
    nodes_truncated: bool
    root_ids: tuple[str, ...]
    nodes: tuple[CompiledProgramInspectionNode, ...]
    page: CompiledProgramInspectionPage
    facts: tuple[CompiledInspectionFact, ...] = ()


@dataclass(frozen=True, slots=True)
class CompiledProgramInspectionLink:
    """A stable many-to-many lowering relation between inspection nodes."""

    source_layer_id: str
    source_node_id: str
    target_layer_id: str
    target_node_id: str
    relation: str


@dataclass(frozen=True, slots=True)
class CompiledProgramInspectionLinkSelection:
    """One bounded projection of links incident to returned layer nodes."""

    links: tuple[CompiledProgramInspectionLink, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class CompiledProgramInspectionLinkIndex:
    """Artifact-scoped adjacency index independent of projected layer pages."""

    links_for_node: Callable[
        [str, str],
        Sequence[CompiledProgramInspectionLink],
    ]

    @classmethod
    def from_links(
        cls,
        links: Sequence[CompiledProgramInspectionLink],
    ) -> CompiledProgramInspectionLinkIndex:
        by_node: dict[
            tuple[str, str],
            list[CompiledProgramInspectionLink],
        ] = {}
        for link in links:
            by_node.setdefault(
                (link.source_layer_id, link.source_node_id),
                [],
            ).append(link)
            by_node.setdefault(
                (link.target_layer_id, link.target_node_id),
                [],
            ).append(link)
        retained = {key: tuple(values) for key, values in by_node.items()}
        return cls(lambda layer_id, node_id: retained.get((layer_id, node_id), ()))

    def project(
        self,
        layers: Sequence[CompiledProgramInspectionLayer],
        *,
        max_links: int,
    ) -> CompiledProgramInspectionLinkSelection:
        """Return unique incident links without requiring both endpoints in-page."""

        selected: list[CompiledProgramInspectionLink] = []
        seen: set[CompiledProgramInspectionLink] = set()
        for layer in layers:
            for node in layer.nodes:
                for link in self.links_for_node(layer.id, node.id):
                    if link in seen:
                        continue
                    if len(selected) == max_links:
                        return CompiledProgramInspectionLinkSelection(
                            links=tuple(selected),
                            truncated=True,
                        )
                    seen.add(link)
                    selected.append(link)
        return CompiledProgramInspectionLinkSelection(
            links=tuple(selected),
            truncated=False,
        )


@dataclass(frozen=True, slots=True)
class CompiledProgramInspection:
    """Structured multi-layer view of one bounded compiled program variant."""

    dialect_id: str
    program_id: str
    snapshot_id: str
    layers: tuple[CompiledProgramInspectionLayer, ...]
    links: tuple[CompiledProgramInspectionLink, ...] = ()
    query: CompiledProgramInspectionQuery | None = None
    warnings: tuple[str, ...] = ()
    schema_id: Literal["scopecat.compiled_program_inspection.v3"] = (
        "scopecat.compiled_program_inspection.v3"
    )


@dataclass(frozen=True, slots=True)
class CompiledProgramInspectionInvertedIndex:
    """Compact filter-to-ordinal lookup for one inspection layer."""

    parent_ordinals: dict[str, tuple[int, ...]]
    kind_ordinals: dict[str, tuple[int, ...]]
    entity_ordinals: dict[str, tuple[int, ...]]
    resource_ordinals: dict[str, tuple[int, ...]]
    result_ordinals: dict[str, tuple[int, ...]]
    entity_complete: bool = True
    resource_complete: bool = True
    result_complete: bool = True

    def candidates(
        self,
        query: CompiledProgramInspectionQuery,
    ) -> tuple[int, ...] | None:
        """Return ordered candidates, or ``None`` when no index applies."""

        selections: list[tuple[int, ...]] = []
        if query.parent_id is not None:
            selections.append(self.parent_ordinals.get(query.parent_id, ()))
        if query.kind is not None:
            selections.append(self.kind_ordinals.get(query.kind, ()))
        if query.entity_id is not None and self.entity_complete:
            selections.append(self.entity_ordinals.get(query.entity_id, ()))
        if query.resource_id is not None and self.resource_complete:
            selections.append(self.resource_ordinals.get(query.resource_id, ()))
        if query.result_id is not None and self.result_complete:
            selections.append(self.result_ordinals.get(query.result_id, ()))
        if not selections:
            return None
        if any(not selection for selection in selections):
            return ()
        smallest_index = min(
            range(len(selections)), key=lambda index: len(selections[index])
        )
        smallest = selections[smallest_index]
        remaining = (
            selection
            for index, selection in enumerate(selections)
            if index != smallest_index
        )
        other_selections = tuple(remaining)
        return tuple(
            ordinal
            for ordinal in smallest
            if all(
                _contains_ordinal(selection, ordinal) for selection in other_selections
            )
        )


@dataclass(slots=True)
class CompiledProgramInspectionInvertedIndexBuilder:
    """Mutable construction helper for lazy inspection layers."""

    parent_ordinals: dict[str, list[int]]
    kind_ordinals: dict[str, list[int]]
    entity_ordinals: dict[str, list[int]]
    resource_ordinals: dict[str, list[int]]
    result_ordinals: dict[str, list[int]]
    entity_complete: bool
    resource_complete: bool
    result_complete: bool

    def __init__(self) -> None:
        self.parent_ordinals = {}
        self.kind_ordinals = {}
        self.entity_ordinals = {}
        self.resource_ordinals = {}
        self.result_ordinals = {}
        self.entity_complete = True
        self.resource_complete = True
        self.result_complete = True

    def add(
        self,
        ordinal: int,
        *,
        parent_id: str | None,
        kind: str,
        entity_ids: Sequence[str] = (),
        resource_ids: Sequence[str] = (),
        result_ids: Sequence[str] = (),
        max_references_per_dimension: int | None = None,
    ) -> None:
        """Index one ordinal without constructing its transport node."""

        if parent_id is not None:
            _append_ordinal(self.parent_ordinals, parent_id, ordinal)
        _append_ordinal(self.kind_ordinals, kind, ordinal)
        if (
            max_references_per_dimension is not None
            and len(entity_ids) > max_references_per_dimension
        ):
            self.entity_complete = False
        else:
            _append_reference_ordinals(self.entity_ordinals, entity_ids, ordinal)
        if (
            max_references_per_dimension is not None
            and len(resource_ids) > max_references_per_dimension
        ):
            self.resource_complete = False
        else:
            _append_reference_ordinals(self.resource_ordinals, resource_ids, ordinal)
        if (
            max_references_per_dimension is not None
            and len(result_ids) > max_references_per_dimension
        ):
            self.result_complete = False
        else:
            _append_reference_ordinals(self.result_ordinals, result_ids, ordinal)

    def build(self) -> CompiledProgramInspectionInvertedIndex:
        return CompiledProgramInspectionInvertedIndex(
            parent_ordinals=_freeze_ordinal_map(self.parent_ordinals),
            kind_ordinals=_freeze_ordinal_map(self.kind_ordinals),
            entity_ordinals=_freeze_ordinal_map(self.entity_ordinals),
            resource_ordinals=_freeze_ordinal_map(self.resource_ordinals),
            result_ordinals=_freeze_ordinal_map(self.result_ordinals),
            entity_complete=self.entity_complete,
            resource_complete=self.resource_complete,
            result_complete=self.result_complete,
        )


def _append_ordinal(
    ordinal_map: dict[str, list[int]],
    value: str,
    ordinal: int,
) -> None:
    ordinal_map.setdefault(value, []).append(ordinal)


def _append_reference_ordinals(
    ordinal_map: dict[str, list[int]],
    values: Sequence[str],
    ordinal: int,
) -> None:
    for value in dict.fromkeys(values):
        _append_ordinal(ordinal_map, value, ordinal)


def _freeze_ordinal_map(
    ordinal_map: dict[str, list[int]],
) -> dict[str, tuple[int, ...]]:
    return {value: tuple(ordinals) for value, ordinals in ordinal_map.items()}


def _contains_ordinal(ordinals: tuple[int, ...], ordinal: int) -> bool:
    index = bisect_left(ordinals, ordinal)
    return index < len(ordinals) and ordinals[index] == ordinal


@dataclass(frozen=True, slots=True)
class CompiledProgramInspectionNodeIndex:
    """Stable random-access node source retained behind paged inspection."""

    node_count: int
    node_at: Callable[
        [int, CompiledProgramInspectionQuery | None],
        CompiledProgramInspectionNode,
    ]
    ordinal_by_id: Callable[[str], int | None] | None = None
    inverted_index: CompiledProgramInspectionInvertedIndex | None = None

    @classmethod
    def from_nodes(
        cls,
        nodes: Sequence[CompiledProgramInspectionNode],
    ) -> CompiledProgramInspectionNodeIndex:
        retained = tuple(nodes)
        ordinals = {node.id: ordinal for ordinal, node in enumerate(retained)}
        inverted_index = CompiledProgramInspectionInvertedIndexBuilder()
        for ordinal, node in enumerate(retained):
            inverted_index.add(
                ordinal,
                parent_id=node.parent_id,
                kind=node.kind,
                entity_ids=node.entity_ids,
                resource_ids=node.resource_ids,
                result_ids=node.result_ids,
                max_references_per_dimension=64,
            )
        return cls(
            node_count=len(retained),
            node_at=lambda ordinal, _query: retained[ordinal],
            ordinal_by_id=ordinals.get,
            inverted_index=inverted_index.build(),
        )


@dataclass(frozen=True, slots=True)
class CompiledProgramInspectionNodeSelection:
    """One projected node page plus stable source ordinals."""

    nodes: tuple[CompiledProgramInspectionNode, ...]
    ordinals: tuple[int, ...]
    page: CompiledProgramInspectionPage


@dataclass(frozen=True, slots=True)
class CompiledProgramInspectionLayerIndex:
    """Reusable metadata and lazy node source for one inspection layer."""

    id: str
    label: str
    kind: str
    root_ids: tuple[str, ...]
    nodes: CompiledProgramInspectionNodeIndex
    facts: tuple[CompiledInspectionFact, ...] = ()

    def project(
        self,
        *,
        query: CompiledProgramInspectionQuery | None,
        default_limit: int,
        snapshot_id: str,
    ) -> tuple[
        CompiledProgramInspectionLayer,
        CompiledProgramInspectionNodeSelection,
    ]:
        selection = query_compiled_program_node_index(
            self.id,
            self.nodes,
            query=query,
            default_limit=default_limit,
            snapshot_id=snapshot_id,
        )
        return (
            CompiledProgramInspectionLayer(
                id=self.id,
                label=self.label,
                kind=self.kind,
                node_count=self.nodes.node_count,
                nodes_truncated=(
                    selection.page.returned_node_count < self.nodes.node_count
                ),
                root_ids=self.root_ids,
                nodes=selection.nodes,
                page=selection.page,
                facts=self.facts,
            ),
            selection,
        )


def query_compiled_program_node_index(
    layer_id: str,
    nodes: CompiledProgramInspectionNodeIndex,
    *,
    query: CompiledProgramInspectionQuery | None,
    default_limit: int,
    snapshot_id: str = "transient",
) -> CompiledProgramInspectionNodeSelection:
    """Project one page without materializing an unfiltered layer."""

    if query is not None and query.layer_id != layer_id:
        return CompiledProgramInspectionNodeSelection(
            nodes=(),
            ordinals=(),
            page=CompiledProgramInspectionPage(
                offset=0,
                limit=default_limit,
                matching_node_count=nodes.node_count,
                returned_node_count=0,
                snapshot_id=snapshot_id,
                next_offset=0 if nodes.node_count else None,
                next_cursor=(
                    _inspection_cursor(snapshot_id, query, 0)
                    if nodes.node_count
                    else None
                ),
            ),
        )
    selected_query = query if query is not None and query.layer_id == layer_id else None
    if (
        selected_query is not None
        and selected_query.snapshot_id is not None
        and selected_query.snapshot_id != snapshot_id
    ):
        raise ValueError("inspection query snapshot does not match compiled artifact")
    offset = (
        0
        if selected_query is None
        else _inspection_cursor_offset(snapshot_id, selected_query)
    )
    limit = (
        default_limit
        if selected_query is None
        else min(selected_query.limit, default_limit)
    )
    selected_nodes: list[CompiledProgramInspectionNode] = []
    selected_ordinals: list[int] = []
    if (
        selected_query is not None
        and selected_query.node_id is not None
        and nodes.ordinal_by_id is not None
    ):
        ordinal = nodes.ordinal_by_id(selected_query.node_id)
        node = None if ordinal is None else nodes.node_at(ordinal, selected_query)
        matches = node is not None and _matches_program_node(node, selected_query)
        matching_node_count = int(matches)
        if matches and offset == 0:
            assert node is not None
            assert ordinal is not None
            selected_ordinals.append(ordinal)
            selected_nodes.append(
                _bound_program_node_references(node, query=selected_query)
            )
    elif selected_query is None or not _program_query_has_filters(selected_query):
        matching_node_count = nodes.node_count
        selected_ordinals.extend(
            range(offset, min(offset + limit, matching_node_count))
        )
        selected_nodes.extend(
            _bound_program_node_references(
                nodes.node_at(ordinal, selected_query),
                query=selected_query,
            )
            for ordinal in selected_ordinals
        )
    else:
        matching_node_count = 0
        candidates = (
            None
            if nodes.inverted_index is None
            else nodes.inverted_index.candidates(selected_query)
        )
        ordinals: Sequence[int] = (
            range(nodes.node_count) if candidates is None else candidates
        )
        for ordinal in ordinals:
            node = nodes.node_at(ordinal, selected_query)
            if not _matches_program_node(node, selected_query):
                continue
            if offset <= matching_node_count < offset + limit:
                selected_ordinals.append(ordinal)
                selected_nodes.append(
                    _bound_program_node_references(node, query=selected_query)
                )
            matching_node_count += 1
    selected = tuple(selected_nodes)
    next_offset = offset + len(selected)
    if next_offset >= matching_node_count:
        next_offset = None
    previous_offset = None if offset == 0 else max(0, offset - limit)
    return CompiledProgramInspectionNodeSelection(
        nodes=selected,
        ordinals=tuple(selected_ordinals),
        page=CompiledProgramInspectionPage(
            offset=offset,
            limit=limit,
            matching_node_count=matching_node_count,
            returned_node_count=len(selected),
            snapshot_id=snapshot_id,
            next_offset=next_offset,
            next_cursor=(
                None
                if next_offset is None
                else _inspection_cursor(snapshot_id, selected_query, next_offset)
            ),
            previous_cursor=(
                None
                if previous_offset is None
                else _inspection_cursor(snapshot_id, selected_query, previous_offset)
            ),
        ),
    )


def _program_query_has_filters(query: CompiledProgramInspectionQuery) -> bool:
    return any(
        value is not None
        for value in (
            query.parent_id,
            query.node_id,
            query.entity_id,
            query.resource_id,
            query.result_id,
            query.kind,
            query.text,
        )
    )


def _inspection_cursor_offset(
    snapshot_id: str,
    query: CompiledProgramInspectionQuery,
) -> int:
    if query.cursor is None:
        return query.offset
    offset_text, separator, signature = query.cursor.partition(".")
    if not separator or not offset_text.isdigit():
        raise ValueError("inspection query cursor is invalid")
    offset = int(offset_text)
    if signature != _inspection_cursor_signature(snapshot_id, query):
        raise ValueError(
            "inspection query cursor does not match its snapshot or filters"
        )
    return offset


def _inspection_cursor(
    snapshot_id: str,
    query: CompiledProgramInspectionQuery | None,
    offset: int,
) -> str:
    return f"{offset}.{_inspection_cursor_signature(snapshot_id, query)}"


def _inspection_cursor_signature(
    snapshot_id: str,
    query: CompiledProgramInspectionQuery | None,
) -> str:
    identity = (
        snapshot_id,
        None if query is None else query.layer_id,
        None if query is None else query.limit,
        None if query is None else query.node_id,
        None if query is None else query.parent_id,
        None if query is None else query.entity_id,
        None if query is None else query.resource_id,
        None if query is None else query.result_id,
        None if query is None else query.kind,
        None if query is None else query.text,
    )
    return sha256(repr(identity).encode()).hexdigest()[:24]


def _matches_program_node(
    node: CompiledProgramInspectionNode,
    query: CompiledProgramInspectionQuery,
) -> bool:
    if query.node_id is not None and node.id != query.node_id:
        return False
    if query.parent_id is not None and node.parent_id != query.parent_id:
        return False
    if query.entity_id is not None and query.entity_id not in node.entity_ids:
        return False
    if query.resource_id is not None and query.resource_id not in node.resource_ids:
        return False
    if query.result_id is not None and query.result_id not in node.result_ids:
        return False
    if query.kind is not None and node.kind != query.kind:
        return False
    if query.text is None:
        return True
    needle = query.text.casefold()
    values = (
        node.id,
        node.kind,
        node.label,
        *node.entity_ids,
        *node.resource_ids,
        *node.result_ids,
        *node.warnings,
    )
    return any(needle in value.casefold() for value in values)


def _bound_program_node_references(
    node: CompiledProgramInspectionNode,
    *,
    query: CompiledProgramInspectionQuery | None,
    limit: int = 64,
) -> CompiledProgramInspectionNode:
    entity_count = max(node.entity_count, len(node.entity_ids))
    resource_count = max(node.resource_count, len(node.resource_ids))
    result_count = max(node.result_count, len(node.result_ids))
    return replace(
        node,
        entity_ids=_bounded_references(
            node.entity_ids, query.entity_id if query else None, limit
        ),
        entity_count=entity_count,
        entity_ids_truncated=node.entity_ids_truncated or entity_count > limit,
        resource_ids=_bounded_references(
            node.resource_ids,
            query.resource_id if query else None,
            limit,
        ),
        resource_count=resource_count,
        resource_ids_truncated=node.resource_ids_truncated or resource_count > limit,
        result_ids=_bounded_references(
            node.result_ids,
            query.result_id if query else None,
            limit,
        ),
        result_count=result_count,
        result_ids_truncated=node.result_ids_truncated or result_count > limit,
    )


def _bounded_references(
    values: tuple[str, ...],
    preferred: str | None,
    limit: int,
) -> tuple[str, ...]:
    if preferred is None or preferred not in values[:limit]:
        return (
            values[:limit]
            if preferred is None or preferred not in values
            else (
                preferred,
                *tuple(value for value in values if value != preferred)[: limit - 1],
            )
        )
    return values[:limit]


@dataclass(frozen=True, slots=True)
class CompiledWaveformInspection:
    """Bounded samples and stable identity for one physical waveform."""

    channel_id: str
    instrument_id: str
    peak_abs: float
    rms: float
    source_sample_count: int
    samples_sha256: str
    sample_indices: tuple[int, ...]
    samples: tuple[float, ...]
    downsampling: Literal["none", "minmax"]


@dataclass(frozen=True, slots=True)
class CompiledPointInspection:
    """One batch-independent physical realization of a point candidate."""

    realization_fingerprint: str
    target_entry_id: str
    facts: tuple[CompiledInspectionFact, ...]
    waveform_count: int
    waveforms_truncated: bool
    waveforms: tuple[CompiledWaveformInspection, ...]
    warnings: tuple[str, ...] = ()

    def fact(self, fact_id: str) -> CompiledInspectionFact:
        return next(fact for fact in self.facts if fact.id == fact_id)


@dataclass(frozen=True, slots=True)
class CompiledInspectionBounds:
    """Hard response budgets applied to one transient inspection."""

    max_points: int
    max_waveforms_per_point: int
    max_samples_per_waveform: int


@dataclass(frozen=True, slots=True)
class CompiledWorkEstimate:
    """Target-owned work for this inspected artifact, never an implied run total.

    Equal bounds denote an exact compiler fact. A range denotes a target bound;
    wall-clock acquisition duration must not be inferred from sequence timing.
    """

    metric: str
    lower: float
    upper: float
    unit: str
    basis: str


@dataclass(frozen=True, slots=True)
class CompiledArtifactInspection:
    """Common inspection envelope shared by pre-run and running views."""

    kind: str
    facts: tuple[CompiledInspectionFact, ...]
    point_count: int
    points_truncated: bool
    bounds: CompiledInspectionBounds
    points: tuple[CompiledPointInspection, ...]
    program: CompiledProgramInspection | None = None
    warnings: tuple[str, ...] = ()
    work_estimates: tuple[CompiledWorkEstimate, ...] = ()
    schema_id: Literal["scopecat.compiled_artifact_inspection.v2"] = (
        "scopecat.compiled_artifact_inspection.v2"
    )

    def fact(self, fact_id: str) -> CompiledInspectionFact:
        return next(fact for fact in self.facts if fact.id == fact_id)


__all__ = [
    "PLANNED_INSTRUMENT_SETTING_LIMIT",
    "CompiledArtifactInspection",
    "CompiledInspectionBounds",
    "CompiledInspectionFact",
    "CompiledPointInspection",
    "CompiledProgramInspection",
    "CompiledProgramInspectionInvertedIndex",
    "CompiledProgramInspectionInvertedIndexBuilder",
    "CompiledProgramInspectionLayer",
    "CompiledProgramInspectionLayerIndex",
    "CompiledProgramInspectionLink",
    "CompiledProgramInspectionLinkIndex",
    "CompiledProgramInspectionLinkSelection",
    "CompiledProgramInspectionNode",
    "CompiledProgramInspectionNodeIndex",
    "CompiledProgramInspectionNodeSelection",
    "CompiledProgramInspectionPage",
    "CompiledProgramInspectionQuery",
    "CompiledWaveformInspection",
    "CompiledWorkEstimate",
    "PlannedInstrumentSetting",
    "query_compiled_program_node_index",
]
