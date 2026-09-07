"""Project-oriented Scopecat command line."""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Never

import typer
from rich.console import Console

app = typer.Typer(
    name="scopecat",
    help="Manage one local Scopecat lab project.",
    no_args_is_help=True,
)
config_app = typer.Typer(
    help="Inspect project configuration sources.",
    no_args_is_help=True,
)
automation_app = typer.Typer(
    help="Run project-owned resident automation.",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")
app.add_typer(automation_app, name="automation")
snapshot_app = typer.Typer(
    help="Create, verify, and restore stopped-project snapshots.",
    no_args_is_help=True,
)
app.add_typer(snapshot_app, name="snapshot")
console = Console()
error_console = Console(stderr=True)

_CURRENT_DIRECTORY = Path()
_DEFAULT_STATIC_DIR = Path(__file__).with_name("static")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@snapshot_app.command("create")
def snapshot_create(
    project: Annotated[Path, typer.Argument(help="Stopped project directory.")],
    destination: Annotated[
        Path, typer.Argument(help="New snapshot directory outside the project.")
    ],
) -> None:
    """Capture the database, immutable objects, source, and runtime versions."""
    from scopecat.project import open_project

    from .snapshots import SnapshotError, create_snapshot
    from .storage.sqlite.project_store import ProjectStoreError

    try:
        manifest = create_snapshot(open_project(project), destination)
    except (SnapshotError, ProjectStoreError, ValueError) as error:
        _fail(error)
    console.print(
        f"[green]snapshot created[/green] {destination.resolve()} "
        f"(schema {manifest.schema_version})"
    )


@snapshot_app.command("verify")
def snapshot_verify(
    snapshot: Annotated[Path, typer.Argument(help="Snapshot directory.")],
) -> None:
    """Verify file hashes, SQLite integrity, schema, and referenced objects."""
    from .snapshots import SnapshotError, verify_snapshot
    from .storage.sqlite.project_store import ProjectStoreError

    try:
        manifest = verify_snapshot(snapshot)
    except (SnapshotError, ProjectStoreError) as error:
        _fail(error)
    console.print(
        f"[green]snapshot verified[/green] {snapshot.resolve()} "
        f"(schema {manifest.schema_version})"
    )


@snapshot_app.command("restore")
def snapshot_restore(
    snapshot: Annotated[Path, typer.Argument(help="Snapshot directory.")],
    destination: Annotated[Path, typer.Argument(help="Fresh project directory.")],
) -> None:
    """Restore verified files; project code and procedures are not started."""
    from .snapshots import SnapshotError, restore_snapshot
    from .storage.sqlite.project_store import ProjectStoreError

    try:
        restore_snapshot(snapshot, destination)
    except (SnapshotError, ProjectStoreError) as error:
        _fail(error)
    console.print(f"[green]snapshot restored[/green] {destination.resolve()}")
    console.print(
        "Reinstall the recorded dependencies before starting the project. "
        "Procedures require explicit dispatch."
    )


def _validate_host(value: str) -> str:
    if value not in _LOOPBACK_HOSTS:
        raise typer.BadParameter("must be a loopback host")
    return value


@app.command("init")
def init_command(
    project: Annotated[
        Path,
        typer.Argument(help="Directory to initialize."),
    ] = _CURRENT_DIRECTORY,
) -> None:
    """Initialize a runnable local lab project."""

    from .lifecycle import DaemonLifecycleError, initialize_project

    try:
        initialized = initialize_project(project)
    except DaemonLifecycleError as error:
        _fail(error)
    console.print(
        f"[green]initialized[/green] {initialized.root}",
        soft_wrap=True,
    )
    console.print(
        f"[dim]config source[/dim] "
        f"{initialized.root / 'src/scopecat_lab/configuration.py'}",
        soft_wrap=True,
    )
    project_arg = _shell_quote(str(initialized.root))
    notebook_arg = _shell_quote(str(initialized.root / "notebooks/01_first_run.py"))
    console.print(
        f"[dim]next[/dim] scopecat config check {project_arg}",
        soft_wrap=True,
    )
    console.print(
        f"[dim]next[/dim] scopecat start {project_arg}",
        soft_wrap=True,
    )
    console.print(
        f"[dim]next[/dim] scopecat open {project_arg}",
        soft_wrap=True,
    )
    console.print(
        f"[dim]first run[/dim] python {notebook_arg}",
        soft_wrap=True,
    )


def _shell_quote(value: str) -> str:
    if sys.platform == "win32":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)


@config_app.command("check")
def config_check(
    project: Annotated[
        Path,
        typer.Argument(help="Project directory or scopecat.toml."),
    ] = _CURRENT_DIRECTORY,
) -> None:
    """Validate the project's lazy bootstrap configuration source."""

    from scopecat.config.resolution import validate_config_profile
    from scopecat.project import open_project
    from scopecat.records.config import config_content_hash

    try:
        selected = open_project(project)
        bootstrap_config = selected.load_bootstrap().bootstrap_config
        if bootstrap_config is None:
            raise ValueError("project bootstrap does not define bootstrap_config")
        config = validate_config_profile(bootstrap_config())
    except _project_config_errors() as error:
        _fail(error)

    console.print(
        f"[green]valid[/green] snapshot={config.id} "
        f"content_hash={config_content_hash(config)}",
        soft_wrap=True,
    )


@config_app.command("diff")
def config_diff(
    project: Annotated[
        Path,
        typer.Argument(help="Project directory or scopecat.toml."),
    ] = _CURRENT_DIRECTORY,
) -> None:
    """Compare executable project configuration with the daemon default."""

    from scopecat.project import open_project

    from .config_commands import diff_project_config

    try:
        result = diff_project_config(open_project(project))
    except _project_config_errors() as error:
        _fail(error)

    if not result.has_drift:
        console.print(
            f"[green]in sync[/green] content_hash={result.source_content_hash}",
            soft_wrap=True,
        )
        return

    console.print(
        f"[yellow]different[/yellow] source={result.source_content_hash} "
        f"daemon={result.active_content_hash}",
        soft_wrap=True,
    )
    for line in result.unified_json_diff():
        console.print(line, markup=False, soft_wrap=True)


@config_app.command("apply")
def config_apply(
    project: Annotated[
        Path,
        typer.Argument(help="Project directory or scopecat.toml."),
    ] = _CURRENT_DIRECTORY,
    actor: Annotated[
        str,
        typer.Option(help="Identity recorded for the configuration change."),
    ] = "local-operator",
    note: Annotated[
        str,
        typer.Option(help="Reason recorded with the immutable revision."),
    ] = "apply project config source",
) -> None:
    """Validate project configuration and make it the daemon default."""

    from scopecat.project import open_project

    from .config_commands import apply_project_config

    try:
        result = apply_project_config(
            open_project(project),
            actor=actor,
            note=note,
        )
    except _project_config_errors() as error:
        _fail(error)

    state = "[green]applied[/green]" if result.changed else "[green]in sync[/green]"
    console.print(
        f"{state} entry={result.entry_id} content_hash={result.source_content_hash}",
        soft_wrap=True,
    )


@config_app.command("export")
def config_export(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Destination JSON snapshot."),
    ],
    project: Annotated[
        Path,
        typer.Argument(help="Project directory or scopecat.toml."),
    ] = _CURRENT_DIRECTORY,
    force: Annotated[
        bool,
        typer.Option("--force", help="Replace an existing destination."),
    ] = False,
) -> None:
    """Export the complete daemon default as generated JSON."""

    from scopecat.project import open_project

    from .config_commands import export_project_config

    try:
        result = export_project_config(
            open_project(project),
            output,
            overwrite=force,
        )
    except _project_config_errors() as error:
        _fail(error)

    console.print(
        f"[green]exported[/green] {result.destination} "
        f"content_hash={result.content_hash}",
        soft_wrap=True,
    )


@automation_app.command("work")
def automation_work(
    project: Annotated[
        Path,
        typer.Argument(help="Project directory or scopecat.toml."),
    ] = _CURRENT_DIRECTORY,
    once: Annotated[
        bool,
        typer.Option("--once", help="Run one bounded automation cycle and exit."),
    ] = False,
    poll_seconds: Annotated[
        float,
        typer.Option(
            "--poll-seconds",
            help="Idle polling interval for the resident automation worker.",
            min=0.001,
        ),
    ] = 1.0,
) -> None:
    """Finalize calibrations, plan work, and execute exact procedures."""

    import signal
    from threading import Event
    from types import FrameType

    from scopecat.api.project_worker import (
        ProjectAutomationCycleResult,
        ProjectAutomationWorker,
    )
    from scopecat.project import open_project

    try:
        selected = open_project(project)
        with selected.connect() as lab:
            worker = ProjectAutomationWorker(
                lab.procedures,
                planner=lab.procedures.interval_planner(),
                calibration_evaluator=lab.calibrations.evaluator(),
                calibration_finalizer=lab.calibrations.publication_finalizer(),
            )
            if once:
                result = worker.cycle()
                outcome = (
                    "[green]cycle complete[/green]"
                    if not result.needs_review
                    else "[red]cycle completed with failures[/red]"
                )
                console.print(
                    f"{outcome} "
                    f"publication_ready="
                    f"{result.publications.ready_items} "
                    f"publication_prepared="
                    f"{result.publications.prepared_items} "
                    f"publication_published="
                    f"{result.publications.published_items} "
                    f"publication_deferred="
                    f"{result.publications.deferred_items} "
                    f"publication_attention="
                    f"{result.publications.attention_items} "
                    f"publication_reconciled="
                    f"{result.publications.reconciled_items} "
                    f"publication_superseded="
                    f"{result.publications.superseded_items} "
                    f"publication_races="
                    f"{result.publications.benign_races} "
                    f"publication_barrier="
                    f"{str(result.config_planning_blocked).lower()} "
                    f"interval_created={result.intervals.created_schedules} "
                    f"calibration_admitted={result.calibrations.admitted_members} "
                    f"calibration_blocked={result.calibrations.blocked_members} "
                    f"materialized={result.schedules.materialized} "
                    f"dispatched={result.procedures.dispatched} "
                    f"planner_failures={result.intervals.failures} "
                    f"interval_drifts={result.intervals.drifted_schedules} "
                    f"publication_failures="
                    f"{result.publications.failures} "
                    f"calibration_failures={result.calibrations.failures} "
                    f"calibration_drifts={result.calibrations.cohort_drifts} "
                    f"schedule_failures={result.schedules.failures} "
                    f"procedure_failures={result.procedures.failures} "
                    f"procedure_conflicts={result.procedures.conflicts} "
                    f"benign_conflicts={result.benign_conflicts}",
                    soft_wrap=True,
                )
                if result.needs_review:
                    raise RuntimeError(
                        "automation worker cycle reported "
                        f"{result.failure_count} failure(s)"
                    )
                return

            console.print(
                f"[green]working[/green] {selected.root} "
                f"[dim](worker {worker.worker_id})[/dim]",
                soft_wrap=True,
            )
            stop_event = Event()

            def report_retry(error: Exception, delay: float) -> None:
                error_console.print(
                    f"[yellow]automation worker control unavailable:[/yellow] {error}; "
                    f"retrying in {delay:g}s",
                    soft_wrap=True,
                )

            def report_cycle(result: ProjectAutomationCycleResult) -> None:
                if result.needs_review:
                    error_console.print(
                        "[yellow]automation cycle needs review:[/yellow] "
                        f"planner_failures={result.intervals.failures} "
                        f"interval_drifts={result.intervals.drifted_schedules} "
                        f"publication_failures="
                        f"{result.publications.failures} "
                        f"publication_attention="
                        f"{result.publications.attention_items} "
                        f"calibration_failures={result.calibrations.failures} "
                        f"calibration_drifts={result.calibrations.cohort_drifts} "
                        f"schedule_failures={result.schedules.failures} "
                        f"procedure_failures={result.procedures.failures} "
                        f"procedure_conflicts={result.procedures.conflicts}",
                        soft_wrap=True,
                    )

            def request_stop(_signum: int, _frame: FrameType | None) -> None:
                stop_event.set()

            previous_sigint = signal.getsignal(signal.SIGINT)
            previous_sigterm = signal.getsignal(signal.SIGTERM)
            signal.signal(signal.SIGINT, request_stop)
            signal.signal(signal.SIGTERM, request_stop)
            try:
                worker.run_forever(
                    stop_event,
                    poll_seconds=poll_seconds,
                    on_cycle=report_cycle,
                    on_retry=report_retry,
                )
            except KeyboardInterrupt:
                stop_event.set()
            finally:
                signal.signal(signal.SIGINT, previous_sigint)
                signal.signal(signal.SIGTERM, previous_sigterm)
    except _project_config_errors() as error:
        _fail(error)


@app.command()
def serve(
    project: Annotated[
        Path,
        typer.Argument(help="Project directory or scopecat.toml."),
    ] = _CURRENT_DIRECTORY,
    host: Annotated[
        str,
        typer.Option(help="Loopback listen address.", callback=_validate_host),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option(
            help="Listen port; 0 selects an available port.",
            min=0,
            max=65535,
        ),
    ] = 0,
    static_dir: Annotated[
        Path | None,
        typer.Option(help="Generated GUI bundle to serve."),
    ] = None,
    api_only: Annotated[
        bool,
        typer.Option(help="Serve the API without the project GUI."),
    ] = False,
    executor_lease_ttl_seconds: Annotated[
        float | None,
        typer.Option(
            "--executor-lease-ttl-seconds",
            help="Override the executor lease TTL for development and testing.",
            min=0.001,
            hidden=True,
        ),
    ] = None,
) -> None:
    """Run the project daemon in the foreground."""

    from scopecat.project import ProjectManifestError, open_project

    from .lifecycle import DaemonLifecycleError, serve_project

    try:
        selected = open_project(project)
        selected_static_dir = _select_static_dir(
            static_dir=static_dir,
            api_only=api_only,
        )
        serve_project(
            selected,
            host=host,
            port=port,
            static_dir=selected_static_dir,
            lease_ttl=_lease_ttl(executor_lease_ttl_seconds),
        )
    except (DaemonLifecycleError, ProjectManifestError, OSError, ValueError) as error:
        _fail(error)


@app.command()
def start(
    project: Annotated[
        Path,
        typer.Argument(help="Project directory or scopecat.toml."),
    ] = _CURRENT_DIRECTORY,
    host: Annotated[
        str,
        typer.Option(help="Loopback listen address.", callback=_validate_host),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option(
            help="Listen port; 0 selects an available port.",
            min=0,
            max=65535,
        ),
    ] = 0,
    static_dir: Annotated[
        Path | None,
        typer.Option(help="Generated GUI bundle to serve."),
    ] = None,
    api_only: Annotated[
        bool,
        typer.Option(help="Start the daemon without the project GUI."),
    ] = False,
    executor_lease_ttl_seconds: Annotated[
        float | None,
        typer.Option(
            "--executor-lease-ttl-seconds",
            help="Override the executor lease TTL for development and testing.",
            min=0.001,
            hidden=True,
        ),
    ] = None,
) -> None:
    """Start the project daemon in the background."""

    from scopecat.project import ProjectManifestError, open_project

    from .lifecycle import DaemonLifecycleError, start_project

    try:
        selected = open_project(project)
        selected_static_dir = _select_static_dir(
            static_dir=static_dir,
            api_only=api_only,
        )
        record = start_project(
            selected,
            host=host,
            port=port,
            static_dir=selected_static_dir,
            lease_ttl=_lease_ttl(executor_lease_ttl_seconds),
        )
    except (DaemonLifecycleError, ProjectManifestError, OSError, ValueError) as error:
        _fail(error)
    console.print(
        f"[green]running[/green] {record.base_url} [dim](pid {record.pid})[/dim]"
    )


def _select_static_dir(
    *,
    static_dir: Path | None,
    api_only: bool,
) -> Path | None:
    if api_only:
        if static_dir is not None:
            raise ValueError("--api-only and --static-dir cannot be used together")
        return None
    selected = _DEFAULT_STATIC_DIR if static_dir is None else static_dir.resolve()
    if not (selected / "index.html").is_file():
        raise ValueError(
            "GUI bundle is not installed; pass its directory with --static-dir "
            "or use --api-only"
        )
    return selected


def _lease_ttl(seconds: float | None) -> timedelta | None:
    return None if seconds is None else timedelta(seconds=seconds)


@app.command()
def stop(
    project: Annotated[
        Path,
        typer.Argument(help="Project directory or scopecat.toml."),
    ] = _CURRENT_DIRECTORY,
) -> None:
    """Stop the project's recorded daemon process."""

    from scopecat.project import ProjectManifestError, open_project

    from .lifecycle import DaemonLifecycleError, stop_project

    try:
        selected = open_project(project)
        previous = stop_project(selected)
    except (DaemonLifecycleError, ProjectManifestError, OSError) as error:
        _fail(error)
    if previous.state == "stale":
        console.print("[yellow]stale[/yellow] record removed")
    else:
        console.print("[green]stopped[/green]")


@app.command()
def status(
    project: Annotated[
        Path,
        typer.Argument(help="Project directory or scopecat.toml."),
    ] = _CURRENT_DIRECTORY,
) -> None:
    """Show process identity and daemon health."""

    from scopecat.project import ProjectManifestError, open_project

    from .lifecycle import inspect_daemon

    try:
        selected = open_project(project)
        observed = inspect_daemon(selected)
    except (ProjectManifestError, OSError) as error:
        _fail(error)

    if observed.state == "running" and observed.record is not None:
        console.print(
            f"[green]running[/green] {observed.record.base_url} "
            f"[dim](pid {observed.record.pid})[/dim]"
        )
        return
    if observed.state == "degraded" and observed.record is not None:
        console.print(
            f"[yellow]degraded[/yellow] {observed.record.base_url}: {observed.detail}"
        )
        return
    if observed.state == "stale":
        console.print(f"[yellow]stale[/yellow]: {observed.detail}")
        return
    console.print("[dim]stopped[/dim]")


@app.command("open")
def open_command(
    project: Annotated[
        Path,
        typer.Argument(help="Project directory or scopecat.toml."),
    ] = _CURRENT_DIRECTORY,
) -> None:
    """Open the project GUI in the system browser."""

    from scopecat.project import ProjectManifestError, open_project

    from .lifecycle import DaemonLifecycleError, open_project_gui

    try:
        selected = open_project(project)
        endpoint = open_project_gui(selected)
    except (DaemonLifecycleError, ProjectManifestError, OSError) as error:
        _fail(error)
    console.print(f"[green]opened[/green] {endpoint}")


def main(argv: Sequence[str] | None = None) -> None:
    """Run the Typer application."""

    app(
        args=None if argv is None else list(argv),
        prog_name="scopecat",
    )


def _fail(error: Exception) -> Never:
    error_console.print(
        f"[red]error:[/red] {error}",
        soft_wrap=True,
    )
    raise typer.Exit(code=1) from error


def _project_config_errors() -> tuple[type[Exception], ...]:
    import httpx2
    from scopecat.kernel.errors import ScopecatError
    from scopecat.project import ProjectManifestError

    return (
        ScopecatError,
        ProjectManifestError,
        httpx2.HTTPError,
        ImportError,
        AttributeError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    )


if __name__ == "__main__":
    main()


__all__ = ["app", "main"]
