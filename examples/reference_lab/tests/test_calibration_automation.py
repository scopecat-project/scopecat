"""One real-daemon vertical for resident calibration orchestration.

Proof validation, manual publication, CAS conflicts, and rollback branches live
in focused project/server tests so they do not repeat the virtual experiments.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from scopecat import Quantity
from scopecat.api.project_worker import ProjectAutomationWorker
from scopecat.automation import RunOutputRef
from scopecat.config.registry.records import (
    CalibrationCohortMergeRegistrySource,
    ContextConfigRegistrySource,
)
from scopecat.project import load_project
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.sample import SampleRevisionDraft
from scopecat_server.lifecycle import start_project, stop_project

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.workflows.drag_beta_automatic_publication import (
    DRAG_BETA_PUBLICATION_POLICY_REF,
)
from reference_lab.workflows.drag_beta_freshness import (
    DRAG_BETA_CALIBRATION_FANOUT_SCOPE,
    drag_beta_semantic_freshness_inputs,
)


def test_resident_automatic_publication_survives_restart_and_q0_only(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "reference-lab-resident-calibration"
    shutil.copytree(EXAMPLE_ROOT / "config", project_root / "config")
    shutil.copytree(EXAMPLE_ROOT / "src", project_root / "src")
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", project_root / "scopecat.toml")
    project = load_project(project_root / "scopecat.toml")

    first_record = start_project(project)
    try:
        with create_application(project_root).connect(first_record.base_url) as lab:
            active_before = lab.config.active()
            sample = lab.samples.create(
                "calibration-chip",
                kind="synthetic",
                content=SampleRevisionDraft(display_name="Calibration chip"),
            )
            saved = lab.config.save_context(
                entry_id="calibration-parked",
                base=ConfigContextRef(
                    entry_id=active_before.entry.id,
                    content_hash=active_before.entry.content_hash,
                ),
                sample=sample.selector(),
                working_point_id="parked",
                label="Parked",
                parameters=active_before.config.parameter_snapshot,
            )
            working_point = ConfigContextRef(
                entry_id=saved.entry.id,
                content_hash=saved.entry.content_hash,
            )
            frozen_sample = lab.config.resolve_context(
                working_point
            ).config_source.sample
            worker = ProjectAutomationWorker(
                lab.procedures,
                planner=lab.procedures.interval_planner(),
                calibration_finalizer=lab.calibrations.publication_finalizer(),
                calibration_evaluator=lab.calibrations.evaluator(
                    working_point=working_point
                ),
                worker_id="reference-lab-resident-before-restart",
                runnable_limit=2,
            )

            completed = worker.cycle()

            assert completed.publications.ready_items == 0
            assert completed.publications.published_items == 0
            assert completed.calibrations.admitted_members == 2
            assert completed.calibrations.created_cohorts == 1
            assert completed.procedures.dispatched == 2
            [summary] = lab.calibrations.list(
                fanout_scope=DRAG_BETA_CALIBRATION_FANOUT_SCOPE
            ).items
            cohort = lab.calibrations.get(summary.cohort_id)
            assert cohort.spec.automatic_publication == (
                DRAG_BETA_PUBLICATION_POLICY_REF
            )
            member_page = lab.calibrations.members(cohort.cohort_id)
            assert tuple(member.spec.target.id for member in member_page.items) == (
                "q0",
                "q1",
            )
            for member in member_page.items:
                procedure = lab.procedures.get(member.procedure_run_id)
                assert procedure.snapshot.resolved_samples == (
                    sample.selector(
                        revision=frozen_sample.revision, context_id="parked"
                    ),
                )
                for step_key in ("baseline", "candidate"):
                    output = procedure.output(step_key)
                    assert isinstance(output, RunOutputRef)
                    snapshot = lab.get_run(output.run_id).snapshot
                    assert snapshot.samples == (frozen_sample,)
            ready = lab.calibrations.publication_finalization(cohort.cohort_id)
            assert ready.state == "ready"
            assert ready.policy == DRAG_BETA_PUBLICATION_POLICY_REF
            assert ready.base_config_source == cohort.spec.config_source
            assert lab.config.active().activation == active_before.activation

        stopped = stop_project(project)
        assert stopped.state == "running"
        restarted_record = start_project(project)

        with create_application(project_root).connect(restarted_record.base_url) as lab:
            worker = ProjectAutomationWorker(
                lab.procedures,
                planner=lab.procedures.interval_planner(),
                calibration_finalizer=lab.calibrations.publication_finalizer(),
                calibration_evaluator=lab.calibrations.evaluator(
                    working_point=working_point
                ),
                worker_id="reference-lab-resident-after-restart",
                runnable_limit=2,
            )

            published = worker.cycle()

            assert published.publications.ready_items == 1
            assert published.publications.prepared_items == 1
            assert published.publications.published_items == 1
            assert published.publications.failures == 0
            assert published.config_planning_blocked is False
            assert published.calibrations.fresh_members == 2
            assert published.calibrations.pending_publication_members == 0
            assert published.calibrations.admitted_members == 0
            assert published.calibrations.created_cohorts == 0
            assert published.procedures.dispatched == 0

            active_published = lab.config.latest_context(working_point)
            assert active_published.entry.id != saved.entry.id
            assert lab.config.active() == active_before
            finalized = lab.calibrations.publication_finalization(cohort.cohort_id)
            assert finalized.state == "published"
            assert finalized.publication is not None
            assert isinstance(
                active_published.entry.source,
                ContextConfigRegistrySource,
            )
            assert isinstance(
                active_published.entry.source.publication,
                CalibrationCohortMergeRegistrySource,
            )
            assert len(active_published.entry.source.publication.contributions) == 2
            calibration_keys = tuple(
                member.spec.calibration_key for member in member_page.items
            )
            statuses = lab.calibrations.status(
                calibration_keys,
                fanout_scope=DRAG_BETA_CALIBRATION_FANOUT_SCOPE,
            )
            assert all(
                status.latest_success is not None
                and status.latest_success.publication is not None
                and status.latest_success.publication.operation_id
                == finalized.publication.operation_id
                for status in statuses.statuses
            )

            replay = worker.cycle()
            assert replay.publications.ready_items == 0
            assert replay.publications.published_items == 0
            assert replay.calibrations.fresh_members == 2
            assert replay.calibrations.created_cohorts == 0
            assert lab.config.active() == active_before

            q0_inputs = drag_beta_semantic_freshness_inputs(
                active_published.config,
                "q0",
            )
            workspace = lab.config.workspace(context="calibration-parked", latest=True)
            workspace["qubits"]["q0"]["drag_beta"] = Quantity(
                q0_inputs.active_drag_beta_ns + 0.5, "ns"
            )
            external_q0 = workspace.save(note="Exercise workspace-owned publication")

            q0_completed = worker.cycle()

            assert q0_completed.publications.ready_items == 0
            assert q0_completed.publications.published_items == 0
            assert q0_completed.calibrations.fresh_members == 1
            assert q0_completed.calibrations.ready_members == 1
            assert q0_completed.calibrations.admitted_members == 1
            assert q0_completed.calibrations.created_cohorts == 1
            assert q0_completed.procedures.dispatched == 1
            summaries = lab.calibrations.list(
                fanout_scope=DRAG_BETA_CALIBRATION_FANOUT_SCOPE
            ).items
            [q0_summary] = (
                item for item in summaries if item.cohort_id != cohort.cohort_id
            )
            q0_cohort = lab.calibrations.get(q0_summary.cohort_id)
            [q0_member] = lab.calibrations.members(q0_cohort.cohort_id).items
            assert q0_member.spec.target.id == "q0"
            assert (
                lab.calibrations.publication_finalization(q0_cohort.cohort_id).state
                == "ready"
            )

            q0_published = worker.cycle()

            assert q0_published.publications.ready_items == 1
            assert q0_published.publications.prepared_items == 1
            assert q0_published.publications.published_items == 1
            assert q0_published.publications.failures == 0
            assert q0_published.config_planning_blocked is False
            assert q0_published.calibrations.fresh_members == 2
            assert q0_published.calibrations.pending_publication_members == 0
            assert q0_published.calibrations.admitted_members == 0
            assert q0_published.calibrations.created_cohorts == 0
            assert q0_published.procedures.dispatched == 0
            active_q0_published = lab.config.latest_context(working_point)
            assert active_q0_published.entry.id != external_q0.name
            assert lab.config.active() == active_before
            q0_finalized = lab.calibrations.publication_finalization(
                q0_cohort.cohort_id
            )
            assert q0_finalized.state == "published"
            assert q0_finalized.publication is not None
            assert isinstance(
                active_q0_published.entry.source,
                ContextConfigRegistrySource,
            )
            assert isinstance(
                active_q0_published.entry.source.publication,
                CalibrationCohortMergeRegistrySource,
            )
            assert len(active_q0_published.entry.source.publication.contributions) == 1
            q0_statuses = {
                status.calibration_key: status
                for status in lab.calibrations.status(
                    calibration_keys,
                    fanout_scope=DRAG_BETA_CALIBRATION_FANOUT_SCOPE,
                ).statuses
            }
            q0_success = q0_statuses[q0_member.spec.calibration_key].latest_success
            assert q0_success is not None
            assert q0_success.publication is not None
            assert (
                q0_success.publication.operation_id
                == q0_finalized.publication.operation_id
            )
            [q1_member] = (
                member for member in member_page.items if member.spec.target.id == "q1"
            )
            q1_success = q0_statuses[q1_member.spec.calibration_key].latest_success
            assert q1_success is not None
            assert q1_success.publication is not None
            assert (
                q1_success.publication.operation_id
                == finalized.publication.operation_id
            )

            q0_replay = worker.cycle()
            assert q0_replay.publications.ready_items == 0
            assert q0_replay.publications.published_items == 0
            assert q0_replay.calibrations.fresh_members == 2
            assert q0_replay.calibrations.created_cohorts == 0
            assert lab.config.active() == active_before
    finally:
        stop_project(project)
