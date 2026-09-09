"""Preview fences use physical resources and the existing manual-event order."""

from datetime import timedelta
from pathlib import Path

import pytest
from scopecat.control.models import DurableEventInput
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.manual_preview import ManualPreviewBinding, PreviewInstrument

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.manual_preview import (
    ManualPreviewChanged,
    ManualPreviewRepository,
)
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def test_manual_mutations_invalidate_only_the_compiled_physical_footprint(
    tmp_path: Path,
) -> None:
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "store.sqlite3"), tmp_path / "objects"
    )
    store.bootstrap()
    try:
        repository = ManualPreviewRepository(store.sqlite)
        control = SQLiteControlPlane(store.sqlite)
        cursor = repository.cursor()
        binding = ManualPreviewBinding(
            request_hash="sha256:" + "1" * 64, config_source_hash="sha256:" + "2" * 64
        )
        target = PreviewInstrument(
            instrument_id="compiled-q0", exclusivity_key="physical-a"
        )
        # Keep the pre-compilation cursor when recording the finished preview.
        unrelated = control.open_instrument_session(
            operation_id="unrelated",
            actor="operator",
            config_entry_id="config",
            config_content_hash="hash",
            instrument_ids=("other",),
            exclusivity_keys=("physical-b",),
            ttl=timedelta(minutes=1),
            expected_config_generation=0,
        )
        control.start_instrument_operation(
            unrelated.session_id,
            instrument_id="other",
            operation_id="other-apply",
            kind="apply",
        )
        fence = repository.record(cursor=cursor, binding=binding, instruments=(target,))
        assert repository.validity(fence).valid
        alias = control.open_instrument_session(
            operation_id="alias",
            actor="operator",
            config_entry_id="config",
            config_content_hash="hash",
            instrument_ids=("manual-alias",),
            exclusivity_keys=("physical-a",),
            ttl=timedelta(minutes=1),
            expected_config_generation=0,
        )
        # Ownership/connection and read-only queries are not mutation facts.
        assert repository.validity(fence).valid
        control.start_instrument_operation(
            alias.session_id,
            instrument_id="manual-alias",
            operation_id="apply",
            kind="apply",
        )
        control.finish_instrument_operation(
            alias.session_id,
            instrument_id="manual-alias",
            operation_id="apply",
            kind="apply",
            status="rejected",
        )
        recorded_after_mutation = repository.record(
            cursor=cursor, binding=binding, instruments=(target,)
        )
        assert not repository.validity(recorded_after_mutation).valid
        validity = repository.validity(fence)
        assert not validity.valid
        assert len(validity.changes) == 1
        assert validity.changes[0].instrument_ids == ("compiled-q0",)
        assert "partial or failed" in validity.changes[0].reason
        with (
            store.sqlite.write_transaction() as connection,
            pytest.raises(ManualPreviewChanged, match="compiled-q0"),
        ):
            repository.require_valid_in_transaction(connection, fence)
        newer = repository.record(
            cursor=repository.cursor(), binding=binding, instruments=(target,)
        )
        assert repository.validity(newer).valid
        analytic = repository.record(cursor=cursor, binding=binding, instruments=())
        assert repository.validity(analytic).valid
        with pytest.raises(ManualPreviewChanged, match="binding"):
            repository.validity(
                newer.model_copy(
                    update={
                        "binding": binding.model_copy(
                            update={
                                "code_revision": AuthorRevisionRef(
                                    content_hash="sha256:" + "3" * 64
                                )
                            }
                        )
                    }
                )
            )
        with store.sqlite.write_transaction() as connection:
            control.append_event_in_transaction(
                connection,
                DurableEventInput(
                    kind="instrument_connection_release_started",
                    payload={
                        "operation_id": "release",
                        "exclusivity_keys": ["physical-a"],
                    },
                ),
            )
        assert not repository.validity(newer).valid
        before_abort = repository.record(
            cursor=repository.cursor(), binding=binding, instruments=(target,)
        )
        with store.sqlite.write_transaction() as connection:
            control.append_event_in_transaction(
                connection,
                DurableEventInput(
                    kind="instrument_session_abort_started",
                    payload={
                        "operation_id": "abort",
                        "exclusivity_keys": ["physical-a"],
                    },
                ),
            )
        abort_changes = repository.validity(before_abort)
        assert not abort_changes.valid
        assert abort_changes.changes[0].action == "abort"
    finally:
        store.close()


def test_admission_checks_manual_events_after_exact_request_key_replay(
    tmp_path: Path,
) -> None:
    from scopecat.automation import ProcedureDefinitionRef, ProcedureSubmitCommand

    from scopecat_server import BackendConflict
    from scopecat_server.services.automation import AutomationService
    from scopecat_server.storage.sqlite.automation import SQLiteAutomationStore
    from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository

    sqlite = SQLiteDatabase(tmp_path / "store.sqlite3")
    store = SQLiteProjectStore(sqlite, tmp_path / "objects")
    store.bootstrap()
    try:
        repository = ManualPreviewRepository(sqlite)
        service = AutomationService(
            SQLiteAutomationStore(sqlite),
            runs=SQLiteRunRepository(sqlite, tmp_path / "objects"),
        )
        binding = ManualPreviewBinding(
            request_hash="sha256:" + "1" * 64, config_source_hash="sha256:" + "2" * 64
        )
        fence = repository.record(
            cursor=repository.cursor(),
            binding=binding,
            instruments=(
                PreviewInstrument(instrument_id="q0", exclusivity_key="physical-a"),
            ),
        )
        command = ProcedureSubmitCommand(
            request_key="original",
            definition=ProcedureDefinitionRef(
                id="manual-probe", version="1", fingerprint="sha256:" + "4" * 64
            ),
            intent={"manual_state": fence.model_dump(mode="json")},
            expected_manual_preview=fence,
        )
        first = service.submit(command)
        with sqlite.write_transaction() as connection:
            SQLiteControlPlane(sqlite).append_event_in_transaction(
                connection,
                DurableEventInput(
                    kind="instrument_connection_release_started",
                    payload={
                        "operation_id": "release",
                        "exclusivity_keys": ["physical-a"],
                    },
                ),
            )
        assert service.submit(command) == first
        with pytest.raises(BackendConflict, match="Manual operation changed q0"):
            service.submit(command.model_copy(update={"request_key": "new"}))
    finally:
        store.close()
