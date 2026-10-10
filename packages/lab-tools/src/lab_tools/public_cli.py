"""Installed application CLI composed over the server's project commands."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, cast

import typer
from rich.console import Console

from scopecat_server.cli import create_app
from scopecat_server.cli import init_command as initialize_project_command

app = create_app(include_init=False)
console = Console()
_CURRENT_DIRECTORY = Path()


@app.command(
    "app",
    add_help_option=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def application_command(context: typer.Context) -> None:
    """Manage the application; show status unless an action is specified."""
    from lab_tools.application import main as application_main

    application_main(context.args)


@app.command(
    "notebook",
    add_help_option=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def notebook_command(context: typer.Context) -> None:
    """Open author code with its registered laboratory's Notebook environment."""
    from lab_tools.author_notebook import main as notebook_main

    notebook_main(context.args)


@app.command(
    "teach",
    add_help_option=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
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
        str | None,
        typer.Option(help="Create editable lesson source for the application."),
    ] = None,
) -> None:
    """Initialize a runnable local lab project."""
    if topic is None:
        initialize_project_command(project)
        return
    from lab_teaching.lessons import LessonTopic

    from .notebook_journey import create_lesson_source

    try:
        create_lesson_source(project.resolve(), cast("LessonTopic", topic))
    except (ImportError, ValueError, OSError) as error:
        Console(stderr=True).print(f"[red]error:[/red] {error}", soft_wrap=True)
        raise typer.Exit(code=1) from error
    console.print(f"[green]initialized author source[/green] {project.resolve()}")
    console.print(
        "在 Scopecat Settings 添加此作者目录并准备环境；课程继续使用同一应用。"
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run the installed application's command line."""
    app(args=None if argv is None else list(argv), prog_name="scopecat")


if __name__ == "__main__":
    main()
