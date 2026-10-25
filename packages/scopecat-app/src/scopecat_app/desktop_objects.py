"""The running native host consumes typed requests; routing stays in this host."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from contextlib import suppress
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx2
import psutil

from scopecat.daemon.client import DaemonClient, DaemonClientError
from scopecat.records.application_open import (
    ApplicationOpenTarget,
    DesktopHostLease,
    DesktopHostRegistration,
    DesktopOpenAcknowledgement,
)

from .desktop_session import DesktopSession


def object_location(page_url: str, target: ApplicationOpenTarget) -> str:
    """Only the host selects an origin; callers provide identities, never URLs."""
    origin = urlsplit(page_url)
    query = {"run": target.object_id, "focus": "run"}
    section = "runs"
    if target.kind == "procedure":
        query = {"procedure": target.object_id}
        section = "decisions" if target.view == "decision" else "launch"
    elif target.kind == "analysis":
        query = {"analysis": target.object_id}
        section = "analyses"
    elif target.kind in {"run_analysis", "sample_analysis"}:
        assert target.owner_id is not None
        owner = "run" if target.kind == "run_analysis" else "sample"
        query = {owner: target.owner_id, f"{owner}-analysis": target.object_id}
        section = "runs" if owner == "run" else "samples"
    return urlunsplit((origin.scheme, origin.netloc, "/", urlencode(query), section))


class DesktopObjectOpener:
    def __init__(
        self,
        session: DesktopSession,
        open_target: Callable[[ApplicationOpenTarget], None],
    ):
        self.session = session
        self.open_target = open_target
        self.instance = uuid4()
        self._client: DaemonClient | None = None
        self._lease: DesktopHostLease | None = None
        self._acks: dict[UUID, DesktopOpenAcknowledgement] = {}
        self._last_error: str | None = None

    def pump(self) -> None:
        base_url = self.session.base_url
        if base_url is None or self.session.closing.is_set():
            return
        try:
            if self._client is None or self._client.base_url != base_url:
                self.close()
                self._client = DaemonClient(base_url, timeout=2)
            client = self._client
            if self._lease is None:
                health = client.health()
                lease = client.register_desktop_host(
                    DesktopHostRegistration(
                        project_id=health.project_id,
                        deployment_id=health.deployment_id,
                        process_id=os.getpid(),
                        process_created=psutil.Process().create_time(),
                        instance=self.instance,
                    )
                )
                # A new daemon/host generation cannot replay old opening requests.
                self._acks = {
                    key: ack for key, ack in self._acks.items() if ack.lease == lease
                }
                self._lease = lease
            for key, ack in tuple(self._acks.items()):
                client.acknowledge_desktop_open(ack)
                del self._acks[key]
            for receipt in client.poll_desktop_open(self._lease).items:
                # Cache a terminal acknowledgement even if native creation fails
                # after making a window. Retrying transport never creates again.
                opened, detail = False, None
                try:
                    with self.session.operation(allow_files=True):
                        self.open_target(receipt.target)
                    opened = True
                except Exception as error:
                    detail = (
                        f"Could not confirm opening: {error}. "
                        "Check existing desktop windows before opening again."
                    )
                ack = DesktopOpenAcknowledgement(
                    lease=self._lease,
                    request_id=receipt.request_id,
                    opened=opened,
                    detail=detail,
                )
                self._acks[receipt.request_id] = ack
                client.acknowledge_desktop_open(ack)
                del self._acks[receipt.request_id]
            self._last_error = None
        except (httpx2.HTTPError, DaemonClientError, ValueError, OSError) as error:
            self._lease = None
            if str(error) != self._last_error:
                logging.getLogger(__name__).warning(
                    "Desktop object opening unavailable: %s", error
                )
                self._last_error = str(error)

    def close(self) -> None:
        if self._client is not None:
            if self._lease is not None:
                with suppress(httpx2.HTTPError, DaemonClientError, ValueError):
                    self._client.unregister_desktop_host(self._lease)
            self._client.close()
        self._client = None
        self._lease = None
        self._acks.clear()
