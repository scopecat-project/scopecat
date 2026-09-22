"""Parameter revision content independent of executable device definitions."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.content import Sha256ContentHash
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot


class ParameterRevisionContent(BaseModel):
    """Scientific parameter declarations/values and their snapshot labels.

    Exact setup association belongs to the owning revision record. This payload
    contains no instrument registry, topology, routing or connection settings.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    system_id: str
    catalog: ParameterCatalog
    parameters: ParameterSnapshot


class ParameterRevisionRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    revision_id: str = Field(min_length=1)
    content_hash: Sha256ContentHash


class ParameterRevision(BaseModel):
    """Independent declarations and values, without execution or validity claims."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    catalog: ParameterCatalog
    parameters: ParameterSnapshot
    content_hash: Sha256ContentHash
    actor: str = Field(min_length=1)
    note: str = ""
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def check_content(self) -> ParameterRevision:
        if self.content_hash != parameter_revision_hash(self.catalog, self.parameters):
            raise ValueError("parameter revision hash does not match content")
        return self

    @property
    def ref(self) -> ParameterRevisionRef:
        return ParameterRevisionRef(revision_id=self.id, content_hash=self.content_hash)


def parameter_revision_hash(
    catalog: ParameterCatalog, parameters: ParameterSnapshot
) -> Sha256ContentHash:
    return sha256_json_hash(
        {
            "codec": "scopecat.parameter-revision.v1",
            "catalog": catalog.model_dump(mode="json"),
            "parameters": parameters.model_dump(mode="json"),
        }
    )
