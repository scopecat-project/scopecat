"""Injectable logical-to-physical placement for the list-mode target."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain, islice
from typing import Protocol

from scopecat.kernel.content_identity import sha256_json_hash

from reference_lab.targets.list_mode.model import (
    ListModeDeviceSnapshot,
    ListModePlacementCandidate,
    ListModePlacementConstraint,
    ListModePlacementConstraintKind,
    ListModePlacementRejection,
    ListModeSignalPlacement,
)

type LogicalSignalKey = tuple[str, str, str]


class ListModePlacementError(ValueError):
    """Report logical signals that the provider cannot place."""

    __slots__ = ("missing_signals",)

    def __init__(self, missing_signals: tuple[LogicalSignalKey, ...]) -> None:
        self.missing_signals = missing_signals
        super().__init__(
            "no configured physical route for "
            + ", ".join("/".join(signal) for signal in missing_signals)
        )


@dataclass(frozen=True, slots=True)
class ListModePlacementDecision:
    """One provider-owned placement plus bounded decision evidence."""

    provider_id: str
    provider_fingerprint: str
    device_snapshot_fingerprint: str
    placements: tuple[ListModeSignalPlacement, ...]
    candidates: tuple[ListModePlacementCandidate, ...]
    candidate_count: int
    constraints: tuple[ListModePlacementConstraint, ...]
    constraint_ids_by_signal: tuple[tuple[LogicalSignalKey, tuple[str, ...]], ...]
    candidate_ids_by_signal: tuple[tuple[LogicalSignalKey, tuple[str, ...]], ...]
    candidate_counts_by_signal: tuple[tuple[LogicalSignalKey, int], ...]


class ListModePlacementProvider(Protocol):
    """Choose physical routes for one immutable device snapshot."""

    @property
    def id(self) -> str: ...

    @property
    def fingerprint(self) -> str: ...

    def place(
        self,
        selected_signals: tuple[LogicalSignalKey, ...],
        snapshot: ListModeDeviceSnapshot,
    ) -> ListModePlacementDecision: ...


@dataclass(frozen=True, slots=True)
class ConfiguredRoutePlacementProvider:
    """Select each signal's configured route and explain nearby alternatives."""

    max_candidates_per_signal: int = 8

    @property
    def id(self) -> str:
        return "reference_lab.configured-route-placement.v1"

    @property
    def fingerprint(self) -> str:
        return sha256_json_hash(
            {
                "schema": "reference_lab.placement_provider.v1",
                "provider_id": self.id,
                "max_candidates_per_signal": self.max_candidates_per_signal,
            }
        )

    def place(
        self,
        selected_signals: tuple[LogicalSignalKey, ...],
        snapshot: ListModeDeviceSnapshot,
    ) -> ListModePlacementDecision:
        routes_by_signal = {
            placement.signal: placement for placement in snapshot.signal_placements
        }
        missing_signals = tuple(
            signal for signal in selected_signals if signal not in routes_by_signal
        )
        if missing_signals:
            raise ListModePlacementError(missing_signals)
        placements = tuple(routes_by_signal[signal] for signal in selected_signals)
        (
            candidates,
            candidate_ids_by_signal,
            candidate_counts_by_signal,
            candidate_count,
        ) = self._placement_candidates(selected_signals, snapshot)
        constraint_ids_by_signal: dict[LogicalSignalKey, list[str]] = {
            signal: [] for signal in selected_signals
        }
        constraints: list[ListModePlacementConstraint] = []

        def add_constraint(
            *,
            id: str,
            kind: ListModePlacementConstraintKind,
            label: str,
            selected: tuple[ListModeSignalPlacement, ...],
            resource_ids: tuple[str, ...],
        ) -> None:
            signals = tuple(placement.signal for placement in selected)
            constraints.append(
                ListModePlacementConstraint(
                    id=id,
                    kind=kind,
                    label=label,
                    signals=signals,
                    entity_ids=tuple(sorted({signal[2] for signal in signals})),
                    resource_ids=resource_ids,
                )
            )
            for signal in signals:
                constraint_ids_by_signal[signal].append(id)

        for placement in placements:
            add_constraint(
                id=f"route:{':'.join(placement.signal)}",
                kind="configured_route",
                label=(
                    f"configured {placement.signal[0]} route for {placement.signal[2]}"
                ),
                selected=(placement,),
                resource_ids=tuple(endpoint.id for endpoint in placement.endpoints),
            )

        for endpoint_id in sorted(
            {
                endpoint.id
                for placement in placements
                for endpoint in placement.endpoints
            }
        ):
            selected = tuple(
                placement
                for placement in placements
                if endpoint_id in {endpoint.id for endpoint in placement.endpoints}
            )
            if len(selected) > 1:
                add_constraint(
                    id=f"shared-endpoint:{endpoint_id}",
                    kind="shared_endpoint",
                    label=f"{len(selected)} logical signals share {endpoint_id}",
                    selected=selected,
                    resource_ids=(endpoint_id,),
                )

        for lo_group_id in sorted(
            {
                placement.lo_group_id
                for placement in placements
                if placement.lo_group_id is not None
            }
        ):
            selected = tuple(
                placement
                for placement in placements
                if placement.lo_group_id == lo_group_id
            )
            add_constraint(
                id=f"shared-lo:{lo_group_id}",
                kind="shared_local_oscillator",
                label=(
                    f"phase/frequency reference is coupled by LO group {lo_group_id}"
                ),
                selected=selected,
                resource_ids=(f"lo-group:{lo_group_id}",),
            )

        for instrument_id, demodulator_slot_id in sorted(
            {
                (placement.endpoints[0].instrument_id, placement.demodulator_slot_id)
                for placement in placements
                if placement.demodulator_slot_id is not None
            }
        ):
            selected = tuple(
                placement
                for placement in placements
                if placement.endpoints[0].instrument_id == instrument_id
                and placement.demodulator_slot_id == demodulator_slot_id
            )
            add_constraint(
                id=f"demodulator:{instrument_id}:{demodulator_slot_id}",
                kind="demodulator_slot",
                label=(f"acquisition is assigned to demodulator {demodulator_slot_id}"),
                selected=selected,
                resource_ids=(f"{instrument_id}:demodulator:{demodulator_slot_id}",),
            )

        add_constraint(
            id=f"timing:{snapshot.timing_instrument_id}",
            kind="timing_domain",
            label=f"events share timing controller {snapshot.timing_instrument_id}",
            selected=placements,
            resource_ids=(snapshot.timing_instrument_id,),
        )
        return ListModePlacementDecision(
            provider_id=self.id,
            provider_fingerprint=self.fingerprint,
            device_snapshot_fingerprint=snapshot.snapshot_fingerprint,
            placements=placements,
            candidates=candidates,
            candidate_count=candidate_count,
            constraints=tuple(constraints),
            constraint_ids_by_signal=tuple(
                (signal, tuple(constraint_ids_by_signal[signal]))
                for signal in selected_signals
            ),
            candidate_ids_by_signal=candidate_ids_by_signal,
            candidate_counts_by_signal=candidate_counts_by_signal,
        )

    def _placement_candidates(
        self,
        selected_signals: tuple[LogicalSignalKey, ...],
        snapshot: ListModeDeviceSnapshot,
    ) -> tuple[
        tuple[ListModePlacementCandidate, ...],
        tuple[tuple[LogicalSignalKey, tuple[str, ...]], ...],
        tuple[tuple[LogicalSignalKey, int], ...],
        int,
    ]:
        routes_by_role: dict[tuple[str, str], list[ListModeSignalPlacement]] = {}
        routes_by_entity: dict[tuple[str, str, str], list[ListModeSignalPlacement]] = {}
        for route in snapshot.signal_placements:
            if not route.endpoints:
                continue
            endpoint_kind = route.endpoints[0].kind
            routes_by_role.setdefault((endpoint_kind, route.signal[0]), []).append(
                route
            )
            routes_by_entity.setdefault(
                (endpoint_kind, route.signal[1], route.signal[2]), []
            ).append(route)

        candidates: list[ListModePlacementCandidate] = []
        candidate_ids_by_signal: list[tuple[LogicalSignalKey, tuple[str, ...]]] = []
        candidate_counts_by_signal: list[tuple[LogicalSignalKey, int]] = []
        total_candidate_count = 0
        for signal in selected_signals:
            endpoint_kind = (
                "acquisition_input" if signal[0] == "acquire" else "waveform_output"
            )
            selected = snapshot.signal_placement(signal)
            same_entity = routes_by_entity.get(
                (endpoint_kind, signal[1], signal[2]),
                [],
            )
            same_role = routes_by_role.get((endpoint_kind, signal[0]), [])
            signal_candidate_count = len(same_role) + sum(
                route.signal[0] != signal[0] for route in same_entity
            )
            total_candidate_count += signal_candidate_count
            candidate_counts_by_signal.append((signal, signal_candidate_count))
            candidate_routes = tuple(
                islice(
                    chain(
                        (selected,),
                        (route for route in same_entity if route != selected),
                        (route for route in same_role if route != selected),
                    ),
                    self.max_candidates_per_signal,
                )
            )
            signal_candidate_ids: list[str] = []
            for route in candidate_routes:
                rejections: list[ListModePlacementRejection] = []
                if route.signal[0] != signal[0]:
                    rejections.append(
                        ListModePlacementRejection(
                            code="signal_role_mismatch",
                            message=(
                                f"requested {signal[0]} but route is configured for "
                                f"{route.signal[0]}"
                            ),
                        )
                    )
                if route.signal[1] != signal[1]:
                    rejections.append(
                        ListModePlacementRejection(
                            code="entity_kind_mismatch",
                            message=(
                                f"requested {signal[1]} but route is configured for "
                                f"{route.signal[1]}"
                            ),
                        )
                    )
                if route.signal[2] != signal[2]:
                    rejections.append(
                        ListModePlacementRejection(
                            code="entity_mismatch",
                            message=(
                                f"requested {signal[2]} but route is configured for "
                                f"{route.signal[2]}"
                            ),
                        )
                    )
                candidate_id = (
                    f"candidate:{':'.join(signal)}->configured:{':'.join(route.signal)}"
                )
                signal_candidate_ids.append(candidate_id)
                candidates.append(
                    ListModePlacementCandidate(
                        id=candidate_id,
                        signal=signal,
                        route=route,
                        status="selected" if not rejections else "rejected",
                        rejections=tuple(rejections),
                    )
                )
            candidate_ids_by_signal.append((signal, tuple(signal_candidate_ids)))
        return (
            tuple(candidates),
            tuple(candidate_ids_by_signal),
            tuple(candidate_counts_by_signal),
            total_candidate_count,
        )


__all__ = [
    "ConfiguredRoutePlacementProvider",
    "ListModePlacementDecision",
    "ListModePlacementError",
    "ListModePlacementProvider",
    "LogicalSignalKey",
]
