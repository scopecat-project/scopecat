"""Route explicit sources while preserving device implementation identities."""

from threading import RLock

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.config import InstrumentBindingSpec
from scopecat.records.device import DriverImplementationRef
from scopecat.records.instrument import InstrumentStateReadback
from scopecat.sdk.instruments.backend import (
    BackendAcquisitionPlan,
    BackendApplyRequest,
    BackendCollectRequest,
    BackendInvokeRequest,
    BackendReadRequest,
)
from scopecat.sdk.instruments.catalog import DriverCatalog
from scopecat.sdk.instruments.commands import (
    AcquisitionPreparationReceipt,
    ApplyReceipt,
    CollectReceipt,
    InvokeReceipt,
)
from scopecat.sdk.instruments.contracts import InstrumentDescription
from scopecat.sdk.instruments.provider import InstrumentProviderDescription
from scopecat.sdk.payloads import PayloadCodecCatalog, PayloadCodecDescription

from .backend import (
    ConnectedInstrument,
    InstrumentBackendEndpoint,
    InstrumentBackendUnavailable,
    InstrumentHandle,
    InstrumentHandleInvalid,
)


def driver_reference(
    endpoint: InstrumentBackendEndpoint, driver_id: str
) -> DriverImplementationRef:
    if isinstance(endpoint, SourceBackendEndpoint):
        return endpoint.driver_reference(driver_id)
    return DriverImplementationRef(
        provider_id=endpoint.provider_id,
        driver_id=driver_id,
        artifact_hash=endpoint.artifact_hash,
    )


class SourceBackendEndpoint:
    """Own a complete worker generation; never share workers across replacements.

    A source failing restoration is excluded and diagnosed. Healthy sources remain
    usable. Handles always route to the worker that created them, never by driver
    name or to a newly enabled implementation.
    """

    def __init__(
        self,
        provider_id: str,
        sources: dict[str, InstrumentBackendEndpoint],
        *,
        legacy_ids: frozenset[str] | None = None,
        errors: dict[str, str] | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.sources = sources
        self.errors = errors or {}
        self._legacy_ids = legacy_ids or frozenset()
        self._closed = False
        self._lock = RLock()
        self._handles: dict[InstrumentHandle, InstrumentBackendEndpoint] = {}
        self._drivers: dict[str, str] = {}
        codecs: dict[str, PayloadCodecDescription] = {}
        for source_id, endpoint in sources.items():
            for driver in endpoint.driver_catalog.drivers:
                if driver.driver_id in self._drivers:
                    raise ValueError(
                        f"driver ID conflict: {driver.driver_id} in "
                        f"{self._drivers[driver.driver_id]} and {source_id}"
                    )
                self._drivers[driver.driver_id] = source_id
            for codec in endpoint.payload_catalog.codecs:
                if codec.schema_id in codecs and codecs[codec.schema_id] != codec:
                    raise ValueError(
                        f"payload codec conflict: {codec.schema_id} "
                        f"in source {source_id}"
                    )
                codecs[codec.schema_id] = codec
        self.driver_catalog = DriverCatalog(
            provider_id=provider_id,
            drivers=tuple(
                driver
                for endpoint in sources.values()
                for driver in endpoint.driver_catalog.drivers
            ),
        )
        self.payload_catalog = PayloadCodecCatalog(codecs=tuple(codecs.values()))
        self.artifact_hash = sha256_json_hash(
            {key: endpoint.artifact_hash for key, endpoint in sources.items()}
        )

    @property
    def healthy(self) -> bool:
        # Worker health is scoped to a source, not a reason to stop unrelated runs.
        return not self._closed

    def diagnostics(self) -> dict[str, str]:
        return {
            **self.errors,
            **{
                key: "driver worker is unavailable; release sessions and refresh source"
                for key, endpoint in self.sources.items()
                if not endpoint.healthy
            },
        }

    def driver_reference(self, driver_id: str) -> DriverImplementationRef:
        source_id = self._drivers[driver_id]
        endpoint = self.sources[source_id]
        digest = (
            endpoint.artifact_hash
            if source_id in self._legacy_ids
            else sha256_json_hash(
                {"source_id": source_id, "implementation": endpoint.artifact_hash}
            )
        )
        return DriverImplementationRef(
            provider_id=endpoint.provider_id, driver_id=driver_id, artifact_hash=digest
        )

    def _source(self, driver_id: str) -> InstrumentBackendEndpoint:
        if self._closed:
            raise InstrumentBackendUnavailable("driver sources are shut down")
        source_id = self._drivers.get(driver_id)
        if source_id is None:
            raise InstrumentBackendUnavailable(
                f"driver is disabled or unavailable: {driver_id}; "
                "inspect driver sources"
            )
        endpoint = self.sources[source_id]
        if not endpoint.healthy:
            raise InstrumentBackendUnavailable(
                f"driver source {source_id} is unavailable"
            )
        return endpoint

    def describe(
        self, bindings: tuple[InstrumentBindingSpec, ...]
    ) -> InstrumentProviderDescription:
        grouped: dict[str, list[InstrumentBindingSpec]] = {}
        for binding in bindings:
            self._source(binding.driver_id)
            grouped.setdefault(self._drivers[binding.driver_id], []).append(binding)
        descriptions = [
            self.sources[key].describe(tuple(group)) for key, group in grouped.items()
        ]
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=tuple(
                item for result in descriptions for item in result.instruments
            ),
            problems=tuple(item for result in descriptions for item in result.problems),
        )

    def connect(
        self, *, binding: InstrumentBindingSpec, expected: InstrumentDescription
    ) -> ConnectedInstrument:
        endpoint = self._source(binding.driver_id)
        connected = endpoint.connect(binding=binding, expected=expected)
        with self._lock:
            if self._closed:
                endpoint.disconnect(connected.handle)
                raise InstrumentBackendUnavailable("driver sources are shut down")
            self._handles[connected.handle] = endpoint
        return connected

    def _owner(self, handle: InstrumentHandle) -> InstrumentBackendEndpoint:
        with self._lock:
            if self._closed or handle not in self._handles:
                raise InstrumentHandleInvalid(
                    "handle is not owned by this source generation"
                )
            return self._handles[handle]

    def read_state(
        self, handle: InstrumentHandle, request: BackendReadRequest
    ) -> InstrumentStateReadback:
        return self._owner(handle).read_state(handle, request)

    def apply_state(
        self, handle: InstrumentHandle, request: BackendApplyRequest
    ) -> ApplyReceipt:
        return self._owner(handle).apply_state(handle, request)

    def invoke(
        self, handle: InstrumentHandle, request: BackendInvokeRequest
    ) -> InvokeReceipt:
        return self._owner(handle).invoke(handle, request)

    def collect(
        self, handle: InstrumentHandle, request: BackendCollectRequest
    ) -> CollectReceipt:
        return self._owner(handle).collect(handle, request)

    def prepare_acquisitions(
        self, handle: InstrumentHandle, plan: BackendAcquisitionPlan
    ) -> AcquisitionPreparationReceipt:
        return self._owner(handle).prepare_acquisitions(handle, plan)

    def abort(self, handle: InstrumentHandle) -> None:
        self._owner(handle).abort(handle)

    def disconnect(self, handle: InstrumentHandle) -> None:
        self._owner(handle).disconnect(handle)
        with self._lock:
            self._handles.pop(handle, None)

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self._handles.clear()
        failures: list[Exception] = []
        for endpoint in self.sources.values():
            try:
                endpoint.shutdown()
            except Exception as error:
                failures.append(error)
        if failures:
            raise ExceptionGroup("driver source shutdown failed", failures)
