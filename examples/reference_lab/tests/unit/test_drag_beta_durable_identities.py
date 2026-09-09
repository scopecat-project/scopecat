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

    # Plan-aware run requests change the durable child protocol and its dependent
    # calibration/publication capabilities; measurement and fit logic is unchanged.
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
            "version": "7",
            "fingerprint": (
                "sha256:f5d8e38a1005777c721c939d4d90af8"
                "0a58f4afa072205680a065543ad814ac4"
            ),
        },
        "verification_procedure": {
            "id": "reference-lab.drag-beta-verification",
            "version": "6",
            "fingerprint": (
                "sha256:c300dab517f519cebd0bae664339393"
                "f43652a637ee8f1bd2a02e2d1d6c75429"
            ),
        },
        "calibration": {
            "id": "reference-lab.drag-beta-freshness",
            "version": "7",
            "fingerprint": (
                "sha256:215263061d5f5289553286a9602722d"
                "bfe5223c8276cb85ec89dec3e03fc3ef9"
            ),
            "success_policy": "published_result",
        },
        "composition": {
            "id": "reference-lab.drag-beta-cohort-composition",
            "version": "7",
            "fingerprint": (
                "sha256:432673fe8137d0554f767a4485dece6"
                "45f5b335b56b8b69fa640e30329842005"
            ),
        },
        "automatic_publication": {
            "id": "reference-lab.drag-beta-automatic-publication",
            "version": "7",
            "fingerprint": (
                "sha256:5254f0981ce431ea1af525dae0db07c"
                "c2c1cbc316bb68ae4e0ca8c8ab0e03b63"
            ),
            "calibration": {
                "id": "reference-lab.drag-beta-freshness",
                "version": "7",
                "fingerprint": (
                    "sha256:215263061d5f5289553286a9602722d"
                    "bfe5223c8276cb85ec89dec3e03fc3ef9"
                ),
                "success_policy": "published_result",
            },
            "composition_policy": {
                "id": "reference-lab.drag-beta-cohort-composition",
                "version": "7",
                "fingerprint": (
                    "sha256:432673fe8137d0554f767a4485dece6"
                    "45f5b335b56b8b69fa640e30329842005"
                ),
            },
        },
    }
