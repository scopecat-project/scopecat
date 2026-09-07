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
    LaunchRequest,
    LaunchResult,
    LaunchSubmission,
)
from scopecat.automation import InterpretationRequest, procedure
from scopecat.records.content import Sha256ContentHash
from scopecat.records.run import ConfigRegistryRunConfigSource

from reference_lab.parameters import CHANNEL_DELAY, Q1_CHANNEL_CALIBRATION
from reference_lab.workflows.ramsey_experiments import parallel_raw_ramsey
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
    config_source: ConfigRegistryRunConfigSource
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
        config, source = lab.config.resolve_with_source("active")
        assert isinstance(source, ConfigRegistryRunConfigSource)
        preview = lab.preview(invocation, config=config)
        return LaunchPreview(
            experiment_id=entry.id,
            request_hash=request.request_hash,
            config_source=source,
            point_count=preview.initial_point_count,
            summary=entry.description,
            resolved_inputs=inputs.model_dump(mode="json"),
        )
    source = request.config_source
    assert source is not None and source.registry_generation is not None
    snapshot = lab.config.entry(source.entry_id)
    if (
        source.selector != "active"
        or source.config_ref != snapshot.entry.config_ref
        or source.content_hash != snapshot.entry.content_hash
    ):
        raise ValueError(
            "preview configuration reference does not match its immutable snapshot"
        )
    active = lab.config.active()
    if (
        active.activation.generation == source.registry_generation
        and active.entry.id != source.entry_id
    ):
        raise ValueError("preview configuration binding does not match its generation")
    intent = LaunchIntent(
        initial_config=snapshot.config,
        config_source=source,
        request_hash=request.request_hash,
        actor=request.actor,
        inputs=inputs.model_dump(mode="json"),
    )
    admitted = lab.procedures.submit(
        definition,
        intent,
        request_key=request.request_key,
        sample=request.sample,
        expected_config_generation=source.registry_generation,
    )
    return LaunchSubmission(procedure_id=admitted.id)
