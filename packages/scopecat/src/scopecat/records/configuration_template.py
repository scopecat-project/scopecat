"""Adapter-owned configuration recipes, imported into existing scientific stores."""

from pydantic import BaseModel, ConfigDict, Field

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.content import Sha256ContentHash


class ConfigurationTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str = ""
    config: ConfigProfileSnapshot

    @property
    def content_hash(self) -> Sha256ContentHash:
        return sha256_json_hash(
            {
                "codec": "scopecat.configuration-template.v1",
                "template": self.model_dump(mode="json"),
            }
        )
