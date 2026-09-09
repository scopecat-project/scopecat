from __future__ import annotations

from datetime import UTC, datetime

import pytest
from scopecat import Quantity
from scopecat.api.calibration_planner import CalibrationPlanningContext
from scopecat.automation import (
    CalibrationConfigSourceRef,
    CalibrationDependencyEvidence,
    CalibrationTargetRef,
)
from scopecat.config.drafts import ConfigDraft
from scopecat.config.parameter_updates import ParameterUpdate
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash

from reference_lab.configuration import bootstrap_config
from reference_lab.parameters import (
    DRIVE_LO_A,
    LO_FREQUENCY,
    Q0,
    Q0_DRAG_BETA,
    Q1_DRAG_BETA,
    QUARTER_TURN_DURATION,
)
from reference_lab.workflows.drag_beta_freshness import (
    DRAG_BETA_CALIBRATION_TARGETS,
    DRAG_BETA_CALIBRATION_VERSION,
    drag_beta_freshness_calibration,
    drag_beta_semantic_freshness_inputs,
)
from reference_lab.workflows.drag_beta_verification import (
    DRAG_BETA_MINIMUM_IMPROVEMENT,
)


def test_drag_beta_freshness_ignores_registry_only_provenance_changes() -> None:
    config = bootstrap_config()
    first = _planning_context(
        config,
        entry_id="active-entry-1",
        generation=1,
    )
    reactivated = _planning_context(
        config,
        entry_id="active-entry-2",
        generation=2,
    )
    target = DRAG_BETA_CALIBRATION_TARGETS[0]

    first_observation = drag_beta_freshness_calibration.observe(first, target)
    reactivated_observation = drag_beta_freshness_calibration.observe(
        reactivated,
        target,
    )

    assert DRAG_BETA_CALIBRATION_VERSION == "7"
    assert first_observation.inputs == drag_beta_semantic_freshness_inputs(
        config,
        "q0",
    )
    assert reactivated_observation.inputs == first_observation.inputs
    assert drag_beta_freshness_calibration.input_fingerprint(
        reactivated_observation.inputs
    ) == drag_beta_freshness_calibration.input_fingerprint(first_observation.inputs)

    intent = drag_beta_freshness_calibration.build_intent(
        reactivated,
        target,
        reactivated_observation.inputs,
        (),
    )

    assert intent.qubit == "q0"
    assert intent.initial_config == config
    assert intent.initial_config_source.entry_id == "active-entry-2"
    assert intent.initial_config_source.config_ref == "active@2"
    assert intent.initial_config_source.registry_generation == 2


def test_drag_beta_freshness_ignores_profile_and_parameter_snapshot_ids() -> None:
    initial = bootstrap_config()
    renamed = initial.model_copy(
        update={
            "id": "renamed-profile",
            "parameter_snapshot": initial.parameter_snapshot.model_copy(
                update={"id": "renamed-parameter-snapshot"}
            ),
        },
        deep=True,
    )

    assert config_content_hash(initial) != config_content_hash(renamed)
    for qubit in ("q0", "q1"):
        assert drag_beta_semantic_freshness_inputs(
            initial,
            qubit,
        ) == drag_beta_semantic_freshness_inputs(renamed, qubit)


def test_drag_beta_freshness_tracks_own_beta_but_not_peer_beta() -> None:
    initial = bootstrap_config()
    q0_changed = _updated_config(
        initial,
        Q0_DRAG_BETA.update(Quantity(0.6, "ns")),
        candidate_id="q0-beta-changed",
    )
    q1_changed = _updated_config(
        initial,
        Q1_DRAG_BETA.update(Quantity(0.55, "ns")),
        candidate_id="q1-beta-changed",
    )

    initial_q0 = drag_beta_semantic_freshness_inputs(initial, "q0")
    initial_q1 = drag_beta_semantic_freshness_inputs(initial, "q1")
    changed_q0 = drag_beta_semantic_freshness_inputs(q0_changed, "q0")
    changed_q1 = drag_beta_semantic_freshness_inputs(q1_changed, "q1")

    assert changed_q0.prerequisite_fingerprint == initial_q0.prerequisite_fingerprint
    assert changed_q0.active_drag_beta_ns == 0.6
    assert changed_q0 != initial_q0
    assert drag_beta_semantic_freshness_inputs(q0_changed, "q1") == initial_q1

    assert changed_q1.prerequisite_fingerprint == initial_q1.prerequisite_fingerprint
    assert changed_q1.active_drag_beta_ns == 0.55
    assert changed_q1 != initial_q1
    assert drag_beta_semantic_freshness_inputs(q1_changed, "q0") == initial_q0


@pytest.mark.parametrize(
    "update",
    (
        Q0[QUARTER_TURN_DURATION].update(Quantity(17.0, "ns")),
        DRIVE_LO_A[LO_FREQUENCY].update(Quantity(4.86e9, "Hz")),
    ),
)
def test_drag_beta_freshness_tracks_non_owned_prerequisites(
    update: ParameterUpdate,
) -> None:
    initial = bootstrap_config()
    changed = _updated_config(initial, update, candidate_id="upstream-changed")

    for qubit in ("q0", "q1"):
        initial_inputs = drag_beta_semantic_freshness_inputs(initial, qubit)
        changed_inputs = drag_beta_semantic_freshness_inputs(changed, qubit)
        assert (
            changed_inputs.prerequisite_fingerprint
            != initial_inputs.prerequisite_fingerprint
        )
        assert changed_inputs.active_drag_beta_ns == initial_inputs.active_drag_beta_ns


def test_drag_beta_freshness_tracks_verification_threshold() -> None:
    config = bootstrap_config()
    initial = drag_beta_semantic_freshness_inputs(config, "q0")
    stricter = drag_beta_semantic_freshness_inputs(
        config,
        "q0",
        minimum_improvement=DRAG_BETA_MINIMUM_IMPROVEMENT * 2,
    )

    assert stricter.prerequisite_fingerprint == initial.prerequisite_fingerprint
    assert stricter.active_drag_beta_ns == initial.active_drag_beta_ns
    assert stricter.minimum_improvement != initial.minimum_improvement
    assert drag_beta_freshness_calibration.input_fingerprint(
        stricter
    ) != drag_beta_freshness_calibration.input_fingerprint(initial)


def test_drag_beta_candidate_projection_matches_merged_sibling_results() -> None:
    initial = bootstrap_config()
    q0_update = Q0_DRAG_BETA.update(Quantity(0.6, "ns"))
    q1_update = Q1_DRAG_BETA.update(Quantity(0.55, "ns"))
    q0_candidate = _updated_config(
        initial,
        q0_update,
        candidate_id="q0-candidate",
    )
    q1_candidate = _updated_config(
        initial,
        q1_update,
        candidate_id="q1-candidate",
    )
    merged = _updated_config(
        initial,
        q0_update,
        q1_update,
        candidate_id="merged-candidate",
    )

    assert drag_beta_semantic_freshness_inputs(
        q0_candidate,
        "q0",
    ) == drag_beta_semantic_freshness_inputs(merged, "q0")
    assert drag_beta_semantic_freshness_inputs(
        q1_candidate,
        "q1",
    ) == drag_beta_semantic_freshness_inputs(merged, "q1")

    merged_with_upstream_change = _updated_config(
        initial,
        q0_update,
        q1_update,
        Q0[QUARTER_TURN_DURATION].update(Quantity(17.0, "ns")),
        candidate_id="merged-with-upstream-change",
    )
    assert drag_beta_semantic_freshness_inputs(
        q0_candidate,
        "q0",
    ) != drag_beta_semantic_freshness_inputs(merged_with_upstream_change, "q0")


def test_drag_beta_freshness_has_two_bounded_independent_targets() -> None:
    context = _planning_context(
        bootstrap_config(),
        entry_id="active-entry",
        generation=1,
    )

    assert drag_beta_freshness_calibration.select_targets(context) == (
        CalibrationTargetRef(kind="logical_qubit", id="q0"),
        CalibrationTargetRef(kind="logical_qubit", id="q1"),
    )
    assert drag_beta_freshness_calibration.max_in_flight == 2
    assert drag_beta_freshness_calibration.success_policy == "published_result"

    observation = drag_beta_freshness_calibration.observe(
        context,
        DRAG_BETA_CALIBRATION_TARGETS[0],
    )
    dependency = CalibrationDependencyEvidence(
        calibration_key="other-calibration",
        cohort_id="prior-cohort",
        member_id="prior-member",
        procedure_run_id="prior-procedure-run",
        freshness_fingerprint="sha256:" + "1" * 64,
        succeeded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="has no dependencies"):
        drag_beta_freshness_calibration.build_intent(
            context,
            DRAG_BETA_CALIBRATION_TARGETS[0],
            observation.inputs,
            (dependency,),
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


def _planning_context(
    config: ConfigProfileSnapshot,
    *,
    entry_id: str,
    generation: int,
) -> CalibrationPlanningContext:
    content_hash = config_content_hash(config)
    return CalibrationPlanningContext(
        config=config,
        config_source=CalibrationConfigSourceRef(
            entry_id=entry_id,
            config_ref=f"active@{generation}",
            content_hash=content_hash,
            registry_generation=generation,
        ),
    )
