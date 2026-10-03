"""Minimal entry point in the selected instrument execution environment."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import cast

from scopecat_server._startup_diagnostics import begin, finish, stage

type _WorkerMain = Callable[
    [object, str, str, tuple[tuple[str, str], ...], str | None], None
]


def run_instrument_worker(
    connection: object,
    project_root: str,
    instrument_backend_spec: str,
    generation: str,
    installed_packages: tuple[tuple[str, str], ...] = (),
    code_root: str | None = None,
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
                "_WorkerMain",
                worker._instrument_worker_main,
            )
            worker_main(
                connection,
                project_root,
                instrument_backend_spec,
                installed_packages,
                code_root,
            )
    finally:
        finish()


__all__ = ["run_instrument_worker"]


if __name__ == "__main__":
    import json
    import socket
    import sys

    from .worker_transport import ByteConnection

    request = cast("dict[str, object]", json.loads(sys.argv[3]))
    with socket.create_connection(
        ("127.0.0.1", int(sys.argv[1])), timeout=30
    ) as channel:
        channel.settimeout(None)
        connection = ByteConnection(channel)
        connection.send_bytes(sys.argv[2].encode())
        run_instrument_worker(
            connection,
            cast("str", request["root"]),
            cast("str", request["factory"]),
            cast("str", request["generation"]),
            tuple(
                (name, distribution)
                for name, distribution in cast(
                    "list[tuple[str, str]]", request["packages"]
                )
            ),
            cast("str | None", request["code_root"]),
        )
