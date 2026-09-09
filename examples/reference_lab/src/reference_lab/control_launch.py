"""Notebook control edits through the same typed preview and procedure boundary."""

from scopecat.api.lab import LabClient
from scopecat.api.procedures import LabProcedureContext
from scopecat.application.controls import (
    ControlEdit,
    control_catalog,
    control_values,
    edit_controls,
)
from scopecat.application.launch import (
    LaunchCatalogEntry,
    LaunchConfigSource,
    LaunchInputSchema,
    LaunchPreview,
    LaunchRequest,
    LaunchSubmission,
)
from scopecat.application.launch_config import (
    launch_config_generation,
    launch_preflight_configuration,
    launch_preflight_meaning,
    launch_sample_selection,
)
from scopecat.automation import procedure
from scopecat.planning.preflight import (
    ExactQuantity,
    PreflightSummary,
    summarize_preflight,
)
from scopecat.records.content import Sha256ContentHash
from scopecat.records.manual_preview import ManualPreviewFence

from reference_lab.launch_config import launch_config
from reference_lab.workflows.frequency_amplitude import CONTROLS, frequency_amplitude
from reference_lab.workflows.temperature_diagnostic import TemperatureDiagnosticIntent

CONTROL_ENTRY = LaunchCatalogEntry(
    id="frequency-amplitude",
    version="1",
    title="Frequency / amplitude model",
    description=(
        "Preview fixed or scanned signal controls against the reviewed "
        "q0 carrier. Project limits: at most 64 points; amplitude at most "
        "0.2 V when detuning exceeds 0.25 GHz. "
        "This analytic model makes no device calls."
    ),
    actions=("preview", "submit"),
    kind="diagnostic",
    configuration_effect="none",
    request=LaunchInputSchema(properties={}),
    controls=control_catalog(CONTROLS),
)


class ControlLaunchIntent(TemperatureDiagnosticIntent):
    manual_state: ManualPreviewFence | None = None
    config_source: LaunchConfigSource
    request_hash: Sha256ContentHash
    actor: str
    edits: dict[str, ControlEdit]


@procedure(
    id="reference_lab.launch_frequency_amplitude",
    version="1",
    intent=ControlLaunchIntent,
)
def launch_frequency_amplitude(
    context: LabProcedureContext, intent: ControlLaunchIntent
) -> None:
    invocation = edit_controls(
        CONTROLS,
        frequency_amplitude(),
        config=intent.initial_config,
        edits=intent.edits,
    )
    context.run(
        "signal",
        invocation,
        config=intent.initial_config,
        config_source=intent.config_source,
        operator=intent.actor,
    )


def control_launch(
    lab: LabClient, request: LaunchRequest
) -> LaunchPreview | LaunchSubmission:
    if request.inputs:
        raise ValueError(
            "this experiment accepts declared control edits, not extra inputs"
        )
    config, source = launch_config(lab, request)
    invocation = edit_controls(
        CONTROLS, frequency_amplitude(), config=config, edits=request.control_edits
    )
    if request.action == "preview":
        preview = lab.preview_invocation(
            invocation, config=config, config_source=source
        )
        return LaunchPreview(
            experiment_id=CONTROL_ENTRY.id,
            manual_state=request.manual_state,
            request_hash=request.request_hash,
            config_source=source,
            point_count=preview.initial_point_count,
            resources=preview.instrument_ids,
            controls=control_values(CONTROLS, invocation, config=config),
            summary=CONTROL_ENTRY.description,
            preflight=PreflightSummary(
                stages=(
                    summarize_preflight(
                        preview,
                        stage_id="signal",
                        label="Configured signal model",
                        configuration=launch_preflight_configuration(source),
                        executions=ExactQuantity(
                            value=1,
                            unit="runs",
                            basis="One declared analytic model run",
                        ),
                        config_content_hash=source.content_hash,
                        configuration_meaning=launch_preflight_meaning(source),
                    ),
                ),
                scope_basis="One analytic model run; all selected grid points.",
            ),
        )
    admitted = lab.procedures.submit(
        launch_frequency_amplitude,
        ControlLaunchIntent(
            initial_config=config,
            config_source=source,
            manual_state=request.manual_state,
            request_hash=request.request_hash,
            actor=request.actor,
            edits=request.control_edits,
        ),
        request_key=request.request_key,
        sample=launch_sample_selection(request, source),
        expected_manual_preview=request.manual_state,
        expected_config_generation=launch_config_generation(source),
    )
    return LaunchSubmission(procedure_id=admitted.id)
