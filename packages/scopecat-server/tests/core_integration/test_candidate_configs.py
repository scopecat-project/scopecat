from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import scopecat as sc
from pydantic import ValidationError
from scopecat.config.candidates import (
    CandidateConfig,
    resolve_candidate_config_from_snapshot,
)
from scopecat.config.changes import (
    load_parameter_change_approval,
    load_parameter_change_proposal,
)
from scopecat.config.parameter_updates import ParameterUpdate
from scopecat.config.registry.service import (
    ConfigRevision,
    DirectConfigRevisionSource,
    publish_config_revision,
)
from scopecat.kernel.errors import CheckFailed, Conflict, DataIntegrityError
from scopecat.kernel.quantity import Quantity
from scopecat.records.parameter import ScalarParameterValue
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat_testkit.config_registry import activate_candidate_config, initialize_setup
from scopecat_testkit.instrument_host import compose_test_instruments
from scopecat_testkit.server.in_process_lab import InProcessLab, in_process_lab
from scopecat_testkit.server.runtime import (
    sqlite_config_registry_unit_of_work,
    sqlite_project_services,
    sqlite_run_repository,
)
from scopecat_testkit.signal_instruments import TestSignalInstrumentProvider
from scopecat_testkit.workflow_fixtures import load_config, load_invocation


def test_candidate_config_resolves_proposal_and_runs_follow_up(
    tmp_path: Path,
) -> None:
    lab = _lab(tmp_path)
    run = lab.prepare(load_invocation()).run()
    analysis = (
        run.analysis("manual readout review")
        .result()
        .propose(
            "drive_frequency",
            sc.replace_scalar_parameter(
                "drive_frequency",
                sc.Quantity(5.5, "GHz"),
            ),
            confidence=0.9,
        )
    )
    outcome = analysis.save()
    candidate = outcome.candidate_config()
    reopened = run.published_analysis(outcome.id)

    follow_up = lab.prepare(load_invocation(), config=candidate).run()
    approval = lab.review_parameter_proposal(
        run,
        "drive_frequency",
        note="checked parameter proposal",
    )

    assert approval.actor == "operator"
    assert approval.proposal_id == "drive_frequency"
    assert candidate.proposal_id == "drive_frequency"
    updated = follow_up.config.parameter_snapshot.get("drive_frequency")
    assert isinstance(updated, ScalarParameterValue)
    assert updated.value == Quantity(value=5.5, unit="GHz")
    proposal = outcome.parameter_proposals[0]
    assert proposal.deltas[0].after == updated
    assert reopened.parameter_proposals == (proposal,)
    assert reopened.proposal(proposal.id) == proposal
    assert reopened.candidate_config() == candidate


def test_candidate_checks_and_run_leave_source_run_unchanged(
    tmp_path: Path,
) -> None:
    lab = _lab(tmp_path)
    source_run = lab.prepare(load_invocation()).run()
    analysis = (
        source_run.analysis("read-only candidate")
        .result()
        .propose(
            "drive-frequency",
            sc.replace_scalar_parameter(
                "drive_frequency",
                sc.Quantity(5.4, "GHz"),
            ),
        )
    )
    outcome = analysis.save()
    candidate = outcome.candidate_config()
    prepared = lab.prepare(load_invocation(), config=candidate)
    storage = sqlite_run_repository(tmp_path)
    snapshot_before = storage.read_snapshot(source_run.id)
    refs_before = _run_ref_contents(tmp_path, source_run.id)

    def assert_source_run_unchanged() -> None:
        assert storage.read_snapshot(source_run.id) == snapshot_before
        assert _run_ref_contents(tmp_path, source_run.id) == refs_before

    assert lab.resolve_config(config=candidate).id == "candidate-drive-frequency"
    assert_source_run_unchanged()
    assert prepared.check().ok
    assert_source_run_unchanged()
    assert_source_run_unchanged()
    assert prepared.preview().point_count == 3
    assert_source_run_unchanged()
    follow_up = prepared.run()

    assert follow_up.config.id == "candidate-drive-frequency"
    assert_source_run_unchanged()


def test_published_analysis_selects_one_of_multiple_durable_proposals(
    tmp_path: Path,
) -> None:
    run = _lab(tmp_path).prepare(load_invocation()).run()
    published = (
        run.analysis("alternative fits")
        .result()
        .propose(
            "first-fit",
            sc.replace_scalar_parameter(
                "drive_frequency",
                sc.Quantity(5.4, "GHz"),
            ),
        )
        .propose(
            "second-fit",
            sc.replace_scalar_parameter(
                "drive_frequency",
                sc.Quantity(5.5, "GHz"),
            ),
        )
        .save()
    )

    with pytest.raises(CheckFailed) as required:
        published.candidate_config()
    assert required.value.problems[0].code == "candidate_config_selection_required"

    assert published.candidate_config("second-fit").proposal_id == "second-fit"

    with pytest.raises(CheckFailed) as missing:
        published.candidate_config("missing-fit")
    assert missing.value.problems[0].code == "candidate_config_selection_not_found"


@pytest.mark.parametrize(
    "update",
    [
        sc.replace_scalar_parameter(
            "missing_frequency",
            sc.Quantity(4.9, "GHz"),
        ),
        sc.replace_table_parameter(
            "drive_frequency",
            [],
        ),
        sc.replace_scalar_parameter("drive_frequency", True),
    ],
)
def test_analysis_rejects_invalid_update_at_propose(
    tmp_path: Path,
    update: ParameterUpdate,
) -> None:
    run = _lab(tmp_path).prepare(load_invocation()).run()

    with pytest.raises(CheckFailed) as error:
        run.analysis("invalid proposal").result().propose("invalid", update)

    assert error.value.problems[0].code == "analysis_parameter_proposal_invalid"


def test_candidate_config_from_snapshot_rejects_stale_base_hash(
    tmp_path: Path,
) -> None:
    run = _lab(tmp_path).prepare(load_invocation()).run()
    candidate = (
        run.analysis("stale hash")
        .result()
        .propose(
            "drive-frequency",
            sc.replace_scalar_parameter(
                "drive_frequency",
                sc.Quantity(5.4, "GHz"),
            ),
        )
        .save()
        .candidate_config()
    )
    changed_source = run.config.model_copy(
        update={"system": run.config.system.model_copy(update={"id": "changed-system"})}
    )

    with pytest.raises(Conflict) as error:
        resolve_candidate_config_from_snapshot(
            candidate,
            source_config=changed_source,
        )

    assert error.value.problems[0].code == ("parameter_change_proposal_base_mismatch")


def test_candidate_config_from_snapshot_rejects_stale_base_id(
    tmp_path: Path,
) -> None:
    run = _lab(tmp_path).prepare(load_invocation()).run()
    proposal = (
        run.analysis("stale id")
        .result()
        .propose(
            "drive-frequency",
            sc.replace_scalar_parameter(
                "drive_frequency",
                sc.Quantity(5.4, "GHz"),
            ),
        )
        .parameter_proposals[0]
    )
    candidate = CandidateConfig(
        parameter_proposal=proposal.model_copy(
            update={"base_config_id": "different-config"}
        )
    )

    with pytest.raises(Conflict) as error:
        resolve_candidate_config_from_snapshot(
            candidate,
            source_config=run.config,
        )

    assert error.value.problems[0].code == (
        "parameter_change_proposal_base_id_mismatch"
    )


def test_candidate_config_rejects_drifted_source_snapshot_before_publish(
    tmp_path: Path,
) -> None:
    lab = _lab(tmp_path)
    run = lab.prepare(load_invocation()).run()
    initialize_setup(
        run.config, unit_of_work=sqlite_config_registry_unit_of_work(tmp_path)
    )
    initial = publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(run.config),
            entry_id="initial",
            actor="operator",
        ),
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
        expected_generation=0,
    )
    candidate = (
        run.analysis("stale fit")
        .result()
        .propose(
            "drive-frequency",
            sc.replace_scalar_parameter(
                "drive_frequency",
                sc.Quantity(5.4, "GHz"),
            ),
        )
        .save()
        .candidate_config()
    )
    stale_source = run.config.model_copy(update={"id": "changed-after-fit"})
    sqlite_run_repository(tmp_path).write_model(
        run.id,
        "config-profile.snapshot.json",
        stale_source,
    )

    with pytest.raises(DataIntegrityError) as error:
        activate_candidate_config(
            candidate=candidate,
            services=sqlite_project_services(tmp_path),
            actor="operator",
        )

    assert error.value.problems[0].code == "run.config_provenance_mismatch"
    with sqlite_config_registry_unit_of_work(tmp_path)() as work:
        assert work.registry.list_entries() == (initial.entry,)
        assert work.registry.current_generation() == 1


def test_parameter_change_proposal_round_trips_and_is_persisted(
    tmp_path: Path,
) -> None:
    run = _lab(tmp_path).prepare(load_invocation()).run()
    analysis = (
        run.analysis("round trip fit")
        .result()
        .propose(
            "drive-frequency",
            sc.replace_scalar_parameter(
                "drive_frequency",
                sc.Quantity(5.4, "GHz"),
            ),
            reason="fit converged",
            confidence=0.8,
        )
    )
    proposal = analysis.parameter_proposals[0]

    restored = ParameterChangeProposal.model_validate_json(proposal.model_dump_json())
    analysis.save()
    persisted = load_parameter_change_proposal(
        run_id=run.id,
        selector=proposal.id,
        services=sqlite_project_services(tmp_path),
    )

    assert restored == proposal
    assert persisted == proposal
    assert persisted.deltas == proposal.deltas


def test_durable_proposal_validation_rejects_invalid_invariants(
    tmp_path: Path,
) -> None:
    run = _lab(tmp_path).prepare(load_invocation()).run()
    proposal = (
        run.analysis("validated copy")
        .result()
        .propose(
            "drive-frequency",
            sc.replace_scalar_parameter(
                "drive_frequency",
                sc.Quantity(5.4, "GHz"),
            ),
        )
        .parameter_proposals[0]
    )

    delta_data = proposal.deltas[0].model_dump(mode="python")
    delta_data["parameter_id"] = "other"
    with pytest.raises(ValidationError):
        type(proposal.deltas[0]).model_validate(delta_data)

    proposal_data = proposal.model_dump(mode="python")
    proposal_data["deltas"] = ()
    with pytest.raises(ValidationError):
        ParameterChangeProposal.model_validate(proposal_data)


def test_proposal_records_are_immutable_but_idempotent(tmp_path: Path) -> None:
    lab = _lab(tmp_path)
    run = lab.prepare(load_invocation()).run()
    first = (
        run.analysis("first fit")
        .result()
        .propose(
            "drive-frequency",
            sc.replace_scalar_parameter(
                "drive_frequency",
                sc.Quantity(5.4, "GHz"),
            ),
            reason="first fit",
        )
    )
    first_outcome = first.save()
    first.save()
    first_proposal = first_outcome.parameter_proposals[0]
    approval = lab.review_parameter_proposal(
        run,
        first_proposal.id,
        note="approval must survive an idempotent analysis-cell retry",
    )
    rebuilt = (
        run.analysis("first fit")
        .result()
        .propose(
            "drive-frequency",
            sc.replace_scalar_parameter(
                "drive_frequency",
                sc.Quantity(5.4, "GHz"),
            ),
            reason="first fit",
        )
    )
    rebuilt_proposal = rebuilt.parameter_proposals[0]
    assert rebuilt_proposal.proposed_at != first_proposal.proposed_at
    rebuilt.save()
    second = (
        run.analysis("second fit")
        .result()
        .propose(
            "drive-frequency",
            sc.replace_scalar_parameter(
                "drive_frequency",
                sc.Quantity(5.5, "GHz"),
            ),
            reason="second fit",
        )
    )

    with pytest.raises(Conflict) as error:
        second.save()

    assert error.value.problems[0].code == "parameter_change_proposal_conflict"
    persisted = load_parameter_change_proposal(
        run_id=run.id,
        selector="drive-frequency",
        services=sqlite_project_services(tmp_path),
    )
    records = (
        sqlite_run_repository(tmp_path)
        .list_contents(
            run.id,
            limit=100,
            role="record",
            kind="parameter_change_proposal",
        )
        .items
    )
    assert persisted == first_proposal
    assert persisted.proposed_at == first_proposal.proposed_at
    assert [record.id for record in records] == [first_proposal.id]
    persisted_approval = load_parameter_change_approval(
        run_id=run.id,
        selector=first_proposal.id,
        storage=sqlite_run_repository(tmp_path),
    )
    assert persisted_approval == approval


def _lab(tmp_path: Path) -> InProcessLab:
    config = load_config()
    composition = compose_test_instruments(
        config=config,
        provider=TestSignalInstrumentProvider(),
    )
    return in_process_lab(
        tmp_path,
        config=config,
        system=composition.system,
        instrument_backend=composition.backend,
    )


def _run_ref_contents(project_root: Path, run_id: str) -> dict[str, str]:
    repository = sqlite_run_repository(project_root)
    with sqlite3.connect(repository.database) as connection:
        return dict(
            connection.execute(
                """
                SELECT ref, digest
                FROM run_repository_refs
                WHERE run_id = ?
                ORDER BY ref
                """,
                (run_id,),
            )
        )
