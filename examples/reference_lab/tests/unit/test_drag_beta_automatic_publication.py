from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest
from scopecat.api.calibration_finalizer import (
    CalibrationPublicationCandidate,
    CalibrationPublicationPlanningContext,
)
from scopecat.api.calibration_publication import CalibrationCohortPublicationPlan
from scopecat.automation.calibration_wire import CalibrationCohortMemberPage
from scopecat.automation.calibrations import CalibrationCohort

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.workflows import drag_beta_automatic_publication
from reference_lab.workflows.drag_beta_automatic_publication import (
    DRAG_BETA_PUBLICATION_POLICY_FINGERPRINT,
    DRAG_BETA_PUBLICATION_POLICY_ID,
    DRAG_BETA_PUBLICATION_POLICY_REF,
    DRAG_BETA_PUBLICATION_POLICY_REGISTRY,
    DRAG_BETA_PUBLICATION_POLICY_VERSION,
    prepare_drag_beta_automatic_publication,
)
from reference_lab.workflows.drag_beta_freshness import (
    DRAG_BETA_CALIBRATION_VERSION,
    drag_beta_freshness_calibration,
)
from reference_lab.workflows.drag_beta_publication import (
    DRAG_BETA_COMPOSITION_POLICY_REF,
    DRAG_BETA_PUBLICATION_ACTOR,
    DRAG_BETA_PUBLICATION_NOTE,
    DragBetaCohortPublication,
)


def test_drag_beta_automatic_policy_binds_its_exact_contract() -> None:
    policy = prepare_drag_beta_automatic_publication

    assert policy.id == DRAG_BETA_PUBLICATION_POLICY_ID
    assert policy.version == DRAG_BETA_PUBLICATION_POLICY_VERSION
    assert policy.version == "8"
    assert policy.fingerprint == DRAG_BETA_PUBLICATION_POLICY_FINGERPRINT
    assert policy.ref == DRAG_BETA_PUBLICATION_POLICY_REF
    assert policy.calibration == drag_beta_freshness_calibration.ref
    assert policy.calibration.version == DRAG_BETA_CALIBRATION_VERSION == "7"
    assert policy.composition_policy == DRAG_BETA_COMPOSITION_POLICY_REF
    assert policy.actor == DRAG_BETA_PUBLICATION_ACTOR
    assert policy.note == DRAG_BETA_PUBLICATION_NOTE
    assert DRAG_BETA_PUBLICATION_POLICY_REGISTRY.capabilities == (policy.ref,)
    assert DRAG_BETA_PUBLICATION_POLICY_REGISTRY.active_bindings == (policy.ref,)
    assert DRAG_BETA_PUBLICATION_POLICY_REGISTRY.resolve(policy.ref) is policy
    assert (
        DRAG_BETA_PUBLICATION_POLICY_REGISTRY.for_calibration(policy.calibration)
        is policy
    )


def test_drag_beta_automatic_policy_fingerprint_covers_every_boundary() -> None:
    policy = prepare_drag_beta_automatic_publication
    actor_changed = replace(policy, actor="another-finalizer")
    note_changed = replace(policy, note="another publication note")
    callback_changed = replace(policy, _prepare=_changed_prepare)
    composition_changed = replace(
        policy,
        composition_policy=policy.composition_policy.model_copy(
            update={"version": "changed"}
        ),
    )

    assert (
        len(
            {
                policy.fingerprint,
                actor_changed.fingerprint,
                note_changed.fingerprint,
                callback_changed.fingerprint,
                composition_changed.fingerprint,
            }
        )
        == 5
    )


def test_drag_beta_automatic_policy_reuses_shared_prepare_core_without_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cohort = cast(
        "CalibrationCohort",
        cast("object", SimpleNamespace(cohort_id="cohort-exact")),
    )
    member_page = cast(
        "CalibrationCohortMemberPage",
        cast("object", SimpleNamespace(items=(object(),))),
    )
    candidate = cast(
        "CalibrationPublicationCandidate",
        cast(
            "object",
            SimpleNamespace(
                cohort=cohort,
                member_page=member_page,
                finalization=SimpleNamespace(revision=7),
            ),
        ),
    )
    plan = cast("CalibrationCohortPublicationPlan", object())
    context = cast(
        "CalibrationPublicationPlanningContext",
        cast("object", SimpleNamespace()),
    )

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
        assert cohort is candidate.cohort
        assert member_page is candidate.member_page
        assert actor == DRAG_BETA_PUBLICATION_ACTOR
        assert note == DRAG_BETA_PUBLICATION_NOTE
        assert expected_finalization_revision == candidate.finalization.revision == 7
        return cast(
            "DragBetaCohortPublication",
            cast("object", SimpleNamespace(plan=plan)),
        )

    monkeypatch.setattr(
        drag_beta_automatic_publication,
        "prepare_drag_beta_cohort_publication_from_context",
        fake_prepare,
    )

    assert (
        prepare_drag_beta_automatic_publication.__wrapped__(context, candidate) is plan
    )


def test_reference_application_registers_drag_beta_automatic_publication() -> None:
    application = create_application(EXAMPLE_ROOT)

    assert application.calibration_publications.capabilities == (
        prepare_drag_beta_automatic_publication.ref,
    )
    selected = application.calibration_publications.for_calibration(
        drag_beta_freshness_calibration.ref
    )
    assert selected is not None
    assert selected.ref == prepare_drag_beta_automatic_publication.ref


def _changed_prepare(
    context: CalibrationPublicationPlanningContext,
    candidate: CalibrationPublicationCandidate,
) -> CalibrationCohortPublicationPlan:
    return prepare_drag_beta_automatic_publication.__wrapped__(context, candidate)
