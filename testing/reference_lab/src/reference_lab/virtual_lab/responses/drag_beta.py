"""Deterministic device-response plan for the DRAG-beta workflow.

The list-mode target compiler remains a waveform compiler. This lab-owned model is
bound later, at the acquisition effect boundary, where one exact target result
address is already correlated with its authored beta and amplification count.
Its fingerprint is included in both invocation intent and raw-run identity.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import cast, override

import numpy as np
from numpy.typing import NDArray
from scopecat import Quantity
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

_OPTIMUM_BETA_NS = 0.75
_BASELINE = 0.04
_CURVATURE = 0.008
_IQ_JITTER = 0.025
_MODEL_VERSION = "drag-beta-quadratic.v1"


@dataclass(frozen=True, slots=True)
class DragBetaResponsePoint:
    """Physical result address and the authored coordinates that drive it."""

    address: TargetAcquisitionAddress
    beta: Quantity
    amplification: int

    @property
    def beta_ns(self) -> float:
        """Return beta in the canonical response-model unit."""

        return _beta_ns(self.beta)


@dataclass(frozen=True, slots=True)
class DragBetaAcquisitionResponse(AcquisitionResponse):
    """Address-keyed deterministic IQ response for one prepared target batch.

    Exactly ``round(shots * p1)`` shots at each point are assigned to state 1.
    A stable hash permutes those assignments across shot order, while small
    bounded IQ jitter keeps the frames device-like without changing binary
    discrimination at the ``-1`` and ``+1`` centroids.
    """

    points: tuple[DragBetaResponsePoint, ...]
    shots: int
    _state_one_shots: dict[
        TargetAcquisitionAddress,
        NDArray[np.bool_],
    ] = field(init=False, repr=False, compare=False, hash=False)
    _fingerprint: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        selected = tuple(self.points)

        payload = {
            "schema": "reference_lab.drag_beta_response_plan.v1",
            "model_version": _MODEL_VERSION,
            "shots": self.shots,
            "optimum_beta_ns": float(_OPTIMUM_BETA_NS).hex(),
            "baseline": float(_BASELINE).hex(),
            "curvature": float(_CURVATURE).hex(),
            "iq_jitter": float(_IQ_JITTER).hex(),
            "points": [
                {
                    "entry_id": point.address.entry_id.value,
                    "slot_id": acquisition_slot_identity_payload(point.address.slot_id),
                    "beta_ns": float(point.beta_ns).hex(),
                    "amplification": point.amplification,
                }
                for point in selected
            ],
        }
        fingerprint = sha256_json_hash(payload)
        state_one_shots: dict[TargetAcquisitionAddress, NDArray[np.bool_]] = {}
        for point in selected:
            probability = _probability_one(
                beta_ns=point.beta_ns,
                amplification=point.amplification,
            )
            count = math.floor(self.shots * probability + 0.5)
            ranked = sorted(
                range(self.shots),
                key=lambda shot_index: _shot_digest(
                    fingerprint,
                    point.address,
                    shot_index,
                    purpose="state",
                ),
            )
            state_one = np.zeros(self.shots, dtype=np.bool_)
            state_one[ranked[:count]] = True
            state_one.flags.writeable = False
            state_one_shots[point.address] = state_one

        object.__setattr__(self, "points", selected)
        object.__setattr__(self, "_state_one_shots", state_one_shots)
        object.__setattr__(self, "_fingerprint", fingerprint)

    @property
    @override
    def fingerprint(self) -> str:
        """Return the stable identity of all response-affecting facts."""

        return self._fingerprint

    @override
    def values_for(
        self,
        *,
        entry_id: TargetCompileEntryId,
        window: DigitizerAcquisitionWindow,
        shot_indices: NDArray[np.int64],
    ) -> DigitizerValueBlock:
        """Generate one vector of deterministic integrated-IQ shots."""

        address = TargetAcquisitionAddress(
            entry_id=entry_id,
            slot_id=window.slot_id,
        )
        state_one = self._state_one_shots[address]
        values = np.empty(len(shot_indices), dtype=np.complex128)
        for position in range(len(shot_indices)):
            shot_index = int(cast("np.int64", shot_indices[position]))
            digest = _shot_digest(
                self.fingerprint,
                address,
                shot_index,
                purpose="jitter",
            )
            real_jitter = _symmetric_fraction(digest[:8]) * _IQ_JITTER
            imag_jitter = _symmetric_fraction(digest[8:16]) * _IQ_JITTER
            values[position] = complex(
                (1.0 if state_one[shot_index] else -1.0) + real_jitter,
                imag_jitter,
            )
        return DigitizerValueBlock(
            values=values,
            available=np.ones(len(shot_indices), dtype=np.bool_),
        )


def _probability_one(
    *,
    beta_ns: float,
    amplification: int,
) -> float:
    probability = (
        _BASELINE + _CURVATURE * amplification**2 * (beta_ns - _OPTIMUM_BETA_NS) ** 2
    )
    if not 0.0 <= probability <= 1.0:
        msg = "DRAG response probability lies outside [0, 1]; narrow the scan"
        raise ValueError(msg)
    return probability


def _shot_digest(
    fingerprint: str,
    address: TargetAcquisitionAddress,
    shot_index: int,
    *,
    purpose: str,
) -> bytes:
    encoded = json.dumps(
        {
            "schema": "reference_lab.drag_beta_shot.v1",
            "response_fingerprint": fingerprint,
            "entry_id": address.entry_id.value,
            "slot_id": acquisition_slot_identity_payload(address.slot_id),
            "shot_index": shot_index,
            "purpose": purpose,
        },
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).digest()


def _symmetric_fraction(raw: bytes) -> float:
    denominator = float((1 << (8 * len(raw))) - 1)
    return 2.0 * int.from_bytes(raw, "big") / denominator - 1.0


def _beta_ns(value: Quantity) -> float:
    return float(value.to("ns").value)


__all__ = [
    "DragBetaAcquisitionResponse",
    "DragBetaResponsePoint",
]
