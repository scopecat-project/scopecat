from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import pytest
from pydantic import BaseModel, ConfigDict, Field, field_serializer

import scopecat as sc
from scopecat.analysis.facts import ANALYSIS_FACT_SCHEMA_CODEC


@dataclass(frozen=True, slots=True)
class _FitSummary:
    resonance: sc.Quantity
    quality: float
    label: str = "fit"


@dataclass(frozen=True, slots=True)
class _RenamedFitSummary:
    resonance: sc.Quantity
    quality: float
    label: str = "renamed default"


@dataclass(frozen=True, slots=True)
class _DifferentFitSummary:
    resonance: sc.Quantity
    converged: bool


class _DescribedFit(BaseModel):
    quality: float = Field(description="First description")


class _RedescribedFit(BaseModel):
    quality: float = Field(description="Changed documentation only")


class _CustomSerializedFit(BaseModel):
    quality: float

    @field_serializer("quality")
    def serialize_quality(self, value: float) -> str:
        return str(value)


def test_fact_schema_hash_uses_stable_scopecat_structure() -> None:
    original = sc.AnalysisFactSchema("tests.fit.v1", _FitSummary)
    renamed = sc.AnalysisFactSchema("tests.fit.v1", _RenamedFitSummary)

    assert original.schema_codec == ANALYSIS_FACT_SCHEMA_CODEC
    assert original.schema_hash == renamed.schema_hash
    assert original.structure == {
        "type": "object",
        "fields": {
            "resonance": {
                "type": "quantity",
                "value": {"type": "float"},
                "unit": {"type": "string"},
            },
            "quality": {"type": "float"},
            "label": {"type": "string"},
        },
    }


def test_fact_schema_hash_ignores_pydantic_documentation() -> None:
    described = sc.AnalysisFactSchema("tests.described.v1", _DescribedFit)
    redescribed = sc.AnalysisFactSchema("tests.described.v1", _RedescribedFit)

    assert described.schema_hash == redescribed.schema_hash


def test_fact_schema_hash_changes_with_the_canonical_shape() -> None:
    original = sc.AnalysisFactSchema("tests.fit.v1", _FitSummary)
    changed = sc.AnalysisFactSchema("tests.fit.v1", _DifferentFitSummary)

    assert original.schema_hash != changed.schema_hash


def test_fact_schema_rejects_serializer_output_outside_its_structure() -> None:
    schema = sc.AnalysisFactSchema("tests.custom-serializer.v1", _CustomSerializedFit)

    with pytest.raises(TypeError, match=r"\$fact\.quality.*float"):
        schema.encode(_CustomSerializedFit(quality=0.9))


def test_fact_schema_ignores_analysis_projection_metadata() -> None:
    @dataclass(frozen=True, slots=True)
    class First:
        value: Annotated[float, sc.AnalysisField(label="First label")]

    @dataclass(frozen=True, slots=True)
    class Second:
        value: Annotated[float, sc.AnalysisField(label="Second label", unit="ratio")]

    assert (
        sc.AnalysisFactSchema("tests.value.v1", First).schema_hash
        == sc.AnalysisFactSchema("tests.value.v1", Second).schema_hash
    )


@dataclass(frozen=True)
class _Selection:
    positions: tuple[int, ...]


@dataclass(frozen=True)
class _SelectedFit:
    selection: _Selection
    coefficients: tuple[float, ...]


class _StrictSelectedFit(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)
    selection: _Selection
    coefficients: tuple[float, ...]


@pytest.mark.parametrize("model", [_SelectedFit, _StrictSelectedFit])
def test_persisted_json_reconstructs_tuple_and_nested_typed_facts(
    model: type[_SelectedFit | _StrictSelectedFit],
) -> None:
    value = model(selection=_Selection((4, 1, 2)), coefficients=(0.5, 1.0))
    schema = sc.AnalysisFactSchema("tests.selected-fit.v1", model)
    encoded = schema.encode(value)
    assert encoded == {
        "selection": {"positions": [4, 1, 2]},
        "coefficients": [0.5, 1.0],
    }
    restored = schema.decode(encoded)
    assert restored == value
    assert restored.selection.positions == (4, 1, 2)
    assert schema.encode(restored) == encoded
    with pytest.raises(TypeError, match="int"):
        schema.decode({"selection": {"positions": [True]}, "coefficients": [0.5]})
