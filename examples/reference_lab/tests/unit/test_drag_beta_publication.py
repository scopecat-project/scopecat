from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
import scopecat as sc
from scopecat import Quantity
from scopecat.api.calibration_finalizer import CalibrationPublicationPlanningContext
from scopecat.api.calibration_publication import CalibrationCohortPublicationPlan
from scopecat.api.lab import LabClient
from scopecat.automation.calibration_wire import CalibrationCohortMemberPage
from scopecat.automation.calibrations import CalibrationCohort
from scopecat.config.drafts import ConfigDraft
from scopecat.config.parameter_updates import ParameterUpdate
from scopecat.daemon.wire import CalibrationPublicationReceipt
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.parameter import TableParameterValue
from scopecat.records.parameter_change import (
    ParameterChangeProposal,
    ParameterValueDelta,
)

from reference_lab.configuration import bootstrap_config
from reference_lab.parameters import QubitParameters
from reference_lab.workflows import drag_beta_publication
from reference_lab.workflows.drag_beta_experiment import DragBetaQubit
from reference_lab.workflows.drag_beta_freshness import (
    drag_beta_freshness_calibration,
    drag_beta_semantic_freshness_inputs,
)
from reference_lab.workflows.drag_beta_publication import (
    DRAG_BETA_COMPOSITION_POLICY_FINGERPRINT,
    DRAG_BETA_COMPOSITION_POLICY_ID,
    DRAG_BETA_COMPOSITION_POLICY_REF,
    DRAG_BETA_COMPOSITION_POLICY_VERSION,
    DRAG_BETA_VERIFICATION_EVIDENCE_STEP,
    DragBetaCohortPublication,
    drag_beta_composition_policy_ref,
    drag_beta_merged_result_input_fingerprint,
    prepare_drag_beta_cohort_publication,
    publish_verified_drag_beta_cohort,
    validate_drag_beta_target_owned_proposal,
)


def test_drag_beta_composition_policy_has_exact_stable_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert DRAG_BETA_COMPOSITION_POLICY_REF.id == DRAG_BETA_COMPOSITION_POLICY_ID
    assert DRAG_BETA_COMPOSITION_POLICY_REF.version == (
        DRAG_BETA_COMPOSITION_POLICY_VERSION
    )
    assert DRAG_BETA_COMPOSITION_POLICY_REF.fingerprint == (
        DRAG_BETA_COMPOSITION_POLICY_FINGERPRINT
    )
    assert DRAG_BETA_COMPOSITION_POLICY_FINGERPRINT.startswith("sha256:")
    assert len(DRAG_BETA_COMPOSITION_POLICY_FINGERPRINT) == len("sha256:") + 64
    assert DRAG_BETA_COMPOSITION_POLICY_VERSION == "7"
    assert DRAG_BETA_VERIFICATION_EVIDENCE_STEP == "verification"
    assert drag_beta_composition_policy_ref() == DRAG_BETA_COMPOSITION_POLICY_REF

    monkeypatch.setattr(
        drag_beta_publication,
        "validate_drag_beta_target_owned_proposal",
        _changed_target_owned_validator,
    )
    assert drag_beta_composition_policy_ref().fingerprint != (
        DRAG_BETA_COMPOSITION_POLICY_REF.fingerprint
    )


def test_manual_drag_beta_prepare_uses_shared_planning_core(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cohort = cast(
        "CalibrationCohort",
        cast("object", SimpleNamespace(cohort_id="cohort-exact")),
    )
    member_page = cast("CalibrationCohortMemberPage", object())

    def read_cohort(cohort_id: str) -> CalibrationCohort:
        assert cohort_id == "cohort-exact"
        return cohort

    def read_members(cohort_id: str) -> CalibrationCohortMemberPage:
        assert cohort_id == "cohort-exact"
        return member_page

    context = cast(
        "CalibrationPublicationPlanningContext",
        cast(
            "object",
            SimpleNamespace(
                cohort=read_cohort,
                cohort_members=read_members,
            ),
        ),
    )
    lab = cast(
        "LabClient",
        cast(
            "object",
            SimpleNamespace(
                calibrations=SimpleNamespace(
                    publication_planning_context=lambda: context
                )
            ),
        ),
    )
    prepared = cast("DragBetaCohortPublication", object())

    def fake_prepare(
        actual_context: CalibrationPublicationPlanningContext,
        *,
        cohort: CalibrationCohort,
        member_page: CalibrationCohortMemberPage,
        actor: str,
        note: str,
        expected_finalization_revision: int | None = None,
    ) -> DragBetaCohortPublication:
        assert actual_context is context
        assert cohort is context.cohort("cohort-exact")
        assert member_page is context.cohort_members("cohort-exact")
        assert actor == "manual-actor"
        assert note == "manual-note"
        assert expected_finalization_revision is None
        return prepared

    monkeypatch.setattr(
        drag_beta_publication,
        "prepare_drag_beta_cohort_publication_from_context",
        fake_prepare,
    )

    assert (
        prepare_drag_beta_cohort_publication(
            lab,
            "cohort-exact",
            actor="manual-actor",
            note="manual-note",
        )
        is prepared
    )


def test_manual_drag_beta_publish_uses_the_prepared_exact_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = cast("CalibrationCohortPublicationPlan", object())
    receipt = cast("CalibrationPublicationReceipt", object())
    published: list[CalibrationCohortPublicationPlan] = []

    def publish(
        actual: CalibrationCohortPublicationPlan,
    ) -> CalibrationPublicationReceipt:
        published.append(actual)
        return receipt

    lab = cast(
        "LabClient",
        cast("object", SimpleNamespace(calibrations=SimpleNamespace(publish=publish))),
    )

    def fake_prepare(
        actual_lab: LabClient,
        cohort_id: str,
        *,
        actor: str,
        note: str,
    ) -> DragBetaCohortPublication:
        assert actual_lab is lab
        assert cohort_id == "cohort-exact"
        assert actor == "manual-actor"
        assert note == "manual-note"
        return cast(
            "DragBetaCohortPublication",
            cast("object", SimpleNamespace(plan=plan)),
        )

    monkeypatch.setattr(
        drag_beta_publication,
        "prepare_drag_beta_cohort_publication",
        fake_prepare,
    )

    assert (
        publish_verified_drag_beta_cohort(
            lab,
            "cohort-exact",
            actor="manual-actor",
            note="manual-note",
        )
        is receipt
    )
    assert published == [plan]


def test_drag_beta_proposal_may_change_only_its_owned_target_cell() -> None:
    base = bootstrap_config()
    q0_candidate = _updated_config(
        base,
        sc.parameter_update(
            QubitParameters.drag_beta,
            sc.EntityRef(id="q0", kind="logical_qubit"),
            Quantity(0.125, "ns"),
        ),
        candidate_id="q0-candidate",
    )

    validate_drag_beta_target_owned_proposal(
        _proposal(base, q0_candidate, proposal_id="q0-drag-beta"),
        "q0",
    )

    q1_candidate = _updated_config(
        base,
        sc.parameter_update(
            QubitParameters.drag_beta,
            sc.EntityRef(id="q1", kind="logical_qubit"),
            Quantity(-0.25, "ns"),
        ),
        candidate_id="q1-candidate",
    )
    with pytest.raises(ValueError, match="non-owned parameter cell"):
        validate_drag_beta_target_owned_proposal(
            _proposal(base, q1_candidate, proposal_id="q0-drag-beta"),
            "q0",
        )

    upstream_candidate = _updated_config(
        base,
        sc.parameter_update(
            QubitParameters.quarter_turn_duration,
            sc.EntityRef(id="q0", kind="logical_qubit"),
            Quantity(17.0, "ns"),
        ),
        candidate_id="upstream-candidate",
    )
    with pytest.raises(ValueError, match="non-owned parameter cell"):
        validate_drag_beta_target_owned_proposal(
            _proposal(base, upstream_candidate, proposal_id="q0-drag-beta"),
            "q0",
        )

    with pytest.raises(ValueError, match="does not match"):
        validate_drag_beta_target_owned_proposal(
            _proposal(base, q0_candidate, proposal_id="wrong-proposal"),
            "q0",
        )


def test_drag_beta_composition_rejects_changed_verified_semantic_inputs() -> None:
    base = bootstrap_config()
    q0_update = sc.parameter_update(
        QubitParameters.drag_beta,
        sc.EntityRef(id="q0", kind="logical_qubit"),
        Quantity(0.125, "ns"),
    )
    q1_update = sc.parameter_update(
        QubitParameters.drag_beta,
        sc.EntityRef(id="q1", kind="logical_qubit"),
        Quantity(-0.25, "ns"),
    )
    q0_candidate = _updated_config(
        base,
        q0_update,
        candidate_id="q0-candidate",
    )
    merged = _updated_config(
        base,
        q0_update,
        q1_update,
        candidate_id="merged",
    )

    result_fingerprint = drag_beta_merged_result_input_fingerprint(
        candidate_config=q0_candidate,
        merged_config=merged,
        qubit="q0",
    )
    assert result_fingerprint == drag_beta_freshness_calibration.input_fingerprint(
        drag_beta_semantic_freshness_inputs(merged, "q0")
    )

    merged_with_upstream_change = _updated_config(
        base,
        q0_update,
        q1_update,
        sc.parameter_update(
            QubitParameters.quarter_turn_duration,
            sc.EntityRef(id="q0", kind="logical_qubit"),
            Quantity(17.0, "ns"),
        ),
        candidate_id="merged-with-upstream-change",
    )
    with pytest.raises(ValueError, match="verified semantic inputs"):
        drag_beta_merged_result_input_fingerprint(
            candidate_config=q0_candidate,
            merged_config=merged_with_upstream_change,
            qubit="q0",
        )


def _changed_target_owned_validator(
    proposal: ParameterChangeProposal,
    qubit: DragBetaQubit,
) -> None:
    validate_drag_beta_target_owned_proposal(proposal, qubit)


def _proposal(
    base: ConfigProfileSnapshot,
    candidate: ConfigProfileSnapshot,
    *,
    proposal_id: str,
) -> ParameterChangeProposal:
    before = base.parameter_snapshot.get(sc.parameter_table_name(QubitParameters))
    after = candidate.parameter_snapshot.get(sc.parameter_table_name(QubitParameters))
    assert isinstance(before, TableParameterValue)
    assert isinstance(after, TableParameterValue)
    return ParameterChangeProposal(
        id=proposal_id,
        source_run_id=f"run-{proposal_id}",
        analysis_record_id=f"analysis-{proposal_id}",
        base_config_id=base.id,
        base_config_content_hash=config_content_hash(base),
        reason="test target-owned DRAG proposal",
        deltas=(
            ParameterValueDelta(
                parameter_id=sc.parameter_table_name(QubitParameters),
                before=before,
                after=after,
            ),
        ),
    )


def _updated_config(
    config: ConfigProfileSnapshot,
    *updates: ParameterUpdate,
    candidate_id: str,
) -> ConfigProfileSnapshot:
    checked = (
        ConfigDraft.from_snapshot(config)
        .apply(*updates)
        .check(candidate_id=candidate_id)
    )
    assert checked.candidate is not None
    return checked.candidate
