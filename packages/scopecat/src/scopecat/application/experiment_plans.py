"""Plan-to-launch projection preserving exact scientific evidence."""

from typing import cast

from pydantic import JsonValue

from scopecat.application.launch import LaunchCatalogEntry, LaunchPreview
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.frozen import thaw_json_value
from scopecat.records.config_context import ContextRunConfigSource
from scopecat.records.experiment_plan import (
    ExperimentPlanDefinition,
    ExperimentPlanRevision,
)
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.plan_ref import PlanAnalysisSource, PlanConfigRef
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ParameterRunConfigSource,
)
from scopecat.records.scientific_selection import (
    ParameterConfiguration,
    SampleSubjectChoice,
    SavedConfiguration,
    WorkingPointConfiguration,
)


def plan_launch_request(
    plan: ExperimentPlanRevision, *, actor: str, record_collection: str | None = None
) -> LaunchRequest:
    definition = plan.definition
    return LaunchRequest(
        action="preview",
        experiment=definition.experiment,
        version=definition.version,
        actor=actor,
        record_collection=record_collection,
        inputs=cast("dict[str, JsonValue]", thaw_json_value(definition.inputs)),
        control_edits=dict(definition.control_edits),
        scan_mode=definition.scan_mode,
        parameter_sweeps=definition.parameter_sweeps,
        selection=definition.selection,
        code_revision=definition.code_revision,
        workspace_id=definition.workspace_id,
        plan_ref=plan.ref,
    )


def validate_plan_launch(
    plan: ExperimentPlanRevision, request: LaunchRequest, entry: LaunchCatalogEntry
) -> None:
    if (
        sha256_json_hash(entry.model_dump(mode="json"))
        != plan.definition.definition_hash
    ):
        raise ValueError(
            "saved plan definition changed; save a new revision and preview it"
        )
    expected = plan_launch_request(
        plan, actor=request.actor, record_collection=request.record_collection
    )
    if request.reviewed is not None:
        if request.reviewed.binding != plan.definition.scientific_binding:
            raise ValueError("launch scientific evidence differs from immutable plan")
        expected = expected.model_copy(update={"reviewed": request.reviewed})
    if (
        request.request_hash != expected.request_hash
        or request.workspace_id != expected.workspace_id
        or request.code_revision != expected.code_revision
    ):
        raise ValueError(
            "launch differs from immutable plan; save a new revision or detach it"
        )


def plan_definition(
    request: LaunchRequest,
    preview: LaunchPreview,
    *,
    source: PlanAnalysisSource | None = None,
) -> ExperimentPlanDefinition:
    frozen = request.model_copy(update={"reviewed": preview.reviewed})
    if frozen.request_hash != preview.request_hash or preview.definition_hash is None:
        raise ValueError("plan requires the matching checked declaration preview")
    config = preview.reviewed.config_source
    if isinstance(config, AnalysisCandidateRunConfigSource):
        raise ValueError("Save plans from a named context, not an unaccepted candidate")
    selection = request.selection
    configuration = (
        WorkingPointConfiguration(ref=config.context, overrides=config.overrides)
        if isinstance(config, ContextRunConfigSource)
        else ParameterConfiguration(
            ref=config.parameters, setup=config.setup, overrides=config.overrides
        )
        if isinstance(config, ParameterRunConfigSource)
        else SavedConfiguration(
            ref=PlanConfigRef(
                entry_id=config.entry_id, content_hash=config.content_hash
            )
        )
    )
    subject = selection.subject
    if isinstance(subject, SampleSubjectChoice):
        samples = preview.reviewed.binding.samples
        if len(samples) != 1:
            raise ValueError("sample plan requires exact subject evidence")
        subject = subject.model_copy(update={"revision": samples[0].revision})
    selection = selection.model_copy(
        update={"subject": subject, "configuration": configuration}
    )
    return ExperimentPlanDefinition(
        experiment=request.experiment,
        version=request.version,
        definition_hash=preview.definition_hash,
        code_revision=preview.code_revision,
        workspace_id=preview.workspace_id,
        inputs=request.inputs,
        control_edits=request.control_edits,
        scan_mode=request.scan_mode,
        parameter_sweeps=request.parameter_sweeps,
        selection=selection,
        scientific_binding=preview.reviewed.binding,
        source=source,
    )
