"""Managed invocation and publication for ordinary and context analysis functions."""

from typing import cast

from pydantic import JsonValue
from scopecat.analysis.arguments import (
    AnalysisArgument,
    bind_arguments,
    encode_arguments,
)
from scopecat.analysis.grouping import partition_groups
from scopecat.api.analysis import (
    Analysis,
    AnalysisContext,
    AnalysisDefinition,
    AnalysisFunctionDefinition,
    AnalysisInvocation,
)
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.api.run import RunHandle
from scopecat.kernel.content_identity import canonical_json, sha256_json_hash
from scopecat.records.author_revision import (
    AuthorAnalysisGroupReceipt,
    AuthorAnalysisReceipt,
    AuthorAnalysisRequest,
)


def analyze(
    run: RunHandle,
    definition: AnalysisDefinition[...] | AnalysisFunctionDefinition[..., object],
    request: AuthorAnalysisRequest,
) -> AuthorAnalysisReceipt:
    function = (
        definition.function
        if isinstance(definition, AnalysisFunctionDefinition)
        else definition.__wrapped__
    )
    step = definition(**bind_arguments(function, request.arguments))
    if request.grouping is not None:
        return _analyze_groups(run, step, request)
    result = step.run(
        AnalysisContext(run=run, default_key=request.key or step.id, step_id=step.id)
    )
    published = _publish(result, request, step)
    return AuthorAnalysisReceipt(
        code_revision=request.code_revision, analysis_id=published.id
    )


def _publish(
    result: Analysis, request: AuthorAnalysisRequest, step: AnalysisInvocation
) -> PublishedAnalysis:
    return (
        result.fact("author_code_revision", request.code_revision.content_hash)
        .fact("author_workspace", request.workspace_id)
        .artifact("author_analysis_arguments", text=canonical_json(request.arguments))
        .artifact(
            "author_analysis_effective_arguments",
            text=canonical_json(
                encode_arguments(
                    request.analysis,
                    cast("dict[str, AnalysisArgument]", dict(step.arguments)),
                )
            ),
        )
        .save()
    )


def _analyze_groups(
    run: RunHandle,
    step: AnalysisInvocation,
    request: AuthorAnalysisRequest,
) -> AuthorAnalysisReceipt:
    if run.status != "completed":
        raise ValueError("offline grouped analysis requires a completed run")
    grouping = request.grouping
    assert grouping is not None
    parent = AnalysisContext(run=run, default_key=request.key or step.id)
    data = parent.measurements()
    receipts: list[AuthorAnalysisGroupReceipt] = []
    for group in partition_groups(data, grouping):
        coordinates = encode_arguments(request.analysis, group.coordinates)
        point_indices = tuple(data.point_indices[index] for index in group.positions)
        selection: dict[str, JsonValue] = {
            "grouping": grouping.model_dump(mode="json"),
            "coordinates": coordinates,
            "point_indices": list(point_indices),
            "data_hash": data.entry.content_hash,
        }
        group_id = sha256_json_hash(selection).removeprefix("sha256:")
        context = AnalysisContext(
            run=run,
            default_key=f"{request.key or step.id}/group/{group_id}",
            step_id=step.id,
            point_selection=group.positions,
            measurement_data=data,
        )
        error_text = None
        try:
            result = step.run(context)
        except Exception as error:
            error_text = f"{type(error).__name__}: {error}"
            result = context.result(title=step.id).fact("group_error", error_text)
        published = _publish(
            result.artifact("group_selection", text=canonical_json(selection)),
            request,
            step,
        )
        receipts.append(
            AuthorAnalysisGroupReceipt(
                coordinates=coordinates,
                point_indices=point_indices,
                analysis_id=published.id,
                error=error_text,
            )
        )
    manifest = _publish(
        parent.result(title=f"Grouped {step.id}")
        .artifact(
            "groups",
            text=canonical_json(
                [receipt.model_dump(mode="json") for receipt in receipts]
            ),
        )
        .artifact("grouping", text=canonical_json(grouping.model_dump(mode="json"))),
        request,
        step,
    )
    return AuthorAnalysisReceipt(
        code_revision=request.code_revision,
        analysis_id=manifest.id,
        groups=tuple(receipts),
    )
