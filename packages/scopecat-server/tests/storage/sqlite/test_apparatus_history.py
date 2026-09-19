from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from scopecat.records.apparatus_history import (
    ApparatusObjectContent,
    ApparatusObjectCreate,
    ApparatusObjectRevise,
    ApparatusObjectRevision,
    ApparatusObservationCreate,
    ApparatusObservationDraft,
)

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.storage.sqlite.apparatus_history import ApparatusHistoryStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


@pytest.fixture
def history(tmp_path: Path) -> Generator[ApparatusHistoryStore]:
    database = SQLiteDatabase(tmp_path / "control.sqlite3")
    project = SQLiteProjectStore(database, tmp_path / "objects")
    project.bootstrap()
    yield ApparatusHistoryStore(database, catalog_id="catalog", objects=project.objects)
    database.close()


def _create(
    history: ApparatusHistoryStore, object_id: str = "L3"
) -> ApparatusObjectRevision:
    return history.create_object(
        ApparatusObjectCreate(
            catalog_id="catalog",
            object_id=object_id,
            actor="operator",
            content=ApparatusObjectContent(
                name="Fridge input", kind="line", aliases=("old_100%",)
            ),
        )
    )


def test_revision_history_exact_references_and_literal_alias_search(
    history: ApparatusHistoryStore,
) -> None:
    original = _create(history)
    renamed = history.revise_object(
        ApparatusObjectRevise(
            expected=original.ref,
            actor="operator",
            content=ApparatusObjectContent(
                name="Drive input", kind="line", aliases=("old_100%",)
            ),
        )
    )
    assert history.get_object("L3") == renamed
    assert history.resolve_object(original.ref) == original
    assert history.get_object("L3", revision=1) == original
    assert history.list_objects(query="100%").items == (renamed,)
    assert history.list_objects(query="100_").items == ()
    assert history.list_objects(query="drive").items == (renamed,)
    assert history.list_objects(query="L3").items == (renamed,)
    second = _create(history, "L4")
    page = history.list_objects(limit=1)
    assert page.items == (second,)
    assert page.next_cursor is not None
    assert history.list_objects(limit=1, before=page.next_cursor).items == (renamed,)
    with pytest.raises(BackendConflict, match="changed"):
        history.revise_object(
            ApparatusObjectRevise(
                expected=original.ref, actor="operator", content=original.content
            )
        )
    with pytest.raises(BackendConflict, match="another catalog"):
        history.resolve_object(
            original.ref.model_copy(update={"catalog_id": "foreign"})
        )
    with pytest.raises(BackendConflict, match="does not match"):
        history.resolve_object(
            original.ref.model_copy(update={"content_hash": "sha256:" + "0" * 64})
        )


def test_observations_keep_conditions_original_revision_and_superseded_history(
    history: ApparatusHistoryStore,
) -> None:
    original = _create(history)
    warm = history.record_observation(
        ApparatusObservationCreate(
            observation_id="room-temp",
            draft=ApparatusObservationDraft(
                subject=original.ref,
                title="Room temperature transmission",
                actor="operator",
                conditions={
                    "temperature": "293 K",
                    "connection": "handwritten description",
                },
            ),
        )
    )
    renamed = history.revise_object(
        ApparatusObjectRevise(
            expected=original.ref,
            actor="operator",
            content=original.content.model_copy(update={"name": "Cold line"}),
        )
    )
    cold_command = ApparatusObservationCreate(
        observation_id="cold",
        draft=ApparatusObservationDraft(
            subject=renamed.ref,
            title="Low temperature transmission",
            actor="operator",
            conditions={"temperature": "unknown"},
            supersedes=warm.id,
        ),
    )
    cold = history.record_observation(cold_command)
    assert history.record_observation(cold_command) == cold
    assert history.get_observation(warm.id) == warm
    assert history.observations("L3").items == (cold, warm)
    page = history.observations("L3", limit=1)
    assert page.next_cursor is not None
    assert history.observations("L3", limit=1, before=page.next_cursor).items == (warm,)
    with pytest.raises(BackendConflict, match="different content"):
        history.record_observation(
            cold_command.model_copy(
                update={"draft": cold.draft.model_copy(update={"note": "different"})}
            )
        )
    other = _create(history, "L4")
    with pytest.raises(BackendConflict, match="another object"):
        history.record_observation(
            ApparatusObservationCreate(
                observation_id="bad",
                draft=cold.draft.model_copy(update={"subject": other.ref}),
            )
        )


def test_owned_attachments_preserve_bytes_and_restrict_lookup(
    history: ApparatusHistoryStore,
) -> None:
    subject = _create(history).ref
    content = b"original measured data\x00\xff\n"
    attachment = history.import_attachment(content, "../../notes.bin")
    # Filename is only a retained label, even when it resembles a local path.
    command = ApparatusObservationCreate(
        observation_id="measurement",
        draft=ApparatusObservationDraft(
            subject=subject,
            title="Original",
            actor="operator",
            attachments=(attachment,),
        ),
    )
    history.record_observation(command)
    assert (
        history.attachment_content(command.observation_id, attachment.content_hash)
        == content
    )
    unrelated = history.import_attachment(b"unrelated", "unrelated.txt")
    with pytest.raises(BackendNotFound, match="not referenced"):
        history.attachment_content(command.observation_id, unrelated.content_hash)
    with pytest.raises(BackendConflict, match="size"):
        history.record_observation(
            ApparatusObservationCreate(
                observation_id="wrong-size",
                draft=command.draft.model_copy(
                    update={
                        "attachments": (
                            attachment.model_copy(update={"size_bytes": 1}),
                        )
                    }
                ),
            )
        )
    history.objects.path_for(attachment.content_hash).write_bytes(b"x" * len(content))
    with pytest.raises(BackendConflict, match="corrupt"):
        history.attachment_content(command.observation_id, attachment.content_hash)
    with pytest.raises(BackendConflict, match="corrupt"):
        history.record_observation(
            command.model_copy(update={"observation_id": "corrupt"})
        )


def test_run_links_require_local_retained_runs_and_support_filtering(
    history: ApparatusHistoryStore,
) -> None:
    subject = _create(history).ref
    command = ApparatusObservationCreate(
        observation_id="linked",
        draft=ApparatusObservationDraft(
            subject=subject, title="Run link", actor="operator", run_ids=("run-1",)
        ),
    )
    with pytest.raises(BackendNotFound, match="run-1"):
        history.record_observation(command)
    with history.database.write_transaction() as connection:
        connection.execute(
            "INSERT INTO runs(run_id,created_at,config_content_hash) VALUES (?,?,?)",
            ("run-1", "2026-09-19T00:00:00Z", "sha256:" + "0" * 64),
        )
    value = history.record_observation(command)
    assert history.observations("L3", run_id="run-1").items == (value,)
    assert history.observations("L3", run_id="run-2").items == ()
    assert history.record_observation(command) == value


def test_attachment_verification_bounds_read_before_size_validation(
    history: ApparatusHistoryStore,
) -> None:
    subject = _create(history).ref
    attachment = history.import_attachment(b"actual bytes are longer", "data.bin")
    declared = attachment.model_copy(update={"size_bytes": 3})
    command = ApparatusObservationCreate(
        observation_id="oversized",
        draft=ApparatusObservationDraft(
            subject=subject,
            title="Oversized reference",
            actor="operator",
            attachments=(declared,),
        ),
    )
    with history.objects.path_for(attachment.content_hash).open("rb") as source:
        reader = MagicMock(wraps=source)
        reader.__enter__.return_value = reader
        with (
            patch.object(Path, "open", return_value=reader),
            pytest.raises(BackendConflict, match="size"),
        ):
            history.record_observation(command)
        reader.read.assert_called_once_with(declared.size_bytes + 1)
    with pytest.raises(BackendNotFound):
        history.get_observation(command.observation_id)
    history.objects.path_for(attachment.content_hash).unlink()
    with pytest.raises(BackendConflict, match="missing or corrupt"):
        history.record_observation(command)
