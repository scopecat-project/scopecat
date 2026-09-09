"""Leaf persistence ports for the project configuration registry."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self

from scopecat.config.registry.records import (
    ConfigRegistryActivationPage,
    ConfigRegistryActivationRecord,
    ConfigRegistryEntry,
    ConfigRegistryEntryPage,
)
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.runs.repository import RunRepository


class ConfigRegistryRepository(Protocol):
    """Persistence boundary for registry records and commit markers."""

    @property
    def active_ref(self) -> str: ...

    def entry_ref(self, entry_id: str) -> str: ...

    def config_ref(self, entry_id: str) -> str: ...

    def entry_exists(self, entry_id: str) -> bool: ...

    def list_entries(self) -> tuple[ConfigRegistryEntry, ...]: ...

    def list_entry_page(
        self,
        *,
        limit: int,
        before: int | None,
    ) -> ConfigRegistryEntryPage: ...

    def read_entry(self, entry_id: str) -> ConfigRegistryEntry: ...

    def read_config(self, ref: str) -> ConfigProfileSnapshot: ...

    def current_generation(self) -> int: ...

    def read_latest_activation(self) -> ConfigRegistryActivationRecord | None: ...

    def read_activation(self, generation: int) -> ConfigRegistryActivationRecord: ...

    def read_latest_entry_activation(
        self, entry_id: str
    ) -> ConfigRegistryActivationRecord | None: ...

    def list_activation_history(self) -> tuple[ConfigRegistryActivationRecord, ...]: ...

    def list_activation_page(
        self,
        *,
        limit: int,
        before: int | None,
    ) -> ConfigRegistryActivationPage: ...

    def commit_revision(
        self,
        *,
        entry: ConfigRegistryEntry,
        config: ConfigProfileSnapshot,
    ) -> None: ...

    def commit_activation(
        self,
        *,
        expected_generation: int,
        record: ConfigRegistryActivationRecord,
    ) -> None: ...


class ConfigRegistryUnitOfWork(Protocol):
    """One configuration-registry transaction with run evidence access."""

    @property
    def registry(self) -> ConfigRegistryRepository: ...

    @property
    def runs(self) -> RunRepository: ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


type ConfigRegistryUnitOfWorkFactory = Callable[[], ConfigRegistryUnitOfWork]
