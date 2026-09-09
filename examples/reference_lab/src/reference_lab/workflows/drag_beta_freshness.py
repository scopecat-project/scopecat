"""Project-owned q0/q1 DRAG freshness policy and bounded registry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict, Field
from scopecat import Quantity
from scopecat.api.calibration_planner import CalibrationPlanningContext
from scopecat.automation import (
    CalibrationDependencyEvidence,
    CalibrationObservation,
    CalibrationRegistry,
    CalibrationTargetRef,
    calibration,
)
from scopecat.kernel.content_identity import stable_content_hash
from scopecat.kernel.entity import EntityRef
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.content import Sha256ContentHash
from scopecat.records.parameter import StoredParameterValue, TableParameterValue
from scopecat.records.run import ConfigRegistryRunConfigSource

from reference_lab.parameters import DRAG_BETA, QUBIT, QUBITS
from reference_lab.workflows.drag_beta_experiment import DragBetaQubit
from reference_lab.workflows.drag_beta_procedure import (
    DragBetaVerificationIntent,
    drag_beta_verification_procedure,
)
from reference_lab.workflows.drag_beta_verification import (
    DRAG_BETA_MINIMUM_IMPROVEMENT,
)

DRAG_BETA_CALIBRATION_ID = "reference-lab.drag-beta-freshness"
DRAG_BETA_CALIBRATION_VERSION = "7"
DRAG_BETA_CALIBRATION_FANOUT_SCOPE = "reference-lab.quantum-chip"
DRAG_BETA_CALIBRATION_TARGETS = tuple(
    CalibrationTargetRef(kind="logical_qubit", id=qubit) for qubit in ("q0", "q1")
)
_DRAG_BETA_PREREQUISITE_CODEC = "reference_lab.drag-beta-prerequisites.v1"
_DRAG_BETA_QUBITS = ("q0", "q1")


class DragBetaFreshnessInputs(BaseModel):
    """Scientific inputs whose changes make one target stale.

    Registry entry identity and generation are intentionally absent: they are
    invocation provenance.  Profile/snapshot IDs and a peer target's owned DRAG
    value are also non-semantic for one member, while its own active DRAG value
    remains explicit so an external edit makes that member stale.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    qubit: DragBetaQubit
    prerequisite_fingerprint: Sha256ContentHash
    active_drag_beta_ns: float
    minimum_improvement: float = Field(
        default=DRAG_BETA_MINIMUM_IMPROVEMENT,
        ge=0.0,
    )


def drag_beta_semantic_freshness_inputs(
    config: ConfigProfileSnapshot,
    qubit: DragBetaQubit,
    *,
    minimum_improvement: float = DRAG_BETA_MINIMUM_IMPROVEMENT,
) -> DragBetaFreshnessInputs:
    """Project one config onto the semantic inputs for one DRAG member.

    The projection conservatively retains the complete system and all accepted
    parameters except the q0/q1 DRAG cells owned by this calibration policy.
    The selected target's cell is carried separately.  This lets independently
    verified q0/q1 candidates compose without treating generated snapshot IDs
    or the peer's result as a new prerequisite.
    """

    qubit_table = config.parameter_snapshot.get(QUBITS.id)
    if not isinstance(qubit_table, TableParameterValue):
        raise ValueError("DRAG freshness requires the qubit parameter table")
    rows = _drag_beta_rows(qubit_table)
    active_drag_beta = rows[qubit][DRAG_BETA.id]
    if not isinstance(active_drag_beta, Quantity):
        raise ValueError("DRAG freshness requires a quantity-valued active beta")

    projected_values = tuple(
        _project_parameter_value(value) for value in config.parameter_snapshot.values
    )
    prerequisite_fingerprint = "sha256:" + stable_content_hash(
        {
            "codec": _DRAG_BETA_PREREQUISITE_CODEC,
            "owned_paths": [
                {
                    "parameter_id": QUBITS.id,
                    "key": {QUBIT.id: owned_qubit},
                    "column": DRAG_BETA.id,
                }
                for owned_qubit in _DRAG_BETA_QUBITS
            ],
            "system": config.system.model_dump(mode="json"),
            "parameter_values": projected_values,
        }
    )
    return DragBetaFreshnessInputs(
        qubit=qubit,
        prerequisite_fingerprint=prerequisite_fingerprint,
        active_drag_beta_ns=float(active_drag_beta.to("ns").value),
        minimum_improvement=minimum_improvement,
    )


def _select_drag_beta_targets(
    _context: CalibrationPlanningContext,
) -> tuple[CalibrationTargetRef, ...]:
    return DRAG_BETA_CALIBRATION_TARGETS


def _observe_drag_beta_target(
    context: CalibrationPlanningContext,
    target: CalibrationTargetRef,
) -> CalibrationObservation[DragBetaFreshnessInputs]:
    if target not in DRAG_BETA_CALIBRATION_TARGETS:
        raise ValueError(f"unsupported reference-lab DRAG target: {target}")
    content_hash = config_content_hash(context.config)
    if context.config_source.content_hash != content_hash:
        raise ValueError("calibration planning config does not match its source hash")
    return CalibrationObservation(
        inputs=drag_beta_semantic_freshness_inputs(
            context.config,
            cast("DragBetaQubit", target.id),
        )
    )


@calibration(
    id=DRAG_BETA_CALIBRATION_ID,
    version=DRAG_BETA_CALIBRATION_VERSION,
    inputs=DragBetaFreshnessInputs,
    procedure=drag_beta_verification_procedure,
    fanout_scope=DRAG_BETA_CALIBRATION_FANOUT_SCOPE,
    max_in_flight=2,
    success_policy="published_result",
    select=_select_drag_beta_targets,
    observe=_observe_drag_beta_target,
)
def drag_beta_freshness_calibration(
    context: CalibrationPlanningContext,
    target: CalibrationTargetRef,
    inputs: DragBetaFreshnessInputs,
    dependencies: tuple[CalibrationDependencyEvidence, ...],
) -> DragBetaVerificationIntent:
    """Build the exact verify-only intent after status/dependency evaluation."""

    if dependencies:
        raise ValueError("reference-lab DRAG calibration has no dependencies")
    if target.id != inputs.qubit:
        raise ValueError("DRAG calibration target does not match its observed input")
    content_hash = config_content_hash(context.config)
    expected_inputs = drag_beta_semantic_freshness_inputs(
        context.config,
        inputs.qubit,
        minimum_improvement=inputs.minimum_improvement,
    )
    if inputs != expected_inputs:
        raise ValueError("DRAG freshness input does not match the planning config")
    source = context.config_source
    if source.content_hash != content_hash:
        raise ValueError("calibration planning config does not match its source hash")
    return DragBetaVerificationIntent(
        qubit=inputs.qubit,
        initial_config=context.config,
        initial_config_source=ConfigRegistryRunConfigSource(
            selector=source.selector,
            entry_id=source.entry_id,
            config_ref=source.config_ref,
            content_hash=source.content_hash,
            registry_generation=source.registry_generation,
        ),
        minimum_improvement=inputs.minimum_improvement,
    )


def _drag_beta_rows(
    table: TableParameterValue,
) -> dict[DragBetaQubit, Mapping[str, object]]:
    selected: dict[DragBetaQubit, Mapping[str, object]] = {}
    for row in table.rows:
        qubit = _drag_beta_row_qubit(row)
        if qubit is None:
            continue
        if qubit in selected:
            raise ValueError(f"DRAG freshness found duplicate qubit row: {qubit}")
        if DRAG_BETA.id not in row:
            raise ValueError(f"DRAG freshness qubit row has no beta: {qubit}")
        selected[qubit] = cast("Mapping[str, object]", row)
    if set(selected) != set(_DRAG_BETA_QUBITS):
        raise ValueError("DRAG freshness requires exactly the q0/q1 qubit rows")
    return selected


def _project_parameter_value(
    value: StoredParameterValue,
) -> object:
    if not isinstance(value, TableParameterValue) or value.id != QUBITS.id:
        return value.model_dump(mode="json")
    projected = value.model_copy(
        update={
            "rows": tuple(
                {
                    column_id: cell
                    for column_id, cell in row.items()
                    if column_id != DRAG_BETA.id or _drag_beta_row_qubit(row) is None
                }
                for row in value.rows
            )
        }
    )
    return projected.model_dump(mode="json")


def _drag_beta_row_qubit(
    row: Mapping[str, object],
) -> DragBetaQubit | None:
    entity = row.get(QUBIT.id)
    if (
        not isinstance(entity, EntityRef)
        or entity.kind != "logical_qubit"
        or entity.id not in _DRAG_BETA_QUBITS
    ):
        return None
    return entity.id


DRAG_BETA_CALIBRATION_REGISTRY = CalibrationRegistry[CalibrationPlanningContext](
    (drag_beta_freshness_calibration,)
)


__all__ = [
    "DRAG_BETA_CALIBRATION_FANOUT_SCOPE",
    "DRAG_BETA_CALIBRATION_ID",
    "DRAG_BETA_CALIBRATION_REGISTRY",
    "DRAG_BETA_CALIBRATION_TARGETS",
    "DRAG_BETA_CALIBRATION_VERSION",
    "DragBetaFreshnessInputs",
    "drag_beta_freshness_calibration",
    "drag_beta_semantic_freshness_inputs",
]
