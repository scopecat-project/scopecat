"""Line lists reuse plan revisions and preserve identity across ordinary reopen."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from scopecat.automation.models import ProcedureDefinitionRef, ProcedureSource
from scopecat.kernel.problems import ProblemPhase, problem
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.experiment_plan import ExperimentPlanDefinition
from scopecat.records.manual_line import ManualLineSelection, PhysicalLine
from scopecat.records.manual_line_scan import ManualLineLabels, ManualLineScanCreate
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.practice import PracticeScope
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.records.scientific_selection import (
    ParameterConfiguration,
    ScientificSelection,
)
from scopecat.records.setup import SetupRevisionRef

from scopecat_server.errors import BackendConflict
from scopecat_server.request_workspace import request_workspace
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.manual_lines import (
    ManualLineStore,
    unresolved_line_result,
)
from scopecat_server.storage.sqlite.practice import PracticeOwnership
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


@pytest.fixture(params=[False, True])
def practice(request: pytest.FixtureRequest) -> Iterator[bool]:
    token = request_workspace.set("source")
    try:
        yield bool(request.param)
    finally:
        request_workspace.reset(token)


def test_line_catalog_reopens_and_renames_without_rewriting_original_identity(
    tmp_path: Path,
    practice: bool,
) -> None:
    store = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "db.sqlite3"), tmp_path / "objects"
    )
    store.bootstrap()
    if practice:
        with store.sqlite.write_transaction() as connection:
            owners = PracticeOwnership(connection)
            owners.save(
                PracticeScope(
                    id="course",
                    title="Course",
                    lesson="parameters",
                    directory="course",
                    kind="notebook",
                    workspace_id="source",
                )
            )
            owners.claim("course", "source", "source")
            owners.claim("course", "parameters", "inputs")
            owners.claim("course", "setup", "setup")
    lines = ManualLineStore(store)
    setup = SetupRevisionRef(revision_id="setup", content_hash="sha256:" + "a" * 64)
    binding = ResolvedScientificBinding(
        subject=UnboundSubject(),
        config_content_hash="sha256:" + "b" * 64,
        setup_content_hash=setup.content_hash,
    )
    definition = ExperimentPlanDefinition(
        experiment="signal",
        version="sha256:" + "c" * 64,
        definition_hash="sha256:" + "d" * 64,
        workspace_id="source",
        code_revision=AuthorRevisionRef(content_hash="sha256:" + "e" * 64),
        selection=ScientificSelection(
            configuration=ParameterConfiguration(
                ref=ParameterRevisionRef(
                    revision_id="inputs", content_hash="sha256:" + "f" * 64
                ),
                setup=setup,
            )
        ),
        scientific_binding=binding,
    )
    command = ManualLineScanCreate(
        scan_id="lines_" + "1" * 32,
        saved_by="operator",
        name="S21 lines",
        template=definition,
        lines=(
            PhysicalLine(line_id="line_" + "2" * 32, label="A"),
            PhysicalLine(line_id="line_" + "3" * 32, label="B"),
        ),
    )
    original = lines.create(command)
    assert lines.create(command) == original
    renamed = lines.labels(
        original.scan_id,
        ManualLineLabels(
            expected_revision=1,
            name=original.name,
            saved_by="editor",
            lines=(
                original.lines[0].model_copy(update={"label": "Readout A"}),
                original.lines[1],
            ),
        ),
    )
    assert renamed.revision == 2
    assert lines.list() == (renamed,)
    if practice:
        token = request_workspace.set(None)
        try:
            assert lines.list() == ()
            with pytest.raises(BackendConflict, match="another practice"):
                lines.get(original.scan_id)
            with pytest.raises(BackendConflict, match="another practice"):
                lines.create(command)
        finally:
            request_workspace.reset(token)
    assert lines.create(command) == original
    with pytest.raises(BackendConflict, match="changed"):
        lines.labels(
            original.scan_id,
            ManualLineLabels(
                expected_revision=1,
                name=original.name,
                saved_by="editor",
                lines=original.lines,
            ),
        )
    with pytest.raises(BackendConflict, match="different content"):
        lines.create(command.model_copy(update={"name": "different"}))
    store.close()
    reopened = SQLiteProjectStore(
        SQLiteDatabase(tmp_path / "db.sqlite3"), tmp_path / "objects"
    )
    reopened.bootstrap()
    try:
        restored = ManualLineStore(reopened)
        assert restored.get(original.scan_id) == renamed
        selection = ManualLineSelection(
            scan_id=renamed.scan_id,
            line_id=renamed.lines[0].line_id,
            label=renamed.lines[0].label,
        )
        assert definition.code_revision is not None
        source = ProcedureSource(
            workspace_id=definition.workspace_id, code_revision=definition.code_revision
        )
        procedure = ProcedureDefinitionRef(
            id="scopecat.author:signal", version="1", fingerprint=definition.version
        )
        with reopened.sqlite.read_connection() as connection:
            # Ordinary parameter changes do not alter the scientific scope.
            restored.require_admission(
                connection,
                definition=procedure,
                intent={"manual_line": selection.model_dump(mode="json")},
                source=source,
                binding=binding.model_copy(
                    update={"config_content_hash": "sha256:" + "4" * 64}
                ),
            )
            with pytest.raises(BackendConflict, match="subject, target or setup"):
                restored.require_admission(
                    connection,
                    definition=procedure,
                    intent={"manual_line": selection.model_dump(mode="json")},
                    source=source,
                    binding=binding.model_copy(
                        update={"setup_content_hash": "sha256:" + "5" * 64}
                    ),
                )
            with pytest.raises(BackendConflict, match="saved order"):
                restored.require_admission(
                    connection,
                    definition=procedure,
                    intent={
                        "manual_line": selection.model_copy(
                            update={"line_id": renamed.lines[1].line_id, "label": "B"}
                        ).model_dump(mode="json")
                    },
                    source=source,
                    binding=binding,
                )
    finally:
        reopened.close()


def test_unknown_history_needs_existing_explicit_reconciliation_even_when_closed() -> (
    None
):
    unknown = RunOutcome(
        run_id="run",
        result="failed",
        certainty="indeterminate",
        problems=(
            problem("test.unknown", "Unknown effect", phase=ProblemPhase.EXECUTION),
        ),
    )
    assert unresolved_line_result("attention_required", unknown)
    assert unresolved_line_result("closed", unknown)
    reconciled = unknown.model_copy(
        update={
            "problems": (
                problem(
                    "daemon.executor_loss_reconciled",
                    "operator reconciled external state after executor loss",
                    phase=ProblemPhase.EXECUTION,
                ),
            )
        }
    )
    assert unresolved_line_result("attention_required", reconciled)
    assert not unresolved_line_result("closed", reconciled)
    assert reconciled.certainty == "indeterminate"
