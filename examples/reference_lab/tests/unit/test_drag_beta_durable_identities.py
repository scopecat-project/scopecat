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

    # Software scenario provenance changes setup/config and child binding contracts.
    # Advance all dependent capabilities with the current development format.
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
            "version": "10",
            "fingerprint": (
                "sha256:7046e277a7572f6e3d07526eb2f0cb44b"
                "f982df3d8c090022ffbba65f3ed86b2"
            ),
        },
        "verification_procedure": {
            "id": "reference-lab.drag-beta-verification",
            "version": "10",
            "fingerprint": (
                "sha256:5a644925bd3085b6ef8a59d3061904a9d"
                "d1819c2090445213a170e637a65ff3b"
            ),
        },
        "calibration": {
            "id": "reference-lab.drag-beta-freshness",
            "version": "11",
            "fingerprint": (
                "sha256:75e8778842cc451572ca9df85f86aa413"
                "2d26e18bdd78e535c0db39fc969fdd7"
            ),
            "success_policy": "published_result",
        },
        "composition": {
            "id": "reference-lab.drag-beta-cohort-composition",
            "version": "12",
            "fingerprint": (
                "sha256:18718c76c3823f6f28486ae3ad464c129"
                "2f8e9c8eb79e5f14289b9b1288bdaa1"
            ),
        },
        "automatic_publication": {
            "id": "reference-lab.drag-beta-automatic-publication",
            "version": "12",
            "fingerprint": (
                "sha256:1519486b54684d2d1c8bec21d9df8281d"
                "2b1dee7e6ff23ab0b83d89afab3306c"
            ),
            "calibration": {
                "id": "reference-lab.drag-beta-freshness",
                "version": "11",
                "fingerprint": (
                    "sha256:75e8778842cc451572ca9df85f86aa413"
                    "2d26e18bdd78e535c0db39fc969fdd7"
                ),
                "success_policy": "published_result",
            },
            "composition_policy": {
                "id": "reference-lab.drag-beta-cohort-composition",
                "version": "12",
                "fingerprint": (
                    "sha256:18718c76c3823f6f28486ae3ad464c129"
                    "2f8e9c8eb79e5f14289b9b1288bdaa1"
                ),
            },
        },
    }
