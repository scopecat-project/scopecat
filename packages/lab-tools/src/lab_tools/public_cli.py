"""Installed application CLI composed over the server's project commands."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from scopecat_server.cli import create_app
from scopecat_server.cli import init_command as initialize_project_command

app = create_app(include_init=False)
console = Console()
_CURRENT_DIRECTORY = Path()


@app.command(
    "app", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def application_command(context: typer.Context) -> None:
    """Open the workbench service entry; optionally register an existing project."""
    from lab_tools.application import main as application_main

    application_main(context.args)


@app.command(
    "notebook",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def notebook_command(context: typer.Context) -> None:
    """Open author code with its registered laboratory's Notebook environment."""
    from lab_tools.author_notebook import main as notebook_main

    notebook_main(context.args)


@app.command(
    "teach", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def teach_command(context: typer.Context) -> None:
    """Start or clear a software practice in an installed application."""
    from lab_tools.practice import main as teaching_main

    teaching_main(context.args)


@app.command("init")
def init_command(
    project: Annotated[
        Path, typer.Argument(help="Directory to initialize.")
    ] = _CURRENT_DIRECTORY,
    topic: Annotated[
        str | None, typer.Option(help="Initialize one standalone tutorial topic.")
    ] = None,
) -> None:
    """Initialize a runnable local lab project."""
    if topic is None:
        initialize_project_command(project)
        return
    from .project import create_project

    try:
        created = create_project(project, topic=topic)
    except (ImportError, ValueError, OSError) as error:
        Console(stderr=True).print(f"[red]error:[/red] {error}", soft_wrap=True)
        raise typer.Exit(code=1) from error
    console.print(f"[green]initialized tutorial[/green] {created.parent}")


def main(argv: Sequence[str] | None = None) -> None:
    """Run the installed application's command line."""
    app(args=None if argv is None else list(argv), prog_name="scopecat")


if __name__ == "__main__":
    main()
