"""Compile one explicit scientific intent into stable ledger identities.

Partitions are by axis *index*, not equal coordinate value. This is deliberate:
duplicate coordinate values retain separate points and separate partitions. It is
not the production analysis grouping rule, which may combine equal values.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from .core import Store


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, allow_nan=False))


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + sha256(encoded.encode()).hexdigest()


def _name(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("author identities must be nonempty strings")
    return quote(value, safe="")


@dataclass(frozen=True)
class Axis:
    """Ordered coordinate choices; optional labels name index partitions."""

    id: str
    values: tuple[str | bool | int | float, ...]
    unit: str
    labels: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _name(self.id)
        if not isinstance(self.unit, str) or not self.values:
            raise ValueError("an axis requires units and at least one value")
        if any(
            not isinstance(value, str | bool | int | float) for value in self.values
        ):
            raise ValueError("axis values must be available scalar coordinates")
        _copy(self.values)
        if self.labels is not None:
            if len(self.labels) != len(self.values) or len(set(self.labels)) != len(
                self.labels
            ):
                raise ValueError("axis labels must uniquely name every index")
            for label in self.labels:
                _name(label)

    def document(self) -> dict:
        return {
            "id": self.id,
            "values": list(self.values),
            "unit": self.unit,
            "labels": None if self.labels is None else list(self.labels),
        }


@dataclass(frozen=True)
class Intent:
    """A product grid with explicit acquisition and analysis partitions.

    ``group_by`` must be a subset of ``work_by``: one acquisition cannot straddle
    incompatible analysis groups. ``static_closed`` is an explicit membership
    commitment. Named open groups are empty until an explicit proposal adds work;
    only a caller's ``close_group`` fixes their final membership.
    """

    run_id: str
    axes: tuple[Axis, ...]
    outputs: dict[str, dict]
    work_by: tuple[str, ...]
    group_by: tuple[str, ...]
    static_closed: bool
    reverse_axes: tuple[str, ...] = ()
    open_groups: tuple[str, ...] = ()
    selection_policy: str = "sole_candidate"
    partition_identity: str = "axis-index"

    def __post_init__(self) -> None:
        _name(self.run_id)
        names = [axis.id for axis in self.axes]
        if not names or len(set(names)) != len(names):
            raise ValueError("intent axes must be nonempty and unique")
        for partition in (self.work_by, self.group_by, self.reverse_axes):
            if len(set(partition)) != len(partition) or not set(partition) <= set(
                names
            ):
                raise ValueError(
                    "partitions and traversal must name unique admitted axes"
                )
        if not set(self.group_by) <= set(self.work_by):
            raise ValueError("work units cannot cross analysis groups")
        if not set(self.reverse_axes) <= set(self.work_by):
            raise ValueError(
                "reverse traversal must name an acquisition partition axis"
            )
        if self.partition_identity != "axis-index":
            raise ValueError("only explicit axis-index partitioning is implemented")
        if not isinstance(self.static_closed, bool):
            raise ValueError("static membership closure must be explicit")
        if self.selection_policy != "sole_candidate":
            raise ValueError(
                "author runtime implements only declared sole-candidate selection"
            )
        if not self.outputs:
            raise ValueError("intent requires output contracts")
        for name in self.outputs:
            _name(name)
        _copy(self.outputs)
        if len(set(self.open_groups)) != len(self.open_groups):
            raise ValueError("open group identities must be unique")
        for name in self.open_groups:
            _name(name)

    def document(self) -> dict:
        return _copy(
            {
                "run_id": self.run_id,
                "axes": [axis.document() for axis in self.axes],
                "outputs": self.outputs,
                "work_by": self.work_by,
                "group_by": self.group_by,
                "static_closed": self.static_closed,
                "reverse_axes": self.reverse_axes,
                "open_groups": self.open_groups,
                "selection_policy": self.selection_policy,
                "partition_identity": self.partition_identity,
            }
        )

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.document())

    def _resolved(
        self, axes: tuple[Axis, ...], scope: str
    ) -> tuple[list[dict], list[dict], dict[tuple, list[str]]]:
        positions = {axis.id: index for index, axis in enumerate(axes)}
        points = []
        partitions: dict[tuple, list[str]] = {}
        for indices in itertools.product(*(range(len(axis.values)) for axis in axes)):
            identity = "point:" + scope + ":" + ":".join(map(str, indices))
            points.append(
                {
                    "id": identity,
                    "coordinates": {
                        axis.id: {"value": axis.values[index], "unit": axis.unit}
                        for axis, index in zip(axes, indices, strict=True)
                    },
                }
            )
            key = tuple(indices[positions[name]] for name in self.work_by)
            partitions.setdefault(key, []).append(identity)
        work_axes = [axes[positions[name]] for name in self.work_by]
        traversal = itertools.product(
            *(
                reversed(range(len(axis.values)))
                if axis.id in self.reverse_axes
                else range(len(axis.values))
                for axis in work_axes
            )
        )
        units = []
        groups: dict[tuple, list[str]] = {}
        for key in traversal:
            suffix = (
                ":".join(
                    f"{_name(name)}={index}"
                    for name, index in zip(self.work_by, key, strict=True)
                )
                or "all"
            )
            identity = f"work:{scope}:{suffix}"
            units.append(
                {
                    "id": identity,
                    "point_ids": partitions[key],
                    "required_outputs": list(self.outputs),
                    "output_contracts": _copy(self.outputs),
                    "selection_policy": self.selection_policy,
                }
            )
            by_axis = dict(zip(self.work_by, key, strict=True))
            group_key = tuple(by_axis[name] for name in self.group_by)
            groups.setdefault(group_key, []).append(identity)
        return points, units, groups

    def _group_id(self, key: tuple[int, ...]) -> str:
        if len(self.group_by) == 1:
            axis = next(axis for axis in self.axes if axis.id == self.group_by[0])
            if axis.labels is not None:
                return axis.labels[key[0]]
        return "group:" + (
            ":".join(
                f"{_name(name)}={index}"
                for name, index in zip(self.group_by, key, strict=True)
            )
            or "all"
        )

    def compile(self, *, commit_mode: str = "atomic") -> dict:
        if commit_mode not in {"atomic", "staged"}:
            raise ValueError("commit mode must be atomic or staged")
        points, units, membership = self._resolved(self.axes, "static")
        groups = [
            {"id": self._group_id(key), "unit_ids": ids, "closed": self.static_closed}
            for key, ids in membership.items()
        ]
        if {group["id"] for group in groups}.intersection(self.open_groups):
            raise ValueError("static group labels collide with open group names")
        groups.extend(
            {"id": name, "unit_ids": [], "closed": False} for name in self.open_groups
        )
        return {
            "run_id": self.run_id,
            "points": points,
            "units": units,
            "groups": groups,
            "author_intent": {
                "schema": "result-ledger.author-intent.v1",
                "intent": self.document(),
                "fingerprint": self.fingerprint,
                "commit_mode": commit_mode,
            },
        }

    def initialize(self, store: Store, *, commit_mode: str = "atomic") -> None:
        store.initialize(**self.compile(commit_mode=commit_mode))

    def verify(self, store: Store) -> None:
        from .core import Conflict

        persisted = store.state().get("author_intent")
        if (
            not isinstance(persisted, dict)
            or persisted.get("intent") != self.document()
            or persisted.get("fingerprint") != self.fingerprint
        ):
            raise Conflict(
                "current author intent does not match admitted scientific intent"
            )

    def extend(
        self,
        store: Store,
        fence: int,
        group_id: str,
        proposal_id: str,
        axis_values: dict[str, tuple],
        source_snapshot: str,
        rule_id: str,
    ) -> None:
        """Persist derived work and its exact decision source; never close the group."""
        self.verify(store)
        if group_id not in self.open_groups:
            raise ValueError("proposal must target a declared open group")
        if set(axis_values) != {axis.id for axis in self.axes}:
            raise ValueError("proposal must provide every admitted coordinate axis")
        _name(proposal_id)
        _name(source_snapshot)
        _name(rule_id)
        axes = tuple(
            Axis(axis.id, tuple(axis_values[axis.id]), axis.unit) for axis in self.axes
        )
        scope = f"open:{_name(group_id)}:{_name(proposal_id)}"
        points, units, _ = self._resolved(axes, scope)
        store.append_work(
            fence,
            points,
            units,
            group_id,
            proposal={
                "id": proposal_id,
                "source_snapshot": source_snapshot,
                "rule_id": rule_id,
                "axis_values": _copy(axis_values),
                "author_intent_fingerprint": self.fingerprint,
                "partition_identity": self.partition_identity,
            },
        )
