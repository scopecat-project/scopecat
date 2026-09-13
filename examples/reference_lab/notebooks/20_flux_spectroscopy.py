"""Run and analyze vendor-neutral resonator flux spectroscopy."""

from __future__ import annotations

# %%
from pathlib import Path

import scopecat as sc

from reference_lab.notebook import show

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# %%
with sc.open_project(PROJECT_ROOT).connect(operator="notebook-demo") as lab:
    # Connecting loads this project's ``src`` tree before workflow imports.
    from reference_lab.workflows.flux_spectroscopy import (
        flux_spectroscopy,
    )
    from reference_lab.workflows.flux_spectroscopy_analysis import (
        FLUX_SPECTROSCOPY_FIT_REVIEW_SCHEMA,
        flux_spectroscopy_analysis,
        flux_spectroscopy_fit_review,
    )

    invocation = flux_spectroscopy.build()
    preview = lab.preview(invocation)
    run = lab.run(
        invocation,
        name="Virtual resonator flux spectroscopy",
        tags=("spectroscopy", "virtual-instruments"),
    )

    analysis = run.analyze(flux_spectroscopy_analysis())
    review = run.analyze(flux_spectroscopy_fit_review())
    quality_review = review.fact_as(
        "quality-review",
        FLUX_SPECTROSCOPY_FIT_REVIEW_SCHEMA,
    )
    candidate = analysis.candidate_config()
    candidate_snapshot = lab.resolve_config(candidate)
    report = run.published_analysis(analysis.id).artifact("fit-report")
    [proposal] = analysis.parameter_proposals

    summary = {
        "run_id": run.id,
        "status": run.status,
        "point_count": preview.point_count,
        "measurement_records": len(run.measurements().records),
        "analysis_id": analysis.id,
        "analysis_revision": analysis.revision,
        "fit_review_id": review.id,
        "fit_review_accepted": quality_review.accepted,
        "fit_report": report.entry.filename,
        "proposal_id": proposal.id,
        "candidate_config_id": candidate_snapshot.id,
    }

show(summary)
