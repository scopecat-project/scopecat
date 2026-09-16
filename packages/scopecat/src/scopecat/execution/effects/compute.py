"""Pure compute-effect evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from scopecat.execution.effects.boundary import EffectBoundary
from scopecat.execution.local.program import (
    BoundInput,
    ComputeOperation,
)
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.payloads import unwrap_payload_values
from scopecat.kernel.point_identity import LogicalPointId
from scopecat.kernel.product_identity import ProductUseId
from scopecat.kernel.value_validation import coerce_literal
from scopecat.records.content import CommandPayload
from scopecat.sdk.payloads import PayloadCodecRegistry


@dataclass(slots=True, kw_only=True)
class EffectEvaluationFrame:
    compute_results: dict[ValueId, object] = field(default_factory=dict)
    payloads: dict[str, CommandPayload] = field(default_factory=dict)


@dataclass(slots=True)
class PointEffectState(EffectEvaluationFrame):
    point_index: int
    logical_id: LogicalPointId
    product_use_ids: set[ProductUseId] = field(default_factory=set)


class ComputeEffectExecutor:
    """Evaluate closed compute nodes and retain their normalized outputs."""

    def __init__(
        self,
        *,
        boundary: EffectBoundary,
        payload_codecs: PayloadCodecRegistry,
    ) -> None:
        self.boundary = boundary
        self.payload_codecs = payload_codecs

    def execute(
        self,
        frame: EffectEvaluationFrame,
        operations: Sequence[ComputeOperation],
    ) -> None:
        for operation in operations:
            try:
                inputs = {
                    name: (
                        value.value
                        if isinstance(value, BoundInput)
                        else unwrap_payload_values(
                            coerce_literal(
                                value.value_type,
                                frame.compute_results[value.value_id],
                                path=(
                                    "operations",
                                    operation.operation_id,
                                    "inputs",
                                    name,
                                ),
                            )
                        )
                    )
                    for name, value in operation.inputs.items()
                }
                raw_result = operation.kernel(**inputs)
                result = unwrap_payload_values(
                    coerce_literal(
                        operation.result.value_type,
                        raw_result,
                        path=("operations", operation.operation_id, "output"),
                    )
                )
                if operation.payload_slot is not None:
                    slot = operation.payload_slot
                    payload = self.payload_codecs.command_payload(
                        slot.id,
                        slot.schema_id,
                        result,
                    )
                else:
                    payload = None
            except Exception as error:
                problem = self.boundary.problem_from_exception(
                    "compute_operation_failed",
                    f"compute operation {operation.operation_id} failed",
                    error,
                    include_exception_message=True,
                    operation_id=operation.operation_id,
                    point_index=(
                        frame.point_index
                        if isinstance(frame, PointEffectState)
                        else None
                    ),
                )
                self.boundary.problems.append(problem)
                return
            frame.compute_results[operation.result.id] = result
            if payload is not None:
                frame.payloads[payload.id] = payload
