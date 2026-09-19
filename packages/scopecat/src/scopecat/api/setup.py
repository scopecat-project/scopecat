"""Explicit notebook operations for the daemon's executable setup authority."""

from dataclasses import dataclass
from uuid import uuid4

from scopecat.config.inventory import InstrumentInventoryChange
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.wire import SetupActivateCommand, SetupSaveCommand
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.setup import (
    ActiveSetupView,
    ExecutableSetupSnapshot,
    SetupRevision,
    SetupRevisionRef,
)


@dataclass(frozen=True, slots=True)
class LabSetupOperations:
    client: DaemonClient
    operator: str

    def active(self) -> ActiveSetupView:
        """Read the daemon's independently selected executable setup."""
        return self.client.active_setup()

    def get(self, name: str) -> SetupRevision:
        return self.client.setup_revision(name)

    def list(self) -> tuple[SetupRevision, ...]:
        return self.client.setup_revisions().items

    def save(
        self,
        setup: ExecutableSetupSnapshot | ConfigProfileSnapshot,
        *,
        name: str,
        note: str = "",
    ) -> SetupRevision:
        """Save a named immutable revision; saving does not select it."""
        snapshot = (
            ExecutableSetupSnapshot.from_config(setup)
            if isinstance(setup, ConfigProfileSnapshot)
            else setup
        )
        return self.client.save_setup(
            SetupSaveCommand(
                revision_id=name, setup=snapshot, actor=self.operator, note=note
            )
        )

    def activate(
        self,
        revision: SetupRevision | SetupRevisionRef | str,
        *,
        expected_generation: int | None = None,
        operation_id: str | None = None,
        changes: tuple[InstrumentInventoryChange, ...] = (),
        note: str = "",
    ) -> ActiveSetupView:
        """Select setup explicitly; declared destructive changes require drained owners.

        Pass a previously reviewed generation to retain that review's freshness.
        Otherwise the current generation is read immediately before submission.
        """
        if isinstance(revision, str):
            revision = self.get(revision)
        ref = revision.ref if isinstance(revision, SetupRevision) else revision
        generation = (
            self.active().activation.generation
            if expected_generation is None
            else expected_generation
        )
        return self.client.activate_setup(
            SetupActivateCommand(
                operation_id=operation_id or f"setup-activation:{uuid4().hex}",
                revision=ref,
                expected_generation=generation,
                actor=self.operator,
                note=note,
                changes=changes,
            )
        )


__all__ = ["LabSetupOperations"]
