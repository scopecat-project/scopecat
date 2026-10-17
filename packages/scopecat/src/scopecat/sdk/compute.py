"""Private markers shared by authored compute lowering."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from inspect import unwrap
from types import CodeType
from typing import cast

from scopecat.program.values import ComputeFunction

_IMPLEMENTATION_ATTRIBUTE = "__scopecat_compute_implementation__"
PYTHON_JSON_CODEC = "scopecat.python-json.v1"


@dataclass(frozen=True, slots=True)
class _ComputeImplementation:
    """Minimal identity retained for Scopecat-owned compute helpers."""

    id: str
    version: str
    deterministic: bool

    @property
    def reference(self) -> str:
        return f"internal:{self.id}@{self.version}"


def mark_compute_implementation_internal[**P, ResultT](
    id: str,
    version: str,
    *,
    deterministic: bool = True,
) -> Callable[[Callable[P, ResultT]], Callable[P, ResultT]]:
    """Mark a Scopecat-owned helper with stable lowering identity."""

    if not id or not version:
        raise ValueError("compute implementation id and version must be non-empty")
    implementation = _ComputeImplementation(
        id=id,
        version=version,
        deterministic=deterministic,
    )

    def mark(fn: Callable[P, ResultT]) -> Callable[P, ResultT]:
        captures = compute_capture_names_internal(fn)
        if captures:
            raise ValueError(
                "internal compute implementations cannot capture nonlocal values: "
                f"{', '.join(captures)}"
            )
        setattr(fn, _IMPLEMENTATION_ATTRIBUTE, implementation)
        return fn

    return mark


def compute_implementation_internal(
    fn: ComputeFunction,
) -> _ComputeImplementation | None:
    """Read a trusted internal implementation marker."""

    value = getattr(fn, _IMPLEMENTATION_ATTRIBUTE, None)
    return value if isinstance(value, _ComputeImplementation) else None


def compute_capture_names_internal(fn: ComputeFunction) -> tuple[str, ...]:
    """Return stable names for Python nonlocals hidden outside explicit inputs."""

    code = getattr(cast("object", unwrap(fn)), "__code__", None)
    return () if not isinstance(code, CodeType) else code.co_freevars


__all__: list[str] = []
