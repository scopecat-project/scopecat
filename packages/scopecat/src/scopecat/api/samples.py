"""Notebook-facing handles for stable physical samples."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import SamplePage, SampleRevisionPage, SampleView
from scopecat.daemon.wire import (
    SampleCreateCommand,
    SampleMutationReceipt,
    SampleReviseCommand,
)
from scopecat.records.sample import (
    SampleArtifactRef,
    SampleRevision,
    SampleRevisionDraft,
    SampleSelector,
)
from scopecat.records.sample_artifact import SampleArtifactPage


class SampleOperations(Protocol):
    """Storage-neutral operations used by sample handles."""

    def get(self, sample_id: str) -> SampleView: ...

    def revisions(
        self,
        sample_id: str,
        *,
        limit: int,
        before: int | None,
    ) -> SampleRevisionPage: ...

    def revision(self, sample_id: str, revision: int) -> SampleRevision: ...

    def revise(
        self,
        sample_id: str,
        content: SampleRevisionDraft,
        *,
        expected_revision: int | None,
        actor: str | None,
        note: str,
        operation_id: str | None,
    ) -> SampleMutationReceipt: ...


class SampleSession(Protocol):
    @property
    def sample_operations(self) -> SampleOperations: ...


@dataclass(frozen=True, slots=True)
class SampleHandle:
    """Stable sample identity with explicit revision selection for runs."""

    session: SampleSession
    id: str

    @property
    def view(self) -> SampleView:
        return self.session.sample_operations.get(self.id)

    def selector(
        self,
        *,
        role: str = "subject",
        revision: int | None = None,
        context_id: str | None = None,
    ) -> SampleSelector:
        return SampleSelector(
            role=role,
            sample_id=self.id,
            revision=revision,
            context_id=context_id,
        )

    def revisions(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> SampleRevisionPage:
        return self.session.sample_operations.revisions(
            self.id,
            limit=limit,
            before=before,
        )

    def revision(self, revision: int) -> SampleRevision:
        return self.session.sample_operations.revision(self.id, revision)

    def revise(
        self,
        content: SampleRevisionDraft,
        *,
        expected_revision: int | None = None,
        actor: str | None = None,
        note: str = "",
        operation_id: str | None = None,
    ) -> SampleMutationReceipt:
        return self.session.sample_operations.revise(
            self.id,
            content,
            expected_revision=expected_revision,
            actor=actor,
            note=note,
            operation_id=operation_id,
        )


@dataclass(frozen=True, slots=True)
class SampleHandlePage:
    items: tuple[SampleHandle, ...] = ()
    next_cursor: int | None = None


@dataclass(frozen=True, slots=True)
class LabSampleOperations:
    """Create, inspect, and revise project samples through the daemon."""

    client: DaemonClient
    session: SampleSession
    operator: str

    def import_artifact(
        self, content: bytes, *, artifact_id: str, title: str, media_type: str
    ) -> SampleArtifactRef:
        """Store bounded attachment bytes; add the returned ref to a sample revision."""
        return self.client.import_sample_artifact(
            content, artifact_id=artifact_id, title=title, media_type=media_type
        )

    def artifacts(self, sample_id: str, revision: int) -> SampleArtifactPage:
        return self.client.sample_artifacts(sample_id, revision)

    def artifact_content(
        self, sample_id: str, revision: int, artifact_id: str
    ) -> bytes:
        return self.client.sample_artifact_content(sample_id, revision, artifact_id)

    def list(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> SampleHandlePage:
        page: SamplePage = self.client.list_samples(limit=limit, before=before)
        return SampleHandlePage(
            items=tuple(
                SampleHandle(session=self.session, id=item.record.id)
                for item in page.items
            ),
            next_cursor=page.next_cursor,
        )

    def get(self, sample_id: str) -> SampleView:
        return self.client.get_sample(sample_id)

    def revisions(
        self,
        sample_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
    ) -> SampleRevisionPage:
        return self.client.sample_revisions(
            sample_id,
            limit=limit,
            before=before,
        )

    def revision(self, sample_id: str, revision: int) -> SampleRevision:
        return self.client.sample_revision(sample_id, revision)

    def handle(self, sample_id: str) -> SampleHandle:
        self.get(sample_id)
        return SampleHandle(session=self.session, id=sample_id)

    def create(
        self,
        sample_id: str,
        *,
        kind: str,
        content: SampleRevisionDraft,
        actor: str | None = None,
        note: str = "",
        operation_id: str | None = None,
    ) -> SampleHandle:
        receipt = self.client.create_sample(
            SampleCreateCommand(
                operation_id=operation_id or uuid4().hex,
                sample_id=sample_id,
                kind=kind,
                actor=actor or self.operator,
                note=note,
                content=content,
            )
        )
        return SampleHandle(session=self.session, id=receipt.record.id)

    def revise(
        self,
        sample_id: str,
        content: SampleRevisionDraft,
        *,
        expected_revision: int | None = None,
        actor: str | None = None,
        note: str = "",
        operation_id: str | None = None,
    ) -> SampleMutationReceipt:
        selected_revision = expected_revision
        if selected_revision is None:
            selected_revision = self.get(sample_id).record.active_revision
        return self.client.revise_sample(
            sample_id,
            SampleReviseCommand(
                operation_id=operation_id or uuid4().hex,
                expected_revision=selected_revision,
                actor=actor or self.operator,
                note=note,
                content=content,
            ),
        )


__all__ = [
    "LabSampleOperations",
    "SampleHandle",
    "SampleHandlePage",
    "SampleOperations",
    "SampleSession",
]
