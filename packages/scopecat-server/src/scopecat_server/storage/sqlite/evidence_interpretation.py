"""Capture exact historical judgments rather than today's latest step attempt."""

import sqlite3
from collections.abc import Iterator
from typing import cast

from scopecat.automation.models import (
    InterpretationOutputRef,
    ProcedureRun,
    ProcedureStepAttempt,
)
from scopecat.data_exchange.models import InterpretationEvidence
from scopecat.records.analysis import AnalysisInterpretationReference


def capture_interpretation_evidence(
    connection: sqlite3.Connection, reference: AnalysisInterpretationReference
) -> InterpretationEvidence:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT run_json FROM procedure_runs WHERE procedure_run_id=?",
            (reference.procedure_run_id,),
        ).fetchone(),
    )
    if row is None:
        raise KeyError(f"missing procedure evidence: {reference.procedure_run_id}")
    procedure = ProcedureRun.model_validate_json(cast("str", row[0]))
    rows = cast(
        "Iterator[sqlite3.Row]",
        connection.execute(
            "SELECT attempt_json FROM procedure_step_attempts "
            "WHERE procedure_run_id=? AND step_key=? ORDER BY attempt",
            (reference.procedure_run_id, reference.step_key),
        ),
    )
    for row in rows:
        step = ProcedureStepAttempt.model_validate_json(cast("str", row[0]))
        if (
            step.state == "succeeded"
            and isinstance(step.output, InterpretationOutputRef)
            and step.output.analysis_reference == reference
        ):
            return InterpretationEvidence(
                reference=reference, procedure=procedure, step=step
            )
    raise KeyError(
        "missing interpretation evidence: "
        f"{reference.procedure_run_id}/{reference.step_key}"
    )
