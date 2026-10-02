"""Inert scientific evidence captured for data exchange, never installation state."""

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.kernel.content_identity import model_wire_content_hash
from scopecat.records.analysis import (
    AnalysisArtifactRecordOutput,
    AnalysisDatasetRecordOutput,
    AnalysisParameterProposalRecordOutput,
    AnalysisRecord,
)
from scopecat.records.author_revision import AuthorRevisionBundle
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.content import ContentEntry
from scopecat.records.experiment_plan import ExperimentPlanRevision
from scopecat.records.parameter_revision import ParameterRevision
from scopecat.records.run import RunSnapshot
from scopecat.records.run_request import RunRequest
from scopecat.records.setup import SetupDefinitionRevision, SetupRevision


class RunEvidence(BaseModel):
    """A run's accepted intent, effective configuration and retained content index.

    Configuration is historical evidence: its connection descriptions are not
    registrations or authorization to operate devices on the receiving machine.
    Keep the original payload intact so its accepted hash remains checkable.
    Referenced revisions and content bytes belong to the surrounding exchange.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    codec: Literal["scopecat.run-evidence.v1"] = "scopecat.run-evidence.v1"
    source_project_id: str = Field(min_length=1)
    snapshot: RunSnapshot
    request: RunRequest
    configuration: ConfigProfileSnapshot
    contents: tuple[ContentEntry, ...]

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if config_content_hash(self.configuration) != self.snapshot.config_content_hash:
            raise ValueError("run evidence configuration differs from accepted content")
        identities = {(item.role, item.id) for item in self.contents}
        if len(identities) != len(self.contents):
            raise ValueError("run evidence contains duplicate content identities")
        return self


class InputRevisionEvidence(BaseModel):
    """Exact input revisions; contains no active heads or device registrations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameters: tuple[ParameterRevision, ...] = ()
    setups: tuple[SetupRevision, ...] = ()
    setup_definitions: tuple[SetupDefinitionRevision, ...] = ()
    plans: tuple[ExperimentPlanRevision, ...] = ()
    authors: tuple[AuthorRevisionBundle, ...] = ()


class AnalysisEvidence(BaseModel):
    """An exact analysis publication and the index of its retained outputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    entry: ContentEntry
    record: AnalysisRecord
    published_at: datetime
    contents: tuple[ContentEntry, ...]

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if (
            self.entry.role != "record"
            or self.entry.kind != "analysis"
            or self.entry.content_hash != model_wire_content_hash(self.record)
        ):
            raise ValueError("analysis evidence differs from its content identity")
        entries = {(item.role, item.id): item for item in self.contents}
        if (
            len(entries) != len(self.contents)
            or entries.get(("record", self.entry.id)) != self.entry
        ):
            raise ValueError("analysis evidence has an inconsistent content index")
        for output in self.record.outputs:
            match output:
                case AnalysisArtifactRecordOutput():
                    entry = entries.get(("artifact", output.content.artifact_id))
                    if (
                        entry is None
                        or entry.content_hash != output.content.content_hash
                    ):
                        raise ValueError(
                            "analysis artifact evidence is missing or differs"
                        )
                case AnalysisDatasetRecordOutput():
                    entry = entries.get(("dataset", output.content.dataset_id))
                    if (
                        entry is None
                        or entry.content_hash != output.content.content_hash
                    ):
                        raise ValueError(
                            "analysis dataset evidence is missing or differs"
                        )
                case AnalysisParameterProposalRecordOutput():
                    entry = entries.get(("record", output.content.proposal_id))
                    if entry is None or entry.kind != "parameter_change_proposal":
                        raise ValueError("analysis proposal evidence is missing")
                case _:
                    pass  # Facts, tables and figure descriptions are inline.
        return self
