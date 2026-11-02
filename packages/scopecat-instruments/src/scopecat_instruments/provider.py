"""Config-driven provider for real and virtual instrument drivers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol, cast

from pydantic import BaseModel, JsonValue, ValidationError
from scopecat.sdk.instruments import (
    DriverCatalog,
    DriverConnectionSpec,
    DriverFault,
    DriverManagedInstrumentConnection,
    DriverSpec,
    InstrumentBindingSpec,
    InstrumentConnectionContext,
    InstrumentDescription,
    InstrumentDriver,
    InstrumentProviderContext,
    InstrumentProviderDescription,
    SerialInstrumentConnection,
    TcpipSocketInstrumentConnection,
    VirtualInstrumentConnection,
)
from scopecat.sdk.instruments.scpi import ScpiIdentity
from scopecat.sdk.problems import Problem

from scopecat_instruments._support import provider_problem
from scopecat_instruments.package_manifest import (
    KEYSIGHT_E5080B,
    LAKESHORE_372,
    PACKAGE_MANIFEST,
    ROHDE_SCHWARZ_SGS100A,
    VIRTUAL_DC_SOURCE,
    VIRTUAL_RF_SOURCE,
    VIRTUAL_TEMPERATURE_MONITOR,
    VIRTUAL_VNA,
    YOKOGAWA_GS200,
    DriverManagedFactory,
    DriverRegistration,
    PythonSymbol,
)
from scopecat_instruments.transport import SerialByteTransport, TcpScpiTransport

if TYPE_CHECKING:
    from scopecat_instruments.virtual.world import VirtualLabWorld


class _IdentifiableDriver(InstrumentDriver, Protocol):
    def identify(self) -> ScpiIdentity: ...


def _options_schema(
    options_type: type[BaseModel],
) -> dict[str, JsonValue]:
    schema = cast("dict[str, JsonValue]", options_type.model_json_schema())
    return {key: value for key, value in schema.items() if key != "title"}


def _driver_spec(registration: DriverRegistration, /) -> DriverSpec:
    return DriverSpec(
        driver_id=registration.id,
        implementation_version=registration.implementation_version,
        label=registration.label,
        manufacturer=registration.manufacturer,
        model=registration.model,
        connections=(
            DriverConnectionSpec(
                kind=registration.connection_kind,
                options_schema=_options_schema(registration.options_type),
            ),
        ),
    )


_DRIVERS = {registration.id: registration for registration in PACKAGE_MANIFEST.drivers}
SUPPORTED_DRIVER_IDS = frozenset(_DRIVERS)
_DRIVER_CATALOG = DriverCatalog(
    provider_id=PACKAGE_MANIFEST.provider_id,
    drivers=tuple(
        _driver_spec(registration) for registration in PACKAGE_MANIFEST.drivers
    ),
)
_VIRTUAL_WORLD = PythonSymbol(
    "scopecat_instruments.virtual.world",
    "VirtualLabWorld",
)


def compose_driver_registrations(
    *groups: Sequence[DriverRegistration],
) -> tuple[DriverRegistration, ...]:
    """Compose driver registrations while preserving order and unique ids."""

    registrations = tuple(registration for group in groups for registration in group)
    ids = [registration.id for registration in registrations]
    if len(ids) != len(set(ids)):
        raise ValueError("instrument driver registrations must have unique ids")
    return registrations


class ConfiguredInstrumentProvider:
    """Instantiate exactly the drivers declared by configured bindings.

    Transport registrations explicitly choose identity probing or
    connection-only probing before a driver is returned. Driver-managed
    factories own construction, probing, and cleanup of their physical SDKs.
    Virtual driver instances share the provider's world, so device state survives
    connection retirement and recreation.
    """

    provider_id = PACKAGE_MANIFEST.provider_id
    driver_catalog = _DRIVER_CATALOG

    def __init__(
        self,
        *,
        world: VirtualLabWorld | None = None,
        seed: int = 0,
        provider_id: str | None = None,
        registrations: Sequence[DriverRegistration] | None = None,
    ) -> None:
        selected = compose_driver_registrations(
            PACKAGE_MANIFEST.drivers if registrations is None else registrations
        )
        self._drivers = {registration.id: registration for registration in selected}
        self.provider_id = (
            PACKAGE_MANIFEST.provider_id if provider_id is None else provider_id
        )
        self.driver_catalog = DriverCatalog(
            provider_id=self.provider_id,
            drivers=tuple(_driver_spec(registration) for registration in selected),
        )
        self.world = world
        if self.world is None and any(
            registration.connection_kind == "virtual" for registration in selected
        ):
            self.world = _create_virtual_world(seed=seed)

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        problems: list[Problem] = []
        descriptions: list[InstrumentDescription] = []
        for index, binding in enumerate(context.bindings):
            try:
                descriptions.append(
                    _describe_driver(binding, self.world, self._drivers)
                )
            except Exception as error:
                problems.append(_configuration_problem(binding, index, error))
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=tuple(descriptions),
            problems=tuple(problems),
        )

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> InstrumentDriver:
        binding = context.binding
        try:
            return _connect_driver(binding, self.world, self._drivers)
        except Exception as error:
            if isinstance(error, DriverFault):
                raise
            raise DriverFault(_connection_problem(binding, error)) from error


class _DescriptionOnlyTransport:
    def write(self, command: str) -> None:
        raise RuntimeError(f"description-only transport cannot write {command!r}")

    def query(self, command: str) -> str:
        raise RuntimeError(f"description-only transport cannot query {command!r}")

    def send(self, request: bytes, /) -> None:
        raise RuntimeError(f"description-only transport cannot send {request!r}")

    def exchange(self, request: bytes, response_size: int, /) -> bytes:
        del response_size
        raise RuntimeError(f"description-only transport cannot exchange {request!r}")

    def close(self) -> None:
        pass


def _driver_registration(
    driver_id: str,
    registrations: dict[str, DriverRegistration],
) -> DriverRegistration:
    registration = registrations.get(driver_id)
    if registration is None:
        raise ValueError(f"unsupported instrument driver_id {driver_id!r}")
    return registration


def _describe_driver(
    binding: InstrumentBindingSpec,
    world: VirtualLabWorld | None,
    registrations: dict[str, DriverRegistration],
) -> InstrumentDescription:
    registration = _driver_registration(binding.driver_id, registrations)
    if registration.connection_kind == "driver_managed":
        connection = _require_driver_managed_connection(registration, binding)
        return _describe_driver_managed(
            registration,
            binding.id,
            connection.options,
        )
    if registration.connection_kind == "virtual":
        if world is None:
            raise RuntimeError("virtual driver registration requires a virtual world")
        return _create_virtual_driver(registration, binding, world).describe()
    connection = _require_real_connection(registration, binding)
    return _create_real_driver(
        registration,
        binding.id,
        _DescriptionOnlyTransport(),
        connection.options,
    ).describe()


def _connect_driver(
    binding: InstrumentBindingSpec,
    world: VirtualLabWorld | None,
    registrations: dict[str, DriverRegistration],
) -> InstrumentDriver:
    registration = _driver_registration(binding.driver_id, registrations)
    if registration.connection_kind == "driver_managed":
        connection = _require_driver_managed_connection(registration, binding)
        return _connect_driver_managed(
            registration,
            binding.id,
            connection.options,
        )
    if registration.connection_kind == "virtual":
        if world is None:
            raise RuntimeError("virtual driver registration requires a virtual world")
        return _create_virtual_driver(registration, binding, world)
    connection = _require_real_connection(registration, binding)
    transport = (
        TcpScpiTransport(
            connection.host,
            connection.port,
            timeout_seconds=connection.timeout_seconds,
        )
        if isinstance(connection, TcpipSocketInstrumentConnection)
        else SerialByteTransport(
            connection.port,
            baud_rate=connection.baud_rate,
            timeout_seconds=connection.timeout_seconds,
            write_timeout_seconds=connection.write_timeout_seconds,
            data_bits=connection.data_bits,
            parity=connection.parity,
            stop_bits=connection.stop_bits,
            xonxoff=connection.xonxoff,
            rtscts=connection.rtscts,
            dsrdtr=connection.dsrdtr,
        )
    )
    driver = _create_real_driver(
        registration,
        binding.id,
        transport,
        connection.options,
    )
    try:
        if registration.probe == "identify":
            cast("_IdentifiableDriver", driver).identify()
        else:
            transport.connect()
    except Exception:
        with suppress(Exception):
            driver.disconnect()
        raise
    return driver


def _create_real_driver(
    registration: DriverRegistration,
    instrument_id: str,
    transport: object,
    raw_options: dict[str, JsonValue],
    /,
) -> InstrumentDriver:
    constructor = cast(
        "Callable[..., InstrumentDriver]",
        registration.implementation.resolve(),
    )
    options = registration.options_type.model_validate(raw_options)
    return constructor(
        instrument_id,
        transport,
        **options.model_dump(),
    )


def _describe_driver_managed(
    registration: DriverRegistration,
    instrument_id: str,
    raw_options: dict[str, JsonValue],
    /,
) -> InstrumentDescription:
    factory = cast(
        "DriverManagedFactory",
        registration.implementation.resolve(),
    )
    options = registration.options_type.model_validate(raw_options)
    return factory.describe(
        instrument_id,
        **cast("dict[str, object]", options.model_dump()),
    )


def _connect_driver_managed(
    registration: DriverRegistration,
    instrument_id: str,
    raw_options: dict[str, JsonValue],
    /,
) -> InstrumentDriver:
    factory = cast(
        "DriverManagedFactory",
        registration.implementation.resolve(),
    )
    options = registration.options_type.model_validate(raw_options)
    return factory.connect(
        instrument_id,
        **cast("dict[str, object]", options.model_dump()),
    )


def _create_virtual_driver(
    registration: DriverRegistration,
    binding: InstrumentBindingSpec,
    world: VirtualLabWorld,
) -> InstrumentDriver:
    connection = binding.connection
    if not isinstance(connection, VirtualInstrumentConnection):
        raise ValueError(f"{registration.id} requires a virtual connection")
    options = registration.options_type.model_validate(connection.options)
    factory = cast(
        "Callable[..., InstrumentDriver]",
        registration.implementation.resolve(),
    )
    return factory(binding.id, world, **options.model_dump())


def _create_virtual_world(*, seed: int) -> VirtualLabWorld:
    constructor = cast(
        "Callable[..., VirtualLabWorld]",
        _VIRTUAL_WORLD.resolve(),
    )
    return constructor(seed=seed)


def _require_real_connection(
    registration: DriverRegistration,
    binding: InstrumentBindingSpec,
) -> TcpipSocketInstrumentConnection | SerialInstrumentConnection:
    connection = binding.connection
    if registration.connection_kind == "tcpip_socket" and isinstance(
        connection,
        TcpipSocketInstrumentConnection,
    ):
        return connection
    if registration.connection_kind == "serial" and isinstance(
        connection,
        SerialInstrumentConnection,
    ):
        return connection
    raise ValueError(
        f"{registration.id} supports only {registration.connection_kind} connections"
    )


def _require_driver_managed_connection(
    registration: DriverRegistration,
    binding: InstrumentBindingSpec,
) -> DriverManagedInstrumentConnection:
    connection = binding.connection
    if isinstance(connection, DriverManagedInstrumentConnection):
        return connection
    raise ValueError(f"{registration.id} supports only driver_managed connections")


def _configuration_problem(
    binding: InstrumentBindingSpec,
    index: int,
    error: Exception,
) -> Problem:
    message = f"invalid driver configuration for {binding.id} ({type(error).__name__})"
    if isinstance(error, ValidationError):
        # Keep actionable model diagnostics without serializing supplied options,
        # exception context, or validation URLs into a user-facing error.
        messages = (
            ": ".join(
                part
                for part in (
                    ".".join(str(part) for part in item["loc"]),
                    item["msg"],
                )
                if part
            )
            for item in error.errors(
                include_input=False, include_context=False, include_url=False
            )
        )
        message += ": " + "; ".join(messages)
    return provider_problem(
        "instrument_driver_configuration_invalid",
        message,
        "context",
        "bindings",
        index,
        details={
            "instrument_id": binding.id,
            "driver_id": binding.driver_id,
            "exception_type": (f"{type(error).__module__}.{type(error).__qualname__}"),
        },
    )


def _connection_problem(
    binding: InstrumentBindingSpec,
    error: Exception,
) -> Problem:
    return provider_problem(
        "instrument_connection_failed",
        f"could not open and identify {binding.id} ({type(error).__name__})",
        "context",
        "binding",
        "connection",
        details={
            "instrument_id": binding.id,
            "driver_id": binding.driver_id,
            "exception_type": (f"{type(error).__module__}.{type(error).__qualname__}"),
        },
    )


__all__ = [
    "KEYSIGHT_E5080B",
    "LAKESHORE_372",
    "ROHDE_SCHWARZ_SGS100A",
    "SUPPORTED_DRIVER_IDS",
    "VIRTUAL_DC_SOURCE",
    "VIRTUAL_RF_SOURCE",
    "VIRTUAL_TEMPERATURE_MONITOR",
    "VIRTUAL_VNA",
    "YOKOGAWA_GS200",
    "ConfiguredInstrumentProvider",
    "compose_driver_registrations",
]
