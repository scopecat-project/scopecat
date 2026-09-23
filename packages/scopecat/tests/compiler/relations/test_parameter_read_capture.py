import pytest
from pydantic import TypeAdapter

from scopecat.compiler.parameter_overlays import (
    PointParameterOverlay,
    parameter_cell_bindings,
    resolve_point_parameters,
)
from scopecat.compiler.relations.context import EvalContext, ParameterRelationData
from scopecat.compiler.relations.evaluation import evaluate_scalar
from scopecat.compiler.relations.evaluator import evaluate_scalar_expression
from scopecat.compiler.relations.parameter_reads import ParameterReadRecorder
from scopecat.compiler.relations.specialization import specialize_scalar_expression
from scopecat.config.parameter_reads import compare_expression_parameter_reads
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.value_types import Entity, Float, Scalar, String
from scopecat.program.expressions import (
    ParameterLookupScalarExpr,
    ParameterLookupUse,
    input_ref,
    lit,
    param,
    parameter_lookup,
)
from scopecat.records.parameter import (
    ParameterSnapshot,
    ScalarParameterValue,
    TableParameterValue,
)
from scopecat.records.parameter_read import ScalarExpressionReadEvidence

FLOAT = Scalar(Float())
STRING = Scalar(String())


def lookup(
    column: str, key: object, *, value_type: Scalar = FLOAT, key_type: Scalar = STRING
) -> ParameterLookupScalarExpr:
    return parameter_lookup(
        ParameterLookupUse(
            table_id="channels",
            key_input_types=(("id", key_type),),
            literal_key_columns=frozenset(),
            column_id=column,
            result_type=value_type,
        ),
        key={"id": key},
    )


def parameters() -> ParameterRelationData:
    return ParameterRelationData(
        scalars={"gain": 2.0},
        tables={
            "channels": [
                {"id": "q0", "peer": "q1", "offset": 0.1},
                {"id": "q1", "peer": "q0", "offset": 0.2},
            ]
        },
    )


def snapshot(offset: float = 0.1) -> ParameterSnapshot:
    return ParameterSnapshot(
        id="current",
        values=(
            ScalarParameterValue(id="gain", value=2.0),
            TableParameterValue(
                id="channels",
                rows=(
                    {"id": "q0", "peer": "q1", "offset": offset},
                    {"id": "q1", "peer": "q0", "offset": 0.2},
                ),
            ),
        ),
    )


def test_specialization_and_evaluation_capture_same_nested_dependencies() -> None:
    peer = lookup("peer", lit("q0", STRING), value_type=STRING)
    expression = lookup("offset", peer) * param("gain", FLOAT)
    captured: list[ScalarExpressionReadEvidence] = []
    for specialize in (False, True):
        recorder = ParameterReadRecorder()
        context = EvalContext(params=parameters(), parameter_reads=recorder)
        result = (
            specialize_scalar_expression(expression, known=context)
            if specialize
            else evaluate_scalar(expression, context)
        )
        assert result == (lit(0.4, FLOAT) if specialize else 0.4)
        evidence = recorder.snapshot()
        codec = TypeAdapter(ScalarExpressionReadEvidence)
        evidence = codec.validate_json(codec.dump_json(evidence))
        assert evidence.incomplete_reasons == ()
        assert len(evidence.scalars) == 1 and len(evidence.keyed) == 2
        assert (
            compare_expression_parameter_reads(evidence, snapshot()).status
            == "unchanged"
        )
        captured.append(evidence)
    assert captured[0] == captured[1]


def test_point_overlay_reads_stay_local_to_each_effective_point() -> None:
    base = parameters()
    expression = lookup("offset", lit("q0", STRING))
    overlay = PointParameterOverlay(
        "channels", 0, {"id": "q0"}, "offset", "sweep", FLOAT
    )
    for offset in (0.3, 0.4):
        recorder = ParameterReadRecorder()
        effective = resolve_point_parameters(
            base, (overlay,), point_row={"sweep": offset}
        )
        assert (
            evaluate_scalar_expression(
                expression, EvalContext(params=effective, parameter_reads=recorder)
            )
            == offset
        )
        evidence = recorder.snapshot()
        assert len(evidence.keyed) == 1
        assert (
            compare_expression_parameter_reads(evidence, snapshot(offset)).status
            == "unchanged"
        )
        assert (
            compare_expression_parameter_reads(evidence, snapshot()).status == "changed"
        )
    assert evaluate_scalar_expression(expression, EvalContext(params=base)) == 0.1


def test_relation_entity_string_keys_keep_their_execution_matching_semantics() -> None:
    entity_type = Scalar(Entity(entity_kind="qubit"))
    expression = lookup(
        "offset",
        lit(EntityRef(id="q0", kind="qubit"), entity_type),
        key_type=entity_type,
    )
    recorder = ParameterReadRecorder()
    assert (
        evaluate_scalar_expression(
            expression, EvalContext(params=parameters(), parameter_reads=recorder)
        )
        == 0.1
    )
    assert (
        compare_expression_parameter_reads(recorder.snapshot(), snapshot()).status
        == "unchanged"
    )


def test_unresolved_specialization_does_not_claim_no_dependencies() -> None:
    recorder = ParameterReadRecorder()
    expression = lookup("offset", input_ref("later", STRING))
    assert (
        specialize_scalar_expression(
            expression, known=EvalContext(params=parameters(), parameter_reads=recorder)
        )
        == expression
    )
    comparison = compare_expression_parameter_reads(recorder.snapshot(), snapshot())
    assert comparison.status == "unknown"
    assert "residual_expression" in comparison.incomplete_reasons


def test_failed_checked_evaluation_retains_incomplete_coverage() -> None:
    recorder = ParameterReadRecorder()
    with pytest.raises(ValueError, match="absent"):
        evaluate_scalar(
            param("absent", FLOAT),
            EvalContext(params=parameters(), parameter_reads=recorder),
        )
    assert (
        compare_expression_parameter_reads(recorder.snapshot(), snapshot()).status
        == "unknown"
    )


def test_symbolic_overlay_cannot_be_mistaken_for_a_read_of_the_base_cell() -> None:
    recorder = ParameterReadRecorder()
    expression = lookup("offset", lit("q0", STRING))
    overlay = PointParameterOverlay(
        "channels", 0, {"id": "q0"}, "offset", "sweep", FLOAT
    )
    result = specialize_scalar_expression(
        expression,
        known=EvalContext(
            params=parameters(), point_row={"sweep": 0.3}, parameter_reads=recorder
        ),
        parameter_cells=parameter_cell_bindings((overlay,)),
    )
    assert result == lit(0.3, FLOAT)
    assert recorder.snapshot().keyed == ()
    assert (
        compare_expression_parameter_reads(recorder.snapshot(), snapshot()).status
        == "unknown"
    )


def test_changed_scalar_is_reported_by_name() -> None:
    recorder = ParameterReadRecorder()
    evaluate_scalar(
        param("gain", FLOAT), EvalContext(params=parameters(), parameter_reads=recorder)
    )
    changed = ParameterSnapshot(
        id="current", values=(ScalarParameterValue(id="gain", value=3.0),)
    )
    comparison = compare_expression_parameter_reads(recorder.snapshot(), changed)
    assert comparison.status == "changed"
    assert comparison.changed_scalars == ("gain",)


def test_failed_specialization_is_not_complete_evidence() -> None:
    recorder = ParameterReadRecorder()
    with pytest.raises(ValueError, match="zero"):
        specialize_scalar_expression(
            param("gain", FLOAT) / 0,
            known=EvalContext(params=parameters(), parameter_reads=recorder),
        )
    assert "specialization_failed" in recorder.snapshot().incomplete_reasons
