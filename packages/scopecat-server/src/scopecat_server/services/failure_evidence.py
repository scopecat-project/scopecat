"""Short saved failure evidence; bulk vendor bytes remain outside run views."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from scopecat.control.models import DurableEvent
from scopecat.daemon.views import RunDetail, RunFailureEvidence, WorkerDiagnosticLink
from scopecat.kernel.problems import Problem


def failure_evidence(
    detail: RunDetail, events: Sequence[DurableEvent], *, truncated: bool
) -> RunFailureEvidence:
    saved: list[Problem] = []
    secondary: list[Problem] = []
    for event in events:
        values = event.payload.get("problems")
        if not isinstance(values, list):
            continue
        target = (
            secondary if event.kind == "run_hardware_finalization_failed" else saved
        )
        target.extend(Problem.model_validate(value) for value in values)
    outcome = detail.snapshot.outcome
    if outcome is not None:
        # A later explicitly resumed terminal result supersedes prior segment
        # failures. Do not present old cleanup events as a new successful-run failure.
        saved = list(outcome.problems)
        secondary = []
    ordered: list[Problem] = []
    seen: set[tuple[str, str | None]] = set()
    for item in (*saved, *secondary):
        key = item.code, item.occurrence_id
        if key not in seen:
            seen.add(key)
            ordered.append(item)
    links: list[WorkerDiagnosticLink] = []
    seen_references: set[tuple[str, int | None]] = set()
    for item in ordered:
        reference = item.details.get("worker_diagnostic")
        if not isinstance(reference, Mapping):
            continue
        link = WorkerDiagnosticLink.model_validate(reference)
        key = link.generation, link.request_id
        if key in seen_references:
            continue
        seen_references.add(key)
        if link.retention == "retained":
            link = link.model_copy(
                update={
                    "href": f"/api/v1/instrument-workers/{link.generation}/diagnostics"
                }
            )
        links.append(link)
    return RunFailureEvidence(
        primary=ordered[0] if ordered else None,
        secondary=tuple(ordered[1:]),
        diagnostics=tuple(links),
        terminal_persistence="confirmed" if outcome is not None else "unconfirmed",
        truncated=truncated,
    )
