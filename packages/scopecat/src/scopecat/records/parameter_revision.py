"""Parameter revision content independent of executable device definitions."""

from pydantic import BaseModel, ConfigDict

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
