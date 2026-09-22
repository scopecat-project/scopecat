# ruff: noqa: F401
# pyright: reportUnusedImport=false, reportUnsupportedDunderAll=false
"""Application-level composition and service bundles."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from scopecat.application.bootstrap import (
        LabBootstrap,
    )
    from scopecat.application.capabilities import LabCapabilities
    from scopecat.application.lab import LabApplication

_EXPORTS = {
    "LabCapabilities": ("scopecat.application.capabilities", "LabCapabilities"),
    "LabBootstrap": ("scopecat.application.bootstrap", "LabBootstrap"),
    "LabApplication": ("scopecat.application.lab", "LabApplication"),
}


def __getattr__(name: str) -> object:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = cast("object", getattr(import_module(module_name), attribute_name))
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = sorted(_EXPORTS)
