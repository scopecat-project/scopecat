"""Isolated software-only registry for device save and connection browser journeys."""

import socket
import sys
from pathlib import Path
from typing import override

import uvicorn
from scopecat.sdk.instruments import (
    DriverCatalog,
    DriverConnectionSpec,
    DriverSpec,
    InstrumentBackend,
    InstrumentConnectionContext,
)
from scopecat_testkit.signal_instruments import (
    TestSignalInstrument,
    TestSignalInstrumentProvider,
)

from scopecat_server.instruments.backend import LocalInstrumentBackendEndpoint
from scopecat_server.runtime import LocalDaemonRuntime

root, static = map(Path, sys.argv[1:])


class ConnectionTestProvider(TestSignalInstrumentProvider):
    @override
    def connect(self, context: InstrumentConnectionContext) -> TestSignalInstrument:
        if context.binding.connection.options.get("gain") == -1:
            raise ValueError("Virtual endpoint unavailable for gain -1")
        return super().connect(context)


provider = ConnectionTestProvider()
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
                        DriverConnectionSpec(
                            kind="virtual",
                            options_schema={
                                "type": "object",
                                "properties": {
                                    "gain": {"type": "number", "default": 1},
                                    "addresses": {
                                        "type": "object",
                                        "title": "控制器地址",
                                        "additionalProperties": {"type": "string"},
                                    },
                                    "interpreter": {
                                        "title": "Python 路径",
                                        "default": None,
                                        "anyOf": [{"type": "string"}, {"type": "null"}],
                                    },
                                },
                            },
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
    server.run(sockets=[listener])
