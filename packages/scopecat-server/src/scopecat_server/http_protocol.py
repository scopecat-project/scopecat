"""Narrow Uvicorn boundary for input queued behind transport closure (#1442)."""

from __future__ import annotations

import asyncio
from typing import override

from uvicorn.protocols.http.auto import AutoHTTPProtocol
from uvicorn.protocols.http.h11_impl import H11Protocol


class ClosingAwareH11Protocol(H11Protocol):
    """Do not feed late callbacks into an already-closed HTTP connection.

    Uvicorn 0.54's idle shutdown closes h11 before a queued Windows transport
    callback can run. Feeding it then attempts a 400 response in CLOSED state.
    Active requests keep their transport open during shutdown and still drain.
    Remove this compatibility boundary when an upstream fix is qualified.
    """

    @override
    def data_received(self, data: bytes) -> None:
        if self.transport.is_closing():
            return
        super().data_received(data)


def http_protocol() -> type[asyncio.Protocol]:
    """Preserve auto selection if an environment already supplies httptools."""
    if AutoHTTPProtocol is H11Protocol:
        return ClosingAwareH11Protocol
    return AutoHTTPProtocol
