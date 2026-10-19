"""Bounded, generation-scoped UI requests; never starts a host or scientific work."""

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock
from time import monotonic
from uuid import UUID, uuid4

import psutil
from scopecat.records.application_open import (
    ApplicationOpenCommand,
    ApplicationOpenReceipt,
    ApplicationOpenTarget,
    DesktopHostLease,
    DesktopHostRegistration,
    DesktopHostStatus,
    DesktopOpenAcknowledgement,
    DesktopOpenBatch,
)

from scopecat_server.errors import BackendNotFound


class ApplicationOpenService:
    def __init__(
        self,
        project_id: str,
        deployment_id: str,
        validate: Callable[[ApplicationOpenTarget], None],
    ):
        self.project_id = project_id
        self.deployment_id = deployment_id
        self.validate = validate
        self._lock = Lock()
        self._registration: DesktopHostRegistration | None = None
        self._lease: DesktopHostLease | None = None
        self._heartbeat = 0.0
        self._commands: dict[UUID, ApplicationOpenCommand] = {}
        self._receipts: dict[UUID, ApplicationOpenReceipt] = {}

    def _identity(self, project_id: str, deployment_id: str) -> None:
        if (project_id, deployment_id) != (self.project_id, self.deployment_id):
            raise ValueError("Open target belongs to another application")

    def _alive(self) -> bool:
        registered = self._registration
        if registered is None or monotonic() - self._heartbeat > 15:
            return False
        try:
            return (
                psutil.Process(registered.process_id).create_time()
                == registered.process_created
            )
        except psutil.Error:
            return False

    def register(self, command: DesktopHostRegistration) -> DesktopHostLease:
        self._identity(command.project_id, command.deployment_id)
        try:
            created = psutil.Process(command.process_id).create_time()
        except psutil.Error as error:
            raise ValueError("Desktop process is not available") from error
        if created != command.process_created:
            raise ValueError("Desktop process identity changed")
        with self._lock:
            if self._alive():
                if self._registration != command:
                    raise ValueError("Another desktop host is already connected")
                assert self._lease is not None
                return self._lease
            self._registration = command
            self._lease = DesktopHostLease(host_id=uuid4(), token=uuid4())
            self._heartbeat = monotonic()
            # Old requests are never replayed into a new host generation.
            self._commands.clear()
            self._receipts.clear()
            return self._lease

    def _require_host(self) -> DesktopHostLease:
        if not self._alive() or self._lease is None:
            raise ValueError(
                "Desktop is not available. Open this application's desktop, "
                "then retry opening the object."
            )
        return self._lease

    def status(self) -> DesktopHostStatus:
        with self._lock:
            return DesktopHostStatus(host_id=self._require_host().host_id)

    def submit(self, command: ApplicationOpenCommand) -> ApplicationOpenReceipt:
        self._identity(command.project_id, command.deployment_id)
        with self._lock:
            lease = self._require_host()
            if command.host_id != lease.host_id:
                raise ValueError(
                    "Desktop connection changed. "
                    "Check its windows before making a new open request."
                )
            age = (datetime.now(UTC) - command.issued_at).total_seconds()
            if not -5 <= age <= 60:
                raise ValueError(
                    "Open request expired. Check the desktop before opening again."
                )
            previous = self._commands.get(command.request_id)
            if previous is not None:
                if previous != command:
                    raise ValueError(
                        "Open request identity already names another target"
                    )
                return self._receipts[command.request_id]
            self.validate(command.target)
            for key, item in tuple(self._commands.items()):
                if (datetime.now(UTC) - item.issued_at).total_seconds() > 60:
                    del self._commands[key]
                    del self._receipts[key]
            if len(self._commands) >= 1024:
                raise ValueError(
                    "Too many recent open requests; wait before opening another object"
                )
            receipt = ApplicationOpenReceipt(
                request_id=command.request_id, target=command.target, state="pending"
            )
            self._commands[command.request_id] = command
            self._receipts[command.request_id] = receipt
            return receipt

    def _authenticate(self, lease: DesktopHostLease) -> None:
        if lease != self._require_host():
            raise ValueError("Desktop host lease changed")

    def poll(self, lease: DesktopHostLease) -> DesktopOpenBatch:
        with self._lock:
            self._authenticate(lease)
            self._heartbeat = monotonic()
            items: list[ApplicationOpenReceipt] = []
            for key, receipt in tuple(self._receipts.items()):
                if receipt.state not in {"pending", "opening"}:
                    continue
                if (
                    datetime.now(UTC) - self._commands[key].issued_at
                ).total_seconds() > 60:
                    self._receipts[key] = receipt.model_copy(
                        update={
                            "state": "failed",
                            "detail": "Open request expired before dispatch",
                        }
                    )
                    continue
                try:
                    self.validate(receipt.target)
                except (KeyError, ValueError, BackendNotFound) as error:
                    self._receipts[key] = receipt.model_copy(
                        update={"state": "failed", "detail": str(error)}
                    )
                    continue
                self._receipts[key] = receipt.model_copy(update={"state": "opening"})
                items.append(self._receipts[key])
                break
            return DesktopOpenBatch(items=tuple(items))

    def acknowledge(
        self, command: DesktopOpenAcknowledgement
    ) -> ApplicationOpenReceipt:
        with self._lock:
            self._authenticate(command.lease)
            receipt = self._receipts.get(command.request_id)
            if receipt is None:
                raise ValueError("Open request is no longer retained")
            if receipt.state in {"opened", "failed"}:
                return receipt
            if receipt.state != "opening":
                raise ValueError("Open request was not dispatched to this host")
            receipt = receipt.model_copy(
                update={
                    "state": "opened" if command.opened else "failed",
                    "detail": command.detail,
                }
            )
            self._receipts[command.request_id] = receipt
            return receipt

    def unregister(self, lease: DesktopHostLease) -> None:
        with self._lock:
            if lease == self._lease:
                self._registration = None
                self._lease = None
