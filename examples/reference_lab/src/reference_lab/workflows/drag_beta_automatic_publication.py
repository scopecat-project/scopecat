"""Resident automatic-publication policy for verified DRAG cohorts."""

from __future__ import annotations

from scopecat.api.calibration_finalizer import (
    CalibrationPublicationCandidate,
    CalibrationPublicationPlanningContext,
    calibration_publication_policy,
)
from scopecat.api.calibration_policy import CalibrationPublicationPolicyRegistry
from scopecat.api.calibration_publication import CalibrationCohortPublicationPlan

from reference_lab.workflows.drag_beta_freshness import (
    drag_beta_freshness_calibration,
)
from reference_lab.workflows.drag_beta_publication import (
    DRAG_BETA_COMPOSITION_POLICY_REF,
    DRAG_BETA_PUBLICATION_ACTOR,
    DRAG_BETA_PUBLICATION_NOTE,
    prepare_drag_beta_cohort_publication_from_context,
)

DRAG_BETA_PUBLICATION_POLICY_ID = "reference-lab.drag-beta-automatic-publication"
DRAG_BETA_PUBLICATION_POLICY_VERSION = "7"


@calibration_publication_policy(
    id=DRAG_BETA_PUBLICATION_POLICY_ID,
    version=DRAG_BETA_PUBLICATION_POLICY_VERSION,
    calibration=drag_beta_freshness_calibration.ref,
    composition_policy=DRAG_BETA_COMPOSITION_POLICY_REF,
    actor=DRAG_BETA_PUBLICATION_ACTOR,
    note=DRAG_BETA_PUBLICATION_NOTE,
)
def prepare_drag_beta_automatic_publication(
    context: CalibrationPublicationPlanningContext,
    candidate: CalibrationPublicationCandidate,
) -> CalibrationCohortPublicationPlan:
    """Reuse the exact Phase 7 proof and return its deterministic plan."""

    prepared = prepare_drag_beta_cohort_publication_from_context(
        context,
        cohort=candidate.cohort,
        member_page=candidate.member_page,
        actor=DRAG_BETA_PUBLICATION_ACTOR,
        note=DRAG_BETA_PUBLICATION_NOTE,
        expected_finalization_revision=candidate.finalization.revision,
    )
    return prepared.plan


DRAG_BETA_PUBLICATION_POLICY_REF = prepare_drag_beta_automatic_publication.ref
DRAG_BETA_PUBLICATION_POLICY_FINGERPRINT = DRAG_BETA_PUBLICATION_POLICY_REF.fingerprint
DRAG_BETA_PUBLICATION_POLICY_REGISTRY = CalibrationPublicationPolicyRegistry(
    (prepare_drag_beta_automatic_publication,),
    active=(prepare_drag_beta_automatic_publication.ref,),
)


__all__ = [
    "DRAG_BETA_PUBLICATION_POLICY_FINGERPRINT",
    "DRAG_BETA_PUBLICATION_POLICY_ID",
    "DRAG_BETA_PUBLICATION_POLICY_REF",
    "DRAG_BETA_PUBLICATION_POLICY_REGISTRY",
    "DRAG_BETA_PUBLICATION_POLICY_VERSION",
    "prepare_drag_beta_automatic_publication",
]
