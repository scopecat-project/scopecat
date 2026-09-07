"""Translate between backend requests and driver-facing values."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from scopecat.kernel.state import StateValue
from scopecat.records.instrument import (
    InstrumentReadback,
    InstrumentStateObservation,
    InstrumentStateReadback,
    state_member_target,
)
from scopecat.sdk.instruments.authoring import (
    DriverAcquisition,
    DriverAcquisitionDimension,
    DriverAcquisitionPlan,
    DriverOutcome,
    DriverReadback,
    DriverRejected,
    DriverScalar,
    DriverStateAssignment,
    DriverStatePatch,
    DriverStateReadback,
    DriverStateReadRequest,
    DriverSuccess,
)
from scopecat.sdk.instruments.backend import (
    BackendAcquisitionPlan,
    BackendApplyRequest,
    BackendCollectRequest,
    BackendReadRequest,
)
from scopecat.sdk.instruments.commands import (
    AcquisitionPreparationReceipt,
    ApplyReceipt,
    CollectReceipt,
    InvokeReceipt,
)


def lower_acquisition_plan(plan: BackendAcquisitionPlan) -> DriverAcquisitionPlan:
    return DriverAcquisitionPlan(
        acquisitions=tuple(lower_acquisition(request) for request in plan.acquisitions)
    )


def project_acquisition_preparation_outcome(
    outcome: DriverOutcome[None],
) -> AcquisitionPreparationReceipt:
    if isinstance(outcome, DriverSuccess):
        return AcquisitionPreparationReceipt(
            metadata=outcome.metadata, measured_cost=outcome.measured_cost
        )
    if isinstance(outcome, DriverRejected):
        return AcquisitionPreparationReceipt(
            status="not_prepared",
            problems=outcome.problems,
            metadata=outcome.metadata,
            measured_cost=outcome.measured_cost,
        )
    return AcquisitionPreparationReceipt(
        status="unknown",
        problems=outcome.problems,
        metadata=outcome.metadata,
        measured_cost=outcome.measured_cost,
    )


def project_state_readback(
    instrument_id: str,
    readback: DriverStateReadback,
) -> InstrumentStateReadback:
    observed_at = datetime.now(UTC)
    return InstrumentStateReadback(
        instrument_id=instrument_id,
        observations=[
            InstrumentStateObservation(
                target=state_member_target(observation.target),
                value=StateValue(observation.value),
                source=observation.source,
                observed_at=observed_at,
                coherence_id=observation.coherence_id,
                entity_ids=observation.entity_ids,
                channel_bindings=observation.channel_bindings,
                metadata=observation.metadata,
            )
            for observation in sorted(
                readback.observations,
                key=lambda item: repr(item.target),
            )
        ],
    )


def lower_state_read_request(request: BackendReadRequest) -> DriverStateReadRequest:
    from scopecat.records.instrument import state_member_ref

    return DriverStateReadRequest(
        targets=frozenset(state_member_ref(target) for target in request.targets)
    )


def lower_state_patch(request: BackendApplyRequest) -> DriverStatePatch:
    return DriverStatePatch(
        values={
            assignment.member: cast("DriverScalar", assignment.value.root)
            for assignment in request.assignments
            if not assignment.channel_bindings
        },
        scoped_values=tuple(
            DriverStateAssignment(
                target=assignment.member,
                value=cast("DriverScalar", assignment.value.root),
                entity_ids=assignment.entity_ids,
                channel_bindings=assignment.channel_bindings,
            )
            for assignment in request.assignments
            if assignment.channel_bindings
        ),
    )


def project_apply_outcome(
    instrument_id: str,
    outcome: DriverOutcome[DriverStateReadback | None],
) -> ApplyReceipt:
    if isinstance(outcome, DriverSuccess):
        return ApplyReceipt(
            readback=(
                project_state_readback(instrument_id, outcome.value)
                if outcome.value is not None
                else None
            ),
            metadata=outcome.metadata,
            measured_cost=outcome.measured_cost,
        )
    if isinstance(outcome, DriverRejected):
        return ApplyReceipt(
            status="not_applied",
            problems=outcome.problems,
            metadata=outcome.metadata,
            measured_cost=outcome.measured_cost,
        )
    return ApplyReceipt(
        status="unknown",
        problems=outcome.problems,
        metadata=outcome.metadata,
        measured_cost=outcome.measured_cost,
    )


def project_invoke_outcome(
    instrument_id: str,
    outcome: DriverOutcome[DriverStateReadback | None],
) -> InvokeReceipt:
    if isinstance(outcome, DriverSuccess):
        return InvokeReceipt(
            readback=(
                project_state_readback(instrument_id, outcome.value)
                if outcome.value is not None
                else None
            ),
            metadata=outcome.metadata,
            measured_cost=outcome.measured_cost,
        )
    if isinstance(outcome, DriverRejected):
        return InvokeReceipt(
            status="not_invoked",
            problems=outcome.problems,
            metadata=outcome.metadata,
            measured_cost=outcome.measured_cost,
        )
    return InvokeReceipt(
        status="unknown",
        problems=outcome.problems,
        metadata=outcome.metadata,
        measured_cost=outcome.measured_cost,
    )


def lower_acquisition(request: BackendCollectRequest) -> DriverAcquisition:
    return DriverAcquisition(
        target=request.target,
        results=frozenset(request.result_target(result) for result in request.results),
        dimensions={
            request.result_target(result): tuple(
                DriverAcquisitionDimension(
                    id=dimension.id,
                    kind=dimension.kind,
                    offset=dimension.offset,
                    size=dimension.size,
                    unit=dimension.unit,
                )
                for dimension in result.dimensions
            )
            for result in request.results
        },
        entity_ids=request.entity_ids,
        channel_bindings=request.channel_bindings,
    )


def project_collect_outcome(
    request: BackendCollectRequest,
    outcome: DriverOutcome[DriverReadback],
) -> CollectReceipt:
    if isinstance(outcome, DriverSuccess):
        requested_targets = {
            request.result_target(result) for result in request.results
        }
        values = {
            result.request_id: outcome.value.values[request.result_target(result)]
            for result in request.results
            if request.result_target(result) in outcome.value.values
        }
        # Keep extras visible to the existing receipt contract validator.
        for index, target in enumerate(
            sorted(set(outcome.value.values) - requested_targets, key=repr)
        ):
            request_id = f"driver-unrequested-{index}"
            while request_id in values:
                request_id = f"_{request_id}"
            values[request_id] = outcome.value.values[target]
        return CollectReceipt(
            readback=InstrumentReadback(
                values=values,
                metadata=outcome.value.metadata,
            ),
            metadata=outcome.metadata,
            measured_cost=outcome.measured_cost,
        )
    if isinstance(outcome, DriverRejected):
        return CollectReceipt(
            status="not_collected",
            problems=outcome.problems,
            metadata=outcome.metadata,
            measured_cost=outcome.measured_cost,
        )
    return CollectReceipt(
        status="unknown",
        problems=outcome.problems,
        metadata=outcome.metadata,
        measured_cost=outcome.measured_cost,
    )


__all__ = [
    "lower_acquisition",
    "lower_acquisition_plan",
    "lower_state_patch",
    "lower_state_read_request",
    "project_acquisition_preparation_outcome",
    "project_apply_outcome",
    "project_collect_outcome",
    "project_invoke_outcome",
    "project_state_readback",
]
