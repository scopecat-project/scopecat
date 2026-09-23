from __future__ import annotations

import socket
import subprocess
import sys
from threading import Thread
from typing import ClassVar, cast, final

import pytest
from pydantic import BaseModel, ConfigDict
from scopecat.kernel.entity import EntityRef
from scopecat.records.config import (
    ConfigProfileSnapshot,
    DriverManagedInstrumentConnection,
    InstrumentBindingSpec,
    InstrumentConnection,
    InstrumentRegistry,
    InstrumentSpec,
    SystemSpec,
    TcpipSocketInstrumentConnection,
    Topology,
    VirtualInstrumentConnection,
    instrument_bindings,
)
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.sdk.instruments import (
    DriverFault,
    InstrumentConnectionContext,
    InstrumentDescription,
    InstrumentProviderContext,
    InstrumentProviderDescription,
)

from scopecat_instruments.drivers import YokogawaGS200
from scopecat_instruments.package_manifest import (
    YOKOGAWA_GS200_DRIVER,
    DriverRegistration,
    PythonSymbol,
)
from scopecat_instruments.provider import (
    KEYSIGHT_E5080B,
    LAKESHORE_372,
    ROHDE_SCHWARZ_SGS100A,
    SUPPORTED_DRIVER_IDS,
    VIRTUAL_DC_SOURCE,
    VIRTUAL_RF_SOURCE,
    VIRTUAL_TEMPERATURE_MONITOR,
    VIRTUAL_VNA,
    YOKOGAWA_GS200,
    ConfiguredInstrumentProvider,
    compose_driver_registrations,
)
from scopecat_instruments.virtual import VirtualDcSource
from scopecat_instruments.virtual.world import VirtualLabWorld


class _ConfiguredVirtualOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    channel_count: int


@final
class _ConfiguredDriverManagedFactory:
    described: ClassVar[list[tuple[str, int]]] = []
    connected: ClassVar[list[tuple[str, int]]] = []

    @staticmethod
    def describe(
        instrument_id: str,
        *,
        channel_count: int,
    ) -> InstrumentDescription:
        _ConfiguredDriverManagedFactory.described.append((instrument_id, channel_count))
        return VirtualDcSource(instrument_id, VirtualLabWorld(seed=0)).describe()

    @staticmethod
    def connect(
        instrument_id: str,
        *,
        channel_count: int,
    ) -> VirtualDcSource:
        _ConfiguredDriverManagedFactory.connected.append((instrument_id, channel_count))
        return VirtualDcSource(instrument_id, VirtualLabWorld(seed=channel_count))


def test_package_manifest_does_not_import_driver_implementations() -> None:
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import scopecat_instruments.package_manifest; "
                "forbidden = ("
                "'scopecat_instruments.provider', "
                "'scopecat_instruments.drivers.gs200', "
                "'scopecat_instruments.drivers.sgs100a', "
                "'scopecat_instruments.drivers.lakeshore372', "
                "'scopecat_instruments.drivers.e5080b', "
                "'scopecat_instruments.virtual.drivers', "
                "'scopecat_instruments.transport'); "
                "loaded = [name for name in forbidden if name in sys.modules]; "
                "assert not loaded, loaded"
            ),
        ],
        check=True,
    )


def test_registered_driver_ids_match_the_lightweight_catalog() -> None:
    assert {
        YOKOGAWA_GS200,
        ROHDE_SCHWARZ_SGS100A,
        LAKESHORE_372,
        KEYSIGHT_E5080B,
        VIRTUAL_RF_SOURCE,
        VIRTUAL_DC_SOURCE,
        VIRTUAL_TEMPERATURE_MONITOR,
        VIRTUAL_VNA,
    } == SUPPORTED_DRIVER_IDS


def test_driver_catalog_exposes_connection_option_schemas() -> None:
    catalog = ConfiguredInstrumentProvider().driver_catalog

    assert catalog.provider_id == ConfiguredInstrumentProvider.provider_id
    assert {driver.driver_id for driver in catalog.drivers} == SUPPORTED_DRIVER_IDS
    gs200 = catalog.get(YOKOGAWA_GS200)
    assert gs200 is not None
    assert (gs200.label, gs200.manufacturer, gs200.model) == (
        "Yokogawa GS200",
        "Yokogawa",
        "GS200",
    )
    [connection] = gs200.connections
    assert connection.kind == "tcpip_socket"
    assert connection.options_schema["properties"] == {
        "monitor_option": {
            "default": False,
            "title": "Monitor Option",
            "type": "boolean",
        },
        "remote_sense": {
            "default": False,
            "title": "Remote Sense",
            "type": "boolean",
        },
        "guard_enabled": {
            "default": False,
            "title": "Guard Enabled",
            "type": "boolean",
        },
    }
    virtual = catalog.get(VIRTUAL_DC_SOURCE)
    assert virtual is not None
    assert virtual.connections[0].kind == "virtual"
    assert virtual.connections[0].options_schema["properties"] == {}


def test_provider_composes_an_external_registration_set() -> None:
    registrations = compose_driver_registrations((YOKOGAWA_GS200_DRIVER,))
    provider = ConfiguredInstrumentProvider(
        provider_id="test.lab.instruments",
        registrations=registrations,
    )

    assert provider.provider_id == "test.lab.instruments"
    assert provider.world is None
    assert [item.driver_id for item in provider.driver_catalog.drivers] == [
        YOKOGAWA_GS200
    ]
    with pytest.raises(ValueError, match="unique ids"):
        compose_driver_registrations(registrations, registrations)


def test_provider_passes_validated_options_to_virtual_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[int] = []

    def factory(
        instrument_id: str,
        world: VirtualLabWorld,
        *,
        channel_count: int,
    ) -> VirtualDcSource:
        received.append(channel_count)
        return VirtualDcSource(instrument_id, world)

    registration = DriverRegistration(
        id="test.configured_virtual",
        implementation_version="v1",
        implementation=PythonSymbol("unused", "factory"),
        connection_kind="virtual",
        options_type=_ConfiguredVirtualOptions,
        label="Configured virtual driver",
    )
    original_resolve = PythonSymbol.resolve

    def resolve(symbol: PythonSymbol) -> object:
        return factory if symbol.module == "unused" else original_resolve(symbol)

    monkeypatch.setattr(PythonSymbol, "resolve", resolve)
    provider = ConfiguredInstrumentProvider(registrations=(registration,))
    config = _config(
        InstrumentSpec(
            id="configured",
            exclusivity_key="configured",
            driver_id=registration.id,
            connection=VirtualInstrumentConnection(options={"channel_count": 4}),
            run_start="preserve",
            success_action="release",
            failure_action="abort_and_release",
        )
    )

    driver = provider.connect(
        InstrumentConnectionContext(binding=instrument_bindings(config)[0])
    )

    assert isinstance(driver, VirtualDcSource)
    assert received == [4]


def test_provider_separates_driver_managed_description_and_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ConfiguredDriverManagedFactory.described.clear()
    _ConfiguredDriverManagedFactory.connected.clear()
    registration = DriverRegistration(
        id="test.driver_managed",
        implementation_version="v1",
        implementation=PythonSymbol("unused", "managed_factory"),
        connection_kind="driver_managed",
        options_type=_ConfiguredVirtualOptions,
        label="Driver-managed test device",
    )
    original_resolve = PythonSymbol.resolve

    def resolve(symbol: PythonSymbol) -> object:
        if symbol.module == "unused":
            return _ConfiguredDriverManagedFactory
        return original_resolve(symbol)

    monkeypatch.setattr(PythonSymbol, "resolve", resolve)
    provider = ConfiguredInstrumentProvider(registrations=(registration,))
    binding = InstrumentBindingSpec(
        id="managed",
        driver_id=registration.id,
        connection=DriverManagedInstrumentConnection(options={"channel_count": 4}),
    )

    description = provider.describe(InstrumentProviderContext(bindings=(binding,)))

    assert not description.problems
    assert [item.instrument_id for item in description.instruments] == ["managed"]
    assert _ConfiguredDriverManagedFactory.described == [("managed", 4)]
    assert _ConfiguredDriverManagedFactory.connected == []
    [connection] = provider.driver_catalog.drivers[0].connections
    assert connection.kind == "driver_managed"
    assert connection.options_schema["required"] == ["channel_count"]

    driver = provider.connect(InstrumentConnectionContext(binding=binding))

    assert _ConfiguredDriverManagedFactory.connected == [("managed", 4)]
    driver.disconnect()


def test_provider_rejects_non_managed_binding_for_driver_managed_factory() -> None:
    registration = DriverRegistration(
        id="test.driver_managed",
        implementation_version="v1",
        implementation=PythonSymbol("unused", "managed_factory"),
        connection_kind="driver_managed",
        options_type=_ConfiguredVirtualOptions,
        label="Driver-managed test device",
    )
    provider = ConfiguredInstrumentProvider(registrations=(registration,))
    binding = InstrumentBindingSpec(
        id="managed",
        driver_id=registration.id,
        connection=VirtualInstrumentConnection(options={"channel_count": 4}),
    )

    description = provider.describe(InstrumentProviderContext(bindings=(binding,)))

    assert not description.instruments
    assert description.problems[0].code == "instrument_driver_configuration_invalid"


class _IdnServer:
    def __init__(
        self,
        response: str,
        *,
        query_responses: dict[str, str] | None = None,
    ) -> None:
        self.query_responses = {
            "*IDN?": response,
            **({} if query_responses is None else query_responses),
        }
        self.commands: list[str] = []
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        address = cast("tuple[str, int]", self._server.getsockname())
        self.port: int = address[1]
        self._thread = Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        connection, _address = cast(
            "tuple[socket.socket, object]",
            self._server.accept(),
        )
        with connection:
            buffer = bytearray()
            while True:
                chunk = connection.recv(1024)
                if not chunk:
                    break
                buffer.extend(chunk)
                while b"\n" in buffer:
                    line, _, remainder = buffer.partition(b"\n")
                    buffer = bytearray(remainder)
                    command = line.decode("ascii")
                    self.commands.append(command)
                    response = self.query_responses.get(command)
                    if response is not None:
                        connection.sendall(response.encode("ascii") + b"\n")
        self._server.close()


def _config(*specs: InstrumentSpec) -> ConfigProfileSnapshot:
    return ConfigProfileSnapshot(
        id="test",
        system=SystemSpec(
            id="test-system",
            topology=Topology(entities=[EntityRef(id="lab", kind="lab")]),
            instrument_registry=InstrumentRegistry(instruments=list(specs)),
            domain_target=None,
            parameter_catalog=ParameterCatalog(id="empty", definitions=[]),
        ),
        parameter_snapshot=ParameterSnapshot(id="empty", values=[]),
    )


def _describe_binding(
    driver_id: str,
    connection: InstrumentConnection,
) -> InstrumentProviderDescription:
    return ConfiguredInstrumentProvider().describe(
        InstrumentProviderContext(
            bindings=(
                InstrumentBindingSpec(
                    id="device",
                    driver_id=driver_id,
                    connection=connection,
                ),
            )
        )
    )


def test_provider_probes_real_device_before_returning_driver() -> None:
    server = _IdnServer("Rohde&Schwarz,SGS100A,1419.5505k02/100001,5.00")
    config = _config(
        InstrumentSpec(
            id="lo",
            exclusivity_key="lo",
            driver_id=ROHDE_SCHWARZ_SGS100A,
            connection=TcpipSocketInstrumentConnection(
                host="127.0.0.1",
                port=server.port,
            ),
            run_start="preserve",
            success_action="release",
            failure_action="abort_and_release",
        )
    )

    driver = ConfiguredInstrumentProvider().connect(
        InstrumentConnectionContext(binding=instrument_bindings(config)[0])
    )

    assert driver.instrument_id == "lo"
    assert server.commands == ["*IDN?"]
    driver.disconnect()


def test_provider_rejects_wrong_device_identity() -> None:
    server = _IdnServer("OTHER,NOT_AN_SGS,123,1")
    config = _config(
        InstrumentSpec(
            id="lo",
            exclusivity_key="lo",
            driver_id=ROHDE_SCHWARZ_SGS100A,
            connection=TcpipSocketInstrumentConnection(
                host="127.0.0.1",
                port=server.port,
            ),
            run_start="preserve",
            success_action="release",
            failure_action="abort_and_release",
        )
    )

    with pytest.raises(DriverFault) as caught:
        ConfiguredInstrumentProvider().connect(
            InstrumentConnectionContext(binding=instrument_bindings(config)[0])
        )

    assert caught.value.problem.code == "instrument_connection_failed"
    assert server.commands == ["*IDN?"]


def test_provider_connects_gs200_with_verified_monitor_profile() -> None:
    server = _IdnServer(
        "YOKOGAWA,GS200,91X000001,2.03",
        query_responses={
            "*OPT?": "/MON",
            ":SENS:REM?": "1",
            ":SENS:GUAR?": "0",
        },
    )
    config = _config(
        InstrumentSpec(
            id="bias",
            exclusivity_key="bias",
            driver_id=YOKOGAWA_GS200,
            connection=TcpipSocketInstrumentConnection(
                host="127.0.0.1",
                port=server.port,
                options={
                    "monitor_option": True,
                    "remote_sense": True,
                    "guard_enabled": False,
                },
            ),
            run_start="preserve",
            success_action="release",
            failure_action="abort_and_release",
        )
    )

    driver = ConfiguredInstrumentProvider().connect(
        InstrumentConnectionContext(binding=instrument_bindings(config)[0])
    )

    assert isinstance(driver, YokogawaGS200)
    assert driver.read_monitor_option().value is True
    assert driver.read_remote_sense().value is True
    assert driver.read_guard_enabled().value is False
    assert server.commands == [
        "*IDN?",
        "*OPT?",
        ":SENS:REM?",
        ":SENS:GUAR?",
    ]
    driver.disconnect()


def test_provider_rejects_gs200_without_requested_monitor_option() -> None:
    server = _IdnServer(
        "YOKOGAWA,GS200,91X000001,2.03",
        query_responses={"*OPT?": "0"},
    )
    config = _config(
        InstrumentSpec(
            id="bias",
            exclusivity_key="bias",
            driver_id=YOKOGAWA_GS200,
            connection=TcpipSocketInstrumentConnection(
                host="127.0.0.1",
                port=server.port,
                options={"monitor_option": True},
            ),
            run_start="preserve",
            success_action="release",
            failure_action="abort_and_release",
        )
    )

    with pytest.raises(DriverFault) as caught:
        ConfiguredInstrumentProvider().connect(
            InstrumentConnectionContext(binding=instrument_bindings(config)[0])
        )

    assert caught.value.problem.code == "instrument_connection_failed"
    assert server.commands == ["*IDN?", "*OPT?"]


@pytest.mark.parametrize(
    ("driver_id", "connection"),
    [
        (
            YOKOGAWA_GS200,
            TcpipSocketInstrumentConnection(
                host="127.0.0.1",
                port=5025,
                options={"monitor_option": "yes"},
            ),
        ),
        (
            KEYSIGHT_E5080B,
            TcpipSocketInstrumentConnection(
                host="127.0.0.1",
                port=5025,
                options={"channel": 0},
            ),
        ),
        (
            VIRTUAL_DC_SOURCE,
            VirtualInstrumentConnection(options={"seed": 7}),
        ),
    ],
)
def test_provider_validates_registered_options(
    driver_id: str,
    connection: InstrumentConnection,
) -> None:
    description = _describe_binding(driver_id, connection)
    assert description.instruments == ()
    assert description.problems[0].code == "instrument_driver_configuration_invalid"


@pytest.mark.parametrize(
    ("driver_id", "connection"),
    [
        (
            VIRTUAL_DC_SOURCE,
            TcpipSocketInstrumentConnection(host="127.0.0.1", port=5025),
        ),
        (
            ROHDE_SCHWARZ_SGS100A,
            VirtualInstrumentConnection(),
        ),
    ],
)
def test_provider_uses_the_registered_connection_kind(
    driver_id: str,
    connection: InstrumentConnection,
) -> None:
    description = _describe_binding(driver_id, connection)
    assert description.instruments == ()
    assert description.problems[0].code == "instrument_driver_configuration_invalid"


def test_virtual_state_survives_driver_recreation() -> None:
    config = _config(
        InstrumentSpec(
            id="flux",
            exclusivity_key="flux",
            driver_id=VIRTUAL_DC_SOURCE,
            connection=VirtualInstrumentConnection(),
            run_start="preserve",
            success_action="release",
            failure_action="abort_and_release",
        ),
        InstrumentSpec(
            id="unused",
            exclusivity_key="unused",
            driver_id=VIRTUAL_DC_SOURCE,
            connection=VirtualInstrumentConnection(),
            run_start="preserve",
            success_action="release",
            failure_action="abort_and_release",
        ),
    )
    provider = ConfiguredInstrumentProvider(seed=17)
    context = InstrumentConnectionContext(
        binding=instrument_bindings(config)[0],
    )

    description = provider.describe(
        InstrumentProviderContext(bindings=instrument_bindings(config))
    )
    first = provider.connect(context)
    assert isinstance(first, VirtualDcSource)
    first.set_voltage_level(0.2)
    first.set_output(True)
    first.disconnect()
    second = provider.connect(context)

    assert [item.instrument_id for item in description.instruments] == [
        "flux",
        "unused",
    ]
    assert isinstance(second, VirtualDcSource)
    assert second.read_output_enabled() is True
    assert provider.world is not None
    assert provider.world.flux_bias() == 0.8


def test_provider_connects_exact_requested_instrument() -> None:
    config = _config(
        InstrumentSpec(
            id="a",
            exclusivity_key="a",
            driver_id=VIRTUAL_DC_SOURCE,
            connection=VirtualInstrumentConnection(),
            run_start="preserve",
            success_action="release",
            failure_action="abort_and_release",
        ),
        InstrumentSpec(
            id="b",
            exclusivity_key="b",
            driver_id=VIRTUAL_DC_SOURCE,
            connection=VirtualInstrumentConnection(),
            run_start="preserve",
            success_action="release",
            failure_action="abort_and_release",
        ),
    )
    provider = ConfiguredInstrumentProvider()
    bindings = instrument_bindings(config)
    description = provider.describe(InstrumentProviderContext(bindings=bindings))
    driver = provider.connect(InstrumentConnectionContext(binding=bindings[1]))

    assert [item.instrument_id for item in description.instruments] == ["a", "b"]
    assert driver.instrument_id == "b"
    driver.disconnect()
