"""Capture driver source once and activate it with the device connection heads."""

import sys
from pathlib import Path
from threading import Lock

from scopecat.author_workspaces import local_author_workspaces
from scopecat.project_sources import (
    materialize_sources,
)
from scopecat.records.author_revision import AuthorRevisionBundle
from scopecat.records.driver_source import DriverSourceSelection, DriverSourceUpdate
from scopecat.runtime_binding import load_runtime_binding

from scopecat_server.author_environment import capture_driver, check
from scopecat_server.errors import BackendConflict
from scopecat_server.instruments.runtime import InstrumentRuntime
from scopecat_server.instruments.worker import SubprocessInstrumentBackendEndpoint
from scopecat_server.services.devices import DeviceService
from scopecat_server.storage.sqlite.driver_sources import DriverSourceRepository
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore


def _start_worker(
    root: Path,
    bundle: AuthorRevisionBundle,
    factory: str,
    python: Path,
) -> SubprocessInstrumentBackendEndpoint:
    check(bundle.manifest, python)
    source = materialize_sources(
        bundle, load_runtime_binding(root).data_root / "driver-sources"
    )
    return SubprocessInstrumentBackendEndpoint(
        root,
        factory,
        python=python,
        code_root=source,
        source_revision=bundle.manifest.ref,
        startup_timeout=30,
        installed_packages=tuple(
            (name, package.distribution)
            for name, package in bundle.manifest.installed_authors.items()
        ),
    )


def restore_driver_source(
    root: Path,
    store: SQLiteProjectStore,
) -> SubprocessInstrumentBackendEndpoint | None:
    repository = DriverSourceRepository(store)
    selected = repository.current()
    if selected is None:
        return None
    endpoint = _start_worker(
        root,
        repository.bundle(selected),
        selected.factory,
        Path(selected.python),
    )
    if endpoint.artifact_hash != selected.artifact_hash:
        endpoint.shutdown()
        raise ValueError("restored driver implementation differs from selected source")
    return endpoint


class DriverSourceService:
    def __init__(
        self,
        root: Path,
        store: SQLiteProjectStore,
        devices: DeviceService,
        instruments: InstrumentRuntime,
    ) -> None:
        self.root = root
        self.store = store
        self.repository = DriverSourceRepository(store)
        self.devices = devices
        self.instruments = instruments
        self._lock = Lock()

    def current(self) -> DriverSourceSelection | None:
        return self.repository.current()

    def update(self, request: DriverSourceUpdate) -> DriverSourceSelection:
        with self._lock:
            recorded = self.repository.get(request.operation_id)
            if recorded is not None:
                if recorded.request != request:
                    raise BackendConflict(
                        "driver source operation has different intent"
                    )
                return recorded
            current = self.current()
            if (
                None if current is None else current.request.operation_id
            ) != request.expected_previous:
                raise BackendConflict(
                    "driver source changed; inspect the active selection and retry"
                )
            source = Path(request.source_root).resolve()
            python = (
                Path(request.python)
                if request.python
                else next(
                    (
                        item.python
                        for item in local_author_workspaces(self.root)
                        if item.root == source
                    ),
                    Path(sys.executable),
                )
            )
            python = python.absolute()
            captured = capture_driver(source, python)
            bundle = captured.bundle
            digest = self.store.objects.put(bundle.model_dump_json().encode()).digest
            replacement = _start_worker(self.root, bundle, captured.factory, python)
            selection = DriverSourceSelection(
                request=request,
                python=str(python),
                code_revision=bundle.manifest.ref,
                factory=captured.factory,
                artifact_hash=replacement.artifact_hash,
            )
            self.devices.replace_backend(
                replacement,
                self.instruments,
                actor=request.actor,
                publish_source=lambda connection: self.repository.publish(
                    connection, selection, digest
                ),
            )
            return selection
