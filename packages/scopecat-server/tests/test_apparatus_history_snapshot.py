"""Historical object evidence survives recovery without becoming live configuration."""

from pathlib import Path

import pytest
from scopecat.project import load_project
from scopecat.records.apparatus_history import (
    ApparatusObjectContent,
    ApparatusObjectCreate,
    ApparatusObjectRevise,
    ApparatusObservationCreate,
    ApparatusObservationDraft,
)
from scopecat_testkit.config_registry import load_config

from scopecat_server import LocalDaemonRuntime
from scopecat_server.snapshots import (
    create_snapshot,
    restore_snapshot,
    verify_store_files,
)
from scopecat_server.storage.sqlite.object_store import ObjectNotFoundError


def test_observation_recovery_retains_original_bytes_and_object_revision(
    tmp_path: Path,
):
    source = tmp_path / "source"
    source.mkdir()
    manifest = source / "scopecat.toml"
    manifest.write_text("[lab]\n")
    content = b"original presentation bytes\x00\xff"
    with LocalDaemonRuntime(source, bootstrap_config=load_config()) as runtime:
        history = runtime.application.apparatus_history
        line = history.create_object(
            ApparatusObjectCreate(
                catalog_id=runtime.application.project_id,
                object_id="line-3",
                actor="Li",
                content=ApparatusObjectContent(
                    name="Input line 3", kind="line", aliases=("L3",)
                ),
            )
        )
        attachment = history.import_attachment(
            content, filename="room-temperature.pptx"
        )
        warm = history.record_observation(
            ApparatusObservationCreate(
                observation_id="room-temperature",
                draft=ApparatusObservationDraft(
                    subject=line.ref,
                    title="Room-temperature characterization",
                    actor="Li",
                    conditions={
                        "temperature": "room temperature",
                        "connection": "as noted on slides",
                    },
                    attachments=(attachment,),
                ),
            )
        )
        history.revise_object(
            ApparatusObjectRevise(
                expected=line.ref,
                content=line.content.model_copy(
                    update={"name": "Input line 3 / old label"}
                ),
                actor="Li",
            )
        )
        cold = history.record_observation(
            ApparatusObservationCreate(
                observation_id="cold",
                draft=ApparatusObservationDraft(
                    subject=line.ref,
                    title="Low-temperature note",
                    actor="Li",
                    conditions={"temperature": "cryogenic, exact temperature unknown"},
                    note="Do not substitute the room-temperature result.",
                ),
            )
        )
    create_snapshot(load_project(manifest), tmp_path / "snapshot")
    restore_snapshot(tmp_path / "snapshot", tmp_path / "restored")
    with LocalDaemonRuntime(tmp_path / "restored") as restored:
        history = restored.application.apparatus_history
        assert history.resolve_object(line.ref) == line
        assert history.get_observation(warm.id) == warm
        assert history.get_observation(cold.id) == cold
        assert history.attachment_content(warm.id, attachment.content_hash) == content
        assert {item.id for item in history.observations(line.ref.object_id).items} == {
            warm.id,
            cold.id,
        }
    # An indexed attachment must not disappear silently from snapshot verification.
    digest = attachment.content_hash.removeprefix("sha256:")
    (tmp_path / "restored" / ".scopecat/objects" / digest[:2] / digest[2:]).unlink()
    with pytest.raises(ObjectNotFoundError):
        verify_store_files(tmp_path / "restored")
