"""Client-local defaults; prepared launches retain their resolved wire requests."""

from enum import Enum
from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field

from scopecat.records.config_context import ConfigContextRef
from scopecat.records.experimental_batch import ExperimentalBatchId
from scopecat.records.record_collection import RecordCollectionId
from scopecat.records.sample import SampleId


class SessionDefault(Enum):
    INHERIT = "session_default"


INHERIT = SessionDefault.INHERIT


class SessionContext(BaseModel):
    """Immutable selection snapshot, independent of daemon-wide active state."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sample: SampleId | None = None
    batch: ExperimentalBatchId | None = None
    working_point: ConfigContextRef | None = None
    collection: RecordCollectionId | None = None
    operator: str = Field(default="operator", min_length=1)

    def scientific_scope(
        self,
        *,
        context: ConfigContextRef | SessionDefault | None,
        sample: str | SessionDefault | None,
        explicit_parameters: bool,
    ) -> tuple[ConfigContextRef | None, str | None]:
        """Explicit scientific selections replace the inherited scope as a whole."""
        if (
            not explicit_parameters
            and isinstance(context, SessionDefault)
            and isinstance(sample, SessionDefault)
        ):
            return self.working_point, self.sample
        return (
            None if isinstance(context, SessionDefault) else context,
            None if isinstance(sample, SessionDefault) else sample,
        )


class SessionContextUpdate(TypedDict, total=False):
    sample: str | None
    batch: str | None
    working_point: ConfigContextRef | None
    collection: str | None
    operator: str
