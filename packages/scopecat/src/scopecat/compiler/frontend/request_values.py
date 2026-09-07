"""Projection from transient scalar syntax to durable run-request values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.quantity import Quantity
from scopecat.program.expressions import (
    BinaryScalarExpr,
    LiteralScalarExpr,
    ParameterLookupScalarExpr,
    ParameterScalarExpr,
    ScalarExpr,
)
from scopecat.records._run_request_values import (
    normalize_json_value,
    normalize_run_request_value,
)


def project_run_request_inputs(inputs: Mapping[str, object]) -> dict[str, object]:
    """Project experiment inputs into durable request values."""

    return {
        key: project_run_request_value(value, path=f"inputs.{key}")
        for key, value in inputs.items()
    }


def project_run_request_value(
    value: object,
    *,
    path: str = "value",
) -> object:
    """Explicitly project supported authoring values into request wire values."""

    if value is None or isinstance(value, str | bool | int | float | Quantity):
        return normalize_run_request_value(value)
    if isinstance(value, complex):
        return normalize_run_request_value(
            {
                "kind": "complex",
                "real": value.real,
                "imag": value.imag,
            }
        )
    if isinstance(value, EntityRef):
        return {
            "kind": "entity",
            "entity_id": value.id,
            "entity_kind": value.kind,
            "metadata": normalize_json_value(value.metadata),
        }
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        projected: dict[str, object] = {}
        for key, item in mapping.items():
            if not isinstance(key, str):
                msg = f"run request object keys must be strings at {path}"
                raise ValueError(msg)
            projected[key] = project_run_request_value(
                item,
                path=f"{path}.{key}",
            )
        return projected
    if isinstance(value, list | tuple):
        sequence = cast("list[object] | tuple[object, ...]", value)
        return [
            project_run_request_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(sequence)
        ]
    msg = f"unsupported authoring run request value at {path}: {type(value).__name__}"
    raise ValueError(msg)


def project_run_request_scalar(expression: ScalarExpr) -> object:
    """Project transient relation syntax into durable request semantics."""

    scalar = expression
    if isinstance(scalar, LiteralScalarExpr):
        return project_run_request_value(scalar.value, path="expression.literal")
    if isinstance(scalar, ParameterScalarExpr):
        return {"kind": "parameter", "parameter_id": scalar.name}
    if isinstance(scalar, ParameterLookupScalarExpr):
        return {
            "kind": "parameter_lookup",
            "table_id": scalar.use.table_id,
            "key": {
                name: project_run_request_scalar(value)
                for name, value in scalar.key.items()
            },
            "column": scalar.use.column_id,
        }
    binary = cast("BinaryScalarExpr", scalar)
    return {
        "kind": "binary",
        "operator": binary.op,
        "left": project_run_request_scalar(binary.left),
        "right": project_run_request_scalar(binary.right),
    }
