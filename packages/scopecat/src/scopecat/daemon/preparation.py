"""Reconnectable author preparation, independent of any one caller's wait."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from typing import Protocol

import httpx2

from scopecat.records.author_revision import (
    AuthorPreparation,
    AuthorPreparationRequest,
    AuthorRevisionState,
)


class PreparationClient(Protocol):
    def author_preparation(
        self, operation_id: str, *, timeout: float | None = None
    ) -> AuthorPreparation: ...
    def cancel_author_preparation(self, operation_id: str) -> AuthorPreparation: ...


class AuthorPreparationTimeout(TimeoutError):
    def __init__(self, operation: AuthorPreparationOperation) -> None:
        self.operation = operation
        super().__init__(
            f"Wait ended; author preparation {operation.id} continues. "
            "Reconnect or wait on the same operation."
        )


class AuthorPreparationDisconnected(ConnectionError):
    def __init__(self, operation: AuthorPreparationOperation) -> None:
        self.operation = operation
        super().__init__(
            f"Cannot observe author preparation {operation.id}; "
            "reconnect to this identity to discover its outcome."
        )


class AuthorPreparationSubmissionUncertain(RuntimeError):
    def __init__(
        self, operation: AuthorPreparationOperation, request: AuthorPreparationRequest
    ) -> None:
        self.operation = operation
        self.request = request
        super().__init__(
            f"Submission outcome unknown for preparation {operation.id}; "
            "inspect that identity before submitting another operation."
        )


class AuthorPreparationFailed(ValueError):
    def __init__(self, operation: AuthorPreparation) -> None:
        self.operation = operation
        super().__init__(
            f"Author preparation {operation.status} during {operation.phase}: "
            f"{operation.error or operation.status}"
        )


class AuthorPreparationOperation:
    def __init__(self, client: PreparationClient, operation_id: str) -> None:
        self.client = client
        self.id = operation_id

    def reconnect(self, client: PreparationClient) -> AuthorPreparationOperation:
        return AuthorPreparationOperation(client, self.id)

    def status(self, *, timeout: float | None = None) -> AuthorPreparation:
        return self.client.author_preparation(self.id, timeout=timeout)

    def cancel(self) -> AuthorPreparation:
        """Request cancellation; inspect/wait until cleanup reaches a terminal state."""
        return self.client.cancel_author_preparation(self.id)

    def wait(
        self,
        *,
        timeout: float | None = None,
        on_progress: Callable[[AuthorPreparation], None] | None = None,
    ) -> AuthorRevisionState:
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError("wait timeout must be finite and nonnegative")
        started = time.monotonic()
        last_report = started - 5
        while True:
            elapsed = time.monotonic() - started
            if timeout is not None and elapsed >= timeout:
                raise AuthorPreparationTimeout(self)
            remaining = None if timeout is None else max(0.001, timeout - elapsed)
            try:
                status = self.status(timeout=remaining)
            except httpx2.TimeoutException as error:
                raise AuthorPreparationTimeout(self) from error
            except httpx2.TransportError as error:
                raise AuthorPreparationDisconnected(self) from error
            if status.result is not None:
                return status.result
            if status.terminal:
                raise AuthorPreparationFailed(status)
            now = time.monotonic()
            if now - last_report >= 5:
                if on_progress is not None:
                    on_progress(status)
                elif now - started >= 10:
                    logging.getLogger(__name__).warning(
                        "Still preparing author source after %.0fs (%s): %s; "
                        "operation %s",
                        elapsed,
                        status.status,
                        status.phase,
                        self.id,
                    )
                last_report = now
            time.sleep(min(0.2, remaining) if remaining is not None else 0.2)
