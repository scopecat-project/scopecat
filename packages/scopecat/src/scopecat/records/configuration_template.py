"""Adapter-owned configuration recipes, imported into existing scientific stores."""

from pydantic import BaseModel, ConfigDict, Field

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.content import Sha256ContentHash
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.setup import ExecutableSetupSnapshot


class ConfigurationTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""
    setup: ExecutableSetupSnapshot
    catalog: ParameterCatalog
    parameters: ParameterSnapshot

    @property
    def content_hash(self) -> Sha256ContentHash:
        return sha256_json_hash(
            {
                "codec": "scopecat.configuration-template.v2",
                "template": self.model_dump(mode="json"),
            }
        )
