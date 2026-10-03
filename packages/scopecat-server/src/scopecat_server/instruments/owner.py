"""One lazily activated backend shared by device and execution services."""

from collections.abc import Callable
from threading import RLock

from .backend import InstrumentBackendEndpoint, InstrumentBackendUnavailable


class InstrumentBackendOwner:
    def __init__(
        self,
        endpoint: InstrumentBackendEndpoint | None = None,
        *,
        activate: Callable[[], InstrumentBackendEndpoint | None] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._activate = activate
        self._lock = RLock()
        self._closed = False

    @property
    def current(self) -> InstrumentBackendEndpoint | None:
        """Inspect without loading driver code, including during health checks."""
        # Reading the published reference must not wait for a vendor startup.
        # Activation/replacement publish it only after obtaining a usable endpoint.
        return self._endpoint

    def get(self) -> InstrumentBackendEndpoint | None:
        with self._lock:
            if self._closed:
                raise InstrumentBackendUnavailable("instrument backend is closed")
            if self._activate is not None:
                self._endpoint = self._activate()
                self._activate = None
            return self._endpoint

    def replace(
        self, endpoint: InstrumentBackendEndpoint
    ) -> InstrumentBackendEndpoint | None:
        """Publish a validated replacement without activating the old selection."""
        with self._lock:
            if self._closed:
                raise InstrumentBackendUnavailable("instrument backend is closed")
            previous = self._endpoint
            self._endpoint = endpoint
            self._activate = None
            return previous

    def close(self) -> InstrumentBackendEndpoint | None:
        """Fence activation and return the endpoint for the normal shutdown drain."""
        with self._lock:
            self._closed = True
            self._activate = None
            return self._endpoint
