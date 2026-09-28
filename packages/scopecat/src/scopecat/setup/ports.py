"""Transaction-local persistence port for executable setup authority."""

from typing import Protocol

from scopecat.records.setup import (
    SetupRevision,
)


class SetupRepository(Protocol):
    def read_revision(self, revision_id: str) -> SetupRevision: ...

    def list_revisions(self) -> tuple[SetupRevision, ...]: ...

    def save_revision(self, revision: SetupRevision) -> SetupRevision: ...
