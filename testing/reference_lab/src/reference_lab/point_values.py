"""Resolved inputs for one quantum compilation point."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QuantumLabPointValues:
    """Resolved program and compiler inputs for one logical point."""

    ordinal: int
    values: tuple[tuple[str, object], ...]

    def value(self, name: str) -> object:
        for input_name, value in self.values:
            if input_name == name:
                return value
        raise KeyError(name)


__all__ = ["QuantumLabPointValues"]
