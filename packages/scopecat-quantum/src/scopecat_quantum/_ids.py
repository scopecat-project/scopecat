"""Nominal and structural identities for transient quantum-domain IR."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self, override
from urllib.parse import quote


def _contains_unicode_surrogate(value: str) -> bool:
    return any("\ud800" <= character <= "\udfff" for character in value)


@dataclass(frozen=True, slots=True)
class _NominalId:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            msg = f"{type(self).__name__} must be non-empty"
            raise ValueError(msg)
        if _contains_unicode_surrogate(self.value):
            msg = f"{type(self).__name__} cannot contain Unicode surrogates"
            raise ValueError(msg)

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class QuantumProgramId(_NominalId):
    """Identity of one mixed gate-and-pulse quantum program."""


@dataclass(frozen=True, slots=True)
class CircuitOperationId(_NominalId):
    """Identity of one operation occurrence inside a circuit."""


@dataclass(frozen=True, slots=True)
class GateId(_NominalId):
    """Identity of one gate semantic definition."""


@dataclass(frozen=True, slots=True)
class QubitId(_NominalId):
    """Logical qubit identity, independent of physical wiring."""


@dataclass(frozen=True, slots=True)
class ReadoutLineId(_NominalId):
    """Readout path identity, independent of any identified sample object."""


@dataclass(frozen=True, slots=True)
class CouplerId(_NominalId):
    """Logical coupler identity, independent of physical wiring."""


@dataclass(frozen=True, slots=True)
class PulseProgramId(_NominalId):
    """Identity of one logical pulse program."""


@dataclass(frozen=True, slots=True)
class _StructuralId:
    """Shared structural identity with an injective canonical rendering."""

    local_id: str
    scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identity_name = type(self).__name__
        if not self.local_id.strip():
            msg = f"{identity_name} local_id must be non-empty"
            raise ValueError(msg)
        if _contains_unicode_surrogate(self.local_id):
            msg = f"{identity_name} local_id cannot contain Unicode surrogates"
            raise ValueError(msg)
        if any(not segment.strip() for segment in self.scope):
            msg = f"{identity_name} scope segments must be non-empty"
            raise ValueError(msg)
        if any(_contains_unicode_surrogate(segment) for segment in self.scope):
            msg = f"{identity_name} scope segments cannot contain Unicode surrogates"
            raise ValueError(msg)

    @property
    def qualified_name(self) -> str:
        """Return an injective human-readable rendering of the structure."""

        return "/".join(
            quote(segment, safe="-._~[]") for segment in (*self.scope, self.local_id)
        )

    @property
    def value(self) -> str:
        """Canonical text form used by diagnostics and transient adapters."""

        return self.qualified_name

    def prefixed(self, *segments: str) -> Self:
        """Return the same local identity under additional outer scopes."""

        if not segments:
            return self
        return type(self)(
            local_id=self.local_id,
            scope=(*segments, *self.scope),
        )

    @override
    def __str__(self) -> str:
        return self.qualified_name


@dataclass(frozen=True, slots=True)
class PulseEventId(_StructuralId):
    """Structural identity of one pulse instruction or scheduled event.

    ``scope`` is an IR-local structural path, not a pre-rendered namespace.
    Reusable pulse templates may therefore carry relative scopes and a
    lowering pass can hygienically prefix them without manufacturing a
    delimiter-sensitive string identity.
    """


@dataclass(frozen=True, slots=True)
class AcquisitionSlotId(_StructuralId):
    """Structural identity of one domain-local acquisition result."""


@dataclass(frozen=True, slots=True)
class PulseImplementationId(_NominalId):
    """Stable identity of one compiler-owned pulse implementation recipe."""


@dataclass(frozen=True, slots=True)
class TargetId(_NominalId):
    """Identity of one quantum compilation target."""


@dataclass(frozen=True, slots=True)
class TargetCompilerId(_NominalId):
    """Versioned identity of one quantum target compiler."""


@dataclass(frozen=True, slots=True)
class TargetArtifactId(_NominalId):
    """Identity of one transient target artifact."""


@dataclass(frozen=True, slots=True)
class TargetCompileEntryId(_NominalId):
    """Identity of one scheduled-program entry in a compile request."""
