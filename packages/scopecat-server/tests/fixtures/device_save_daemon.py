"""Isolated software-only device registry for the save/replay browser journey."""

import socket
import sys
from pathlib import Path

import uvicorn
from scopecat.sdk.instruments import (
    DriverCatalog,
    DriverConnectionSpec,
    DriverSpec,
    InstrumentBackend,
)
from scopecat_testkit.signal_instruments import TestSignalInstrumentProvider

from scopecat_server.instruments.backend import LocalInstrumentBackendEndpoint
from scopecat_server.runtime import LocalDaemonRuntime

root, static = map(Path, sys.argv[1:])
provider = TestSignalInstrumentProvider()
endpoint = LocalInstrumentBackendEndpoint(
    InstrumentBackend(
        provider=provider,
        driver_catalog=DriverCatalog(
            provider_id=provider.provider_id,
            drivers=(
                DriverSpec(
                    driver_id="tests.signal_instrument",
                    implementation_version="v0",
                    label="Software signal",
                    connections=(
                        DriverConnectionSpec(kind="virtual", options_schema={}),
                    ),
                ),
            ),
        ),
    )
)
with (
    LocalDaemonRuntime(root, instrument_endpoint=endpoint) as runtime,
    socket.socket() as listener,
):
    listener.bind(("127.0.0.1", 0))
    (root / "url").write_text(f"http://127.0.0.1:{listener.getsockname()[1]}")
    server = uvicorn.Server(
        uvicorn.Config(runtime.app(static_dir=static), log_level="warning")
    )
    server.run(sockets=[listener])
