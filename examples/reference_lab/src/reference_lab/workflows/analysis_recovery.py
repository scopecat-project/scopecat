"""Deliberate software failure after one virtual thermometer acquisition."""

from typing import cast

import scopecat as sc
from pydantic import BaseModel, ConfigDict
from scopecat.api.procedures import LabProcedureContext
from scopecat.automation import ProcedureRecoveryAdapter, RunOutputRef, procedure

from reference_lab.workflows.temperature_diagnostic import (
    TemperatureDiagnosticIntent,
    temperature_diagnostic,
)


@sc.analysis_step(id="reference_lab.temperature_summary")
def temperature_summary(context: sc.AnalysisContext, *, fail: bool) -> sc.Analysis:
    if fail:
        raise ValueError("demonstration software analysis failure")
    value = context.measurements()["temperature"].require_magnitudes("K")[0]
    return context.result("Retained temperature summary").fact(
        "temperature", {"kelvin": float(cast("float", value))}
    )


@procedure(
    id="reference_lab.failed_temperature_analysis",
    version="1",
    intent=TemperatureDiagnosticIntent,
)
def failed_temperature_analysis(
    context: LabProcedureContext, intent: TemperatureDiagnosticIntent
) -> None:
    run = context.run("sample", temperature_diagnostic(), config=intent.initial_config)
    context.analyze_run("summary", run, temperature_summary(fail=True))


class RetainedTemperatureIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run: RunOutputRef


@procedure(
    id="reference_lab.recovered_temperature_analysis",
    version="1",
    intent=RetainedTemperatureIntent,
)
def recovered_temperature_analysis(
    context: LabProcedureContext, intent: RetainedTemperatureIntent
) -> None:
    context.analyze_run("summary", intent.run, temperature_summary(fail=False))


def _retained_intent(
    _source: TemperatureDiagnosticIntent, run: RunOutputRef
) -> RetainedTemperatureIntent:
    return RetainedTemperatureIntent(run=run)


TEMPERATURE_ANALYSIS_RECOVERY = ProcedureRecoveryAdapter(
    id="reference_lab.temperature_analysis",
    source=failed_temperature_analysis,
    destination=recovered_temperature_analysis,
    run_step="sample",
    failed_analysis_step="summary",
    build_intent=_retained_intent,
)
