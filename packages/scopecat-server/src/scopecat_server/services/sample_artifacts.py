"""Bounded, content-owned sample attachments; never resolve local URI paths."""

from urllib.parse import quote, urlsplit

from scopecat.records.sample import SampleArtifactRef, SampleRevision
from scopecat.records.sample_artifact import (
    MAX_SAMPLE_ARTIFACT_BYTES,
    SAMPLE_ARTIFACT_MEDIA_TYPES,
    SampleArtifactDelivery,
    is_owned_sample_artifact_uri,
)

from scopecat_server.storage.sqlite.object_store import (
    ImmutableObjectStore,
    ObjectCorruptError,
    ObjectNotFoundError,
    ObjectStoreError,
)

_IMPORT_REPAIR = (
    "Maintainer: read the intended file locally, import its bytes with "
    "lab.samples.import_artifact, and add the returned reference "
    "in a new sample revision."
)


def validate_artifact_bytes(content: bytes, media_type: str | None) -> None:
    if not content or len(content) > MAX_SAMPLE_ARTIFACT_BYTES:
        raise ValueError("Sample attachments require 1 to 8388608 bytes (8 MiB).")
    if media_type not in SAMPLE_ARTIFACT_MEDIA_TYPES:
        raise ValueError(
            "Unsupported attachment media type; use PNG, JPEG, WebP, PDF "
            "or UTF-8 plain text. Convert HTML/SVG to a supported "
            "non-active format first."
        )
    signatures = {
        "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": content.startswith(b"\xff\xd8\xff"),
        "image/webp": content.startswith(b"RIFF") and content[8:12] == b"WEBP",
        "application/pdf": content.startswith(b"%PDF-"),
    }
    if media_type == "text/plain":
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("Plain text attachments must be UTF-8.") from error
    elif not signatures[media_type]:
        raise ValueError(
            "Attachment bytes do not match the declared supported media type."
        )


class SampleArtifacts:
    def __init__(self, objects: ImmutableObjectStore) -> None:
        self.objects = objects

    def import_bytes(
        self, content: bytes, *, artifact_id: str, title: str, media_type: str
    ) -> SampleArtifactRef:
        validate_artifact_bytes(content, media_type)
        # Validate metadata before publishing content; the immutable sample revision
        # subsequently owns the returned reference, just as other content records do.
        reference = SampleArtifactRef(
            id=artifact_id, title=title, uri="pending", media_type=media_type
        )
        return reference.model_copy(update={"uri": self.objects.put(content).digest})

    def content(self, artifact: SampleArtifactRef) -> bytes:
        if not is_owned_sample_artifact_uri(artifact.uri):
            raise ValueError(
                "This reference is not a stored project attachment. " + _IMPORT_REPAIR
            )
        path = self.objects.path_for(artifact.uri)
        try:
            if path.stat().st_size > MAX_SAMPLE_ARTIFACT_BYTES:
                raise ValueError("Stored attachment exceeds the supported 8 MiB limit.")
        except FileNotFoundError as error:
            raise ObjectNotFoundError(path) from error
        content = self.objects.read(artifact.uri)
        validate_artifact_bytes(content, artifact.media_type)
        return content

    def resolve(
        self, revision: SampleRevision, artifact: SampleArtifactRef
    ) -> SampleArtifactDelivery:
        if is_owned_sample_artifact_uri(artifact.uri):
            try:
                self.content(artifact)
            except ObjectNotFoundError:
                reason = "Stored attachment bytes are missing from this project."
            except ObjectCorruptError:
                reason = (
                    "Stored attachment content does not match its SHA-256 reference."
                )
            except ObjectStoreError, OSError:
                reason = "Stored attachment bytes cannot be read."
            except ValueError as error:
                reason = str(error)
            else:
                return SampleArtifactDelivery(
                    artifact=artifact,
                    status="stored",
                    url=(
                        f"/api/v1/samples/{quote(revision.sample_id, safe='')}"
                        f"/revisions/{revision.revision}/artifacts/"
                        f"{quote(artifact.id, safe='')}/content"
                    ),
                    reason=(
                        "Stored in this project; included in project snapshots. "
                        "PDF files download; supported images and plain text "
                        "open in the browser."
                    ),
                )
        else:
            try:
                parsed = urlsplit(artifact.uri)
                _ = (
                    parsed.port
                )  # Reject malformed ports rather than offering a dead link.
                remote = (
                    parsed.scheme in {"http", "https"}
                    and bool(parsed.hostname)
                    and not parsed.username
                    and not parsed.password
                    and not any(
                        char.isspace() or ord(char) < 32 for char in artifact.uri
                    )
                    and "\\" not in artifact.uri
                )
            except ValueError:
                remote = False
            if remote:
                return SampleArtifactDelivery(
                    artifact=artifact,
                    status="external",
                    url=artifact.uri,
                    reason=(
                        "External website; availability is not checked and "
                        "its bytes are not included in snapshots."
                    ),
                )
            reason = (
                "Relative paths and file/project URIs are not delivered "
                "by the daemon; arbitrary paths and URI execution "
                "are unsupported."
            )
        return SampleArtifactDelivery(
            artifact=artifact,
            status="unavailable",
            reason=reason,
            repair=_IMPORT_REPAIR
            + " For missing/corrupt stored bytes, restore the original "
            "complete project snapshot.",
        )
