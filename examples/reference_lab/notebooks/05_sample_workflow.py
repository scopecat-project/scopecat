"""Register one chip, bind a run, and publish longitudinal sample analysis."""

from __future__ import annotations

# %%
import scopecat as sc
from scopecat.api.run import RunHandle
from scopecat.kernel.entity import EntityRef
from scopecat.records.config import Topology
from scopecat.records.sample import SampleRevisionDraft

from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.notebook import show
from reference_lab.workflows.ramsey_experiments import q0_ramsey


@sc.analysis_step(id="reference-lab.sample-run-summary")
def sample_run_summary(
    context: sc.AnalysisContext,
    *,
    run: RunHandle,
) -> sc.Analysis:
    """Retain a small longitudinal conclusion over one exact chip run."""

    measurements = context.measurements(run, id="ramsey")
    return (
        context.result("Chip A17 run summary")
        .fact("measurement-records", len(measurements.records))
        .fact("run-status", run.status)
    )


# %%
with sc.open_project(EXAMPLE_ROOT).connect(operator="gallery") as lab:
    chip = lab.samples.create(
        "chip-a17",
        kind="chip",
        content=SampleRevisionDraft(
            display_name="Chip A17",
            status="available",
            design_ref="reference-lab:four-qubit-v1",
            tags=("reference-lab", "four-qubit"),
            topology=Topology(
                entities=[EntityRef(id=f"q{index}", kind="qubit") for index in range(4)]
            ),
        ),
        note="Register the deterministic reference chip",
        operation_id="reference-lab:sample:chip-a17:create",
    )
    chip.revise(
        SampleRevisionDraft(
            display_name="Chip A17 in virtual cooldown",
            status="mounted",
            design_ref="reference-lab:four-qubit-v1",
            tags=("reference-lab", "four-qubit", "virtual-cooldown"),
            topology=chip.revision(1).content.topology,
        ),
        expected_revision=1,
        note="Mount the chip for gallery measurements",
        operation_id="reference-lab:sample:chip-a17:mount",
    )
    run = lab.run(
        q0_ramsey.build(),
        name="Chip A17 q0 Ramsey",
        tags=("gallery", "sample", "ramsey"),
        sample=chip.selector(context_id="virtual-cooldown-1"),
    )
    analysis = lab.analyze(sample_run_summary(run=run), sample=chip)
    binding = run.samples[0]
    sample_workflow_summary = {
        "sample_id": chip.id,
        "active_revision": chip.view.record.active_revision,
        "bound_revision": binding.revision,
        "binding_role": binding.role,
        "binding_context": binding.context_id,
        "run_status": run.status,
        "analysis_subject": analysis.view.analysis.subject.kind,
        "analysis_inputs": len(analysis.view.analysis.inputs),
    }
show(sample_workflow_summary)
