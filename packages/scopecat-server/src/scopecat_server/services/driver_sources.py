"""Capture driver source once and activate it with the device connection heads."""

import sys
from contextlib import suppress
from pathlib import Path
from threading import Lock

from scopecat.author_workspaces import local_author_workspaces
from scopecat.project_sources import (
    materialize_sources,
)
from scopecat.records.author_revision import AuthorRevisionBundle
from scopecat.records.driver_source import (
    DriverSourceCollection,
    DriverSourceCommand,
    DriverSourceSelection,
    DriverSourceUpdate,
    LocalDriverSource,
    LocalDriverSourceState,
)
from scopecat.runtime_binding import load_runtime_binding

from scopecat_server.author_environment import capture_driver, check
from scopecat_server.errors import BackendConflict
from scopecat_server.instruments.backend import (
    InstrumentBackendEndpoint,
    InstrumentBackendError,
)
from scopecat_server.instruments.runtime import InstrumentRuntime
from scopecat_server.instruments.sources import SourceBackendEndpoint
from scopecat_server.instruments.worker import SubprocessInstrumentBackendEndpoint
from scopecat_server.services.builtin_sources import capture_builtin
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
        bundle, load_runtime_binding(root).materialization_root / "driver-sources"
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
) -> InstrumentBackendEndpoint | None:
    repository = DriverSourceRepository(store)
    collection = repository.collection()
    if collection is not None:
        return _restore_collection(root, repository, collection)
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
            if self.repository.collection() is not None:
                raise BackendConflict(
                    "multiple source management is active; "
                    "update the selected source in Driver sources"
                )
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

    def sources(self) -> LocalDriverSourceState:
        collection = self.repository.collection()
        try:
            endpoint = self.devices.endpoint
        except (InstrumentBackendError, ValueError, OSError) as error:
            # Preserve the original source repair control when an older worker
            # cannot start. Do not infer an implementation identity from its name.
            return LocalDriverSourceState(
                collection=collection or DriverSourceCollection(),
                errors={"application": str(error)},
                unmanaged_backend=True,
            )
        if collection is not None:
            return LocalDriverSourceState(
                collection=collection,
                errors=endpoint.diagnostics()
                if isinstance(endpoint, SourceBackendEndpoint)
                else {},
            )
        current = self.current()
        if current is None:
            return LocalDriverSourceState(
                collection=DriverSourceCollection(),
                unmanaged_backend=endpoint is not None,
            )
        if endpoint is None:
            raise BackendConflict("selected source is unavailable")
        return LocalDriverSourceState(
            collection=DriverSourceCollection(
                provider_id=endpoint.provider_id,
                sources=(
                    LocalDriverSource(
                        id="legacy",
                        selection=current,
                        legacy_identity=True,
                        driver_ids=tuple(
                            driver.driver_id
                            for driver in endpoint.driver_catalog.drivers
                        ),
                    ),
                ),
            )
        )

    def _capture_source(
        self, request: DriverSourceCommand, existing: LocalDriverSource | None
    ) -> tuple[
        LocalDriverSource, SubprocessInstrumentBackendEndpoint | None, dict[str, str]
    ]:
        if existing is None and request.source_id == "legacy":
            raise BackendConflict(
                "legacy is reserved for retained single-source references"
            )
        if request.builtin is not None:
            if request.source_root is not None:
                raise BackendConflict(
                    "choose either a built-in source or a local project"
                )
            source = Path(f"builtin:{request.builtin}")
        elif request.source_root:
            source = Path(request.source_root).resolve()
        else:
            raise BackendConflict("choose a local driver project or a built-in source")
        if existing is not None and existing.builtin != request.builtin:
            raise BackendConflict(
                "a source cannot change between built-in and local; "
                "add a separate source"
            )
        python = Path(sys.executable).absolute()
        captured = (
            capture_builtin(request.builtin, python)
            if request.builtin is not None
            else capture_driver(source, python)
        )
        bundle = captured.bundle
        digest = self.store.objects.put(bundle.model_dump_json().encode()).digest
        candidate: SubprocessInstrumentBackendEndpoint | None = _start_worker(
            self.root, bundle, captured.factory, python
        )
        selection = DriverSourceSelection(
            request=DriverSourceUpdate(
                operation_id=request.operation_id,
                source_root=str(source),
                actor=request.actor,
            ),
            python=str(python),
            code_revision=bundle.manifest.ref,
            factory=captured.factory,
            artifact_hash=candidate.artifact_hash,
        )
        entry = LocalDriverSource(
            id=request.source_id,
            enabled=existing.enabled if existing else True,
            builtin=request.builtin,
            selection=selection,
            legacy_identity=existing.legacy_identity if existing else False,
            driver_ids=tuple(
                driver.driver_id for driver in candidate.driver_catalog.drivers
            ),
        )
        bundles = {bundle.manifest.ref.content_hash: digest}
        if not entry.enabled:
            candidate.shutdown()
            candidate = None
        return entry, candidate, bundles

    def manage(self, request: DriverSourceCommand) -> DriverSourceCollection:
        with self._lock:
            recorded = self.repository.collection(request.operation_id)
            if recorded is not None:
                if recorded.request != request:
                    raise BackendConflict(
                        "driver source operation has different intent"
                    )
                return recorded
            state = self.sources()
            if state.unmanaged_backend:
                raise BackendConflict(
                    "the application supplies an unmanaged backend; capture its "
                    "project through the existing source control before adding sources"
                )
            current = state.collection
            if current.operation_id != request.expected_previous:
                raise BackendConflict("driver sources changed; reload before retrying")
            entries = {entry.id: entry for entry in current.sources}
            existing = entries.get(request.source_id)
            bundles: dict[str, str] = {}
            candidate: SubprocessInstrumentBackendEndpoint | None = None
            if request.action == "capture":
                entry, candidate, bundles = self._capture_source(request, existing)
                entries[request.source_id] = entry
            else:
                if request.source_root is not None or request.builtin is not None:
                    raise BackendConflict(
                        "enable/disable uses the retained source snapshot"
                    )
                if existing is None:
                    raise BackendConflict("driver source does not exist")
                entries[request.source_id] = existing.model_copy(
                    update={"enabled": request.action == "enable"}
                )
            workers: dict[str, InstrumentBackendEndpoint] = {}
            try:
                # Only enabled catalogs participate in routing. Retired devices
                # retain their exact source identity even if another source later
                # provides the same driver ID.
                owners: dict[str, str] = {}
                for entry in entries.values():
                    if not entry.enabled:
                        continue
                    for driver_id in entry.driver_ids:
                        if driver_id in owners:
                            raise BackendConflict(
                                f"driver ID conflict: {driver_id} belongs to "
                                f"{owners[driver_id]} and {entry.id}"
                            )
                        owners[driver_id] = entry.id
                    endpoint = (
                        candidate
                        if entry.id == request.source_id and candidate is not None
                        else _restore_entry(self.root, self.repository, entry)
                    )
                    workers[entry.id] = endpoint
                collection = DriverSourceCollection(
                    operation_id=request.operation_id,
                    provider_id=current.provider_id,
                    sources=tuple(entries.values()),
                    request=request,
                )
                replacement = SourceBackendEndpoint(
                    collection.provider_id,
                    workers,
                    legacy_ids=frozenset(
                        entry.id for entry in entries.values() if entry.legacy_identity
                    ),
                )
            except BaseException:
                if candidate is not None and candidate not in workers.values():
                    with suppress(Exception):
                        candidate.shutdown()
                for endpoint in workers.values():
                    with suppress(Exception):
                        endpoint.shutdown()
                raise
            self.devices.replace_backend(
                replacement,
                self.instruments,
                actor=request.actor,
                publish_source=lambda connection: self.repository.publish_collection(
                    connection, collection, bundles
                ),
            )
            return collection


def _restore_entry(
    root: Path, repository: DriverSourceRepository, entry: LocalDriverSource
) -> SubprocessInstrumentBackendEndpoint:
    selected = entry.selection
    endpoint = _start_worker(
        root, repository.bundle(selected), selected.factory, Path(selected.python)
    )
    if endpoint.artifact_hash != selected.artifact_hash:
        endpoint.shutdown()
        raise ValueError(
            "restored implementation differs; explicitly refresh this source"
        )
    return endpoint


def _restore_collection(
    root: Path, repository: DriverSourceRepository, collection: DriverSourceCollection
) -> SourceBackendEndpoint:
    workers: dict[str, InstrumentBackendEndpoint] = {}
    errors: dict[str, str] = {}
    for entry in collection.sources:
        if not entry.enabled:
            continue
        try:
            workers[entry.id] = _restore_entry(root, repository, entry)
        except Exception as error:
            errors[entry.id] = str(error)
    try:
        return SourceBackendEndpoint(
            collection.provider_id,
            workers,
            legacy_ids=frozenset(
                entry.id for entry in collection.sources if entry.legacy_identity
            ),
            errors=errors,
        )
    except BaseException:
        for endpoint in workers.values():
            with suppress(Exception):
                endpoint.shutdown()
        raise
