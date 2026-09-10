"""Runtime bindings and checked evaluation for scalar expressions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import numpy as np

from scopecat.compiler.relations.context import EvalContext, ParameterRelationData
from scopecat.compiler.relations.scalar_eval import read_path
from scopecat.compiler.relations.verification import (
    ExpressionImportNamespace,
    ExpressionPointRequirement,
    ExpressionTypeBindings,
    RowType,
    TypedExpressionImport,
    scalar_expression_imports,
    scalar_expression_point_requirement,
)
from scopecat.kernel.value_data import CellValue, Row, is_cell_value
from scopecat.kernel.value_types import (
    Array,
    Scalar,
    Table,
    TableColumn,
    ValueType,
)
from scopecat.kernel.value_validation import (
    ValueValidationError,
    coerce_literal,
)
from scopecat.program.expressions import ScalarExpr
from scopecat.program.table_values import (
    LiteralTableSource,
    ParameterTableSource,
    TableSource,
    TopologyConnectionSetSource,
    TopologyEntitySetSource,
)


def evaluate_scalar(
    expression: ScalarExpr,
    ctx: EvalContext,
    *,
    bindings: ExpressionTypeBindings | None = None,
    expected_type: Scalar | None = None,
) -> CellValue:
    """Evaluate an expression against normalized runtime bindings.

    ``bindings`` and ``expected_type`` retain narrower types selected while
    verifying a generic canonical expression, without retyping or wrapping it.
    """

    from scopecat.compiler.relations.evaluator import evaluate_scalar_expression

    normalized = _prepare_context(expression, ctx, bindings=bindings)
    result = evaluate_scalar_expression(expression, normalized)
    return cast(
        "CellValue",
        _normalize_materialized_result(
            expected_type or expression.value_type,
            result,
        ),
    )


def evaluate_table_value(
    source: TableSource,
    value_type: Table,
    ctx: EvalContext,
) -> list[Row]:
    """Materialize a whole-table compiler input against one point context."""

    if isinstance(source, LiteralTableSource):
        result: object = source.rows
    elif isinstance(source, ParameterTableSource):
        result = _parameter_table_rows(
            source.parameter_id,
            ctx.params,
            table_rows=None,
            path=("parameters", source.parameter_id),
        )
    elif not isinstance(source, TopologyEntitySetSource | TopologyConnectionSetSource):
        try:
            result = ctx.inputs[source.input_id]
        except KeyError as error:
            msg = f"unknown table input {source.input_id!r}"
            raise KeyError(msg) from error
    else:
        raise ValueError(
            "topology selection sources must resolve during configuration binding"
        )
    return cast(
        "list[Row]",
        _normalize_materialized_result(value_type, result),
    )


def normalize_relation_parameter_import(
    expression: ScalarExpr,
    imported: TypedExpressionImport,
    params: ParameterRelationData,
) -> object:
    """Normalize one used parameter import without evaluating its plan.

    Compiler binding uses the same boundary as runtime dispatch so an
    accepted configuration cannot become a late missing-parameter or
    parameter-type failure merely because no relation has been evaluated yet.
    """

    if imported not in scalar_expression_imports(expression):
        msg = "parameter import is not used by the supplied scalar expression"
        raise ValueError(msg)
    return _normalize_parameter_import(
        imported,
        params,
        table_rows=None,
    )


def _prepare_context(
    expression: ScalarExpr,
    ctx: EvalContext,
    *,
    bindings: ExpressionTypeBindings | None,
) -> EvalContext:
    return _normalize_evaluation_context(expression, ctx, bindings=bindings)


def _normalize_parameter_import(
    imported: TypedExpressionImport,
    params: ParameterRelationData,
    *,
    table_rows: list[Row] | None,
) -> object:
    if imported.namespace is not ExpressionImportNamespace.PARAMETER:
        msg = "parameter import normalization requires the parameter namespace"
        raise ValueError(msg)
    path = ("parameters", imported.id)
    if imported.lookup is not None:
        return _normalize_parameter_table_import(
            imported,
            params,
            table_rows=table_rows,
            path=path,
        )
    try:
        value = params.scalar(imported.id)
    except (KeyError, TypeError) as error:
        raise ValueValidationError(
            path,
            str(error),
            code="unknown_parameter",
        ) from error
    return _normalize_typed_value(imported.value_type, value, path=path)


def _normalize_parameter_table_import(
    imported: TypedExpressionImport,
    params: ParameterRelationData,
    *,
    table_rows: list[Row] | None,
    path: tuple[str, str],
) -> list[Row]:
    lookup = imported.lookup
    if lookup is None:
        raise AssertionError("lookup import is unexpectedly missing its signature")
    rows = _parameter_table_rows(
        imported.id,
        params,
        table_rows=table_rows,
        path=path,
    )

    normalized_rows = rows
    for index, row in enumerate(rows):
        result_path = (*path, index, lookup.column_id)
        try:
            value = read_path(row, lookup.column_id)
        except (KeyError, TypeError) as error:
            key = {name: row.get(name) for name, _ in lookup.key_input_types}
            raise ValueValidationError(
                result_path,
                f"{imported.id}[{key!r}].{lookup.column_id}: "
                "parameter is unknown; "
                "supply or calibrate this value in the parameter workspace "
                "and preview again",
            ) from error
        normalized = _normalize_typed_value(
            lookup.result_type,
            value,
            path=result_path,
        )
        normalized_rows[index] = cast(
            "Row",
            _replace_path_value(row, lookup.column_id, normalized),
        )
    return normalized_rows


def _parameter_table_rows(
    parameter_id: str,
    params: ParameterRelationData,
    *,
    table_rows: list[Row] | None,
    path: tuple[str, str],
) -> list[Row]:
    if table_rows is not None:
        return [dict(row) for row in table_rows]
    try:
        return params.table_rows(parameter_id)
    except KeyError as error:
        actual_shape = params.parameter_shape(parameter_id)
        if actual_shape is not None:
            raise ValueValidationError(
                path,
                f"expected table parameter, got {actual_shape}",
            ) from error
        raise ValueValidationError(
            path,
            str(error),
            code="unknown_parameter",
        ) from error


def _normalize_evaluation_context(
    expression: ScalarExpr,
    ctx: EvalContext,
    *,
    bindings: ExpressionTypeBindings | None,
) -> EvalContext:
    """Snapshot and normalize every dynamic value the expression consumes."""

    inputs: dict[str, object] = dict(ctx.inputs)
    parameter_scalars: dict[str, CellValue] = {}
    tables_by_parameter: dict[str, list[Row]] = {}

    for intrinsic_import in scalar_expression_imports(expression):
        imported = _bound_scalar_import(intrinsic_import, bindings)
        path = (imported.namespace.value + "s", imported.id)
        if imported.namespace is ExpressionImportNamespace.INPUT:
            try:
                value = read_path(inputs, imported.id)
            except (KeyError, TypeError) as error:
                raise ValueValidationError(path, str(error)) from error
            normalized = _normalize_typed_value(
                imported.value_type,
                value,
                path=path,
            )
            inputs = _replace_path_value(inputs, imported.id, normalized)
            continue
        normalized = _normalize_parameter_import(
            imported,
            ctx.params,
            table_rows=tables_by_parameter.get(imported.id),
        )
        if imported.lookup is not None:
            tables_by_parameter[imported.id] = cast("list[Row]", normalized)
        else:
            parameter_scalars[imported.id] = cast("CellValue", normalized)

    point_requirement = _bound_point_requirement(expression, bindings)
    point_row = (
        _normalize_point_row(
            point_requirement,
            ctx.point_row,
            path=("rows", "point"),
        )
        if point_requirement is not None
        else {}
    )
    if point_row is None:
        raise ValueValidationError(
            ("rows", "point"),
            "required row binding is missing",
        )

    return EvalContext(
        params=ParameterRelationData(
            scalars=parameter_scalars,
            tables=tables_by_parameter,
        ),
        point_row=point_row,
        inputs=inputs,
    )


def _bound_scalar_import(
    imported: TypedExpressionImport,
    bindings: ExpressionTypeBindings | None,
) -> TypedExpressionImport:
    if bindings is None or imported.lookup is not None:
        return imported
    available = (
        bindings.inputs
        if imported.namespace is ExpressionImportNamespace.INPUT
        else bindings.parameters
    )
    value_type = available.get(imported.id)
    if value_type is None:
        return imported
    return TypedExpressionImport(
        namespace=imported.namespace,
        id=imported.id,
        value_type=value_type,
    )


def _bound_point_requirement(
    expression: ScalarExpr,
    bindings: ExpressionTypeBindings | None,
) -> ExpressionPointRequirement | None:
    requirement = scalar_expression_point_requirement(expression)
    if requirement is None or bindings is None or bindings.point_row is None:
        return requirement
    return ExpressionPointRequirement(
        row_type=bindings.point_row,
        column_references=requirement.column_references,
    )


def _replace_path_value(
    source: Mapping[str, object],
    path: str,
    value: object,
) -> dict[str, object]:
    selected: dict[str, object] = dict(source)
    if path in selected:
        selected[path] = value
        return selected

    parts = path.split(".")
    current = selected
    for part in parts[:-1]:
        nested = current.get(part)
        if not isinstance(nested, Mapping):
            raise AssertionError(f"validated path {path!r} is unexpectedly absent")
        nested_copy: dict[str, object] = dict(cast("Mapping[str, object]", nested))
        current[part] = nested_copy
        current = nested_copy
    current[parts[-1]] = value
    return selected


def _normalize_point_row(
    requirement: ExpressionPointRequirement | None,
    row: Row | None,
    *,
    path: tuple[str, str],
) -> Row | None:
    if requirement is None:
        return dict(row) if row is not None else None
    return _normalize_row_role(
        requirement.row_type,
        row,
        set(requirement.column_references),
        path=path,
    )


def _normalize_row_role(
    row_type: RowType | None,
    row: Row | None,
    references: set[str],
    *,
    path: tuple[str, str],
) -> Row | None:
    if row_type is None or not references:
        return dict(row) if row is not None else None
    columns = _referenced_row_columns(row_type, references)
    if not columns:
        return dict(row) if row is not None else None
    if row is None:
        raise ValueValidationError(path, "required row binding is missing")
    normalized = dict(row)
    for column in columns:
        if column.id not in row:
            raise ValueValidationError(
                path,
                f"table row is missing required columns: {column.id}",
            )
        normalized[column.id] = cast(
            "CellValue",
            coerce_literal(
                column.value_type,
                row[column.id],
                path=(*path, column.id),
            ),
        )
    return normalized


def _referenced_row_columns(
    row_type: RowType,
    references: set[str],
) -> tuple[TableColumn, ...]:
    columns = {column.id: column for column in row_type.columns}
    selected: set[str] = set()
    for reference in references:
        if reference in columns:
            selected.add(reference)
            continue
        root = reference.split(".", maxsplit=1)[0]
        if root in columns:
            selected.add(root)
    return tuple(column for column in row_type.columns if column.id in selected)


def _normalize_materialized_result(
    value_type: ValueType,
    value: object,
) -> CellValue | np.ndarray | list[Row]:
    """Normalize a result and enforce its runtime carrier contract."""

    return _normalize_typed_value(value_type, value, path=("result",))


def _normalize_typed_value(
    value_type: ValueType,
    value: object,
    *,
    path: tuple[str | int, ...],
) -> CellValue | np.ndarray | list[Row]:
    normalized = _restore_runtime_collection_carriers(
        value_type,
        coerce_literal(value_type, value, path=path),
    )
    if isinstance(value_type, Scalar):
        if not is_cell_value(normalized):
            raise ValueValidationError(
                path,
                f"unsupported scalar runtime value {normalized!r}",
            )
        return normalized
    if isinstance(value_type, Array):
        return cast("np.ndarray", normalized)
    rows = list(cast("tuple[dict[str, object], ...]", normalized))
    for index, row in enumerate(rows):
        for column_id, item in row.items():
            if not is_cell_value(item):
                raise ValueValidationError(
                    (*path, index, column_id),
                    f"unsupported table runtime cell {item!r}",
                )
    return cast("list[Row]", rows)


def _restore_runtime_collection_carriers(
    value_type: ValueType,
    value: object,
) -> object:
    """Use mutable runtime collections while retaining normalized scalar atoms."""

    if isinstance(value_type, Scalar):
        return value
    if isinstance(value_type, Array):
        return value
    selected_rows: list[dict[str, object]] = []
    columns = {column.id: column for column in value_type.columns}
    for row in cast("tuple[dict[str, object], ...]", value):
        selected = dict(row)
        for column_id, column in columns.items():
            if column_id in selected:
                selected[column_id] = _restore_runtime_collection_carriers(
                    column.value_type,
                    selected[column_id],
                )
        selected_rows.append(selected)
    return selected_rows
