"""Minimal multiprocessing entry point for an instrument worker."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import cast

from .worker_output import capture_worker_output


def run_instrument_worker(
    connection: object,
    project_root: str,
    instrument_backend_spec: str,
    generation: str,
) -> None:
    """Load the driver RPC runtime only after the spawned process is ready."""

    with capture_worker_output(Path(project_root), generation):
        worker = import_module("scopecat_server.instruments.worker")
        worker_main = cast(
            "Callable[[object, str, str], None]",
            worker._instrument_worker_main,
        )
        worker_main(
            connection,
            project_root,
            instrument_backend_spec,
        )


__all__ = ["run_instrument_worker"]
