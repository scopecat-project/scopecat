"""Runtime bindings for relation evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from scopecat.compiler.relations.parameter_reads import ParameterReadRecorder
from scopecat.compiler.relations.scalar_eval import cell_matches, read_path
from scopecat.kernel.value_data import CellValue, Row


class ParameterRelationData:
    """Resolved immutable parameter bindings for relation evaluation."""

    __slots__ = ("_scalars", "_tables")

    _scalars: dict[str, CellValue]
    _tables: dict[str, tuple[Row, ...]]

    def __init__(
        self,
        *,
        scalars: Mapping[str, CellValue] | None = None,
        tables: Mapping[str, Sequence[Mapping[str, CellValue]]] | None = None,
    ) -> None:
        scalar_bindings = {} if scalars is None else dict(scalars)
        table_rows = (
            {}
            if tables is None
            else {
                table_id: tuple(dict(row) for row in rows)
                for table_id, rows in tables.items()
            }
        )
        collisions = sorted(scalar_bindings.keys() & table_rows.keys())
        if collisions:
            msg = (
                "parameter ids must be unique across scalar and table "
                f"shapes: {', '.join(collisions)}"
            )
            raise ValueError(msg)
        self._scalars = scalar_bindings
        self._tables = table_rows

    def parameter_shape(self, parameter_id: str) -> str | None:
        """Return the stored shape for one parameter id, if present."""

        if parameter_id in self._scalars:
            return "scalar"
        if parameter_id in self._tables:
            return "table"
        return None

    def scalar(self, parameter_id: str) -> CellValue:
        try:
            return self._scalars[parameter_id]
        except KeyError as error:
            msg = f"unknown scalar parameter {parameter_id!r}"
            raise KeyError(msg) from error

    def table_rows(self, table_id: str) -> list[Row]:
        try:
            return [dict(row) for row in self._tables[table_id]]
        except KeyError as error:
            msg = f"unknown parameter table {table_id!r}"
            raise KeyError(msg) from error

    def with_table_cell(
        self,
        table_id: str,
        *,
        row_index: int,
        column_id: str,
        value: CellValue,
    ) -> ParameterRelationData:
        """Return bindings with one table cell lexically overridden."""

        try:
            rows = self._tables[table_id]
        except KeyError as error:
            msg = f"unknown parameter table {table_id!r}"
            raise KeyError(msg) from error
        if row_index < 0 or row_index >= len(rows):
            msg = f"parameter table {table_id!r} has no row {row_index}"
            raise IndexError(msg)
        row = rows[row_index]
        if column_id not in row:
            msg = (
                f"parameter table {table_id!r} row does not contain "
                f"column {column_id!r}"
            )
            raise KeyError(msg)
        updated_row = dict(row)
        updated_row[column_id] = value
        return ParameterRelationData(
            scalars=self._scalars,
            tables={
                **self._tables,
                table_id: (
                    *rows[:row_index],
                    updated_row,
                    *rows[row_index + 1 :],
                ),
            },
        )

    def lookup_row(self, table_id: str, key: Mapping[str, CellValue]) -> Row:
        return self.table_rows(table_id)[self.lookup_row_index(table_id, key)]

    def lookup_row_index(
        self,
        table_id: str,
        key: Mapping[str, CellValue],
    ) -> int:
        matches = [
            row_index
            for row_index, row in enumerate(self.table_rows(table_id))
            if all(
                cell_matches(row.get(column), value) for column, value in key.items()
            )
        ]
        if len(matches) != 1:
            msg = f"{table_id!r} key {dict(key)!r} matched {len(matches)} rows"
            raise ValueError(msg)
        return matches[0]


@dataclass(slots=True)
class EvalContext:
    """Closed bindings for one relation evaluation."""

    params: ParameterRelationData = field(default_factory=ParameterRelationData)
    point_row: Row = field(default_factory=dict)
    inputs: dict[str, object] = field(default_factory=dict)
    parameter_reads: ParameterReadRecorder | None = None

    def parameter_scalar(self, name: str) -> CellValue:
        try:
            value = self.params.scalar(name)
        except KeyError:
            if self.parameter_reads is not None:
                self.parameter_reads.incomplete(f"unresolved_scalar:{name}")
            raise
        if self.parameter_reads is not None:
            self.parameter_reads.scalar(name, value)
        return value

    def parameter_lookup(
        self, table: str, key: Mapping[str, CellValue], column: str
    ) -> CellValue:
        try:
            value = read_path(self.params.lookup_row(table, key), column)
        except KeyError, TypeError, ValueError:
            if self.parameter_reads is not None:
                self.parameter_reads.incomplete(f"unresolved_lookup:{table}.{column}")
            raise
        if self.parameter_reads is not None:
            self.parameter_reads.lookup(table, key, column, value)
        return value


__all__ = ["EvalContext", "ParameterRelationData"]
