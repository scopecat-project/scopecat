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
from scopecat.records.config_context import ContextRunConfigSource
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.plan_ref import PlanConfigRef
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ConfigRegistryRunConfigSource,
    ParameterRunConfigSource,
    RunSnapshot,
)
from scopecat.records.scientific_binding import RegisteredTargetSubject, UnboundSubject
from scopecat.records.scientific_scope import DeclaredBatch, UnscopedBatch
from scopecat.records.scientific_selection import (
    CandidateConfiguration,
    ParameterConfiguration,
    RegisteredTargetChoice,
    SampleSubjectChoice,
    SavedConfiguration,
    ScientificSelection,
    UnboundSubjectChoice,
    WorkingPointConfiguration,
)

if TYPE_CHECKING:
    from scopecat.api.lab import LabClient


def comparison_selection(run: RunSnapshot) -> ScientificSelection:
    """Pin follow-up inputs to a retained run instead of today's lab defaults.

    This selects inputs, not scientific approval or permission to execute them.
    A launch still requires a new preview and current authority checks.
    """
    source = run.config_source
    if isinstance(source, ParameterRunConfigSource):
        configuration = ParameterConfiguration(
            ref=source.parameters, setup=source.setup, overrides=source.overrides
        )
    elif isinstance(source, ConfigRegistryRunConfigSource):
        configuration = SavedConfiguration(
            ref=PlanConfigRef(
                entry_id=source.entry_id, content_hash=source.content_hash
            )
        )
    elif isinstance(source, ContextRunConfigSource):
        configuration = WorkingPointConfiguration(
            ref=source.context, overrides=source.overrides
        )
    elif isinstance(source, AnalysisCandidateRunConfigSource):
        configuration = CandidateConfiguration(source=source)
    else:
        raise ValueError("follow-up requires retained configuration provenance")
    binding = run.scientific_binding
    if isinstance(binding.subject, RegisteredTargetSubject):
        subject = RegisteredTargetChoice(ref=binding.subject.ref)
    elif isinstance(binding.subject, UnboundSubject):
        subject = UnboundSubjectChoice()
    else:
        if len(binding.samples) != 1 or binding.samples[0].role != "subject":
            raise ValueError("authored follow-up requires one subject sample")
        sample = binding.samples[0]
        subject = SampleSubjectChoice(
            sample_id=sample.sample_id, revision=sample.revision
        )
    batch = binding.samples[0].batch_id if binding.samples else None
    return ScientificSelection(
        subject=subject,
        configuration=configuration,
        batch=DeclaredBatch(id=batch) if batch is not None else UnscopedBatch(),
    )


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
