"""One retained thermometer sample, with no instrument or configuration writes."""

from __future__ import annotations

import scopecat as sc
from pydantic import BaseModel, ConfigDict, field_validator
from scopecat.api.procedures import LabProcedureContext
from scopecat.automation import procedure
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.frozen import thaw_json_value
from scopecat.records.config import ConfigProfileSnapshot
from scopecat_instruments import TemperatureSampleProducts, temperature_readout


@sc.experiment(id="reference_lab.temperature_diagnostic")
def temperature_diagnostic(
    experiment: sc.ExperimentContext,
) -> TemperatureSampleProducts:
    thermometer = temperature_readout(
        experiment, for_=sc.one(EntityRef(id="cryostat", kind="cryostat"))
    )
    return thermometer.sample()


class TemperatureDiagnosticIntent(BaseModel):
    """Pin the diagnostic configuration before a durable resource wait."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    initial_config: ConfigProfileSnapshot

    @field_validator("initial_config", mode="before")
    @classmethod
    def thaw_initial_config(cls, value: object) -> object:
        return thaw_json_value(value)


@procedure(
    id="reference_lab.temperature_diagnostic",
    version="1",
    intent=TemperatureDiagnosticIntent,
)
def temperature_diagnostic_procedure(
    context: LabProcedureContext, intent: TemperatureDiagnosticIntent
) -> None:
    context.run("sample", temperature_diagnostic(), config=intent.initial_config)
