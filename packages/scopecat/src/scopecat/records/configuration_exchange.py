"""Non-executable exchange of existing author, parameter and setup definitions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.author_revision import AuthorRevisionBundle
from scopecat.records.content import Sha256ContentHash
from scopecat.records.device import DeviceRevisionRef
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.parameter_branch import ParameterBranch
from scopecat.records.parameter_revision import ParameterRevisionRef
from scopecat.records.setup import SetupDefinitionRevision, SetupRevision


class _ExchangeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConfigurationExchange(_ExchangeModel):
    """A transfer document, never an executable configuration or store clone."""

    format: Literal["scopecat.configuration-exchange.v1"] = (
        "scopecat.configuration-exchange.v1"
    )
    label: str = Field(min_length=1, max_length=200)
    origin_store: str = Field(min_length=1)
    parameter_origin: ParameterRevisionRef
    catalog: ParameterCatalog
    parameters: ParameterSnapshot
    values_included: bool
    setup: SetupDefinitionRevision | None = None
    source: AuthorRevisionBundle | None = None

    @property
    def content_hash(self) -> Sha256ContentHash:
        return sha256_json_hash(self.model_dump(mode="json"))


class ConfigurationExport(_ExchangeModel):
    parameter_revision: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=200)
    include_values: bool = True
    setup_definition: str | None = None
    workspace: str | None = None
    source_revision: Sha256ContentHash | None = None


class ConfigurationInspection(_ExchangeModel):
    content_hash: Sha256ContentHash
    document: ConfigurationExchange
    source_files: tuple[str, ...]
    requirements: tuple[str, ...]
    notices: tuple[str, ...]


class ConfigurationDerive(_ExchangeModel):
    document: ConfigurationExchange
    operation_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    actor: str = Field(min_length=1)
    include_setup: bool = False
    # Explicit reviewed receiver revisions, keyed by sender's logical role.
    bindings: dict[str, DeviceRevisionRef] = Field(default_factory=dict)


class ConfigurationDerivation(_ExchangeModel):
    content_hash: Sha256ContentHash
    branch: ParameterBranch
    setup: SetupRevision | None = None
    source_pending: bool
    setup_pending: bool


class ConfigurationImportSummary(_ExchangeModel):
    content_hash: Sha256ContentHash
    label: str
    origin_store: str
    derivations: tuple[ConfigurationDerivation, ...] = ()
