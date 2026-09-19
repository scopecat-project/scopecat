"""Human-maintained apparatus observations, not live wiring or calibration state."""

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from scopecat.daemon.client import DaemonClient
from scopecat.records.apparatus_history import (
    MAX_APPARATUS_ATTACHMENT_BYTES,
    ApparatusAttachment,
    ApparatusObjectContent,
    ApparatusObjectCreate,
    ApparatusObjectPage,
    ApparatusObjectRef,
    ApparatusObjectRevise,
    ApparatusObjectRevision,
    ApparatusObservation,
    ApparatusObservationCreate,
    ApparatusObservationDraft,
    ApparatusObservationPage,
)


class LabApparatusOperations:
    """Record and browse observations without asserting their present applicability."""

    def __init__(self, client: DaemonClient, *, operator: str) -> None:
        self._client = client
        self._operator = operator

    def create(
        self,
        object_id: str,
        *,
        name: str,
        kind: str,
        aliases: tuple[str, ...] = (),
        description: str = "",
        actor: str | None = None,
    ) -> ApparatusObjectRevision:
        return self._client.create_apparatus_object(
            ApparatusObjectCreate(
                catalog_id=self._client.health().project_id,
                object_id=object_id,
                content=ApparatusObjectContent(
                    name=name, kind=kind, aliases=aliases, description=description
                ),
                actor=self._operator if actor is None else actor,
            )
        )

    def get(
        self, object_id: str, *, revision: int | None = None
    ) -> ApparatusObjectRevision:
        return self._client.apparatus_object(object_id, revision=revision)

    def resolve(self, ref: ApparatusObjectRef) -> ApparatusObjectRevision:
        return self._client.resolve_apparatus_object(ref)

    def revise(
        self,
        ref: ApparatusObjectRef,
        *,
        name: str | None = None,
        kind: str | None = None,
        aliases: tuple[str, ...] | None = None,
        description: str | None = None,
        actor: str | None = None,
    ) -> ApparatusObjectRevision:
        current = self.resolve(ref).content
        content = ApparatusObjectContent(
            name=current.name if name is None else name,
            kind=current.kind if kind is None else kind,
            aliases=current.aliases if aliases is None else aliases,
            description=current.description if description is None else description,
        )
        return self._client.revise_apparatus_object(
            ApparatusObjectRevise(
                expected=ref,
                content=content,
                actor=self._operator if actor is None else actor,
            )
        )

    def search(
        self, query: str = "", *, limit: int = 100, before: int | None = None
    ) -> ApparatusObjectPage:
        return self._client.apparatus_objects(query=query, limit=limit, before=before)

    def observe(
        self,
        ref: ApparatusObjectRef,
        *,
        title: str,
        conditions: dict[str, str] | None = None,
        note: str = "",
        run_ids: tuple[str, ...] = (),
        attachments: tuple[ApparatusAttachment, ...] = (),
        observed_at: datetime | None = None,
        supersedes: str | None = None,
        actor: str | None = None,
        observation_id: str | None = None,
    ) -> ApparatusObservation:
        return self._client.record_apparatus_observation(
            ApparatusObservationCreate(
                observation_id=str(uuid4())
                if observation_id is None
                else observation_id,
                draft=ApparatusObservationDraft(
                    subject=ref,
                    title=title,
                    actor=self._operator if actor is None else actor,
                    conditions={} if conditions is None else conditions,
                    note=note,
                    run_ids=run_ids,
                    attachments=attachments,
                    observed_at=observed_at,
                    supersedes=supersedes,
                ),
            )
        )

    def observation(self, observation_id: str) -> ApparatusObservation:
        return self._client.apparatus_observation(observation_id)

    def history(
        self,
        object_id: str,
        *,
        limit: int = 100,
        before: int | None = None,
        run_id: str | None = None,
    ) -> ApparatusObservationPage:
        return self._client.apparatus_history(
            object_id, limit=limit, before=before, run_id=run_id
        )

    def import_file(self, path: str | Path) -> ApparatusAttachment:
        """Copy a local file into daemon-owned history storage (at most 64 MiB)."""
        path = Path(path)
        with path.open("rb") as source:
            content = source.read(MAX_APPARATUS_ATTACHMENT_BYTES + 1)
        if not content or len(content) > MAX_APPARATUS_ATTACHMENT_BYTES:
            raise ValueError(
                "Apparatus attachment must contain between 1 byte and 64 MiB"
            )
        return self._client.import_apparatus_attachment(content, filename=path.name)

    def read_attachment(self, observation_id: str, content_hash: str) -> bytes:
        return self._client.apparatus_attachment(observation_id, content_hash)
