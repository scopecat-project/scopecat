"""Small immutable references shared by plans and execution records."""

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.content import Sha256ContentHash


class ExperimentPlanRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    plan_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    content_hash: Sha256ContentHash


class PlanConfigRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    entry_id: str = Field(min_length=1)
    content_hash: Sha256ContentHash


class PlanAnalysisSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str = Field(min_length=1)
    analysis_id: str = Field(min_length=1)
    publication_hash: Sha256ContentHash


class ProcedureChildSubmission(BaseModel):
    """Locate the existing durable step that authorizes a plan-derived child."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    procedure_run_id: str = Field(min_length=1)
    step_key: str = Field(min_length=1)
