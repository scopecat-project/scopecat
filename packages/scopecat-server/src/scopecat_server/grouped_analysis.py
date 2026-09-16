"""Offline group publication using the existing ordinary function and analysis store."""

from collections.abc import Mapping

from pydantic import JsonValue
from scopecat.analysis.arguments import encode_arguments
from scopecat.analysis.grouping import partition_groups
from scopecat.api.analysis import AnalysisContext, AnalysisFunctionDefinition
from scopecat.api.run import RunHandle
from scopecat.kernel.content_identity import canonical_json, sha256_json_hash
from scopecat.records.author_revision import (
    AuthorAnalysisGroupReceipt,
    AuthorAnalysisReceipt,
    AuthorAnalysisRequest,
)


def analyze_groups(
    run: RunHandle,
    definition: AnalysisFunctionDefinition[..., object],
    arguments: Mapping[str, object],
    request: AuthorAnalysisRequest,
) -> AuthorAnalysisReceipt:
    if run.status != "completed":
        raise ValueError("offline grouped analysis requires a completed run")
    grouping = request.grouping
    assert grouping is not None
    parent = AnalysisContext(run=run, default_key=request.key or definition.id)
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
            default_key=f"{request.key or definition.id}/group/{group_id}",
            step_id=definition.id,
            point_selection=group.positions,
            measurement_data=data,
        )
        error_text = None
        try:
            result = definition(**arguments).run(context)
        except Exception as error:
            error_text = f"{type(error).__name__}: {error}"
            result = context.result(title=definition.id).fact("group_error", error_text)
        published = (
            result.artifact("group_selection", text=canonical_json(selection))
            .artifact(
                "author_analysis_arguments", text=canonical_json(request.arguments)
            )
            .fact("author_code_revision", request.code_revision.content_hash)
            .save()
        )
        receipts.append(
            AuthorAnalysisGroupReceipt(
                coordinates=coordinates,
                point_indices=point_indices,
                analysis_id=published.id,
                error=error_text,
            )
        )
    manifest = (
        parent.result(title=f"Grouped {definition.id}")
        .artifact(
            "groups",
            text=canonical_json(
                [receipt.model_dump(mode="json") for receipt in receipts]
            ),
        )
        .artifact("grouping", text=canonical_json(grouping.model_dump(mode="json")))
        .fact("author_code_revision", request.code_revision.content_hash)
        .artifact("author_analysis_arguments", text=canonical_json(request.arguments))
        .save()
    )
    return AuthorAnalysisReceipt(
        code_revision=request.code_revision,
        analysis_id=manifest.id,
        groups=tuple(receipts),
    )
