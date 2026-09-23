"""Partial evaluation for pure scalar expressions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from scopecat.compiler.relations.context import EvalContext
from scopecat.compiler.relations.scalar_eval import cell_matches, eval_binary, read_path
from scopecat.compiler.relations.verification import (
    ExpressionVerificationError,
    verify_scalar_expression,
)
from scopecat.kernel.value_data import CellValue
from scopecat.kernel.value_types import Scalar
from scopecat.kernel.value_validation import coerce_literal
from scopecat.program.expressions import (
    BinaryScalarExpr,
    ComputeResultScalarExpr,
    InputScalarExpr,
    LiteralScalarExpr,
    ModuleExportScalarExpr,
    ParameterLookupScalarExpr,
    ParameterScalarExpr,
    PointColumnScalarExpr,
    ScalarExpr,
    lit,
)

_KNOWN_EVALUATION_ERRORS = (ArithmeticError, KeyError, TypeError, ValueError)


@dataclass(frozen=True, slots=True)
class ParameterCellBinding:
    """One statically identified config cell replaced by a residual expression."""

    table_id: str
    key: tuple[tuple[str, CellValue], ...]
    column_id: str
    replacement: ScalarExpr

    def __post_init__(self) -> None:
        if not self.table_id or not self.column_id or not self.key:
            msg = "parameter cell binding ids and key must be non-empty"
            raise ValueError(msg)
        key_ids = tuple(column_id for column_id, _value in self.key)
        if key_ids != tuple(sorted(set(key_ids))):
            msg = "parameter cell binding keys must be unique and ordered"
            raise ValueError(msg)


def specialize_scalar_expression(
    expression: ScalarExpr,
    *,
    known: EvalContext,
    parameter_cells: Sequence[ParameterCellBinding] = (),
) -> ScalarExpr:
    """Partially evaluate one pure scalar expression.

    Missing bindings remain canonical expressions. Fully known operations
    either become typed literals or fail verification deterministically. No
    external effect can be represented or executed here.
    """

    try:
        result = _specialize_scalar_expression(
            expression, known=known, parameter_cells=parameter_cells
        )
    except _KNOWN_EVALUATION_ERRORS:
        if known.parameter_reads is not None:
            known.parameter_reads.incomplete("specialization_failed")
        raise
    if known.parameter_reads is not None and not isinstance(result, LiteralScalarExpr):
        known.parameter_reads.incomplete("residual_expression")
    return result


def _specialize_scalar_expression(
    expression: ScalarExpr,
    *,
    known: EvalContext,
    parameter_cells: Sequence[ParameterCellBinding],
) -> ScalarExpr:
    scalar = expression
    match scalar:
        case LiteralScalarExpr():
            return lit(scalar.value, scalar.value_type)
        case PointColumnScalarExpr():
            return _known_leaf(
                scalar,
                lambda: read_path(known.point_row, scalar.name),
            )
        case InputScalarExpr():
            return _known_leaf(
                scalar,
                lambda: read_path(known.inputs, scalar.name),
            )
        case ParameterScalarExpr():
            return _known_leaf(scalar, lambda: known.parameter_scalar(scalar.name))
        case ComputeResultScalarExpr() | ModuleExportScalarExpr():
            return scalar
        case ParameterLookupScalarExpr():
            return _specialize_parameter_lookup(
                scalar,
                known=known,
                parameter_cells=parameter_cells,
            )
        case BinaryScalarExpr():
            return _specialize_binary(
                scalar,
                known=known,
                parameter_cells=parameter_cells,
            )
        case _:
            raise AssertionError("unknown scalar expression node")


def _known_leaf(
    expression: ScalarExpr,
    resolve: Callable[[], object],
) -> ScalarExpr:
    try:
        value = resolve()
        return _typed_literal(value, expression.value_type)
    except _KNOWN_EVALUATION_ERRORS:
        return expression


def _typed_literal(value: object, value_type: Scalar) -> LiteralScalarExpr:
    normalized = coerce_literal(value_type, value)
    return lit(cast("CellValue", normalized), value_type)


def _specialize_parameter_lookup(
    expression: ParameterLookupScalarExpr,
    *,
    known: EvalContext,
    parameter_cells: Sequence[ParameterCellBinding],
) -> ScalarExpr:
    key_results = {
        name: specialize_scalar_expression(
            value,
            known=known,
            parameter_cells=parameter_cells,
        )
        for name, value in expression.key.items()
    }
    if all(isinstance(result, LiteralScalarExpr) for result in key_results.values()):
        key = {
            name: cast("LiteralScalarExpr", result).value
            for name, result in key_results.items()
        }
        matched = _matching_parameter_cell(
            parameter_cells,
            table_id=expression.use.table_id,
            key=key,
            column_id=expression.use.column_id,
        )
        if matched is not None:
            if known.parameter_reads is not None:
                known.parameter_reads.incomplete(
                    f"symbolic_overlay:{expression.use.table_id}.{expression.use.column_id}"
                )
            return specialize_scalar_expression(matched.replacement, known=known)
        try:
            value = known.parameter_lookup(
                expression.use.table_id, key, expression.use.column_id
            )
        except ValueError as error:
            raise ExpressionVerificationError(
                "parameter_lookup_failed",
                (),
                str(error),
            ) from error
        except KeyError, TypeError:
            pass
        else:
            try:
                return _typed_literal(value, expression.value_type)
            except _KNOWN_EVALUATION_ERRORS:
                pass
    if known.parameter_reads is not None:
        known.parameter_reads.incomplete(
            f"residual_lookup:{expression.use.table_id}.{expression.use.column_id}"
        )
    return ParameterLookupScalarExpr(
        use=expression.use,
        key=key_results,
    )


def _specialize_binary(
    expression: BinaryScalarExpr,
    *,
    known: EvalContext,
    parameter_cells: Sequence[ParameterCellBinding],
) -> ScalarExpr:
    left = specialize_scalar_expression(
        expression.left,
        known=known,
        parameter_cells=parameter_cells,
    )
    right = specialize_scalar_expression(
        expression.right,
        known=known,
        parameter_cells=parameter_cells,
    )
    residual = BinaryScalarExpr(
        op=expression.op,
        left=left,
        right=right,
    )
    if isinstance(left, LiteralScalarExpr) and isinstance(right, LiteralScalarExpr):
        try:
            value = eval_binary(expression.op, left.value, right.value)
        except ZeroDivisionError:
            return verify_scalar_expression(residual)
        except _KNOWN_EVALUATION_ERRORS as error:
            raise ExpressionVerificationError(
                "scalar_evaluation_failed",
                (),
                str(error),
            ) from error
        else:
            try:
                return _typed_literal(value, expression.value_type)
            except _KNOWN_EVALUATION_ERRORS as error:
                raise ExpressionVerificationError(
                    "specialized_result_invalid",
                    (),
                    str(error),
                ) from error
    return residual


def _matching_parameter_cell(
    bindings: Sequence[ParameterCellBinding],
    *,
    table_id: str,
    key: dict[str, CellValue],
    column_id: str,
) -> ParameterCellBinding | None:
    for binding in reversed(tuple(bindings)):
        if (
            binding.table_id == table_id
            and binding.column_id == column_id
            and _keys_equal(binding.key, key)
        ):
            return binding
    return None


def _keys_equal(
    expected: tuple[tuple[str, CellValue], ...],
    actual: dict[str, CellValue],
) -> bool:
    if {column_id for column_id, _value in expected} != set(actual):
        return False
    return all(cell_matches(value, actual[column_id]) for column_id, value in expected)


__all__ = [
    "ParameterCellBinding",
    "specialize_scalar_expression",
]
