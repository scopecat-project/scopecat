"""Core input reads retained in the existing domain invocation ledger."""

from collections.abc import Mapping
from typing import cast

from scopecat.kernel.json_types import JsonValue
from scopecat.records.execution import DomainInvocationIntent
from scopecat.records.parameter_read import DomainInputParameterEvidence
from scopecat.sdk.domain.batch import DomainBatchRequest

_KEY = "scopecat.domain.parameter_reads"


def attach_domain_input_reads(
    context: DomainBatchRequest, target_intent: Mapping[str, JsonValue]
) -> Mapping[str, JsonValue]:
    """Attach only when the core supplied materialization evidence."""
    entries = context.inputs.parameter_reads
    if entries is None:
        return target_intent
    if _KEY in target_intent:
        raise ValueError("target intent already contains core domain input reads")
    expected = {
        (point.ordinal, kind, name)
        for point in context.points
        for kind, columns in (
            ("program", context.inputs.program),
            ("compiler", context.inputs.compiler),
        )
        for name, _ in columns
    }
    actual = {
        (entry.point_ordinal, entry.input_kind, entry.input_id) for entry in entries
    }
    if actual != expected or len(actual) != len(entries):
        raise ValueError(
            "domain input reads must cover exactly the selected points and inputs"
        )
    record = DomainInputParameterEvidence(entries=entries)
    return {**target_intent, _KEY: cast("JsonValue", record.model_dump(mode="json"))}


def read_domain_input_reads(
    intent: DomainInvocationIntent,
) -> DomainInputParameterEvidence:
    """Decode retained reads without loading author code or re-evaluating inputs."""
    if _KEY not in intent.target_intent:
        raise ValueError("invocation has no domain input parameter evidence")
    return DomainInputParameterEvidence.model_validate(intent.target_intent[_KEY])
