from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import BaseModel
from scopecat.kernel.errors import (
    CheckFailed,
    Conflict,
    DataIntegrityError,
    NotFound,
    StorageError,
)
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.content import BytesWrite, ContentEntry, ModelWrite
from scopecat.records.run import (
    ConfigRegistryRunConfigSource,
    RunConfigSource,
    RunSnapshot,
)
from scopecat.records.run_request import RunRequest
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.runs.admission import RunSkeleton
from scopecat.runs.refs import CONFIG_PROFILE_SNAPSHOT_REF, SCIENTIFIC_BINDING_REF
from scopecat.runs.repository import (
    RunContentPublication,
    TerminalRunCommit,
)
from scopecat_testkit.authoring import load_config
from scopecat_testkit.server.runtime import SQLiteTestRunRepository

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


class _Record(BaseModel):
    value: str


class _PortableRecord(BaseModel):
    message: str
    value: float = 0.0


def _repository(root: Path) -> SQLiteTestRunRepository:
    sqlite = SQLiteDatabase(root / "control.sqlite3")
    SQLiteProjectStore(
        sqlite,
        root / "objects",
    ).bootstrap()
    repository = SQLiteTestRunRepository(
        sqlite,
        root / "objects",
    )
    return repository


def _snapshot(run_id: str) -> RunSnapshot:
    return RunSnapshot(
        scientific_binding=ResolvedScientificBinding(
            subject=UnboundSubject(),
            config_content_hash=f"sha256:{'0' * 64}",
            setup_content_hash="sha256:" + "0" * 64,
        ),
        run_id=run_id,
        created_at=datetime(2026, 7, 23, tzinfo=UTC),
        config_content_hash=f"sha256:{'0' * 64}",
    )


def _outcome(run_id: str) -> RunOutcome:
    return RunOutcome(
        run_id=run_id,
        result="succeeded",
        certainty="known",
    )


def _content(content_id: str) -> ContentEntry:
    return ContentEntry(
        role="artifact",
        id=content_id,
        kind="test",
        content_hash=f"{content_id}-content",
    )


def _terminal_commit(run_id: str, content_id: str) -> TerminalRunCommit:
    return TerminalRunCommit(
        run_id=run_id,
        outcome=_outcome(run_id),
        contents=(_content(content_id),),
        models=(
            ModelWrite(
                ref=f"records/{content_id}.json",
                value=_Record(value=content_id),
            ),
        ),
    )


def _object_files(repository: SQLiteRunRepository) -> set[Path]:
    return {
        path
        for path in repository.objects.root.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    }


def _portable_snapshot(run_id: str, day: int) -> RunSnapshot:
    return RunSnapshot(
        scientific_binding=ResolvedScientificBinding(
            subject=UnboundSubject(),
            config_content_hash="sha256:" + "0" * 64,
            setup_content_hash="sha256:" + "0" * 64,
        ),
        run_id=run_id,
        created_at=datetime(2026, 1, day, tzinfo=UTC),
        config_content_hash="sha256:" + "0" * 64,
        outcome=RunOutcome(
            run_id=run_id,
            result="succeeded",
            certainty="known",
        ),
    )


def _config_source(content_hash: str) -> RunConfigSource:
    return ConfigRegistryRunConfigSource(
        selector="active",
        entry_id="config-entry",
        config_ref="configs/config-entry.json",
        content_hash=content_hash,
        registry_generation=1,
    )


def _structured_run_inputs(
    run_id: str,
    *,
    config: ConfigProfileSnapshot | None = None,
    with_source: bool = True,
) -> RunSkeleton:
    selected_config = load_config() if config is None else config
    content_hash = config_content_hash(selected_config)
    return RunSkeleton(
        snapshot=RunSnapshot(
            scientific_binding=ResolvedScientificBinding(
                subject=UnboundSubject(),
                config_content_hash=content_hash,
                setup_content_hash="sha256:" + "0" * 64,
            ),
            run_id=run_id,
            config_content_hash=content_hash,
            config_source=_config_source(content_hash) if with_source else None,
        ),
        request=RunRequest(experiment_id=f"scratch:{run_id}"),
        config=selected_config,
    )


def test_round_trips_all_portable_content(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    run_id = "run-repository-contract"
    snapshot = _portable_snapshot(run_id, 1)
    record = _PortableRecord(message="record", value=float("inf"))

    repository.write_snapshot(snapshot)
    repository.write_model(run_id, "records/model.json", record)
    repository.write_text(run_id, "artifacts/note.txt", "note")
    repository.write_bytes(run_id, "artifacts/blob.bin", b"\x00\xff")

    assert repository.read_snapshot(run_id) == snapshot
    assert (
        repository.read_model(run_id, "records/model.json", _PortableRecord) == record
    )
    assert repository.read_text(run_id, "artifacts/note.txt") == "note\n"
    assert repository.read_bytes(run_id, "artifacts/blob.bin") == b"\x00\xff"
    assert repository.exists(run_id, "records/model.json")
    assert not repository.exists(run_id, "records/missing.json")


def test_lists_runs_by_creation_time(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.write_snapshot(_portable_snapshot("run-later", 2))
    repository.write_snapshot(_portable_snapshot("run-earlier", 1))

    assert [snapshot.run_id for snapshot in repository.list_runs()] == [
        "run-earlier",
        "run-later",
    ]


def test_run_projection_is_relational_without_a_manifest_object(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    run_id = "run-relational-projection"
    snapshot = _portable_snapshot(run_id, 1)
    report = _content("report")

    repository.write_snapshot(snapshot)
    repository.publish_content(RunContentPublication(run_id=run_id, entries=(report,)))

    with sqlite3.connect(repository.database) as connection:
        assert connection.execute(
            "SELECT config_content_hash FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone() == (snapshot.config_content_hash,)
        assert connection.execute(
            "SELECT result, certainty FROM run_outcomes WHERE run_id = ?",
            (run_id,),
        ).fetchone() == ("succeeded", "known")
        assert connection.execute(
            "SELECT role, content_id, kind FROM run_contents WHERE run_id = ?",
            (run_id,),
        ).fetchone() == ("artifact", "report", "test")
        assert connection.execute(
            """
            SELECT COUNT(*) FROM run_repository_refs
            WHERE run_id = ? AND ref = 'manifest.json'
            """,
            (run_id,),
        ).fetchone() == (0,)
    assert (
        repository.read_model(run_id, SCIENTIFIC_BINDING_REF, ResolvedScientificBinding)
        == snapshot.scientific_binding
    )
    assert repository.read_snapshot(run_id) == snapshot
    assert (
        repository.read_content(
            run_id,
            role="artifact",
            content_id="report",
        )
        == report
    )


@pytest.mark.parametrize(
    ("where_clause", "expected_index"),
    (
        ("run_id = ?", "run_contents_owner_sequence"),
        ("run_id = ? AND role = ?", "run_contents_owner_role_sequence"),
        ("run_id = ? AND kind = ?", "run_contents_owner_kind_sequence"),
        (
            "run_id = ? AND role = ? AND kind = ?",
            "run_contents_owner_role_kind_sequence",
        ),
    ),
)
def test_run_content_pages_use_owner_specific_sequence_indexes(
    tmp_path: Path,
    where_clause: str,
    expected_index: str,
) -> None:
    repository = _repository(tmp_path)
    parameters = ("run", "record", "analysis")[: where_clause.count("?")]
    with sqlite3.connect(repository.database) as connection:
        plan = " ".join(
            row[3]
            for row in connection.execute(
                f"""
                EXPLAIN QUERY PLAN
                SELECT sequence, entry_json
                FROM run_contents
                WHERE {where_clause}
                ORDER BY sequence DESC
                LIMIT 101
                """,  # noqa: S608 - fixed test cases above
                parameters,
            )
        )
    assert expected_index in plan
    assert "TEMP B-TREE" not in plan


def test_run_content_index_supports_exact_and_bounded_reads(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    run_id = "run-content-index"
    repository.write_snapshot(_snapshot(run_id))
    repository.publish_content(
        RunContentPublication(
            run_id=run_id,
            entries=(
                _content("first"),
                _content("second"),
                ContentEntry(
                    role="record",
                    id="analysis-fit",
                    kind="analysis",
                    content_hash="analysis-content",
                ),
            ),
        )
    )

    head = repository.list_contents(run_id, limit=1, role="artifact")
    assert [entry.id for entry in head.items] == ["second"]
    assert head.next_cursor is not None
    tail = repository.list_contents(
        run_id,
        limit=1,
        before=head.next_cursor,
        role="artifact",
    )
    assert [entry.id for entry in tail.items] == ["first"]
    assert tail.next_cursor is None
    assert (
        repository.read_content(
            run_id,
            role="record",
            content_id="analysis-fit",
        ).kind
        == "analysis"
    )


def test_structured_run_inputs_bind_source_and_snapshot_hashes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    skeleton = _structured_run_inputs("run-provenance-round-trip")

    repository.write_run_skeleton(skeleton)
    assert (
        repository.read_config_profile_snapshot(skeleton.snapshot.run_id)
        == skeleton.config
    )


def test_terminal_commit_publishes_outcome_and_content(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    run_id = "run-terminal-commit"
    operator_note = ContentEntry(
        role="artifact",
        id="operator-note",
        kind="attachment",
        content_hash="operator-note-content",
    )
    repository.write_snapshot(
        RunSnapshot(
            scientific_binding=ResolvedScientificBinding(
                subject=UnboundSubject(),
                config_content_hash="sha256:" + "0" * 64,
                setup_content_hash="sha256:" + "0" * 64,
            ),
            run_id=run_id,
            config_content_hash="sha256:" + "0" * 64,
        )
    )
    repository.publish_content(
        RunContentPublication(run_id=run_id, entries=(operator_note,))
    )
    outcome = RunOutcome(
        run_id=run_id,
        result="succeeded",
        certainty="known",
    )
    terminal_evidence = ContentEntry(
        role="record",
        id="terminal-evidence",
        kind="contract_evidence",
        content_hash="terminal-evidence-content",
    )

    committed = repository.commit_terminal(
        TerminalRunCommit(
            run_id=run_id,
            outcome=outcome,
            contents=(terminal_evidence,),
            models=(
                ModelWrite(
                    ref="records/terminal-evidence.json",
                    value=_PortableRecord(message="terminal"),
                ),
            ),
        )
    )

    assert {
        entry.id for entry in repository.list_contents(run_id, limit=100).items
    } == {
        "operator-note",
        "terminal-evidence",
    }
    assert repository.read_snapshot(run_id) == committed
    assert repository.read_model(
        run_id,
        "records/terminal-evidence.json",
        _PortableRecord,
    ) == _PortableRecord(message="terminal")


def test_content_publication_merges_entries_and_refs(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    run_id = "run-content-publication"
    existing = ContentEntry(
        role="artifact",
        id="existing",
        kind="attachment",
        content_hash="existing-content",
    )
    published = ContentEntry(
        role="record",
        id="analysis",
        kind="analysis",
        content_hash="analysis-content",
    )
    repository.write_snapshot(_portable_snapshot(run_id, 1))
    repository.publish_content(
        RunContentPublication(run_id=run_id, entries=(existing,))
    )

    repository.publish_content(
        RunContentPublication(
            run_id=run_id,
            entries=(published,),
            models=(
                ModelWrite(
                    ref="records/analysis.json",
                    value=_PortableRecord(message="analysis"),
                ),
            ),
            bytes=(
                BytesWrite(
                    ref="artifacts/summary.txt",
                    content=b"summary\n",
                ),
            ),
        )
    )

    assert {
        entry.id for entry in repository.list_contents(run_id, limit=100).items
    } == {existing.id, published.id}
    assert repository.read_model(
        run_id,
        "records/analysis.json",
        _PortableRecord,
    ) == _PortableRecord(message="analysis")
    assert repository.read_bytes(run_id, "artifacts/summary.txt") == b"summary\n"


def test_immutable_content_conflict_does_not_partially_publish(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    run_id = "run-content-conflict"
    original = ContentEntry(
        role="record",
        id="proposal",
        kind="parameter_change_proposal",
        content_hash="original-content",
    )
    repository.write_snapshot(_portable_snapshot(run_id, 1))
    repository.publish_content(
        RunContentPublication(
            run_id=run_id,
            entries=(original,),
            models=(
                ModelWrite(
                    ref="records/proposal.json",
                    value=_PortableRecord(message="original"),
                    replace=False,
                ),
            ),
        )
    )
    repository.publish_content(
        RunContentPublication(
            run_id=run_id,
            entries=(original,),
            models=(
                ModelWrite(
                    ref="records/proposal.json",
                    value=_PortableRecord(message="original"),
                    replace=False,
                ),
            ),
        )
    )
    before = (
        repository.read_snapshot(run_id),
        repository.list_contents(run_id, limit=100).items,
    )

    with pytest.raises(Conflict) as captured:
        repository.publish_content(
            RunContentPublication(
                run_id=run_id,
                entries=(
                    ContentEntry(
                        role="record",
                        id="other",
                        kind="analysis",
                        content_hash="other-content",
                    ),
                ),
                models=(
                    ModelWrite(
                        ref="records/proposal.json",
                        value=_PortableRecord(message="different"),
                        replace=False,
                    ),
                ),
                bytes=(
                    BytesWrite(
                        ref="artifacts/should-not-publish.txt",
                        content=b"uncommitted",
                    ),
                ),
            )
        )

    assert captured.value.problems[0].code == "run.ref_conflict"
    assert (
        repository.read_snapshot(run_id),
        repository.list_contents(run_id, limit=100).items,
    ) == before
    assert not repository.exists(run_id, "artifacts/should-not-publish.txt")
    assert repository.read_model(
        run_id,
        "records/proposal.json",
        _PortableRecord,
    ) == _PortableRecord(message="original")


def test_duplicate_immutable_refs_conflict_within_one_publication(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    run_id = "run-content-duplicate-immutable"
    repository.write_snapshot(_portable_snapshot(run_id, 1))
    before = (
        repository.read_snapshot(run_id),
        repository.list_contents(run_id, limit=100).items,
    )

    with pytest.raises(Conflict) as captured:
        repository.publish_content(
            RunContentPublication(
                run_id=run_id,
                entries=(
                    ContentEntry(
                        role="record",
                        id="proposal",
                        kind="parameter_change_proposal",
                        content_hash="second-content",
                    ),
                ),
                models=(
                    ModelWrite(
                        ref="records/proposal.json",
                        value=_PortableRecord(message="first"),
                        replace=False,
                    ),
                    ModelWrite(
                        ref="records/proposal.json",
                        value=_PortableRecord(message="second"),
                        replace=False,
                    ),
                ),
            )
        )

    assert captured.value.problems[0].code == "run.ref_conflict"
    assert (
        repository.read_snapshot(run_id),
        repository.list_contents(run_id, limit=100).items,
    ) == before
    assert not repository.exists(run_id, "records/proposal.json")


def test_structured_run_reads_detect_snapshot_drift(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    skeleton = _structured_run_inputs("run-snapshot-drift")
    repository.write_run_skeleton(skeleton)
    drifted = skeleton.config.model_copy(update={"id": "drifted-config"})
    repository.write_model(
        skeleton.snapshot.run_id,
        CONFIG_PROFILE_SNAPSHOT_REF,
        drifted,
    )

    with pytest.raises(DataIntegrityError) as captured:
        repository.read_config_profile_snapshot(skeleton.snapshot.run_id)
    assert captured.value.problems[0].code == "run.config_provenance_mismatch"


def test_direct_snapshot_run_is_protected_by_its_config_hash(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    skeleton = _structured_run_inputs(
        "run-direct-snapshot",
        with_source=False,
    )
    repository.write_run_skeleton(skeleton)
    repository.write_model(
        skeleton.snapshot.run_id,
        CONFIG_PROFILE_SNAPSHOT_REF,
        skeleton.config.model_copy(update={"id": "drifted-direct-snapshot"}),
    )

    with pytest.raises(DataIntegrityError) as captured:
        repository.read_config_profile_snapshot(skeleton.snapshot.run_id)

    assert captured.value.problems[0].code == "run.config_provenance_mismatch"


def test_config_read_remains_independent_for_capture_runs(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    run_id = "capture-config"
    config = load_config()
    repository.write_snapshot(
        RunSnapshot(
            scientific_binding=ResolvedScientificBinding(
                subject=UnboundSubject(),
                config_content_hash=config_content_hash(config),
                setup_content_hash="sha256:" + "0" * 64,
            ),
            run_id=run_id,
            config_content_hash=config_content_hash(config),
        )
    )
    repository.write_model(run_id, CONFIG_PROFILE_SNAPSHOT_REF, config)

    assert repository.read_config_profile_snapshot(run_id) == config


def test_missing_run_and_ref_have_stable_errors(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(NotFound) as missing_run:
        repository.read_snapshot("run-missing")
    assert missing_run.value.problems[0].code == "run.not_found"

    with pytest.raises(DataIntegrityError) as missing_ref:
        repository.read_text("run-missing", "records/missing.json")
    assert missing_ref.value.problems[0].code == "run.ref_missing"


@pytest.mark.parametrize("ref", ("../outside.json", "/outside.json"))
def test_rejects_refs_outside_run_namespace(tmp_path: Path, ref: str) -> None:
    repository = _repository(tmp_path)

    with pytest.raises(CheckFailed) as captured:
        repository.write_text("run-contract", ref, "escape")
    assert captured.value.problems[0].code == "run.ref_path_escape"


def test_equal_content_reuses_one_immutable_object(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    repository.write_snapshot(_portable_snapshot("run-a", 1))
    repository.write_snapshot(_portable_snapshot("run-b", 1))
    repository.write_bytes("run-a", "artifacts/a.bin", b"same")
    repository.write_bytes("run-b", "artifacts/b.bin", b"same")

    with sqlite3.connect(repository.database) as connection:
        digests = {
            row[0]
            for row in connection.execute(
                "SELECT digest FROM run_repository_refs "
                "WHERE ref LIKE 'artifacts/%' ORDER BY run_id"
            )
        }
    assert len(digests) == 1
    assert len(_object_files(repository)) == 2


def test_cached_measurement_chunks_follow_current_ref_and_owner(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    ref = "data/measurement_dataset/raw-measurements/chunks/0.arrow"
    for run_id in ("run-a", "run-b"):
        repository.write_snapshot(_portable_snapshot(run_id, 1))
        repository.write_bytes(run_id, ref, run_id.encode())
    assert repository.read_bytes("run-a", ref) == b"run-a"
    assert repository.read_bytes("run-b", ref) == b"run-b"
    repository.write_bytes("run-a", ref, b"replacement")
    assert repository.read_bytes("run-a", ref) == b"replacement"
    assert repository.read_bytes("run-b", ref) == b"run-b"
    with sqlite3.connect(repository.database) as connection:
        connection.execute(
            "DELETE FROM run_repository_refs WHERE run_id = ?", ("run-a",)
        )
    with pytest.raises(DataIntegrityError):
        repository.read_bytes("run-a", ref)


def test_run_refs_follow_the_relational_owner_lifecycle(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    run_id = "run-ref-owner"
    repository.write_snapshot(_portable_snapshot(run_id, 1))
    repository.write_bytes(run_id, "artifacts/value.bin", b"value")

    with repository.sqlite.write_transaction() as connection:
        connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        remaining = connection.execute(
            "SELECT COUNT(*) FROM run_repository_refs WHERE run_id = ?",
            (run_id,),
        ).fetchone()

    assert remaining is not None
    assert remaining[0] == 0


def test_terminal_commit_rolls_back_all_refs_if_outcome_publish_fails(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    run_id = "run-rollback"
    repository.write_snapshot(_snapshot(run_id))
    objects_before = _object_files(repository)
    with sqlite3.connect(repository.database) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_terminal_outcome
            BEFORE INSERT ON run_outcomes
            WHEN NEW.run_id = 'run-rollback'
            BEGIN
                SELECT RAISE(ABORT, 'injected failure');
            END
            """
        )

    with pytest.raises(StorageError):
        repository.commit_terminal(
            TerminalRunCommit(
                run_id=run_id,
                outcome=_outcome(run_id),
                models=(
                    ModelWrite(
                        ref="records/terminal-evidence.json",
                        value=_Record(value="terminal"),
                    ),
                ),
            )
        )

    assert repository.read_snapshot(run_id).outcome is None
    assert not repository.exists(run_id, "records/terminal-evidence.json")
    assert len(_object_files(repository)) > len(objects_before)


def test_concurrent_terminal_commits_merge_relational_contents(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    run_id = "run-concurrent-terminal"
    repository.write_snapshot(_snapshot(run_id))
    repository.publish_content(
        RunContentPublication(run_id=run_id, entries=(_content("existing"),))
    )
    start = Barrier(2)

    def publish(content_id: str) -> RunSnapshot:
        start.wait(timeout=5)
        return repository.commit_terminal(_terminal_commit(run_id, content_id))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (
            pool.submit(publish, "first"),
            pool.submit(publish, "second"),
        )
        for future in futures:
            future.result()

    peer = SQLiteRunRepository(repository.sqlite, repository.objects.root)
    assert {entry.id for entry in peer.list_contents(run_id, limit=100).items} == {
        "existing",
        "first",
        "second",
    }


def test_terminal_commit_primitive_uses_and_leaves_the_callers_transaction(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    run_id = "run-terminal-transaction"
    repository.write_snapshot(_snapshot(run_id))
    repository.publish_content(
        RunContentPublication(run_id=run_id, entries=(_content("existing"),))
    )
    objects_before = _object_files(repository)
    prepared_first = repository.prepare_terminal_commit(
        _terminal_commit(run_id, "first")
    )
    prepared_second = repository.prepare_terminal_commit(
        _terminal_commit(run_id, "second")
    )

    with sqlite3.connect(
        repository.database,
        isolation_level=None,
    ) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        repository.commit_prepared_terminal_in_transaction(
            connection,
            prepared_first,
        )
        repository.commit_prepared_terminal_in_transaction(
            connection,
            prepared_second,
        )

        assert connection.in_transaction
        assert {
            row[0]
            for row in connection.execute(
                "SELECT content_id FROM run_contents WHERE run_id = ?",
                (run_id,),
            )
        } == {
            "existing",
            "first",
            "second",
        }
        assert (
            connection.execute(
                """
            SELECT COUNT(*) FROM run_repository_refs
            WHERE run_id = ? AND ref IN (?, ?)
            """,
                (
                    run_id,
                    "records/first.json",
                    "records/second.json",
                ),
            ).fetchone()[0]
            == 2
        )
        connection.rollback()

    assert repository.read_snapshot(run_id).outcome is None
    assert not repository.exists(run_id, "records/first.json")
    assert not repository.exists(run_id, "records/second.json")
    assert len(_object_files(repository)) > len(objects_before)


def test_prepared_content_uses_and_leaves_the_callers_transaction(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    run_id = "run-prepared-content"
    repository.write_snapshot(_snapshot(run_id))
    objects_before = _object_files(repository)
    publication = RunContentPublication(
        run_id=run_id,
        entries=(_content("prepared"),),
        bytes=(
            BytesWrite(
                ref="artifacts/prepared.bin",
                content=b"prepared",
            ),
        ),
    )

    prepared = repository.prepare_content_publication(publication)

    assert len(_object_files(repository)) > len(objects_before)
    assert not repository.exists(run_id, "artifacts/prepared.bin")
    with sqlite3.connect(
        repository.database,
        isolation_level=None,
    ) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        repository.publish_prepared_content_in_transaction(
            connection,
            prepared,
        )

        assert connection.in_transaction
        row = connection.execute(
            """
            SELECT content_id FROM run_contents
            WHERE run_id = ? AND role = 'artifact'
            """,
            (run_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "prepared"
        assert (
            connection.execute(
                """
                SELECT COUNT(*) FROM run_repository_refs
                WHERE run_id = ? AND ref = ?
                """,
                (run_id, "artifacts/prepared.bin"),
            ).fetchone()[0]
            == 1
        )
        connection.rollback()

    assert repository.list_contents(run_id, limit=100).items == ()
    assert not repository.exists(run_id, "artifacts/prepared.bin")


def test_content_publications_merge_relational_rows_across_writers(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    run_id = "run-concurrent-content"
    repository.write_snapshot(_snapshot(run_id))
    repository.publish_content(
        RunContentPublication(run_id=run_id, entries=(_content("existing"),))
    )
    peer = SQLiteRunRepository(repository.sqlite, repository.objects.root)
    ready = Barrier(2)

    def publish(selected: SQLiteRunRepository, content_id: str) -> None:
        prepared = selected.prepare_content_publication(
            RunContentPublication(
                run_id=run_id,
                entries=(_content(content_id),),
                bytes=(
                    BytesWrite(
                        ref=f"artifacts/{content_id}.bin",
                        content=content_id.encode(),
                    ),
                ),
            )
        )
        ready.wait(timeout=5)
        with sqlite3.connect(
            selected.database,
            isolation_level=None,
            timeout=5,
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            selected.publish_prepared_content_in_transaction(connection, prepared)
            connection.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (
            pool.submit(publish, repository, "first"),
            pool.submit(publish, peer, "second"),
        )
        for future in futures:
            future.result()

    assert {
        entry.id for entry in repository.list_contents(run_id, limit=100).items
    } == {
        "existing",
        "first",
        "second",
    }


def test_corrupt_indexed_object_is_data_integrity_failure(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.write_snapshot(_portable_snapshot("run-corrupt", 1))
    repository.write_bytes("run-corrupt", "artifacts/value.bin", b"original")
    with sqlite3.connect(repository.database) as connection:
        row = connection.execute(
            """
            SELECT digest FROM run_repository_refs
            WHERE run_id = 'run-corrupt' AND ref = 'artifacts/value.bin'
            """
        ).fetchone()
    assert row is not None
    repository.objects.path_for(row[0]).write_bytes(b"corrupt")

    with pytest.raises(DataIntegrityError) as captured:
        repository.read_bytes("run-corrupt", "artifacts/value.bin")

    assert captured.value.problems[0].code == "run.ref_invalid"
