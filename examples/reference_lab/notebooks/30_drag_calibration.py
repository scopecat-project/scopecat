"""Run the supported DRAG-beta calibration through an exact config restore."""

from __future__ import annotations

# %%
import scopecat as sc
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ConfigPublishOutputRef,
    RunOutputRef,
)
from scopecat.records.run import ConfigRegistryRunConfigSource

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import show
from reference_lab.workflows.drag_beta_experiment import drag_beta_experiment
from reference_lab.workflows.drag_beta_procedure import (
    DragBetaProcedureIntent,
    drag_beta_calibration_procedure,
    drag_beta_calibration_request_key,
)
from reference_lab.workflows.drag_beta_verification import (
    DRAG_BETA_VERIFICATION_SCHEMA,
)
from reference_lab.workflows.production_drag_gate import production_drag_experiment

# %%
lab = sc.open_project(EXAMPLE_ROOT).connect()
initial_config, initial_config_source = lab.config.resolve_with_source("active")
if (
    initial_config_source is None
    or initial_config_source.kind != "config_registry"
    or initial_config_source.registry_generation is None
):
    raise RuntimeError("active config has no exact registry generation")
prepared = lab.prepare(drag_beta_experiment.build(), config=initial_config)
preview = prepared.preview()
procedure = lab.procedures.start(
    drag_beta_calibration_procedure,
    DragBetaProcedureIntent(
        initial_config=initial_config,
        initial_config_source=initial_config_source,
    ),
    request_key=drag_beta_calibration_request_key(initial_config_source),
)

# %%
baseline_output = procedure.output("baseline")
fit_output = procedure.output("fit")
candidate_output = procedure.output("candidate")
verification_output = procedure.output("verification")
accept_output = procedure.output("accept")
if not isinstance(baseline_output, RunOutputRef):
    raise TypeError("DRAG baseline step did not produce a run")
if not isinstance(fit_output, AnalysisPublicationOutputRef):
    raise TypeError("DRAG fit step did not produce an analysis publication")
if not isinstance(candidate_output, RunOutputRef):
    raise TypeError("DRAG candidate step did not produce a run")
if not isinstance(verification_output, AnalysisPublicationOutputRef):
    raise TypeError("DRAG verification step did not produce an analysis publication")
if not isinstance(accept_output, ConfigPublishOutputRef):
    raise TypeError("DRAG accept step did not publish a configuration")

baseline_run = lab.get_run(baseline_output.run_id)
analysis = baseline_run.published_analysis(fit_output.analysis_record_id)
candidate = analysis.candidate_config()
fit_report = baseline_run.published_analysis(analysis.id).artifact("fit-report")
[proposal] = analysis.parameter_proposals

# The procedure's candidate run records analysis provenance without changing default.
candidate_run = lab.get_run(candidate_output.run_id)

# %%
verification = lab.published_analysis(
    verification_output.analysis_record_id,
)
verification_decision = verification.fact_as(
    "decision",
    DRAG_BETA_VERIFICATION_SCHEMA,
)
verification_report = verification.artifact("verification-report")
if not verification_decision.accepted:
    raise RuntimeError("DRAG beta candidate did not improve the verification scan")

# The procedure changed the default only after checkpointing the exact decision proof.
accepted = lab.config.entry(accept_output.entry_id)
if accepted.entry.content_hash != accept_output.entry_content_hash:
    raise RuntimeError("published procedure output does not match its config entry")
accepted_source = ConfigRegistryRunConfigSource(
    selector="active",
    entry_id=accepted.entry.id,
    config_ref=accepted.entry.config_ref,
    content_hash=accepted.entry.content_hash,
    registry_generation=accept_output.generation,
)

production_run = lab.execute_invocation(
    production_drag_experiment.build(),
    config=accepted.config,
    config_source=accepted_source,
    name="Production X90 with accepted DRAG beta",
    tags=("calibration", "production-gate", "active-config"),
)

# %%
# Restore the exact starting entry; relative undo is not safe to replay.
restored = lab.config.activate_entry(
    initial_config_source.entry_id,
    operation_id=f"reference-lab.drag-beta.restore:{procedure.id}",
    expected_generation=accept_output.generation,
    actor="nightly-calibration",
    note="restore the exact starting config after the calibration example",
)
candidate_source = candidate_run.snapshot.config_source
production_source = production_run.snapshot.config_source
accepted_source_identity = (
    accepted_source.selector,
    accepted_source.entry_id,
    accepted_source.config_ref,
    accepted_source.content_hash,
    accepted_source.registry_generation,
)
production_source_identity = (
    (
        production_source.selector,
        production_source.entry_id,
        production_source.config_ref,
        production_source.content_hash,
        production_source.registry_generation,
    )
    if production_source is not None and production_source.kind == "config_registry"
    else None
)
procedure_snapshot = procedure.snapshot
procedure_steps = procedure.steps(limit=10).items

drag_beta_summary = {
    "procedure": procedure.id,
    "procedure_state": procedure_snapshot.state,
    "procedure_status": (
        None
        if procedure_snapshot.closure is None
        else procedure_snapshot.closure.status
    ),
    "procedure_steps": {step.step_key: step.state for step in procedure_steps},
    "status": baseline_run.status,
    "point_count": preview.point_count,
    "analysis": analysis.id,
    "proposal_id": proposal.id,
    "output_kinds": [output.kind for output in analysis.outputs],
    "execution_evidence": len(analysis.executions),
    "fit_report": fit_report.entry.filename,
    "proposal_evidence": proposal.evidence_output_ids,
    "verification": verification.id,
    "verification_subject": verification.view.analysis.subject.kind,
    "verification_inputs": [
        (item.id, item.target, item.role) for item in verification.inputs
    ],
    "verification_improvement": verification_decision.improvement,
    "verification_accepted": verification_decision.accepted,
    "verification_report": verification_report.entry.filename,
    "verification_is_project_owned": all(
        entry.id != verification.id
        for entry in (
            *baseline_run.contents(role="record").items,
            *candidate_run.contents(role="record").items,
        )
    ),
    "accepted_verification": (
        accepted.entry.source.acceptance.decision.analysis_record_id
        if accepted.entry.source.kind == "candidate_config"
        and accepted.entry.source.acceptance.kind == "cross_run_verification"
        else None
    ),
    "accepted_output_matches_entry": (
        accept_output.entry_id == accepted.entry.id
        and accept_output.entry_content_hash == accepted.entry.content_hash
    ),
    "accepted_source_identity": accepted_source_identity,
    "production_source_identity": production_source_identity,
    "production_config_content_hash": production_run.snapshot.config_content_hash,
    "candidate_run_uses_analysis": (
        candidate_source is not None
        and candidate_source.kind == "analysis_candidate"
        and candidate_source.proposal_id == candidate.proposal_id
    ),
    "accepted_as_default": (
        production_source_identity == accepted_source_identity
        and production_run.snapshot.config_content_hash == accepted.entry.content_hash
    ),
    "restore_operation": restored.operation.operation_id,
    "default_restored": (
        restored.activation.entry_id == initial_config_source.entry_id
        and restored.activation.entry_content_hash == initial_config_source.content_hash
    ),
}
show(drag_beta_summary)
