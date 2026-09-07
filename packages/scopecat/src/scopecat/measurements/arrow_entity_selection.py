"""Native Arrow leaf indices and selected diagnostics, never full-width NumPy data."""

from __future__ import annotations

from collections.abc import Sequence
from math import prod
from typing import cast

import numpy as np
from numpy.typing import NDArray

from scopecat.records.measurement import MeasurementArrayUnavailableGroup


def entity_leaf_indices(
    shape: tuple[int, ...], *, axis: int, positions: Sequence[int | None]
) -> NDArray[np.int64]:
    prefix, suffix = prod(shape[:axis]), prod(shape[axis + 1 :])
    selected = np.asarray(
        [0 if index is None else index for index in positions], dtype=np.int64
    )
    indices = (
        np.arange(prefix, dtype=np.int64)[:, None, None] * shape[axis] * suffix
        + selected[None, :, None] * suffix
        + np.arange(suffix, dtype=np.int64)[None, None, :]
    )
    indices[:, [i for i, value in enumerate(positions) if value is None], :] = -1
    return indices.reshape(-1)


def select_availability(
    groups: Sequence[MeasurementArrayUnavailableGroup],
    indices: NDArray[np.int64],
    *,
    dimension_id: str,
) -> tuple[MeasurementArrayUnavailableGroup, ...]:
    source_to_target = {
        int(source): target
        for target, source in enumerate(cast("list[int]", indices.tolist()))
        if source >= 0
    }
    selected: list[MeasurementArrayUnavailableGroup] = []
    for group in groups:
        targets = tuple(
            source_to_target[index]
            for index in group.flat_indices
            if index in source_to_target
        )
        if targets:
            selected.append(
                group.model_copy(update={"flat_indices": tuple(sorted(targets))})
            )
    missing = tuple(int(index) for index in np.flatnonzero(indices < 0))
    if missing:
        selected.append(
            MeasurementArrayUnavailableGroup(
                reason="missing",
                flat_indices=missing,
                metadata={"entity_alignment": "absent", "dimension_id": dimension_id},
            )
        )
    return tuple(selected)
