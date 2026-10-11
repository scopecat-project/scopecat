"""Deterministic channel-aware Ramsey response model."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import override

import numpy as np
from numpy.typing import NDArray
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat_quantum._ids import TargetCompileEntryId
from scopecat_quantum.targets import TargetAcquisitionAddress

from reference_lab.targets.list_mode.execution_model import (
    AcquisitionResponse,
    DigitizerValueBlock,
)
from reference_lab.targets.list_mode.model import (
    DigitizerAcquisitionWindow,
    acquisition_slot_identity_payload,
)


@dataclass(frozen=True, slots=True)
class RamseyResponsePoint:
    address: TargetAcquisitionAddress
    phase_rad: float
    contrast: float
    available: bool = True


@dataclass(frozen=True, slots=True)
class RamseyAcquisitionResponse(AcquisitionResponse):
    """Return stable binary IQ shots for every point and acquisition channel."""

    points: tuple[RamseyResponsePoint, ...]
    shots: int
    _state_one_shots: dict[TargetAcquisitionAddress, NDArray[np.bool_]] = field(
        init=False,
        repr=False,
        compare=False,
        hash=False,
    )
    _fingerprint: str = field(init=False, repr=False)
    _unavailable: frozenset[TargetAcquisitionAddress] = field(
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        payload = {
            "schema": "reference_lab.ramsey_response.v1",
            "shots": self.shots,
            "points": [
                {
                    "entry_id": point.address.entry_id.value,
                    "slot_id": acquisition_slot_identity_payload(point.address.slot_id),
                    "phase_rad": float(point.phase_rad).hex(),
                    "contrast": float(point.contrast).hex(),
                    "available": point.available,
                }
                for point in self.points
            ],
        }
        fingerprint = sha256_json_hash(payload)
        selected: dict[TargetAcquisitionAddress, NDArray[np.bool_]] = {}
        for point in self.points:
            if not point.available:
                continue
            probability = 0.5 * (1.0 + point.contrast * math.cos(point.phase_rad))
            count = math.floor(self.shots * probability + 0.5)
            ranked = sorted(
                range(self.shots),
                key=lambda shot: _digest(fingerprint, point.address, shot),
            )
            state_one = np.zeros(self.shots, dtype=np.bool_)
            state_one[ranked[:count]] = True
            state_one.flags.writeable = False
            selected[point.address] = state_one
        object.__setattr__(self, "_state_one_shots", selected)
        object.__setattr__(
            self,
            "_unavailable",
            frozenset(point.address for point in self.points if not point.available),
        )
        object.__setattr__(self, "_fingerprint", fingerprint)

    @property
    @override
    def fingerprint(self) -> str:
        return self._fingerprint

    @override
    def values_for(
        self,
        *,
        entry_id: TargetCompileEntryId,
        window: DigitizerAcquisitionWindow,
        shot_indices: NDArray[np.int64],
    ) -> DigitizerValueBlock:
        address = TargetAcquisitionAddress(
            entry_id=entry_id,
            slot_id=window.slot_id,
        )
        if address in self._unavailable:
            return DigitizerValueBlock(
                values=np.zeros(len(shot_indices), dtype=np.complex128),
                available=np.zeros(len(shot_indices), dtype=np.bool_),
            )
        state_one = self._state_one_shots[address][shot_indices]
        return DigitizerValueBlock(
            values=np.where(state_one, 1.0, -1.0).astype(np.complex128),
            available=np.ones(len(shot_indices), dtype=np.bool_),
        )


def _digest(
    fingerprint: str,
    address: TargetAcquisitionAddress,
    shot: int,
) -> bytes:
    encoded = json.dumps(
        {
            "fingerprint": fingerprint,
            "entry_id": address.entry_id.value,
            "slot_id": acquisition_slot_identity_payload(address.slot_id),
            "shot": shot,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).digest()


__all__ = ["RamseyAcquisitionResponse", "RamseyResponsePoint"]
