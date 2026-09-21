"""Deferred keyed parameter projections, independent of experiment domains."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, cast

from pydantic import AfterValidator, SerializerFunctionWrapHandler, WrapSerializer

from scopecat.authoring.parameter_fields import (
    ResolvedParameterField,
    stored_parameter_value,
)
from scopecat.authoring.parameter_models import (
    ParameterModel,
    parameter_fields,
    parameter_key,
    parameter_table_name,
)
from scopecat.kernel.frozen import FrozenMapping
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_identity import (
    ScalarIdentity,
    scalar_identity,
    scalar_values_equal,
)
from scopecat.kernel.value_validation import coerce_literal
from scopecat.records.parameter import (
    ParameterAtomValue,
    ParameterSnapshot,
    TableParameterValue,
)


def _value(
    value: object, field: ResolvedParameterField, table: str
) -> ParameterAtomValue:
    return cast(
        "ParameterAtomValue",
        coerce_literal(
            field.value_type,
            stored_parameter_value(value, field, label=f"{table}.{field.name}"),
            path=(table, field.name),
        ),
    )


def _freeze_query_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return FrozenMapping(value.items())


def _serialize_query_mapping(
    value: Mapping[str, object], handler: SerializerFunctionWrapHandler
) -> object:
    return cast("object", handler(dict(value)))


type QueryMapping[T] = Annotated[
    Mapping[str, T],
    AfterValidator(_freeze_query_mapping),
    WrapSerializer(_serialize_query_mapping),
]


@dataclass(frozen=True, slots=True)
class QueryInput:
    """A named value supplied by the execution context, not a table column."""

    name: str


@dataclass(frozen=True, slots=True)
class ParameterQueryResult:
    """Resolved inputs and their selected scientific source (transient evidence)."""

    snapshot_id: str
    table: str
    key: QueryMapping[ParameterAtomValue]
    fields: QueryMapping[str]
    values: QueryMapping[ParameterAtomValue]
    key_sources: tuple[ParameterQueryResult, ...] = ()


@dataclass(slots=True)
class _Evaluation:
    """One resolve call owns reuse; nothing survives a snapshot/context change."""

    snapshot: ParameterSnapshot
    context: Mapping[str, object]
    rows: dict[
        tuple[type[ParameterModel], tuple[ScalarIdentity, ...]],
        Mapping[str, ParameterAtomValue],
    ] = field(default_factory=dict)
    expressions: dict[int, ParameterExpressionResult] = field(default_factory=dict)

    def row(
        self, model: type[ParameterModel], key: Mapping[str, ParameterAtomValue]
    ) -> Mapping[str, ParameterAtomValue]:
        identity = (
            model,
            tuple(scalar_identity(key[name]) for name in parameter_key(model)),
        )
        cached = self.rows.get(identity)
        if cached is not None:
            return cached
        table = parameter_table_name(model)
        declarations = {item.name: item for item in parameter_fields(model)}
        stored = self.snapshot.get(table)
        if not isinstance(stored, TableParameterValue):
            raise ValueError(
                f"{table}: parameter table missing from snapshot {self.snapshot.id!r}"
            )
        # Keep the existing scientific comparison, including quantity tolerance.
        matches = [
            row
            for row in stored.rows
            if all(
                scalar_values_equal(
                    _value(row.get(name), declarations[name], table), value
                )
                for name, value in key.items()
            )
        ]
        if len(matches) != 1:
            raise ValueError(
                f"{table}: lookup {key!r} expected one row, found {len(matches)}"
            )
        self.rows[identity] = matches[0]
        return matches[0]


@dataclass(frozen=True, slots=True)
class ParameterProjection:
    model: type[ParameterModel]
    keys: Mapping[str, object]
    fields: Mapping[str, str]

    def resolve(
        self, snapshot: ParameterSnapshot, context: Mapping[str, object]
    ) -> ParameterQueryResult:
        return self._resolve(_Evaluation(snapshot, context))

    def _resolve(self, evaluation: _Evaluation) -> ParameterQueryResult:
        table = parameter_table_name(self.model)
        declarations = {field.name: field for field in parameter_fields(self.model)}
        key: dict[str, ParameterAtomValue] = {}
        key_sources: list[ParameterQueryResult] = []
        for name, source in self.keys.items():
            if isinstance(source, ParameterExpression):
                evaluated = source._resolve(evaluation)  # pyright: ignore[reportPrivateUsage] - shared internal evaluation
                source = evaluated.value
                key_sources.extend(evaluated.sources)
            if isinstance(source, QueryInput):
                if source.name not in evaluation.context:
                    raise ValueError(f"{table}: missing query input {source.name!r}")
                source = evaluation.context[source.name]
            key[name] = _value(source, declarations[name], table)
        selected = evaluation.row(self.model, key)
        values: dict[str, ParameterAtomValue] = {}
        for output, name in self.fields.items():
            value = selected.get(name)
            if value is None:
                raise ValueError(
                    f"{table}: lookup {key!r}, input {output!r}: {name} is unknown"
                )
            values[output] = _value(value, declarations[name], table)
        return ParameterQueryResult(
            evaluation.snapshot.id,
            table,
            FrozenMapping(key.items()),
            self.fields,
            FrozenMapping(values.items()),
            tuple(key_sources),
        )


@dataclass(frozen=True, slots=True)
class ParameterLookup:
    model: type[ParameterModel]
    keys: Mapping[str, object]

    def __getitem__(self, field: str) -> ParameterExpression:
        """Reference one required field, including as another lookup's key."""
        return ParameterExpression(projection=self.select(field), field=field)

    def select(self, *fields: str, **aliases: str) -> ParameterProjection:
        """Select required inputs, optionally mapping output names to columns."""
        selected = dict.fromkeys(fields)
        if len(selected) != len(fields) or selected.keys() & aliases.keys():
            raise ValueError("parameter projection has duplicate output names")
        columns = {field.name for field in parameter_fields(self.model)}
        mapping = {**{name: name for name in fields}, **aliases}
        if not mapping or not set(mapping.values()) <= columns:
            raise ValueError(
                f"{parameter_table_name(self.model)}: select declared fields"
            )
        return ParameterProjection(
            self.model, self.keys, FrozenMapping(mapping.items())
        )


@dataclass(frozen=True, slots=True)
class ParameterTableQuery:
    model: type[ParameterModel]

    def lookup(self, **keys: object) -> ParameterLookup:
        """Require the complete primary key; never silently pick a first match."""
        expected = parameter_key(self.model)
        if not expected or set(keys) != set(expected):
            raise ValueError(
                f"{parameter_table_name(self.model)}: expected keys {expected}"
            )
        return ParameterLookup(self.model, FrozenMapping(keys.items()))


def parameter_table(model: type[ParameterModel]) -> ParameterTableQuery:
    """Describe a parameter query without reading a snapshot."""
    return ParameterTableQuery(model)


@dataclass(frozen=True, slots=True)
class ParameterExpressionResult:
    value: ParameterAtomValue
    sources: tuple[ParameterQueryResult, ...]


def _calculate(
    operation: str, left: ParameterAtomValue, right: ParameterAtomValue
) -> ParameterAtomValue:
    if isinstance(left, bool) or not isinstance(left, int | float | Quantity):
        raise TypeError("parameter arithmetic requires numbers or quantities")
    if isinstance(right, bool) or not isinstance(right, int | float | Quantity):
        raise TypeError("parameter arithmetic requires numbers or quantities")
    if operation == "+":
        if isinstance(left, Quantity) and isinstance(right, Quantity):
            return left + right
        if not isinstance(left, Quantity) and not isinstance(right, Quantity):
            return left + right
    elif operation == "-":
        if isinstance(left, Quantity) and isinstance(right, Quantity):
            return left - right
        if not isinstance(left, Quantity) and not isinstance(right, Quantity):
            return left - right
    elif operation == "*":
        return left * right
    elif operation == "/":
        if isinstance(left, Quantity):
            return left / right
        if not isinstance(right, Quantity):
            return left / right
    raise TypeError(
        f"unsupported parameter arithmetic: {type(left).__name__} "
        f"{operation} {type(right).__name__}"
    )


@dataclass(frozen=True, slots=True)
class ParameterExpression:
    """A field or arithmetic expression evaluated against one effective snapshot."""

    projection: ParameterProjection | None = None
    field: str | None = None
    operation: str | None = None
    left: ParameterExpression | int | float | Quantity = 0
    right: ParameterExpression | int | float | Quantity = 0

    def __add__(
        self, other: ParameterExpression | float | Quantity
    ) -> ParameterExpression:
        return ParameterExpression(operation="+", left=self, right=other)

    def __radd__(self, other: float | Quantity) -> ParameterExpression:
        return ParameterExpression(operation="+", left=other, right=self)

    def __sub__(
        self, other: ParameterExpression | float | Quantity
    ) -> ParameterExpression:
        return ParameterExpression(operation="-", left=self, right=other)

    def __rsub__(self, other: float | Quantity) -> ParameterExpression:
        return ParameterExpression(operation="-", left=other, right=self)

    def __mul__(
        self, other: ParameterExpression | float | Quantity
    ) -> ParameterExpression:
        return ParameterExpression(operation="*", left=self, right=other)

    def __rmul__(self, other: float | Quantity) -> ParameterExpression:
        return ParameterExpression(operation="*", left=other, right=self)

    def __truediv__(
        self, other: ParameterExpression | float | Quantity
    ) -> ParameterExpression:
        return ParameterExpression(operation="/", left=self, right=other)

    def __rtruediv__(self, other: float | Quantity) -> ParameterExpression:
        return ParameterExpression(operation="/", left=other, right=self)

    def resolve(
        self, snapshot: ParameterSnapshot, context: Mapping[str, object]
    ) -> ParameterExpressionResult:
        return self._resolve(_Evaluation(snapshot, context))

    def _resolve(self, evaluation: _Evaluation) -> ParameterExpressionResult:
        cached = evaluation.expressions.get(id(self))
        if cached is not None:
            return cached
        result = self._evaluate(evaluation)
        evaluation.expressions[id(self)] = result
        return result

    def _evaluate(self, evaluation: _Evaluation) -> ParameterExpressionResult:
        if self.projection is not None:
            assert self.field is not None
            result = self.projection._resolve(evaluation)  # pyright: ignore[reportPrivateUsage] - shared internal evaluation
            return ParameterExpressionResult(result.values[self.field], (result,))
        assert self.operation is not None
        left = _expression_value(self.left, evaluation)
        right = _expression_value(self.right, evaluation)
        value = _calculate(self.operation, left.value, right.value)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("parameter arithmetic produced a non-finite value")
        return ParameterExpressionResult(value, (*left.sources, *right.sources))


def _expression_value(
    value: ParameterExpression | ParameterAtomValue,
    evaluation: _Evaluation,
) -> ParameterExpressionResult:
    return (
        value._resolve(evaluation)  # pyright: ignore[reportPrivateUsage] - shared internal evaluation
        if isinstance(value, ParameterExpression)
        else ParameterExpressionResult(value, ())
    )


@dataclass(frozen=True, slots=True)
class ParameterInputsResult:
    values: Mapping[str, ParameterAtomValue]
    sources: Mapping[str, tuple[ParameterQueryResult, ...]]


@dataclass(frozen=True, slots=True)
class ParameterInputs:
    expressions: Mapping[str, ParameterExpression | ParameterAtomValue]

    def resolve(
        self, snapshot: ParameterSnapshot, context: Mapping[str, object]
    ) -> ParameterInputsResult:
        values: dict[str, ParameterAtomValue] = {}
        sources: dict[str, tuple[ParameterQueryResult, ...]] = {}
        evaluation = _Evaluation(snapshot, context)
        for name, expression in self.expressions.items():
            try:
                result = _expression_value(expression, evaluation)
            except (TypeError, ValueError, ZeroDivisionError) as error:
                raise ValueError(f"parameter input {name!r}: {error}") from error
            values[name], sources[name] = result.value, result.sources
        return ParameterInputsResult(
            FrozenMapping(values.items()), FrozenMapping(sources.items())
        )


def parameter_inputs(
    **inputs: ParameterExpression | ParameterAtomValue,
) -> ParameterInputs:
    """Combine named values from independent or dependent keyed lookups."""
    return ParameterInputs(FrozenMapping(inputs.items()))
