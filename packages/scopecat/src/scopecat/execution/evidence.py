"""Build durable run evidence from local effect results."""

from __future__ import annotations

from scopecat.execution.effect_result import RunEffectResult
from scopecat.kernel.content_identity import model_wire_content_hash
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.measurements.datasets import (
    MEASUREMENT_DATASET_KIND,
    RAW_MEASUREMENTS_DATASET_ID,
)
from scopecat.records.content import ContentEntry
from scopecat.records.costs import RunCompilationCost
from scopecat.records.execution import (
    InstrumentStateEvidence,
    summarize_instrument_state_evidence,
)
from scopecat.records.measurement import MeasurementDatasetSchema
from scopecat.runs.refs import record_content_ref
from scopecat.sdk.domain.evidence import DomainExecutionEvidence

COMPILATION_COST_ID = "compilation-cost"
COMPILATION_COST_KIND = "run_compilation_cost"


def compilation_cost_ref() -> str:
    return record_content_ref(record_id=COMPILATION_COST_ID, kind=COMPILATION_COST_KIND)


INSTRUMENT_STATE_EVIDENCE_ID = "instrument-state-evidence"
INSTRUMENT_STATE_EVIDENCE_KIND = "instrument_state_evidence"
DOMAIN_EXECUTION_EVIDENCE_ID = "domain-execution-evidence"
DOMAIN_EXECUTION_EVIDENCE_KIND = "domain_execution_evidence"


def instrument_state_evidence_ref() -> str:
    return record_content_ref(
        record_id=INSTRUMENT_STATE_EVIDENCE_ID,
        kind=INSTRUMENT_STATE_EVIDENCE_KIND,
    )


def domain_execution_evidence_ref() -> str:
    return record_content_ref(
        record_id=DOMAIN_EXECUTION_EVIDENCE_ID,
        kind=DOMAIN_EXECUTION_EVIDENCE_KIND,
    )


def build_terminal_contents(
    *,
    outcome: RunOutcome,
    measurement_count: int,
    dataset_content_hash: str | None,
    dataset_schema: MeasurementDatasetSchema | None,
    expected_record_count: int | None,
    instrument_state: InstrumentStateEvidence | None,
    domain_execution: DomainExecutionEvidence | None = None,
    compilation_cost: RunCompilationCost | None = None,
) -> tuple[ContentEntry, ...]:
    incomplete_run = outcome.result != "succeeded"
    partial = incomplete_run and (
        expected_record_count is None or expected_record_count != measurement_count
    )
    datasets: list[ContentEntry] = []
    if dataset_content_hash is not None:
        if dataset_schema is None:
            raise ValueError("recorded measurements require a sealed dataset contract")
        datasets.append(
            ContentEntry(
                role="dataset",
                id=RAW_MEASUREMENTS_DATASET_ID,
                kind=MEASUREMENT_DATASET_KIND,
                media_type="application/x-ndjson",
                schema=dataset_schema.model_dump(mode="json"),
                content_hash=dataset_content_hash,
                metadata=(
                    {
                        "partial": partial,
                        "run_result": outcome.result,
                        "run_certainty": outcome.certainty,
                        **(
                            {"expected_record_count": expected_record_count}
                            if expected_record_count is not None
                            else {}
                        ),
                    }
                    if partial
                    else {}
                ),
            )
        )
    elif measurement_count:
        raise ValueError("recorded measurements require a sealed dataset contract")
    records: list[ContentEntry] = []
    if compilation_cost is not None:
        records.append(
            ContentEntry(
                role="record",
                id=COMPILATION_COST_ID,
                kind=COMPILATION_COST_KIND,
                media_type="application/json",
                content_hash=model_wire_content_hash(compilation_cost),
            )
        )
    if instrument_state is not None:
        records.append(
            ContentEntry(
                role="record",
                id=INSTRUMENT_STATE_EVIDENCE_ID,
                kind=INSTRUMENT_STATE_EVIDENCE_KIND,
                media_type="application/json",
                content_hash=model_wire_content_hash(instrument_state),
                metadata={
                    "summary": summarize_instrument_state_evidence(
                        instrument_state
                    ).model_dump(mode="json")
                },
            )
        )
    if domain_execution is not None:
        records.append(
            ContentEntry(
                role="record",
                id=DOMAIN_EXECUTION_EVIDENCE_ID,
                kind=DOMAIN_EXECUTION_EVIDENCE_KIND,
                media_type="application/json",
                content_hash=model_wire_content_hash(domain_execution),
                metadata={
                    "detail_complete": domain_execution.detail_complete,
                    "attempt_count": domain_execution.attempt_count,
                    "receipt_count": domain_execution.receipt_count,
                    "completed_count": domain_execution.completed_count,
                    "target_ids": list(domain_execution.target_ids),
                    "transition_policies": list(domain_execution.transition_policies),
                },
            )
        )
    return (*records, *datasets)


def build_instrument_state_evidence(
    run_id: str,
    result: RunEffectResult,
) -> InstrumentStateEvidence:
    return InstrumentStateEvidence(
        run_id=run_id,
        observed_state=list(result.observed_state),
        baseline_state=list(result.baseline_state),
        final_state=list(result.final_state),
        state_actions=result.state_actions,
        finalization_actions=list(result.finalization_actions),
    )
