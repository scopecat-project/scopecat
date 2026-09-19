from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Event

import httpx2

import scopecat.api.instruments as instrument_api
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.wire import (
    InstrumentSessionLeaseReceipt,
    InstrumentSessionOpenReceipt,
)
from scopecat.records.instrument import InstrumentStateSnapshot
from scopecat.records.setup import (
    SetupRevisionRef,
)
from scopecat.sdk.instruments import InstrumentDescription


def test_instrument_heartbeat_recovers_from_temporary_unavailability() -> None:
    now = datetime.now(UTC)
    renewed_at = now - timedelta(seconds=3)
    session = InstrumentSessionOpenReceipt(
        session_id="session-1",
        actor="operator",
        setup=SetupRevisionRef(
            revision_id="baseline", content_hash="sha256:" + "0" * 64
        ),
        instrument_ids=("source",),
        configured_default_instrument_ids=(),
        descriptions=(
            InstrumentDescription(
                instrument_id="source",
                implementation_id="tests.source",
                implementation_version="1",
            ),
        ),
        observed_state=(InstrumentStateSnapshot(instrument_id="source"),),
        opened_at=renewed_at,
        renewed_at=renewed_at,
        expires_at=now + timedelta(seconds=3),
    )
    renewed = InstrumentSessionLeaseReceipt(
        session_id=session.session_id,
        renewed_at=now,
        expires_at=now + timedelta(seconds=3),
    )
    recovered = Event()
    attempts = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        assert request.url.path.endswith("/instrument-sessions/session-1/heartbeat")
        attempts += 1
        if attempts == 1:
            return httpx2.Response(
                503,
                json={"detail": "project database writer is busy"},
            )
        recovered.set()
        return httpx2.Response(200, content=renewed.model_dump_json())

    with DaemonClient(
        "http://daemon.test",
        transport=httpx2.MockTransport(handler),
    ) as client:
        heartbeat = instrument_api._InstrumentSessionHeartbeat(client, session)
        heartbeat.start()
        try:
            assert recovered.wait(timeout=5)
            heartbeat.require_live()
            assert attempts == 2
        finally:
            heartbeat.close()
