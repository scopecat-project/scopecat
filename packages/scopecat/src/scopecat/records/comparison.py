"""Typed retained-run selections, model declarations and publication identities."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from scopecat.records.author_revision import AuthorRevisionRef


class ComparisonParameter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    label: str
    default: FiniteFloat
    minimum: FiniteFloat | None = None
    maximum: FiniteFloat | None = None


class ComparisonModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    version: str
    title: str
    description: str
    coordinate: str
    observable: str
    parameters: tuple[ComparisonParameter, ...] = ()


class ComparisonSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    points: tuple[int, ...] = Field(min_length=1, max_length=10000)


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: Literal["list", "inspect", "fit", "candidate", "reject", "handoff"]
    code_revision: AuthorRevisionRef | None = None
    model_id: str = ""
    model_version: str = ""
    primary_run: str = ""
    secondary_run: str = ""
    primary: ComparisonSelection | None = None
    secondary: ComparisonSelection | None = None
    parameters: dict[str, FiniteFloat] = Field(default_factory=dict)
    analysis_id: str = ""
    analysis_hash: str = ""
    actor: str = Field(default="operator", min_length=1)
    reason: str = ""


class ComparisonCurve(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: str
    content_hash: str
    coordinate: str
    observable: str
    coordinate_unit: str | None
    observable_unit: str | None
    x: tuple[FiniteFloat, ...]
    y: tuple[FiniteFloat, ...]


class ComparisonCatalog(BaseModel):
    kind: Literal["catalog"] = "catalog"
    models: tuple[ComparisonModel, ...] = ()
    code_revision: AuthorRevisionRef | None = None


class ComparisonInspection(BaseModel):
    kind: Literal["inspection"] = "inspection"
    code_revision: AuthorRevisionRef | None = None
    primary: ComparisonCurve
    secondary: ComparisonCurve


class ComparisonPublication(BaseModel):
    kind: Literal["publication"] = "publication"
    run_id: str
    analysis_id: str
    publication_hash: str
