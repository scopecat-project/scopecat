# ruff: noqa: F401
# pyright: reportUnusedImport=false, reportUnsupportedDunderAll=false
"""Lazy facade for configuration-registry records, ports, and use cases."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from scopecat.config.registry.ports import (
        ConfigRegistryRepository,
        ConfigRegistryUnitOfWork,
        ConfigRegistryUnitOfWorkFactory,
    )
    from scopecat.config.registry.records import (
        BoundParameterRegistrySource,
        CandidateAcceptance,
        CandidateConfigRegistrySource,
        ConfigRegistryEntry,
        ConfigRegistryEntryPage,
        ConfigRegistryEntrySource,
        CrossRunCandidateAcceptance,
        DirectConfigRegistrySource,
        ManualCandidateAcceptance,
        ManualConfigDraftRegistrySource,
        ParameterConfigRegistrySource,
    )
    from scopecat.config.registry.service import (
        CandidateConfigRevisionSource,
        ConfigRegistryEntrySnapshot,
        ConfigRegistryMutationResult,
        ConfigRegistryPageSnapshot,
        ConfigRevision,
        ConfigRevisionSource,
        DirectConfigRevisionSource,
        InstrumentInventoryMigrationDelta,
        InstrumentInventoryMigrationPlan,
        load_config_registry_entry_snapshot,
        load_config_registry_page,
        plan_instrument_inventory_migration,
        resolve_config_registry_config_source,
    )


_RECORD_EXPORTS = (
    "BoundParameterRegistrySource",
    "CandidateAcceptance",
    "CandidateConfigRegistrySource",
    "ConfigRegistryEntry",
    "ConfigRegistryEntryPage",
    "ConfigRegistryEntrySource",
    "CrossRunCandidateAcceptance",
    "DirectConfigRegistrySource",
    "ParameterConfigRegistrySource",
    "ManualCandidateAcceptance",
    "ManualConfigDraftRegistrySource",
)
_PORT_EXPORTS = (
    "ConfigRegistryRepository",
    "ConfigRegistryUnitOfWork",
    "ConfigRegistryUnitOfWorkFactory",
)
_SERVICE_EXPORTS = (
    "CandidateConfigRevisionSource",
    "ConfigRevision",
    "ConfigRevisionSource",
    "ConfigRegistryEntrySnapshot",
    "ConfigRegistryMutationResult",
    "ConfigRegistryPageSnapshot",
    "DirectConfigRevisionSource",
    "InstrumentInventoryMigrationDelta",
    "InstrumentInventoryMigrationPlan",
    "load_config_registry_entry_snapshot",
    "load_config_registry_page",
    "plan_instrument_inventory_migration",
    "resolve_config_registry_config_source",
)
_EXPORTS = {
    **{name: ("scopecat.config.registry.records", name) for name in _RECORD_EXPORTS},
    **{name: ("scopecat.config.registry.ports", name) for name in _PORT_EXPORTS},
    **{name: ("scopecat.config.registry.service", name) for name in _SERVICE_EXPORTS},
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
