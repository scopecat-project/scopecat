# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, assert_type

import pytest

import scopecat as sc
from scopecat.analysis.facts import ordinary_result_schema
from scopecat.api.analysis import AnalysisInvocation
from scopecat.measurements.dataset import Dataset


@dataclass(frozen=True)
class Fit:
    candidate: float | None
    converged: bool


@sc.analysis_function
def fit(data: Dataset, *, threshold: float = 0.2) -> Fit:
    values = data["result"].require_values()
    return Fit(float(len(values)) if threshold < 1 else None, threshold < 1)


def test_ordinary_function_keeps_python_configuration_signature() -> None:
    invocation = assert_type(fit(threshold=0.5), AnalysisInvocation)
    assert invocation.implementation_fingerprint.startswith("sha256:")
    assert invocation.arguments == (("threshold", 0.5),)
    assert fit().arguments == (("threshold", 0.2),)
    with pytest.raises(TypeError, match="unexpected keyword"):
        fit(unknown=2)  # pyright: ignore[reportCallIssue]


def test_plain_dataclass_rows_infer_nullable_fields_and_explicit_units() -> None:
    @dataclass
    class Row:
        x: float = field(metadata={"unit": "GHz", "role": "coordinate"})
        candidate: float | None

    table = sc.derived_dataset([Row(4.8, None), Row(4.9, None)])
    assert table.schema.fields[0].unit == "GHz"
    assert table.schema.fields[0].role == "coordinate"
    assert table.table["candidate"].to_pylist() == [None, None]
    assert str(table.table.schema.field("candidate").type) == "double"


def test_inferred_result_schema_preserves_failure_and_rejects_arbitrary_objects() -> (
    None
):
    schema = ordinary_result_schema(Fit)
    assert schema.decode(schema.encode(Fit(None, False))) == Fit(None, False)
    with pytest.raises(TypeError, match="standard dataclass"):
        ordinary_result_schema(object)
    with pytest.raises(TypeError, match="requires"):
        schema.encode(object())  # pyright: ignore[reportArgumentType]


if TYPE_CHECKING:

    def types(data: Dataset, result: sc.AnalysisResult[Fit]) -> None:
        assert_type(fit.function(data, threshold=0.5), Fit)
        assert_type(result.value.candidate, float | None)
        fit(threshold="invalid")  # pyright: ignore[reportArgumentType]
        result.value.candidate + 1  # pyright: ignore[reportOptionalOperand,reportUnusedExpression]
