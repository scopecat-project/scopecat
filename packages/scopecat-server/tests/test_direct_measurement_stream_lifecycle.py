"""A pinned original must be released even before streaming starts."""

import asyncio
from collections.abc import Generator
from contextlib import contextmanager

import pytest
from scopecat.records.instrument import InstrumentReadback
from scopecat.records.measurement import MeasurementScalar
from scopecat.sdk.instruments.commands import CollectReceipt
from starlette.requests import ClientDisconnect
from starlette.types import Message, Scope

from scopecat_server.http.transport import _raw_receipt_response
from scopecat_server.instruments.retained_receipt import RawCollectReceipt


def _original(events: list[str]):
    @contextmanager
    def source() -> Generator[RawCollectReceipt]:
        raw = RawCollectReceipt.from_receipt(
            CollectReceipt(
                readback=InstrumentReadback(
                    values={"value": MeasurementScalar.create(value=1.0)}
                )
            )
        )
        events.append("entered")
        try:
            yield raw
        finally:
            events.append("closed")

    return source()


def _scope() -> Scope:
    return {"type": "http", "asgi": {"spec_version": "2.4"}}


async def _receive() -> Message:
    return {"type": "http.disconnect"}


@pytest.mark.parametrize("phase", ["http.response.start", "http.response.body"])
def test_disconnect_releases_pin_without_waiting_for_response_gc(phase: str) -> None:
    events: list[str] = []
    response = _raw_receipt_response(_original(events))
    sent: list[str] = []

    async def send(message: Message) -> None:
        sent.append(message["type"])
        if message["type"] == phase:
            raise OSError("synthetic disconnected client")

    async def run() -> None:
        with pytest.raises(ClientDisconnect):
            await response(_scope(), _receive, send)
        # Keep the response alive: garbage collection is not the ownership fence.
        assert response.status_code == 200
        assert events == ["entered", "closed"]

    asyncio.run(run())
    assert sent[-1] == phase
    assert events == ["entered", "closed"]


@pytest.mark.parametrize("phase", ["http.response.start", "http.response.body"])
def test_cancelled_stream_releases_pin_at_headers_or_body(phase: str) -> None:
    events: list[str] = []
    response = _raw_receipt_response(_original(events))

    async def run() -> None:
        reached = asyncio.Event()
        blocked = asyncio.Event()

        async def send(message: Message) -> None:
            if message["type"] == phase:
                reached.set()
                await blocked.wait()

        task = asyncio.create_task(response(_scope(), _receive, send))
        await asyncio.wait_for(reached.wait(), timeout=5)
        assert events == ["entered"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert response.status_code == 200
        assert events == ["entered", "closed"]

    asyncio.run(run())
    assert events == ["entered", "closed"]


def test_completed_stream_closes_original_exactly_once() -> None:
    events: list[str] = []
    response = _raw_receipt_response(_original(events))
    messages: list[Message] = []

    async def send(message: Message) -> None:
        messages.append(message)

    asyncio.run(response(_scope(), _receive, send))
    assert messages[0]["type"] == "http.response.start"
    assert messages[-1] == {
        "type": "http.response.body",
        "body": b"",
        "more_body": False,
    }
    assert events == ["entered", "closed"]
