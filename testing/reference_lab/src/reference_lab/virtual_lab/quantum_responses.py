"""Deterministic acquisition responses for the virtual quantum lab."""

from __future__ import annotations

import math
from typing import cast

from scopecat import Quantity
from scopecat_quantum import authoring as quantum
from scopecat_quantum.program_targets import PreparedQuantumTargetEntry
from scopecat_quantum.targets import TargetAcquisitionAddress

from reference_lab.point_values import QuantumLabPointValues
from reference_lab.targets.list_mode import AcquisitionResponse
from reference_lab.virtual_lab.responses.drag_beta import (
    DragBetaAcquisitionResponse,
    DragBetaResponsePoint,
)
from reference_lab.virtual_lab.responses.ramsey import (
    RamseyAcquisitionResponse,
    RamseyResponsePoint,
)
from reference_lab.workflows.drag_beta_calibration import (
    drag_beta_program,
)
from reference_lab.workflows.ramsey import (
    parallel_two_qubit_ramsey_program,
    ramsey_program,
)


def quantum_lab_response(
    program: quantum.Program,
    points: tuple[QuantumLabPointValues, ...],
    entries: tuple[PreparedQuantumTargetEntry, ...],
    shots: int,
) -> AcquisitionResponse | None:
    """Select the one workflow-specific response used by the demo."""

    if program.id == drag_beta_program.id:
        [result] = program.results
        return DragBetaAcquisitionResponse(
            points=tuple(
                DragBetaResponsePoint(
                    address=_result_addresses(entry, result)[0],
                    beta=cast("Quantity", point.value("beta")),
                    amplification=cast("int", point.value("amplification")),
                )
                for entry, point in zip(entries, points, strict=True)
            ),
            shots=shots,
        )
    if program.id not in {
        ramsey_program.id,
        parallel_two_qubit_ramsey_program.id,
    }:
        return None
    return RamseyAcquisitionResponse(
        points=tuple(
            RamseyResponsePoint(
                address=address,
                phase_rad=float(
                    cast(
                        "Quantity",
                        point.value(
                            result.id.removesuffix("_iq_shots") + "_phase"
                            if result.id != "iq_shots"
                            else "phase"
                        ),
                    )
                    .to("rad")
                    .value
                )
                + _ramsey_precession(cast("Quantity", point.value("delay"))),
                contrast=_ramsey_contrast(cast("Quantity", point.value("delay"))),
                available=not (
                    result.id == "q1_iq_shots"
                    and cast("Quantity", point.value("delay")) == Quantity(128, "ns")
                ),
            )
            for entry, point in zip(entries, points, strict=True)
            for result in program.results
            for address in _result_addresses(entry, result)
        ),
        shots=shots,
    )


def _ramsey_contrast(delay: Quantity) -> float:
    delay_ns = float(delay.to("ns").value)
    return 0.9 * math.exp(-delay_ns / 400.0)


def _ramsey_precession(delay: Quantity) -> float:
    delay_ns = float(delay.to("ns").value)
    return 2.0 * math.pi * 0.0125 * delay_ns


def _result_addresses(
    entry: PreparedQuantumTargetEntry,
    result: quantum.MeasurementResult,
) -> tuple[TargetAcquisitionAddress, ...]:
    if result.entity_set is None:
        [address] = tuple(
            address
            for address in entry.acquisition_addresses
            if address.slot_id == result.acquisition_slot_id
        )
        return (address,)
    return tuple(
        address
        for address in entry.acquisition_addresses
        if address.slot_id.local_id == result.id
    )


__all__ = ["quantum_lab_response"]
