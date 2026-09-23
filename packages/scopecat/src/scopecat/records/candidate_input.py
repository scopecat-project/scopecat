"""Exact retained candidate input, shared by run provenance and contexts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from scopecat.records.config import ConfigContentHash


class AnalysisCandidateRunConfigSource(BaseModel):
    """Analysis candidate resolved for one run without becoming the default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["analysis_candidate"] = "analysis_candidate"
    source_run_id: str
    analysis_record_id: str
    proposal_id: str
    base_config_content_hash: ConfigContentHash
    content_hash: ConfigContentHash

    @model_validator(mode="after")
    def validate_identity(self) -> AnalysisCandidateRunConfigSource:
        if (
            not self.source_run_id
            or not self.analysis_record_id
            or not self.proposal_id
        ):
            raise ValueError("analysis candidate run source identity must be non-empty")
        return self
