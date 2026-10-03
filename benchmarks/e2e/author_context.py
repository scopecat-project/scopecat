"""Explicit scientific inputs shared by the virtual reference benchmarks."""

from collections.abc import Callable
from typing import cast

from scopecat.application.author_project import AuthorProject
from scopecat.project import load_project_symbol
from scopecat.records.parameter_revision import ParameterRevisionContent


def select_reference_context(author: AuthorProject) -> None:
    """Select independent parameters/setup without publishing a global default."""
    assert author.project_root is not None
    factory = cast(
        "Callable[[], ParameterRevisionContent]",
        load_project_symbol(
            "reference_lab.configuration:initial_parameters",
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
