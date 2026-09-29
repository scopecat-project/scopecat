"""Exact retained judgment authority shared by run and project analyses."""

from scopecat.automation.models import InterpretationOutputRef
from scopecat.records.analysis import AnalysisInterpretationReference

from scopecat_server.errors import BackendConflict
from scopecat_server.storage.sqlite.automation import SQLiteAutomationStore
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane


def validate_interpretation(
    control: SQLiteControlPlane, source: AnalysisInterpretationReference
) -> None:
    store = SQLiteAutomationStore(control.sqlite)
    with control.sqlite.read_connection() as connection:
        step = store.latest_step_attempt_in_transaction(
            connection, source.procedure_run_id, source.step_key
        )
    if (
        step is None
        or step.state != "succeeded"
        or not isinstance(step.output, InterpretationOutputRef)
        or step.output.analysis_reference != source
    ):
        raise BackendConflict(
            "analysis interpretation must match an existing successful judgment"
        )
