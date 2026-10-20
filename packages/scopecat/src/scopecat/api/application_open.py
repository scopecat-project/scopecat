"""Object-owned desktop requests, without browser URLs or application startup."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx2

from scopecat.records.application_open import (
    ApplicationOpenCommand,
    ApplicationOpenReceipt,
    ApplicationOpenTarget,
)

if TYPE_CHECKING:
    from scopecat.daemon.client import DaemonClient


class ApplicationOpenTimeout(TimeoutError):
    """Keep request.wait() to observe/retry this exact request; do not open anew."""

    def __init__(self, request: ApplicationOpenRequest):
        self.request = request
        super().__init__(
            "Desktop opening is not yet confirmed. "
            "Retry error.request.wait(), or inspect the desktop."
        )


@dataclass(frozen=True)
class ApplicationOpenRequest:
    client: DaemonClient
    command: ApplicationOpenCommand

    def wait(self, *, timeout: float = 5) -> ApplicationOpenReceipt:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("open timeout must be finite and non-negative")
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ApplicationOpenTimeout(self)
            try:
                receipt = self.client.open_application_object(
                    self.command, timeout=remaining
                )
            except httpx2.TransportError as error:
                raise ApplicationOpenTimeout(self) from error
            if time.monotonic() >= deadline:
                raise ApplicationOpenTimeout(self)
            if receipt.state == "opened":
                return receipt
            if receipt.state == "failed":
                raise ValueError(receipt.detail or "Desktop could not open this object")
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))


def open_in_application(
    client: DaemonClient, target: ApplicationOpenTarget, *, timeout: float = 5
) -> ApplicationOpenReceipt:
    """Open through this object's connection, never another/default application."""
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("open timeout must be finite and non-negative")
    deadline = time.monotonic() + timeout

    def remaining() -> float:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise TimeoutError("Desktop connection timed out; no open request sent")
        return budget

    health = client.health(timeout=remaining())
    host = client.application_open_host(timeout=remaining())
    return ApplicationOpenRequest(
        client,
        ApplicationOpenCommand(
            project_id=health.project_id,
            deployment_id=health.deployment_id,
            host_id=host.host_id,
            request_id=uuid4(),
            target=target,
        ),
    ).wait(timeout=max(0, deadline - time.monotonic()))
