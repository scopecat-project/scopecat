"""Transaction-local persistence port for executable setup authority."""

from typing import Protocol

from scopecat.records.setup import (
    ActiveSetupView,
    SetupActivationOperation,
    SetupRevision,
    SetupRevisionRef,
)


class SetupRepository(Protocol):
    def read_current(self) -> ActiveSetupView | None: ...

    def read_revision(self, revision_id: str) -> SetupRevision: ...

    def list_revisions(self) -> tuple[SetupRevision, ...]: ...

    def save_revision(self, revision: SetupRevision) -> SetupRevision: ...

    def read_activation_operation(
        self, operation_id: str
    ) -> SetupActivationOperation | None: ...

    def activate(
        self,
        *,
        revision: SetupRevisionRef,
        expected_generation: int,
        operation_id: str,
        intent_hash: str,
        actor: str,
        note: str,
    ) -> ActiveSetupView: ...
