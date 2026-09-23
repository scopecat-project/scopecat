"""Deterministic implementation of scalar evaluation semantics."""

from __future__ import annotations

from scopecat.compiler.relations.context import EvalContext
from scopecat.compiler.relations.scalar_eval import (
    eval_binary,
    read_path,
)
from scopecat.kernel.value_data import CellValue
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
)


def evaluate_scalar_expression(expression: ScalarExpr, ctx: EvalContext) -> CellValue:
    try:
        return _evaluate_scalar_expression(expression, ctx)
    except ArithmeticError, KeyError, TypeError, ValueError:
        if ctx.parameter_reads is not None:
            ctx.parameter_reads.incomplete("evaluation_failed")
        raise


def _evaluate_scalar_expression(expression: ScalarExpr, ctx: EvalContext) -> CellValue:
    scalar = expression
    match scalar:
        case LiteralScalarExpr():
            return scalar.value
        case PointColumnScalarExpr():
            return read_path(ctx.point_row, scalar.name)
        case InputScalarExpr():
            return read_path(ctx.inputs, scalar.name)
        case ParameterScalarExpr():
            return ctx.parameter_scalar(scalar.name)
        case ComputeResultScalarExpr():
            msg = "compute results cannot be evaluated as pure scalar expressions"
            raise TypeError(msg)
        case ModuleExportScalarExpr():
            msg = "unresolved module exports cannot be evaluated"
            raise ValueError(msg)
        case ParameterLookupScalarExpr():
            resolved_key = {
                name: evaluate_scalar_expression(value, ctx)
                for name, value in scalar.key.items()
            }
            return ctx.parameter_lookup(
                scalar.use.table_id, resolved_key, scalar.use.column_id
            )
        case BinaryScalarExpr():
            return eval_binary(
                scalar.op,
                evaluate_scalar_expression(scalar.left, ctx),
                evaluate_scalar_expression(scalar.right, ctx),
            )
        case _:
            raise AssertionError("unknown scalar expression node")
