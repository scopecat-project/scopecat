"""Built-in software lesson using ordinary runs, analyses and durable decisions."""

from dataclasses import dataclass
from typing import Annotated, Literal, cast

import scopecat as sc
from pydantic import BaseModel, ConfigDict
from scopecat.api.procedures import LabProcedureContext
from scopecat.application.lab import LabApplication
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.setup import SetupRevisionRef


class PeakPracticeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    config: ConfigProfileSnapshot
    setup: SetupRevisionRef


@sc.compute
def response(frequency: sc.Quantity) -> float:
    x = frequency.to("MHz").value
    return float(0.15 + 1 / (1 + ((x - 6500) / 1.2) ** 2))


@sc.experiment(id="scopecat.practice.synthetic-peak")
def sweep(
    _context: sc.ExperimentContext,
    frequency: Annotated[
        sc.Input[sc.Quantity], sc.ControlSpec(unit="MHz", scannable=True)
    ],
) -> dict[str, object]:
    return {"frequency": frequency, "response": response(frequency)}


@dataclass(frozen=True)
class CurvePoint:
    frequency: Annotated[float, sc.AnalysisField(unit="MHz", role="coordinate")]
    response: Annotated[float, sc.AnalysisField()]


@sc.analysis_step(id="scopecat.practice.peak-curve")
def curve(context: sc.AnalysisContext) -> sc.Analysis:
    data = context.measurements()
    x = cast(
        "list[float]", data["frequency"].dense.to("MHz").values.reshape(-1).tolist()
    )
    y = cast("list[float]", data["response"].dense.values.reshape(-1).tolist())
    points = [CurvePoint(a, b) for a, b in zip(x, y, strict=True)]
    return (
        context.result("Synthetic response — no device connected")
        .dataset("curve", points)
        .figure(dataset="curve", id="trace", kind="line", x="frequency", y="response")
    )


@dataclass(frozen=True)
class PeakChoice:
    outcome: Literal["Peak selected", "No clear peak", "Unsure"]
    frequency_mhz: float | None


PEAK_CHOICE = sc.AnalysisFactSchema("scopecat.practice.peak-choice.v1", PeakChoice)


@sc.procedure(
    id="scopecat.practice.manual-peaks", version="1", intent=PeakPracticeIntent
)
def manual_peaks(context: LabProcedureContext, intent: PeakPracticeIntent) -> None:
    scan = context.run(
        "scan",
        sweep.build(frequency=sc.Quantity(6500, "MHz")).with_axis(
            sc.axis(
                sweep.controls.fields[0].ref,
                [sc.Quantity(6490 + index * 0.25, "MHz") for index in range(81)],
            )
        ),
        config=intent.config,
        setup=intent.setup,
        name="Practice: synthetic frequency scan",
    )
    plotted = context.analyze_run("curve", scan, curve())
    attempt = 1
    problem = ""
    while True:
        choice = context.interpret(
            f"choose-{attempt}",
            title="Choose a peak frequency",
            instructions=(
                "Find the highest point of the upward peak (not a dip). "
                "Hover over the curve "
                "to read its horizontal coordinate, then type the frequency; "
                "clicking does not fill it. "
                "Enter a frequency in MHz between "
                "6490 and 6510, or choose No clear peak / Unsure and leave it empty. "
                "Record your answer, open the task and choose Continue task to finish. "
                "This example peaks at about 6500 MHz; "
                "completion records your judgment, "
                "not its correctness or a published calibration. " + problem
            ),
            schema=PEAK_CHOICE,
            inputs=(scan, plotted),
            response_template=PeakChoice("Unsure", None),
            metadata={
                "field_labels": {
                    "outcome": "Your judgment",
                    "frequency_mhz": "Peak frequency (MHz)",
                },
                "field_constraints": {
                    "frequency_mhz": {
                        "minimum": 6490,
                        "maximum": 6510,
                        "required_when": {"field": "outcome", "value": "Peak selected"},
                        "empty_otherwise": True,
                    }
                },
            },
        )
        value = choice.value
        if value.outcome == "Peak selected":
            if value.frequency_mhz is None:
                problem = "Enter a peak frequency when choosing Peak selected."
            elif not 6490 <= value.frequency_mhz <= 6510:
                problem = "Peak frequency must be between 6490 and 6510 MHz."
            else:
                return
        elif value.frequency_mhz is not None:
            problem = "Leave the frequency empty for No clear peak or Unsure."
        else:
            return
        attempt += 1


def application() -> LabApplication:
    return LabApplication(procedures=(manual_peaks,))
