"""Plan-to-launch projection; every execution still needs a fresh normal preview."""

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
from scopecat.records.run import AnalysisCandidateRunConfigSource


def plan_launch_request(plan: ExperimentPlanRevision, *, actor: str) -> LaunchRequest:
    definition = plan.definition
    return LaunchRequest(
        action="preview",
        experiment=definition.experiment,
        version=definition.version,
        actor=actor,
        inputs=cast("dict[str, JsonValue]", thaw_json_value(definition.inputs)),
        control_edits=dict(definition.control_edits),
        configuration=definition.configuration,
        context=definition.context,
        overrides=definition.overrides,
        sample_binding=definition.sample,
        sample=definition.sample.sample_id if definition.sample else None,
        code_revision=definition.code_revision,
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
            "saved plan definition changed; explicitly save a new "
            "revision and preview it"
        )
    expected = plan_launch_request(plan, actor=request.actor)
    if (
        request.request_hash != expected.request_hash
        or request.code_revision != expected.code_revision
    ):
        raise ValueError(
            "launch differs from the immutable plan; save a new "
            "revision or detach it before preview"
        )


def plan_definition(
    request: LaunchRequest,
    preview: LaunchPreview,
    *,
    source: PlanAnalysisSource | None = None,
) -> ExperimentPlanDefinition:
    """Freeze a successful preview's selections, not its execution permission."""
    if request.request_hash != preview.request_hash or preview.definition_hash is None:
        raise ValueError("plan requires the matching checked declaration preview")
    if request.sample is not None and preview.sample_binding is None:
        raise ValueError(
            "preview must resolve the selected sample revision before saving"
        )
    config = preview.config_source
    if isinstance(config, AnalysisCandidateRunConfigSource):
        raise ValueError("Save plans from a named context, not an unaccepted candidate")
    context = config if isinstance(config, ContextRunConfigSource) else None
    return ExperimentPlanDefinition(
        experiment=request.experiment,
        version=request.version,
        definition_hash=preview.definition_hash,
        code_revision=preview.code_revision,
        inputs=request.inputs,
        control_edits=request.control_edits,
        configuration=PlanConfigRef(
            entry_id=config.entry_id, content_hash=config.content_hash
        )
        if not isinstance(config, ContextRunConfigSource)
        else None,
        context=context.context if context else None,
        overrides=context.overrides if context else (),
        sample=preview.sample_binding,
        source=source,
    )
