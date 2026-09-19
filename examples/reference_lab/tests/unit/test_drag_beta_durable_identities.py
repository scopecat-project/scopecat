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

    # Fixed selections now use setup fences rather than global activation.
    # Advance procedure and transitive policy identities for this wire contract.
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
                "sha256:cf31e98d821144151b72fb4f15213bb8"
                "bd0f848587775521c262f3b2817c6bd0"
            ),
        },
        "verification_procedure": {
            "id": "reference-lab.drag-beta-verification",
            "version": "8",
            "fingerprint": (
                "sha256:7df205a91f643e710b7d088ae73ddff8"
                "f337ae3b84c340d5308407e19c9114ba"
            ),
        },
        "calibration": {
            "id": "reference-lab.drag-beta-freshness",
            "version": "9",
            "fingerprint": (
                "sha256:835464afcdbe22cf69e7308252d23fb2"
                "037e5435d834f6ff3b38217909657f28"
            ),
            "success_policy": "published_result",
        },
        "composition": {
            "id": "reference-lab.drag-beta-cohort-composition",
            "version": "10",
            "fingerprint": (
                "sha256:ccf9ac13ee9da4e2f48edbab549d7f71"
                "ee5e4c7e02ceb5f09dce927dc13652ba"
            ),
        },
        "automatic_publication": {
            "id": "reference-lab.drag-beta-automatic-publication",
            "version": "10",
            "fingerprint": (
                "sha256:5116c5d4b5533577424d2e2eb2df35b9"
                "6051911070c73ece3d44d5a82c567574"
            ),
            "calibration": {
                "id": "reference-lab.drag-beta-freshness",
                "version": "9",
                "fingerprint": (
                    "sha256:835464afcdbe22cf69e7308252d23fb2"
                    "037e5435d834f6ff3b38217909657f28"
                ),
                "success_policy": "published_result",
            },
            "composition_policy": {
                "id": "reference-lab.drag-beta-cohort-composition",
                "version": "10",
                "fingerprint": (
                    "sha256:ccf9ac13ee9da4e2f48edbab549d7f71"
                    "ee5e4c7e02ceb5f09dce927dc13652ba"
                ),
            },
        },
    }
