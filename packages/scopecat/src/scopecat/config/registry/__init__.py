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
        ConfigActivationOperation,
        ConfigPublishOperation,
        ConfigRegistryActivationPage,
        ConfigRegistryActivationRecord,
        ConfigRegistryEntry,
        ConfigRegistryEntryPage,
        ConfigRegistryEntrySource,
        CrossRunCandidateAcceptance,
        DirectConfigRegistrySource,
        ManualCandidateAcceptance,
        ManualConfigDraftRegistrySource,
        ParameterConfigRegistrySource,
        config_activation_intent_hash,
        config_publish_intent_hash,
    )
    from scopecat.config.registry.service import (
        ACTIVE_CONFIG_REGISTRY_ENTRY_SELECTOR,
        ActiveConfigRegistrySnapshot,
        CandidateConfigRevisionSource,
        ConfigRegistryEntrySnapshot,
        ConfigRegistryMutationResult,
        ConfigRegistryPageSnapshot,
        ConfigRevision,
        ConfigRevisionSource,
        DirectConfigRevisionSource,
        InstrumentInventoryMigrationDelta,
        InstrumentInventoryMigrationPlan,
        ManualConfigDraftResult,
        ManualConfigDraftRevisionSource,
        ParameterConfigRevisionSource,
        activate_config_registry_entry,
        load_active_config_registry_snapshot,
        load_config_registry_activation,
        load_config_registry_activation_page,
        load_config_registry_entry_snapshot,
        load_config_registry_page,
        plan_instrument_inventory_migration,
        preview_manual_config_draft,
        publish_config_revision,
        resolve_config_registry_config_source,
    )


_RECORD_EXPORTS = (
    "BoundParameterRegistrySource",
    "CandidateAcceptance",
    "CandidateConfigRegistrySource",
    "ConfigActivationOperation",
    "ConfigPublishOperation",
    "ConfigRegistryActivationPage",
    "ConfigRegistryActivationRecord",
    "ConfigRegistryEntry",
    "ConfigRegistryEntryPage",
    "ConfigRegistryEntrySource",
    "CrossRunCandidateAcceptance",
    "DirectConfigRegistrySource",
    "ParameterConfigRegistrySource",
    "ManualCandidateAcceptance",
    "ManualConfigDraftRegistrySource",
    "config_activation_intent_hash",
    "config_publish_intent_hash",
)
_PORT_EXPORTS = (
    "ConfigRegistryRepository",
    "ConfigRegistryUnitOfWork",
    "ConfigRegistryUnitOfWorkFactory",
)
_SERVICE_EXPORTS = (
    "ACTIVE_CONFIG_REGISTRY_ENTRY_SELECTOR",
    "ActiveConfigRegistrySnapshot",
    "CandidateConfigRevisionSource",
    "ConfigRevision",
    "ConfigRevisionSource",
    "ConfigRegistryEntrySnapshot",
    "ConfigRegistryMutationResult",
    "ConfigRegistryPageSnapshot",
    "DirectConfigRevisionSource",
    "ParameterConfigRevisionSource",
    "InstrumentInventoryMigrationDelta",
    "InstrumentInventoryMigrationPlan",
    "ManualConfigDraftResult",
    "ManualConfigDraftRevisionSource",
    "activate_config_registry_entry",
    "load_active_config_registry_snapshot",
    "load_config_registry_activation",
    "load_config_registry_activation_page",
    "load_config_registry_entry_snapshot",
    "load_config_registry_page",
    "plan_instrument_inventory_migration",
    "preview_manual_config_draft",
    "publish_config_revision",
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
