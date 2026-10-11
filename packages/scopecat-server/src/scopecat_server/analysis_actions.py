"""Describe declared ordinary analyses without running an analysis or experiment."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Annotated, Literal, cast, get_args, get_origin, get_type_hints

from scopecat.analysis.arguments import AnalysisArgument, encode_arguments
from scopecat.api.analysis import AnalysisFunctionDefinition
from scopecat.application.authoring import AuthorExperiments
from scopecat.kernel.quantity import Quantity
from scopecat.records.analysis_actions import (
    AnalysisActionInput,
    RunAnalysisAction,
    RunAnalysisActions,
    RunAnalysisActionsRequest,
)


def describe_run_analyses(
    request: RunAnalysisActionsRequest,
    *,
    metadata: Mapping[str, object],
    authors: AuthorExperiments | None,
    load_definition: Callable[[str], object],
) -> RunAnalysisActions:
    """The caller reads metadata from request.run_id and loads its exact source.

    Definition loading must enforce the same configured-source boundary as analysis
    execution. No current-source fallback or application-wide function discovery.
    """
    if (
        metadata.get("author_workspace") != request.workspace_id
        or metadata.get("author_code_revision") != request.code_revision.content_hash
    ):
        raise ValueError(
            "analysis actions require the run's exact workspace and source"
        )
    declaration = metadata.get("author_declaration")
    experiment = (
        next(
            (
                item
                for item in authors.experiments
                if item.workspace_id == request.workspace_id
                and item.code_revision == request.code_revision
                and dict(item.source) == declaration
            ),
            None,
        )
        if authors is not None
        else None
    )
    if experiment is None:
        raise ValueError("run author declaration is unavailable in its retained source")
    actions: list[RunAnalysisAction] = []
    for title, reference in experiment.declaration.analyses.items():
        try:
            definition = load_definition(reference)
            if not isinstance(definition, AnalysisFunctionDefinition):
                raise ValueError(
                    "This entry supports ordinary Dataset analysis functions only."
                )
            function = definition.function
            inputs = _inputs(function)
            actions.append(
                RunAnalysisAction(
                    title=title,
                    analysis=reference,
                    description=inspect.getdoc(function) or "",
                    inputs=inputs,
                )
            )
        except (TypeError, ValueError, AttributeError, ImportError, NameError) as error:
            actions.append(
                RunAnalysisAction(
                    title=title,
                    analysis=reference,
                    description="",
                    unavailable_reason=str(error),
                )
            )
    return RunAnalysisActions(
        run_id=request.run_id,
        workspace_id=request.workspace_id,
        code_revision=request.code_revision,
        experiment=experiment.declaration.id,
        actions=tuple(actions),
    )


def _inputs(function: Callable[..., object]) -> tuple[AnalysisActionInput, ...]:
    hints = cast("Mapping[str, object]", get_type_hints(function, include_extras=True))
    result: list[AnalysisActionInput] = []
    for parameter in tuple(inspect.signature(function).parameters.values())[1:]:
        if parameter.kind not in (
            parameter.POSITIONAL_OR_KEYWORD,
            parameter.KEYWORD_ONLY,
        ):
            raise ValueError(
                f"{parameter.name}: use Python for positional or variadic inputs."
            )
        annotation = hints.get(parameter.name)
        if get_origin(annotation) is Annotated:
            annotation = cast("tuple[object, ...]", get_args(annotation))[0]
        kinds: dict[
            object,
            Literal["number", "integer", "text", "boolean", "quantity", "choice"],
        ] = {
            float: "number",
            int: "integer",
            str: "text",
            bool: "boolean",
            Quantity: "quantity",
        }
        choices: tuple[str, ...] = ()
        kind = kinds.get(annotation)
        if get_origin(annotation) is Literal:
            values = cast("tuple[object, ...]", get_args(annotation))
            if values and all(isinstance(value, str) for value in values):
                choices = cast("tuple[str, ...]", values)
                kind = "choice"
        if kind is None:
            raise ValueError(
                f"{parameter.name}: input type is not supported in Runs; use Python."
            )
        required = cast("object", parameter.default) is inspect.Parameter.empty
        default = (
            None
            if required
            else encode_arguments(
                function.__name__,
                {parameter.name: cast("AnalysisArgument", parameter.default)},
            )[parameter.name]
        )
        result.append(
            AnalysisActionInput(
                name=parameter.name,
                kind=kind,
                required=required,
                default=default,
                choices=choices,
            )
        )
    return tuple(result)
