"""Measure one backend call without changing its result, retry or ownership policy."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from scopecat.records.costs import OperationCostMeasurement, RunOperationCost

from scopecat_server.instruments.actors import OwnedInstrument


@dataclass
class OperationObservation:
    measured: OperationCostMeasurement | None = None
    status: Literal["completed", "rejected", "unknown"] = "unknown"

    def receipt(self, status: str, measured: OperationCostMeasurement | None) -> None:
        self.measured = measured
        self.status = (
            "unknown"
            if status == "unknown"
            else "rejected"
            if status.startswith("not_")
            else "completed"
        )


@contextmanager
def observe_operation(
    records: list[RunOperationCost],
    instrument: OwnedInstrument,
    operation_id: str,
    operation: Literal["apply", "invoke", "collect", "prepare"],
) -> Generator[OperationObservation]:
    observation = OperationObservation()
    started = perf_counter()
    try:
        yield observation
    finally:
        records.append(
            RunOperationCost(
                operation_id=operation_id,
                instrument_id=instrument.instrument_id,
                operation=operation,
                wall_seconds=perf_counter() - started,
                status=observation.status,
                connection_generation=instrument.connection_generation,
                connection_context=instrument.connection_context,
                measured=observation.measured,
            )
        )
