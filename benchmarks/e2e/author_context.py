"""Explicit scientific inputs shared by the device-free author benchmarks."""

from collections.abc import Callable
from typing import cast

from scopecat.application.author_project import AuthorProject
from scopecat.project import load_project_symbol
from scopecat.records.parameter_revision import ParameterRevisionContent


def select_author_context(author: AuthorProject) -> None:
    """Select independent parameters/setup without publishing a global default."""
    assert author.project_root is not None
    factory = cast(
        "Callable[[], ParameterRevisionContent]",
        load_project_symbol(
            "ui_signal.application:initial_parameters",
            author.project_root,
            subject="benchmark parameters",
            require_callable=True,
        ),
    )
    content = factory()
    parameters = author.parameters.save(
        name="benchmark-parameters",
        catalog=content.catalog,
        parameters=content.parameters,
    )
    author.use(parameters=parameters, setup=author.setup.get("initial"))
