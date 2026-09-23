"""Client-local selection; prepared launches retain their resolved evidence."""

from enum import Enum
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.config_context import ConfigContextRef
from scopecat.records.parameter_revision import ParameterRevision, ParameterRevisionRef
from scopecat.records.record_collection import RecordCollectionId
from scopecat.records.scientific_selection import ScientificSelection
from scopecat.records.setup import SetupRevision, SetupRevisionRef
from scopecat.records.target_catalog import TargetRevisionRef


class SessionDefault(Enum):
    INHERIT = "session_default"


INHERIT = SessionDefault.INHERIT


class SessionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    science: ScientificSelection = Field(default_factory=ScientificSelection)
    collection: RecordCollectionId | None = None
    operator: str = Field(default="operator", min_length=1)
    parameter_branch: str | None = None


class SessionContextUpdate(TypedDict, total=False):
    selection: ScientificSelection
    target: str | TargetRevisionRef | None
    sample: str | None
    batch: str | None
    working_point: ConfigContextRef | None
    parameters: str | ParameterRevision | ParameterRevisionRef | None
    parameter_branch: str | None
    setup: SetupRevision | SetupRevisionRef | None
    collection: str | None
    operator: str
