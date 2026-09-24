"""Domain program contracts shared across authoring and compilation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from scopecat.kernel.symbols import SymbolId
from scopecat.kernel.value_data import CellValue
from scopecat.kernel.value_types import ValueType


@dataclass(frozen=True, slots=True)
class DomainParameterRead:
    """A guaranteed declarative read and the reads selecting its exact key."""

    table: str
    key: tuple[tuple[str, CellValue], ...]
    columns: tuple[str, ...]
    selection_reads: tuple[DomainParameterRead, ...] = ()


class DomainParameterReads(Protocol):
    def __call__(
        self, inputs: Mapping[str, object], parameters: object
    ) -> tuple[DomainParameterRead, ...]:
        """Resolve guaranteed reads from static inputs without executing the domain.

        The snapshot is opaque to the shared program model, like the domain body.
        The application supplies a ParameterSnapshot. Missing inputs are dynamic.
        Unsupported or conditional reads must not be reported. These declarations
        authorize sweeps, not scientific reuse.
        """
        ...


@dataclass(frozen=True, slots=True)
class DomainInputPort:
    """One typed plan-stage input accepted by a domain program."""

    id: str
    value_type: ValueType

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("domain input port ids must be non-empty")


@dataclass(frozen=True, slots=True)
class DomainResultPort:
    """One named output whose contract is interpreted by the dialect."""

    id: str
    contract: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("domain result port ids must be non-empty")


@dataclass(frozen=True, slots=True)
class DomainProgramDef:
    """Opaque domain program declaration retained through target linking.

    Program inputs describe runtime program semantics and may remain residual.
    Compiler inputs only configure lowering and must be captured by the target
    artifact, which keeps large parameter collections out of the program ABI.
    """

    id: str
    dialect_id: str
    dialect_version: str
    body: object = field(repr=False)
    input_ports: tuple[DomainInputPort, ...] = ()
    compiler_input_ports: tuple[DomainInputPort, ...] = ()
    result_ports: tuple[DomainResultPort, ...] = ()
    parameter_reads: DomainParameterReads | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not all((self.id, self.dialect_id, self.dialect_version)):
            raise ValueError(
                "domain program, dialect, and dialect version ids must be non-empty"
            )
        _require_unique("domain input port", tuple(p.id for p in self.input_ports))
        _require_unique(
            "domain compiler input port",
            tuple(p.id for p in self.compiler_input_ports),
        )
        _require_unique(
            "domain input port",
            tuple(p.id for p in (*self.input_ports, *self.compiler_input_ports)),
        )
        _require_unique("domain result port", tuple(p.id for p in self.result_ports))

    @property
    def symbol_id(self) -> SymbolId:
        return SymbolId(local_id=self.id)

    def __deepcopy__(self, _memo: dict[int, object]) -> DomainProgramDef:
        # The body is trusted frozen transient IR owned by its dialect.
        return self


def _require_unique(label: str, values: tuple[str, ...]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} ids must be unique")
