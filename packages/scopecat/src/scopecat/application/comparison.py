"""Bounded retained-run reanalysis requests for a project-owned Python model.

This protocol never admits an acquisition. Publications belong to the explicitly
selected primary run; the secondary run remains an independently owned input.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel

from scopecat.records.comparison import (
    ComparisonCatalog,
    ComparisonInspection,
    ComparisonPublication,
    ComparisonRequest,
)
from scopecat.records.launch_request import LaunchRequest

if TYPE_CHECKING:
    from scopecat.api.lab import LabClient


class ComparisonHandoff(BaseModel):
    kind: Literal["handoff"] = "handoff"
    request: LaunchRequest
    source_run: str
    source_analysis: str
    source_hash: str


type ComparisonResult = (
    ComparisonCatalog | ComparisonInspection | ComparisonPublication | ComparisonHandoff
)


class ComparisonProvider(Protocol):
    def __call__(
        self, lab: LabClient, request: ComparisonRequest
    ) -> ComparisonResult: ...
