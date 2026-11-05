"""Real loopback transports with explicit late-callback ordering, not frequency."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
import uvicorn
from uvicorn.server import ServerState

from scopecat_server.http_protocol import ClosingAwareH11Protocol, http_protocol


@pytest.mark.parametrize("completed", [False, True], ids=["idle", "completed"])
@pytest.mark.parametrize(
    "payload",
    [b"GET /late HTTP/1.1\r\nHost: localhost\r\n\r\n", b"invalid\r\n\r\n"],
    ids=["valid", "malformed"],
)
def test_closed_tcp_transport_rejects_queued_input(
    completed: bool, payload: bytes
) -> None:
    asyncio.run(_tcp_case("completed" if completed else "idle", payload))


@pytest.mark.parametrize("mode", ["active", "malformed-open"])
def test_open_tcp_transport_keeps_request_and_error_handling(mode: str) -> None:
    asyncio.run(_tcp_case(mode, b"invalid\r\n\r\n"))


async def _tcp_case(mode: str, payload: bytes) -> None:
    loop = asyncio.get_running_loop()
    errors: list[dict[str, Any]] = []
    loop.set_exception_handler(lambda _loop, context: errors.append(context))
    state = ServerState()
    calls: list[str] = []
    bodies: list[bytes] = []
    protocols: list[ClosingAwareH11Protocol] = []
    started = asyncio.Event()
    done = asyncio.Event()
    connected = asyncio.Event()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        calls.append(scope["path"])
        started.set()
        body = b""
        while True:
            event = await receive()
            body += event.get("body", b"")
            if not event.get("more_body", False):
                break
        bodies.append(body)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"2")],
            }
        )
        await send({"type": "http.response.body", "body": b"OK"})
        done.set()

    config = uvicorn.Config(app, lifespan="off", log_config=None)

    def factory() -> ClosingAwareH11Protocol:
        protocol = ClosingAwareH11Protocol(config, state, {})
        protocols.append(protocol)
        connected.set()
        return protocol

    server = await loop.create_server(factory, "127.0.0.1", 0)
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1]), 5
        )
        await asyncio.wait_for(connected.wait(), 5)
        protocol = protocols[0]
        if mode == "completed":
            writer.write(b"GET /first HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            await asyncio.wait_for(done.wait(), 5)
            response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            response += await asyncio.wait_for(reader.readexactly(2), 5)
            assert response.endswith(b"OK") and b"200 OK" in response
        if mode == "active":
            writer.write(
                b"POST /active HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Length: 4\r\n\r\nab"
            )
            await writer.drain()
            await asyncio.wait_for(started.wait(), 5)
            protocol.shutdown()
            assert not protocol.transport.is_closing()
            writer.write(b"cd")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 5)
            assert response.endswith(b"OK") and b"200 OK" in response
            assert bodies == [b"abcd"]
        elif mode == "malformed-open":
            writer.write(payload)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 5)
            assert b"400 Bad Request" in response
        else:
            protocol.shutdown()
            assert protocol.transport.is_closing()
            # Real transport, forced callback ordering: this does not claim the
            # client naturally delivers these bytes after shutdown every time.
            loop.call_soon(protocol.data_received, payload)
            await asyncio.sleep(0)
            assert await asyncio.wait_for(reader.read(), 5) == b""
        writer.close()
        await writer.wait_closed()
        for _ in range(50):
            if not state.connections and not state.tasks:
                break
            await asyncio.sleep(0.01)
        assert not state.connections and not state.tasks
        assert calls == ({"completed": ["/first"], "active": ["/active"]}.get(mode, []))
        assert not errors
    finally:
        if writer is not None:
            writer.close()
        server.close()
        await server.wait_closed()
        for protocol in tuple(state.connections):
            protocol.transport.close()
        for task in tuple(state.tasks):
            task.cancel()
        await asyncio.gather(*state.tasks, return_exceptions=True)


def test_protocol_selection_preserves_other_auto_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uvicorn.protocols.http.h11_impl import H11Protocol

    monkeypatch.setattr("scopecat_server.http_protocol.AutoHTTPProtocol", H11Protocol)
    assert http_protocol() is ClosingAwareH11Protocol
    monkeypatch.setattr(
        "scopecat_server.http_protocol.AutoHTTPProtocol", asyncio.Protocol
    )
    assert http_protocol() is asyncio.Protocol
