from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError
from scopecat_testkit.records import assert_model_round_trip

from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ConfigActivationOutputRef,
    ConfigPublishOutputRef,
    InterpretationOutputRef,
    InterpretationRequest,
    InterpretationResponse,
    ProcedureClosure,
    ProcedureDefinitionRef,
    ProcedureRun,
    ProcedureStepAttempt,
    ProcedureStepOperation,
    ProcedureStepOutputRef,
    RunOutputRef,
    procedure_intent_hash,
)
from scopecat.records.analysis import ProjectAnalysisSubject

_HASH = "sha256:" + "1" * 64
_OTHER_HASH = "sha256:" + "2" * 64
_START = datetime(2026, 8, 18, tzinfo=UTC)
_END = _START + timedelta(seconds=1)
_INTENT: dict[str, JsonValue] = {"target_ids": ["q0"]}


@dataclass(frozen=True)
class _ResonatorSelection:
    resonator: str


def _definition() -> ProcedureDefinitionRef:
    return ProcedureDefinitionRef(
        id="drag-calibration",
        version="1",
        fingerprint=_HASH,
    )


def test_procedure_run_round_trips_versioned_definition() -> None:
    run = ProcedureRun(
        procedure_run_id="procedure-1",
        request_key="drag-q0",
        definition=_definition(),
        intent=_INTENT,
        intent_hash=procedure_intent_hash(_definition(), _INTENT),
        revision=1,
        state="ready",
        created_at=_START,
        updated_at=_END,
    )

    restored = assert_model_round_trip(run)

    assert restored == run
    assert restored.definition.version == "1"


def test_procedure_run_rejects_waiting_and_requires_terminal_details() -> None:
    common = {
        "procedure_run_id": "procedure-1",
        "request_key": "drag-q0",
        "definition": _definition(),
        "intent": _INTENT,
        "intent_hash": procedure_intent_hash(_definition(), _INTENT),
        "revision": 1,
    }

    with pytest.raises(ValidationError, match="Input should be"):
        ProcedureRun.model_validate({**common, "state": "waiting"})
    with pytest.raises(ValidationError, match="requires a reason"):
        ProcedureRun.model_validate({**common, "state": "attention_required"})
    with pytest.raises(ValidationError, match="requires a closure"):
        ProcedureRun.model_validate({**common, "state": "closed"})


def test_closed_procedure_run_has_typed_terminal_result() -> None:
    run = ProcedureRun(
        procedure_run_id="procedure-1",
        request_key="drag-q0",
        definition=_definition(),
        intent=_INTENT,
        intent_hash=procedure_intent_hash(_definition(), _INTENT),
        revision=4,
        state="closed",
        created_at=_START,
        updated_at=_END,
        closure=ProcedureClosure(status="succeeded", closed_at=_END),
    )

    assert assert_model_round_trip(run) == run
    with pytest.raises(ValidationError, match="requires a reason"):
        ProcedureClosure(status="failed", closed_at=_END)


def test_procedure_run_rejects_intent_hash_not_covering_canonical_intent() -> None:
    with pytest.raises(ValidationError, match="must cover its definition"):
        ProcedureRun(
            procedure_run_id="procedure-1",
            request_key="drag-q0",
            definition=_definition(),
            intent=_INTENT,
            intent_hash=_OTHER_HASH,
            revision=1,
            state="ready",
        )


def test_procedure_intent_is_recursively_immutable_and_json_round_trips() -> None:
    intent: dict[str, JsonValue] = {
        "target": {"ids": ["q0"], "options": {"verify": True}}
    }
    run = ProcedureRun(
        procedure_run_id="procedure-1",
        request_key="drag-q0",
        definition=_definition(),
        intent=intent,
        intent_hash=procedure_intent_hash(_definition(), intent),
        revision=1,
        state="ready",
    )

    target = cast("dict[str, object]", run.intent["target"])
    ids = cast("list[object]", target["ids"])
    with pytest.raises(TypeError, match="immutable"):
        cast("dict[str, object]", run.intent)["new"] = True
    with pytest.raises(TypeError):
        target["new"] = True
    with pytest.raises(TypeError):
        ids[0] = "q1"

    assert assert_model_round_trip(run) == run
    assert run.intent_hash == procedure_intent_hash(run.definition, run.intent)


@pytest.mark.parametrize(
    "operation,output",
    [
        ("run", RunOutputRef(run_id="run-1")),
        (
            "analysis",
            AnalysisPublicationOutputRef(
                subject=ProjectAnalysisSubject(),
                analysis_record_id="analysis-verify-r1",
            ),
        ),
        (
            "config_activation",
            ConfigActivationOutputRef(
                generation=3,
                entry_id="config-3",
                entry_content_hash=_HASH,
            ),
        ),
        (
            "config_publish",
            ConfigPublishOutputRef(
                generation=4,
                entry_id="config-4",
                entry_content_hash=_HASH,
            ),
        ),
    ],
)
def test_successful_step_attempt_round_trips_typed_output(
    operation: ProcedureStepOperation,
    output: ProcedureStepOutputRef,
) -> None:
    attempt = ProcedureStepAttempt(
        procedure_run_id="procedure-1",
        step_key="verify",
        attempt=1,
        operation=operation,
        intent_hash=_OTHER_HASH,
        revision=2,
        state="succeeded",
        started_at=_START,
        updated_at=_END,
        finished_at=_END,
        output=output,
    )

    restored = assert_model_round_trip(attempt)

    assert restored == attempt
    assert type(restored.output) is type(output)


def test_step_attempt_rejects_output_from_another_operation() -> None:
    with pytest.raises(ValidationError, match="output kind must match"):
        ProcedureStepAttempt(
            procedure_run_id="procedure-1",
            step_key="baseline",
            attempt=1,
            operation="run",
            intent_hash=_OTHER_HASH,
            revision=1,
            state="succeeded",
            started_at=_START,
            updated_at=_END,
            finished_at=_END,
            output=AnalysisPublicationOutputRef(
                subject=ProjectAnalysisSubject(),
                analysis_record_id="analysis-fit-r1",
            ),
        )


def test_interpretation_attempt_retains_exact_typed_request_and_response() -> None:
    schema = AnalysisFactSchema(
        "tests.resonator-selection.v1",
        _ResonatorSelection,
    )
    request = InterpretationRequest(
        title="Select resonator",
        instructions="Return the selected resonator.",
        schema_id=schema.id,
        schema_hash=schema.schema_hash,
        structure=schema.structure,
        response_template={"resonator": "replace after reviewing the trace"},
    )
    waiting = ProcedureStepAttempt(
        procedure_run_id="procedure-1",
        step_key="select-resonator",
        attempt=1,
        operation="interpretation",
        intent_hash=request.request_hash,
        revision=2,
        state="waiting_for_input",
        started_at=_START,
        updated_at=_END,
        interpretation_request=request,
    )
    output = InterpretationOutputRef(
        procedure_run_id=waiting.procedure_run_id,
        step_key=waiting.step_key,
        request_hash=request.request_hash,
        response=InterpretationResponse(
            actor="operator@example.test",
            actor_kind="human",
            value={"resonator": "r2"},
            submitted_at=_END,
        ),
    )
    succeeded = waiting.model_copy(
        update={
            "revision": 3,
            "state": "succeeded",
            "finished_at": _END,
            "output": output,
        }
    )

    assert assert_model_round_trip(waiting) == waiting
    assert assert_model_round_trip(succeeded).output == output
    with pytest.raises(ValidationError, match="response template"):
        InterpretationRequest(
            title=request.title,
            instructions=request.instructions,
            schema_id=request.schema_id,
            schema_hash=request.schema_hash,
            structure=request.structure,
            response_template={"resonator": 2},
        )
    with pytest.raises(ValidationError, match="schema hash"):
        InterpretationRequest(
            title=request.title,
            instructions=request.instructions,
            schema_id=request.schema_id,
            schema_hash=_HASH,
            structure=request.structure,
        )


def test_step_attempt_records_unique_exact_upstream_outputs() -> None:
    upstream = RunOutputRef(run_id="run-baseline")
    with pytest.raises(ValidationError, match="input references must be unique"):
        ProcedureStepAttempt(
            procedure_run_id="procedure-1",
            step_key="fit",
            attempt=1,
            operation="analysis",
            intent_hash=_OTHER_HASH,
            inputs=(upstream, upstream),
            revision=1,
            state="running",
            started_at=_START,
            updated_at=_START,
        )


def test_step_attempt_enforces_terminal_and_attention_details() -> None:
    common = {
        "procedure_run_id": "procedure-1",
        "step_key": "baseline",
        "attempt": 1,
        "operation": "run",
        "intent_hash": _OTHER_HASH,
        "revision": 1,
        "started_at": _START,
        "updated_at": _END,
    }

    with pytest.raises(ValidationError, match="requires output and finish time"):
        ProcedureStepAttempt.model_validate({**common, "state": "succeeded"})
    with pytest.raises(
        ValidationError,
        match="failed procedure step requires a reason",
    ):
        ProcedureStepAttempt.model_validate(
            {**common, "state": "failed", "finished_at": _END}
        )
    with pytest.raises(
        ValidationError,
        match=r"attention-required.*requires a reason",
    ):
        ProcedureStepAttempt.model_validate({**common, "state": "attention_required"})


def test_procedure_models_are_immutable_and_hashes_are_explicit() -> None:
    definition = _definition()

    with pytest.raises(ValidationError, match="frozen"):
        definition.version = "2"
    with pytest.raises(ValidationError):
        ProcedureDefinitionRef(
            id="drag-calibration",
            version="1",
            fingerprint="not-a-content-hash",
        )


@pytest.mark.parametrize("frequency", [6490, 6510, 6489, 6511, None])
def test_opt_in_answer_contract_and_legacy_identity(frequency: float | None):
    from scopecat.automation import AnswerCondition, AnswerContract, ScalarAnswerRule
    from scopecat.kernel.content_identity import stable_content_hash

    structure: JsonValue = {
        "type": "object",
        "fields": {
            "outcome": {"type": "literal", "values": ["Peak selected", "Unsure"]},
            "frequency": {
                "type": "union",
                "variants": [{"type": "float"}, {"type": "null"}],
            },
        },
    }
    from scopecat.analysis.facts import analysis_fact_structure_hash

    legacy = {
        "title": "Peak",
        "instructions": "Choose",
        "schema_id": "peak.v1",
        "schema_codec": "scopecat.analysis-fact-schema.v1",
        "schema_hash": analysis_fact_structure_hash(structure),
        "structure": structure,
        "response_template": None,
        "metadata": {},
    }
    request = InterpretationRequest.model_validate(legacy)
    assert request.model_dump(mode="json") == legacy
    assert request.request_hash == "sha256:" + stable_content_hash(
        {
            "codec": "scopecat.interpretation-request.v1",
            "request": legacy,
        }
    )
    value: JsonValue = {"outcome": "Peak selected", "frequency": frequency}
    assert request.normalize_answer(value) == value
    request = InterpretationRequest.model_validate(
        {
            **legacy,
            "answer_contract": AnswerContract(
                fields={
                    "frequency": ScalarAnswerRule(
                        minimum=6490,
                        maximum=6510,
                        required_when=AnswerCondition(
                            field="outcome", value="Peak selected"
                        ),
                        empty_otherwise=True,
                    ),
                }
            ),
        }
    )
    assert (
        InterpretationRequest.model_validate_json(request.model_dump_json()) == request
    )
    assert (
        request.request_hash
        != InterpretationRequest.model_validate(legacy).request_hash
    )
    if frequency in (6490, 6510):
        assert request.normalize_answer(value) == value
    else:
        with pytest.raises(ValueError, match="frequency"):
            request.normalize_answer(value)
    assert request.normalize_answer({"outcome": "Unsure", "frequency": frequency}) == {
        "outcome": "Unsure",
        "frequency": None,
    }
    with pytest.raises(TypeError):
        request.normalize_answer({"outcome": "Unsure"})


@pytest.mark.parametrize(
    "rules",
    [
        {"frequency": {"minimum": 2, "maximum": 1}},
        {"frequency": {"empty_otherwise": True}},
        {"missing": {"minimum": 0}},
        {"choice": {"minimum": 0}},
        {"frequency": {"required_when": {"field": "missing", "value": "yes"}}},
        {"frequency": {"required_when": {"field": "choice", "value": 1}}},
    ],
)
def test_answer_contract_rejects_invalid_declarations(rules: dict[str, JsonValue]):
    from scopecat.analysis.facts import analysis_fact_structure_hash

    structure: JsonValue = {
        "type": "object",
        "fields": {
            "choice": {"type": "string"},
            "frequency": {
                "type": "union",
                "variants": [{"type": "float"}, {"type": "null"}],
            },
        },
    }
    with pytest.raises(ValidationError):
        InterpretationRequest.model_validate(
            {
                "title": "Peak",
                "instructions": "Choose",
                "schema_id": "peak.v1",
                "schema_hash": analysis_fact_structure_hash(structure),
                "structure": structure,
                "answer_contract": {"version": 1, "fields": rules},
            }
        )
