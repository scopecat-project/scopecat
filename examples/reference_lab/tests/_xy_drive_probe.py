"""Observe real virtual-AWG emissions inside the instrument worker."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import override

import numpy as np
from pydantic import BaseModel
from scopecat.sdk.instruments import InstrumentBackend

from reference_lab.bench_devices import BenchSignalWorld
from reference_lab.payloads import reference_lab_payload_codecs
from reference_lab.provider import ReferenceLabProvider


class WaveformEmission(BaseModel):
    pid: int
    component_path: tuple[str, ...]
    samples: tuple[float, ...]
    sample_rate_hz: float
    amplitude_v: float
    offset_v: float
    output_enabled: bool
    repeat: bool


class _ObservedWorld(BenchSignalWorld):
    def __init__(self, destination: Path) -> None:
        super().__init__()
        self.destination = destination

    @override
    def emit(
        self,
        *,
        component_path: tuple[str, ...],
        normalized_samples: Sequence[float] | np.ndarray,
        sample_rate_hz: float,
        amplitude_v: float,
        offset_v: float,
        output_enabled: bool,
        repeat: bool,
    ) -> bool:
        captured = super().emit(
            component_path=component_path,
            normalized_samples=normalized_samples,
            sample_rate_hz=sample_rate_hz,
            amplitude_v=amplitude_v,
            offset_v=offset_v,
            output_enabled=output_enabled,
            repeat=repeat,
        )
        emission = WaveformEmission(
            pid=os.getpid(),
            component_path=component_path,
            samples=tuple(float(value) for value in normalized_samples),
            sample_rate_hz=sample_rate_hz,
            amplitude_v=amplitude_v,
            offset_v=offset_v,
            output_enabled=output_enabled,
            repeat=repeat,
        )
        with self.destination.open("a", encoding="utf-8") as stream:
            stream.write(emission.model_dump_json() + "\n")
        return captured


def create_backend(project_root: Path) -> InstrumentBackend:
    provider = ReferenceLabProvider()
    # Observe the existing simulator, without replacing its drivers or transport.
    provider._bench = _ObservedWorld(project_root / "xy-emissions.jsonl")
    return InstrumentBackend(
        provider=provider,
        driver_catalog=provider.driver_catalog,
        payload_codecs=reference_lab_payload_codecs(),
    )
