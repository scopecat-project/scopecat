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

    # Explicit experiment builds change procedure and transitive policy identity.
    # Advance their durable versions; scientific measurement and fit stay unchanged.
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
            "version": "8",
            "fingerprint": (
                "sha256:d9af400d7aac20bf87062fa6def36af62"
                "7251b2adecc660c51adc31d24a7e2ca"
            ),
        },
        "verification_procedure": {
            "id": "reference-lab.drag-beta-verification",
            "version": "7",
            "fingerprint": (
                "sha256:27d687f5a69cbddbc1bffd92340a9bba6"
                "86eb189ad7bc1dc094ddc92956d10f0"
            ),
        },
        "calibration": {
            "id": "reference-lab.drag-beta-freshness",
            "version": "8",
            "fingerprint": (
                "sha256:0ac7a851e5aeadb6aed3aadd0ff41c5fd"
                "82bc1e8db3adfc442f49346b91cc31c"
            ),
            "success_policy": "published_result",
        },
        "composition": {
            "id": "reference-lab.drag-beta-cohort-composition",
            "version": "9",
            "fingerprint": (
                "sha256:3533262a933abef22e4ddb940db8e8d0d"
                "05cc8248817348a482b46bcd83768ce"
            ),
        },
        "automatic_publication": {
            "id": "reference-lab.drag-beta-automatic-publication",
            "version": "9",
            "fingerprint": (
                "sha256:1d279b564c53ffacbe36f11d26cd8c5e9"
                "80246bad50088384387294f3c998d3b"
            ),
            "calibration": {
                "id": "reference-lab.drag-beta-freshness",
                "version": "8",
                "fingerprint": (
                    "sha256:0ac7a851e5aeadb6aed3aadd0ff41c5fd"
                    "82bc1e8db3adfc442f49346b91cc31c"
                ),
                "success_policy": "published_result",
            },
            "composition_policy": {
                "id": "reference-lab.drag-beta-cohort-composition",
                "version": "9",
                "fingerprint": (
                    "sha256:3533262a933abef22e4ddb940db8e8d0d"
                    "05cc8248817348a482b46bcd83768ce"
                ),
            },
        },
    }
