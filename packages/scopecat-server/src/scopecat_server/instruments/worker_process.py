"""Minimal multiprocessing entry point for an instrument worker."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import cast

from scopecat_server._startup_diagnostics import begin, finish, stage


def run_instrument_worker(
    connection: object,
    project_root: str,
    instrument_backend_spec: str,
    generation: str,
    installed_packages: tuple[tuple[str, str], ...] = (),
) -> None:
    """Load the driver RPC runtime only after the spawned process is ready."""

    begin(process="instrument")
    stage(f"generation={generation}; importing output capture")
    try:
        from .worker_output import capture_worker_output

        stage("initializing output capture")
        with capture_worker_output(Path(project_root), generation):
            stage("output capture ready; importing RPC runtime")
            worker = import_module("scopecat_server.instruments.worker")
            stage("RPC runtime imported")
            worker_main = cast(
                "Callable[[object, str, str, tuple[tuple[str, str], ...]], None]",
                worker._instrument_worker_main,
            )
            worker_main(
                connection, project_root, instrument_backend_spec, installed_packages
            )
    finally:
        finish()


__all__ = ["run_instrument_worker"]
