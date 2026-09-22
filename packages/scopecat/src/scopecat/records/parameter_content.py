"""Parameter editing inputs without equipment or scientific-context ownership."""

from pydantic import BaseModel, ConfigDict

from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot


class ParameterContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    parameter_catalog: ParameterCatalog
    parameter_snapshot: ParameterSnapshot
