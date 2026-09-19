"""Relational terminal snapshot and content publication behavior."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from scopecat.execution.evidence import (
    build_terminal_contents,
    instrument_state_evidence_ref,
)
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.kernel.state import StateValue
from scopecat.records.content import ContentEntry
from scopecat.records.execution import (
    InstrumentFinalizationActionEvidence,
    InstrumentStateActionEvidence,
    InstrumentStateActionEvidenceLog,
    InstrumentStateEvidence,
    summarize_instrument_state_evidence,
)
from scopecat.records.instrument import (
    InstrumentStateSetting,
    InstrumentStateSnapshot,
    state_member_target,
    state_observation,
)
from scopecat.records.measurement import (
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementPointCloudPointDomain,
)
from scopecat.records.run import RunSnapshot
from scopecat.records.scientific_binding import (
    ResolvedScientificBinding,
    UnboundSubject,
)
from scopecat.runs.repository import (
    RunContentPublication,
    TerminalRunCommit,
)
from scopecat.sdk.instruments import PropertyRef
from scopecat_testkit.server.runtime import sqlite_run_repository

_CONFIG_HASH = "sha256:" + "0" * 64


def test_run_snapshot_is_immutable() -> None:
    snapshot = RunSnapshot(
        scientific_binding=ResolvedScientificBinding(
            subject=UnboundSubject(),
            config_content_hash=_CONFIG_HASH,
            setup_content_hash="sha256:" + "0" * 64,
        ),
        run_id="run-immutable",
        config_content_hash=_CONFIG_HASH,
    )

    assert snapshot.status == "planned"
    with pytest.raises(ValidationError):
        snapshot.__setattr__("run_id", "changed")


def _successful_outcome(run_id: str) -> RunOutcome:
    return RunOutcome(
        run_id=run_id,
        result="succeeded",
        certainty="known",
    )


def test_terminal_outcome_and_content_are_relational(tmp_path: Path) -> None:
    run_id = "run-domain"
    outcome = _successful_outcome(run_id)
    contents = build_terminal_contents(
        outcome=outcome,
        measurement_count=0,
        dataset_content_hash=None,
        dataset_schema=None,
        expected_record_count=None,
        instrument_state=None,
    )
    storage = sqlite_run_repository(tmp_path)
    accepted = RunSnapshot(
        scientific_binding=ResolvedScientificBinding(
            subject=UnboundSubject(),
            config_content_hash=_CONFIG_HASH,
            setup_content_hash="sha256:" + "0" * 64,
        ),
        run_id=run_id,
        config_content_hash=_CONFIG_HASH,
    )
    storage.write_snapshot(accepted)

    committed = storage.commit_terminal(
        TerminalRunCommit(
            run_id=run_id,
            outcome=outcome,
            contents=contents,
        )
    )

    assert committed.outcome == outcome
    assert committed.status == "completed"
    assert committed.created_at == accepted.created_at
    assert not storage.exists(run_id, instrument_state_evidence_ref())
    assert storage.read_snapshot(run_id) == committed
    assert storage.list_contents(run_id, limit=100).items == contents


def test_terminal_contents_publish_a_sealed_empty_measurement_dataset() -> None:
    outcome = _successful_outcome("run-empty-dataset")
    schema = MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementPointCloudPointDomain(columns=()),
        dimensions=[MeasurementDimension(id="point", kind="point", size=0)],
    )

    contents = build_terminal_contents(
        outcome=outcome,
        measurement_count=0,
        dataset_content_hash="sha256:empty-dataset",
        dataset_schema=schema,
        expected_record_count=0,
        instrument_state=None,
    )

    [dataset] = contents
    assert dataset.role == "dataset"
    assert dataset.id == "raw-measurements"
    assert dataset.data_schema == schema.model_dump(mode="json")


def test_terminal_commit_preserves_existing_content(tmp_path: Path) -> None:
    run_id = "run-existing-attachment"
    outcome = _successful_outcome(run_id)
    storage = sqlite_run_repository(tmp_path)
    storage.write_snapshot(
        RunSnapshot(
            scientific_binding=ResolvedScientificBinding(
                subject=UnboundSubject(),
                config_content_hash=_CONFIG_HASH,
                setup_content_hash="sha256:" + "0" * 64,
            ),
            run_id=run_id,
            config_content_hash=_CONFIG_HASH,
        )
    )
    attachment = ContentEntry(
        role="artifact",
        id="operator-note",
        kind="attachment",
        content_hash="operator-note-content",
        media_type="text/plain",
    )
    workflow_record = ContentEntry(
        role="record",
        id="approval",
        kind="workflow_decision",
        content_hash="approval-content",
    )
    storage.publish_content(
        RunContentPublication(
            run_id=run_id,
            entries=(attachment, workflow_record),
        )
    )
    committed = storage.commit_terminal(
        TerminalRunCommit(run_id=run_id, outcome=outcome)
    )

    assert (
        storage.read_content(
            run_id,
            role="artifact",
            content_id=attachment.id,
        )
        == attachment
    )
    assert (
        storage.read_content(
            run_id,
            role="record",
            content_id=workflow_record.id,
        )
        == workflow_record
    )
    assert storage.read_snapshot(run_id) == committed


def test_terminal_contents_index_supplied_instrument_state() -> None:
    outcome = _successful_outcome("run-instrument")
    observed = _instrument_state("scope", output=False, scale=1.0)
    baseline = _instrument_state("scope", output=True, scale=1.0)
    final = _instrument_state("scope", output=True, scale=2.0)
    instrument_state = InstrumentStateEvidence(
        run_id=outcome.run_id,
        observed_state=[observed],
        baseline_state=[baseline],
        final_state=[final],
        state_actions=InstrumentStateActionEvidenceLog(
            total_count=2,
            retained_prefix=(
                InstrumentStateActionEvidence(
                    operation_id="point-0.enable-output",
                    instrument_id="scope",
                    point_index=0,
                    status="applied",
                    assignments=(
                        InstrumentStateSetting(
                            target=state_member_target(
                                PropertyRef("test.output/v1", (), "enabled")
                            ),
                            value=StateValue(True),
                        ),
                    ),
                    metadata={"device_command": "output on"},
                ),
            ),
            detail_complete=False,
        ),
        finalization_actions=[
            InstrumentFinalizationActionEvidence(
                operation_id="hardware.finish.safe_operation.scope.0",
                instrument_id="scope",
                kind="safe_operation",
                status="completed",
                metadata={"device_status": "safe"},
            )
        ],
    )

    contents = build_terminal_contents(
        outcome=outcome,
        measurement_count=0,
        dataset_content_hash=None,
        dataset_schema=None,
        expected_record_count=None,
        instrument_state=instrument_state,
    )

    [evidence_entry] = [entry for entry in contents if entry.role == "record"]
    assert evidence_entry.id == "instrument-state-evidence"
    assert evidence_entry.metadata == {
        "summary": {
            "instrument_ids": ["scope"],
            "baseline_change_count": 1,
            "final_change_count": 1,
            "baseline_changed_instrument_ids": ["scope"],
            "final_changed_instrument_ids": ["scope"],
            "missing_final_instrument_ids": [],
            "finalization_action_count": 1,
            "state_action_count": 2,
            "retained_state_action_count": 1,
            "state_action_detail_complete": False,
        }
    }


def test_state_evidence_summary_keeps_missing_final_readback_neutral() -> None:
    observed = _instrument_state("scope", output=False, scale=1.0)
    evidence = InstrumentStateEvidence(
        run_id="run-incomplete-readback",
        observed_state=[observed],
        baseline_state=[observed],
    )

    summary = summarize_instrument_state_evidence(evidence)

    assert summary.final_change_count == 0
    assert summary.final_changed_instrument_ids == ()
    assert summary.missing_final_instrument_ids == ("scope",)
    assert "state_actions" not in evidence.model_dump(mode="json")
    assert "finalization_actions" not in evidence.model_dump(mode="json")
    assert "state_action_count" not in summary.model_dump(mode="json")
    assert "retained_state_action_count" not in summary.model_dump(mode="json")
    assert "state_action_detail_complete" not in summary.model_dump(mode="json")
    assert "finalization_action_count" not in summary.model_dump(mode="json")
    assert "rejected_finalization_action_count" not in summary.model_dump(mode="json")


def _instrument_state(
    instrument_id: str,
    *,
    output: bool,
    scale: float,
) -> InstrumentStateSnapshot:
    return InstrumentStateSnapshot(
        instrument_id=instrument_id,
        observations=[
            state_observation(
                PropertyRef("test.output/v1", (), "enabled"),
                StateValue(output),
            ),
            state_observation(
                PropertyRef("test.vertical/v1", ("channel-1",), "scale"),
                StateValue(scale),
            ),
        ],
    )
