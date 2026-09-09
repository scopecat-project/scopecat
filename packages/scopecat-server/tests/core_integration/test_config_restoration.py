"""Synthetic immutable revisions exercise restoration without devices or workers."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from scopecat.config.registry import (
    CandidateConfigRegistrySource,
    ConfigRegistryEntry,
    ConfigRevision,
    DirectConfigRevisionSource,
    ManualCandidateAcceptance,
    activate_config_registry_entry,
    load_active_config_registry_snapshot,
    publish_config_revision,
)
from scopecat.config.registry.records import (
    CalibrationCohortMergeRegistrySource,
    ConfigCompositionEvidenceStepRef,
    ConfigCompositionPolicyRef,
    ConfigRegistryEntrySource,
    ManualConfigDraftRegistrySource,
    ResolvedCalibrationCohortMergeContribution,
    ResolvedVerifiedParameterProposalProofV1,
)
from scopecat.config.registry.service import load_config_registry_entry_snapshot
from scopecat.kernel.errors import Conflict, DataIntegrityError
from scopecat.records.analysis import ProjectAnalysisDecisionReference
from scopecat.records.config import ConfigContentHash, config_content_hash
from scopecat_testkit.config_registry import load_config
from scopecat_testkit.server.runtime import sqlite_config_registry_unit_of_work


@pytest.mark.parametrize("kind", ["candidate", "draft", "cohort"])
def test_restore_previously_accepted_candidate(tmp_path: Path, kind: str) -> None:
    uow = sqlite_config_registry_unit_of_work(tmp_path)
    base = load_config()
    publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(base), entry_id="base", actor="operator"
        ),
        unit_of_work=uow,
        expected_generation=0,
    )
    candidate = base.model_copy(update={"id": "candidate-a"})
    with uow() as work:
        entry = ConfigRegistryEntry(
            id="candidate-a",
            config_ref=work.registry.config_ref("candidate-a"),
            content_hash=config_content_hash(candidate),
            actor="reviewer",
            source=_source(kind, config_content_hash(base)),
        )
        work.registry.commit_revision(entry=entry, config=candidate)
    first = activate_config_registry_entry(
        entry_id=entry.id,
        unit_of_work=uow,
        actor="operator",
        expected_generation=1,
    )
    publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(
                base.model_copy(update={"id": "later-b"})
            ),
            entry_id="later-b",
            actor="operator",
        ),
        unit_of_work=uow,
        expected_generation=2,
    )
    restored = activate_config_registry_entry(
        entry_id=entry.id,
        unit_of_work=uow,
        actor="operator",
        expected_generation=3,
    )
    assert restored.entry == first.entry == entry
    assert restored.activation is not None
    assert restored.activation.generation == 4
    assert restored.activation.restored_from_generation == 2
    assert restored.activation.previous_entry_id == "later-b"
    assert first.activation is not None
    assert first.activation.restored_from_generation is None
    assert "restored_from_generation" not in first.activation.model_dump(mode="json")
    assert (
        load_config_registry_entry_snapshot(
            entry_id=entry.id, unit_of_work=uow
        ).latest_activation
        == restored.activation
    )
    with pytest.raises(Conflict, match=r"config_registry\.conflict"):
        activate_config_registry_entry(
            entry_id="later-b",
            unit_of_work=uow,
            actor="operator",
            expected_generation=3,
        )
    # A copied, never-activated identity must still pass the original base check,
    # even when it has exactly the content of an accepted historical revision.
    with uow() as work:
        never_accepted = entry.model_copy(
            update={
                "id": "never-accepted",
                "config_ref": work.registry.config_ref("never-accepted"),
            }
        )
        work.registry.commit_revision(entry=never_accepted, config=candidate)
    with pytest.raises(Conflict, match=r"config_registry\.stale_candidate"):
        activate_config_registry_entry(
            entry_id=never_accepted.id,
            unit_of_work=uow,
            actor="operator",
            expected_generation=4,
        )
    assert load_active_config_registry_snapshot(unit_of_work=uow).config == candidate


def _source(kind: str, base_hash: ConfigContentHash) -> ConfigRegistryEntrySource:
    if kind == "candidate":
        return CandidateConfigRegistrySource(
            run_id="retained-run",
            proposal_id="accepted-proposal",
            base_config_content_hash=base_hash,
            acceptance=ManualCandidateAcceptance(),
        )
    if kind == "draft":
        return ManualConfigDraftRegistrySource(
            base_entry_id="base",
            base_config_content_hash=base_hash,
            base_registry_generation=1,
        )
    return CalibrationCohortMergeRegistrySource(
        cohort_id="cohort",
        spec_hash=base_hash,
        composition_policy_ref=ConfigCompositionPolicyRef(
            id="composition",
            version="1",
            fingerprint=base_hash,
        ),
        base_entry_id="base",
        base_config_content_hash=base_hash,
        base_registry_generation=1,
        candidate_id="candidate-a",
        contributions=(
            ResolvedCalibrationCohortMergeContribution(
                member_id="member",
                result_input_fingerprint=base_hash,
                proof=ResolvedVerifiedParameterProposalProofV1(
                    evidence_step=ConfigCompositionEvidenceStepRef(
                        procedure_run_id="procedure",
                        step_key="verify",
                        attempt=1,
                    ),
                    baseline_run_id="baseline-run",
                    fit_analysis_record_id="fit",
                    proposal_id="proposal",
                    candidate_run_id="candidate-run",
                    decision=ProjectAnalysisDecisionReference(
                        analysis_record_id="verification",
                        output_id="decision",
                        schema_id="quality",
                        schema_hash=base_hash,
                    ),
                ),
            ),
        ),
    )


@pytest.mark.parametrize("field", ["entry_id", "entry_content_hash"])
def test_restoration_requires_exact_historical_identity(
    tmp_path: Path, field: str
) -> None:
    uow = sqlite_config_registry_unit_of_work(tmp_path)
    for generation, entry_id in enumerate(("old", "new")):
        publish_config_revision(
            revision=ConfigRevision(
                source=DirectConfigRevisionSource(
                    load_config().model_copy(update={"id": entry_id})
                ),
                entry_id=entry_id,
                actor="operator",
            ),
            unit_of_work=uow,
            expected_generation=generation,
        )
    # Corrupt the retained JSON independently of the indexed entry ID. Historical
    # evidence must be checked just like the current active identity.
    with closing(
        sqlite3.connect(tmp_path / ".scopecat-test" / "control.sqlite3")
    ) as connection:
        connection.execute(
            "UPDATE config_registry_activations "
            "SET record_json = json_set(record_json, ?, ?) WHERE generation = 1",
            (
                f"$.{field}",
                "wrong-entry" if field == "entry_id" else "sha256:" + "f" * 64,
            ),
        )
        connection.commit()
    with pytest.raises(
        DataIntegrityError, match=r"config_registry\.active_content_mismatch"
    ):
        activate_config_registry_entry(
            entry_id="old",
            unit_of_work=uow,
            actor="operator",
            expected_generation=2,
        )
    assert (
        load_active_config_registry_snapshot(unit_of_work=uow).activation.generation
        == 2
    )
