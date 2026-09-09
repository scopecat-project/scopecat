"""One typed catalog for a retained diagnostic and a reviewed timing candidate."""

from __future__ import annotations

from dataclasses import dataclass

import scopecat as sc
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from scopecat.api.lab import LabClient
from scopecat.api.procedures import LabProcedureContext
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchCatalogEntry,
    LaunchInputSchema,
    LaunchPreview,
    LaunchResult,
    LaunchSubmission,
    validate_launch_control_edits,
)
from scopecat.application.launch_config import (
    launch_config_generation,
    launch_preflight_configuration,
    launch_preflight_meaning,
    launch_sample_selection,
)
from scopecat.automation import InterpretationRequest, procedure
from scopecat.config.parameter_updates import materialize_parameter_updates
from scopecat.planning.preflight import (
    ExactQuantity,
    PreflightSummary,
    summarize_preflight,
)
from scopecat.records.config import config_content_hash
from scopecat.records.content import Sha256ContentHash
from scopecat.records.launch_request import LaunchConfigSource, LaunchRequest
from scopecat.records.manual_preview import ManualPreviewFence

from reference_lab.control_launch import CONTROL_ENTRY, control_launch
from reference_lab.launch_config import launch_config
from reference_lab.parameters import CHANNEL_DELAY, Q1_CHANNEL_CALIBRATION
from reference_lab.workflows.ramsey_experiments import RAMSEY_SHOTS, parallel_raw_ramsey
from reference_lab.workflows.temperature_diagnostic import (
    TemperatureDiagnosticIntent,
    temperature_diagnostic,
)


class DiagnosticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TimingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    delay_ns: float = Field(default=1.0, ge=0.1, le=10, title="Q1 channel delay (ns)")


@dataclass(frozen=True)
class TimingReview:
    accepted: bool
    note: str


TIMING_REVIEW = sc.AnalysisFactSchema(
    "reference_lab.channel_timing_review.v1", TimingReview
)
REVIEW_INSTRUCTIONS = (
    "Review the source and candidate runs. Configuration acceptance is a "
    "separate action in the run's parameter proposals."
)


class LaunchIntent(TemperatureDiagnosticIntent):
    manual_state: ManualPreviewFence | None = None
    config_source: LaunchConfigSource
    request_hash: Sha256ContentHash
    actor: str
    inputs: dict[str, JsonValue]


@procedure(id="reference_lab.launch_temperature", version="1", intent=LaunchIntent)
def launch_temperature(context: LabProcedureContext, intent: LaunchIntent) -> None:
    context.run(
        "diagnostic",
        temperature_diagnostic(),
        config=intent.initial_config,
        config_source=intent.config_source,
        operator=intent.actor,
    )


@sc.analysis_step(id="reference_lab.channel_timing_candidate")
def timing_analysis(context: sc.AnalysisContext, delay_ns: float) -> sc.Analysis:
    # Retain the exact data being reviewed; the delay is operator supplied, not fit.
    context.measurements()
    return context.result("Channel timing candidate").propose(
        "q1-channel-delay",
        Q1_CHANNEL_CALIBRATION[CHANNEL_DELAY].update(delay_ns),
        reason="align q1 acquisition with the shared readout window",
    )


@procedure(id="reference_lab.launch_channel_timing", version="1", intent=LaunchIntent)
def launch_channel_timing(context: LabProcedureContext, intent: LaunchIntent) -> None:
    inputs = TimingRequest.model_validate(intent.inputs)
    source = context.run(
        "source",
        parallel_raw_ramsey(),
        config=intent.initial_config,
        config_source=intent.config_source,
        operator=intent.actor,
    )
    proposal = context.analyze_run("proposal", source, timing_analysis(inputs.delay_ns))
    candidate = context.published_analysis(proposal).candidate_config()
    verified = context.run(
        "candidate",
        parallel_raw_ramsey(),
        config=candidate,
        inputs=(proposal,),
        operator=intent.actor,
    )
    context.interpret(
        "review",
        title="Review q1 channel timing",
        instructions=REVIEW_INSTRUCTIONS,
        schema=TIMING_REVIEW,
        inputs=(source, proposal, verified),
    )


CATALOG = LaunchCatalog(
    entries=(
        LaunchCatalogEntry(
            id="temperature",
            version="1",
            title="Temperature diagnostic",
            description=(
                "Retain one thermometer sample without changing device state "
                "or default configuration."
            ),
            actions=("preview", "submit"),
            kind="diagnostic",
            configuration_effect="none",
            request=LaunchInputSchema.model_validate(
                DiagnosticRequest.model_json_schema()
            ),
        ),
        LaunchCatalogEntry(
            id="channel-timing",
            version="1",
            title="Q1 channel timing candidate",
            description=(
                "Measure, propose a timing change, run its candidate and request "
                "review. Accepting it as default remains a separate operator action."
            ),
            actions=("preview", "submit"),
            kind="calibration",
            configuration_effect="candidate",
            request=LaunchInputSchema.model_validate(TimingRequest.model_json_schema()),
            review=InterpretationRequest(
                title="Review q1 channel timing",
                schema_id=TIMING_REVIEW.id,
                schema_codec=TIMING_REVIEW.schema_codec,
                schema_hash=TIMING_REVIEW.schema_hash,
                structure=TIMING_REVIEW.structure,
                instructions=REVIEW_INSTRUCTIONS,
            ),
        ),
        CONTROL_ENTRY,
    )
)


def launch_provider(lab: LabClient, request: LaunchRequest) -> LaunchResult:
    if request.action == "list":
        return CATALOG
    entry = next(
        (entry for entry in CATALOG.entries if entry.id == request.experiment), None
    )
    if entry is None or entry.version != request.version:
        raise ValueError("unknown experiment or changed catalog version")
    if request.action not in entry.actions:
        raise ValueError(f"{request.action} is not supported for {entry.id}")
    validate_launch_control_edits(CATALOG, request)
    if entry.id == CONTROL_ENTRY.id:
        return control_launch(lab, request)
    inputs = (
        DiagnosticRequest if entry.kind == "diagnostic" else TimingRequest
    ).model_validate(request.inputs)
    invocation = (
        temperature_diagnostic()
        if entry.kind == "diagnostic"
        else parallel_raw_ramsey()
    )
    definition = (
        launch_temperature if entry.kind == "diagnostic" else launch_channel_timing
    )
    if request.action == "preview":
        config, source = launch_config(lab, request)
        preview = lab.preview_invocation(
            invocation, config=config, config_source=source
        )
        stages = [
            summarize_preflight(
                preview,
                stage_id="diagnostic" if entry.kind == "diagnostic" else "source",
                label="Retained temperature diagnostic"
                if entry.kind == "diagnostic"
                else "Selected-configuration source run",
                configuration=launch_preflight_configuration(source),
                config_content_hash=source.content_hash,
                configuration_meaning=launch_preflight_meaning(source),
                executions=ExactQuantity(
                    value=1,
                    unit="runs",
                    basis=(
                        "Declared workflow stage; cancellation or failure may "
                        "prevent execution"
                    ),
                ),
                shots_per_point_per_entity=None
                if entry.kind == "diagnostic"
                else ExactQuantity(
                    value=RAMSEY_SHOTS,
                    unit="shots",
                    basis="Ramsey invocation.with_shots per point and entity",
                ),
                entity_ids=("cryostat",)
                if entry.kind == "diagnostic"
                else ("q0", "q1"),
            )
        ]
        if isinstance(inputs, TimingRequest):
            parameters, _ = materialize_parameter_updates(
                catalog=config.parameter_catalog,
                base=config.parameter_snapshot,
                updates=(
                    Q1_CHANNEL_CALIBRATION[CHANNEL_DELAY].update(inputs.delay_ns),
                ),
                candidate_id="preflight-channel-timing.parameters",
            )
            candidate = config.model_copy(
                update={
                    "id": "preflight-channel-timing",
                    "parameter_snapshot": parameters,
                }
            )
            candidate_preview = lab.preview(invocation, config=candidate)
            stages.append(
                summarize_preflight(
                    candidate_preview,
                    stage_id="candidate",
                    label="Proposed-configuration verification run",
                    configuration="proposed_candidate",
                    config_content_hash=config_content_hash(candidate),
                    configuration_meaning=(
                        "Compiles the proposed delay; has not run or been "
                        "verified. Default acceptance remains a separate action."
                    ),
                    executions=ExactQuantity(
                        value=1,
                        unit="runs",
                        basis=(
                            "Declared candidate stage after source analysis; not a "
                            "promise of completion"
                        ),
                    ),
                    shots_per_point_per_entity=ExactQuantity(
                        value=RAMSEY_SHOTS,
                        unit="shots",
                        basis="Ramsey invocation.with_shots per point and entity",
                    ),
                    entity_ids=("q0", "q1"),
                )
            )
        return LaunchPreview(
            experiment_id=entry.id,
            manual_state=request.manual_state,
            request_hash=request.request_hash,
            config_source=source,
            point_count=preview.initial_point_count,
            resources=tuple(
                sorted({item for stage in stages for item in stage.instrument_ids})
            ),
            preflight=PreflightSummary(
                stages=tuple(stages),
                scope_basis=(
                    "One diagnostic run. No configuration writes."
                    if entry.kind == "diagnostic"
                    else (
                        "Source run, analysis, proposed-configuration run, "
                        "then review. "
                        "Point counts are per stage; preview does not validate a "
                        "physical outcome."
                    )
                ),
            ),
            summary=entry.description,
            resolved_inputs=inputs.model_dump(mode="json"),
        )
    config, source = launch_config(lab, request)
    intent = LaunchIntent(
        initial_config=config,
        config_source=source,
        manual_state=request.manual_state,
        request_hash=request.request_hash,
        actor=request.actor,
        inputs=inputs.model_dump(mode="json"),
    )
    admitted = lab.procedures.submit(
        definition,
        intent,
        request_key=request.request_key,
        sample=launch_sample_selection(request, source),
        expected_manual_preview=request.manual_state,
        expected_config_generation=launch_config_generation(source),
        plan_ref=request.plan_ref,
        plan_request=request if request.plan_ref is not None else None,
    )
    return LaunchSubmission(procedure_id=admitted.id)
