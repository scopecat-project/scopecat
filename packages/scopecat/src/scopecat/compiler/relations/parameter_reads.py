"""A context-local recorder shared by scalar evaluation and specialization."""

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import ValidationError

from scopecat.kernel.value_data import CellValue
from scopecat.records.parameter import ScalarParameterValue
from scopecat.records.parameter_read import (
    KeyedParameterRead,
    ScalarExpressionReadEvidence,
)


@dataclass
class ParameterReadRecorder:
    """One recorder per effective context; no process-global capture state."""

    _scalars: list[ScalarParameterValue] = field(default_factory=list)
    _keyed: list[KeyedParameterRead] = field(default_factory=list)
    _incomplete: list[str] = field(default_factory=list)

    def incomplete(self, reason: str) -> None:
        if reason not in self._incomplete:
            self._incomplete.append(reason)

    def scalar(self, name: str, value: CellValue) -> None:
        try:
            read = ScalarParameterValue.model_validate({"id": name, "value": value})
        except ValidationError:
            self.incomplete(f"unrecordable_scalar:{name}")
        else:
            if read not in self._scalars:
                self._scalars.append(read)

    def lookup(
        self, table: str, key: Mapping[str, CellValue], column: str, value: CellValue
    ) -> None:
        try:
            read = KeyedParameterRead.model_validate(
                {
                    "table": table,
                    "key": tuple(
                        {"id": name, "value": item} for name, item in key.items()
                    ),
                    "cells": ({"id": column, "value": value},),
                }
            )
        except ValidationError:
            self.incomplete(f"unrecordable_lookup:{table}.{column}")
        else:
            if read not in self._keyed:
                self._keyed.append(read)

    def snapshot(self) -> ScalarExpressionReadEvidence:
        return ScalarExpressionReadEvidence(
            scalars=tuple(self._scalars),
            keyed=tuple(self._keyed),
            incomplete_reasons=tuple(self._incomplete),
        )
