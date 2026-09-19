"""Golden identities for the reference calibration's durable capabilities."""

from __future__ import annotations

from reference_lab.workflows.drag_beta_automatic_publication import (
    DRAG_BETA_PUBLICATION_POLICY_REF,
)
from reference_lab.workflows.drag_beta_freshness import (
    drag_beta_freshness_calibration,
)
from reference_lab.workflows.drag_beta_procedure import (
    drag_beta_calibration_procedure,
    drag_beta_verification_procedure,
)
from reference_lab.workflows.drag_beta_publication import (
    DRAG_BETA_COMPOSITION_POLICY_REF,
)


def test_drag_beta_durable_capability_manifest_changes_explicitly() -> None:
    """Require an intentional version-and-fingerprint review for code changes."""

    # Automated verification and publication now belong to an exact working point.
    # Advance their capabilities; the explicit manual global procedure is retained.
    assert {
        "manual_procedure": drag_beta_calibration_procedure.ref.model_dump(mode="json"),
        "verification_procedure": drag_beta_verification_procedure.ref.model_dump(
            mode="json"
        ),
        "calibration": drag_beta_freshness_calibration.ref.model_dump(mode="json"),
        "composition": DRAG_BETA_COMPOSITION_POLICY_REF.model_dump(mode="json"),
        "automatic_publication": DRAG_BETA_PUBLICATION_POLICY_REF.model_dump(
            mode="json"
        ),
    } == {
        "manual_procedure": {
            "id": "reference-lab.drag-beta-calibration",
            "version": "9",
            "fingerprint": (
                "sha256:cf31e98d821144151b72fb4f15213bb"
                "8bd0f848587775521c262f3b2817c6bd0"
            ),
        },
        "verification_procedure": {
            "id": "reference-lab.drag-beta-verification",
            "version": "9",
            "fingerprint": (
                "sha256:ad7f2a667e355a23e2de51feae0598b"
                "e8c0e5125551aa3afdd7490d25f2148ff"
            ),
        },
        "calibration": {
            "id": "reference-lab.drag-beta-freshness",
            "version": "10",
            "fingerprint": (
                "sha256:8d77cf5aa1ce351da3717bac5f11049"
                "a40e122a1c69b6551f8699632e78af585"
            ),
            "success_policy": "published_result",
        },
        "composition": {
            "id": "reference-lab.drag-beta-cohort-composition",
            "version": "11",
            "fingerprint": (
                "sha256:71c4f87805fe6a412ee866efc9e0154"
                "4224120509929c63bf22292b609e42215"
            ),
        },
        "automatic_publication": {
            "id": "reference-lab.drag-beta-automatic-publication",
            "version": "11",
            "fingerprint": (
                "sha256:87f729ec1a1b1efba012cc259f72448"
                "3aacb2427c889d66fb53e6e4fa903a2bf"
            ),
            "calibration": {
                "id": "reference-lab.drag-beta-freshness",
                "version": "10",
                "fingerprint": (
                    "sha256:8d77cf5aa1ce351da3717bac5f11049"
                    "a40e122a1c69b6551f8699632e78af585"
                ),
                "success_policy": "published_result",
            },
            "composition_policy": {
                "id": "reference-lab.drag-beta-cohort-composition",
                "version": "11",
                "fingerprint": (
                    "sha256:71c4f87805fe6a412ee866efc9e0154"
                    "4224120509929c63bf22292b609e42215"
                ),
            },
        },
    }
