"""Software-only VNA for control-window layout and saved-curve browser checks."""

import socket
import sys
import threading
from pathlib import Path

import uvicorn
from scopecat.sdk.instruments import (
    DriverCatalog,
    DriverConnectionSpec,
    DriverSpec,
    InstrumentBackend,
    InstrumentConnectionContext,
    InstrumentProviderContext,
    InstrumentProviderDescription,
)
from scopecat_instruments.virtual import VirtualLabWorld, VirtualNetworkAnalyzer

from scopecat_server.instruments.backend import LocalInstrumentBackendEndpoint
from scopecat_server.runtime import LocalDaemonRuntime


class CurveProvider:
    provider_id = "tests.curve_layout"

    def __init__(self) -> None:
        self.world = VirtualLabWorld(seed=42)

    def describe(
        self, context: InstrumentProviderContext
    ) -> InstrumentProviderDescription:
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=tuple(
                VirtualNetworkAnalyzer(binding.id, self.world).describe()
                for binding in context.bindings
            ),
        )

    def connect(self, context: InstrumentConnectionContext) -> VirtualNetworkAnalyzer:
        return VirtualNetworkAnalyzer(context.binding.id, self.world)


root, static = map(Path, sys.argv[1:])
provider = CurveProvider()
endpoint = LocalInstrumentBackendEndpoint(
    InstrumentBackend(
        provider=provider,
        driver_catalog=DriverCatalog(
            provider_id=provider.provider_id,
            drivers=(
                DriverSpec(
                    driver_id="scopecat.virtual.vna",
                    implementation_version="v1",
                    label="Synthetic network analyzer",
                    connections=(
                        DriverConnectionSpec(
                            kind="virtual",
                            options_schema={"type": "object", "properties": {}},
                        ),
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

    def stop_from_stdin() -> None:
        # The test sends one explicit token. Legacy fixtures with ignored stdin
        # see EOF and keep running; an EOF is not a shutdown request.
        if sys.stdin.readline().strip() == "stop":
            server.should_exit = True

    threading.Thread(target=stop_from_stdin, daemon=True).start()
    server.run(sockets=[listener])
