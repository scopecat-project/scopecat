"""Definition-local names allocated before domain result handles are created."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

_names: ContextVar[set[str] | None] = ContextVar(
    "domain_occurrence_names", default=None
)


@contextmanager
def occurrence_names_internal() -> Generator[None]:
    """Give each experiment/module definition its own deterministic namespace."""
    token = _names.set(set())
    try:
        yield
    finally:
        _names.reset(token)


def domain_occurrence_name(name: str, *, explicit: bool = False) -> str:
    """Select a call name in the current definition, or keep it outside one.

    Domain frontends call this once, before creating results. Modifiers retain
    that name. Explicit names are never rewritten; conflicting names fail.
    """
    names = _names.get()
    if names is None:
        return name
    if explicit:
        if name in names:
            raise ValueError(f"duplicate explicit domain call name: {name!r}")
        selected = name
    else:
        selected = name
        suffix = 2
        while selected in names:
            selected = f"{name}.{suffix}"
            suffix += 1
    names.add(selected)
    return selected
