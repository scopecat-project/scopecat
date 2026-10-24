# Scopecat instrument provider

`scopecat-instruments` supplies Scopecat's config-driven provider for a focused
set of real and virtual laboratory instruments. Device access is owned by the
project daemon so notebooks, the GUI, and experiment runs share the same
exclusive claims, interface validation, operation receipts, and audit trail.

The configured connection kinds are:

- `virtual`, for a deterministic simulated device selected by `driver_id`;
- `driver_managed`, for a physical vendor SDK or composite connection whose
  lazy factory owns construction, probing, and cleanup;
- `tcpip_socket`, for a configured host, port, and timeout;
- `serial`, for a named port with explicit baud rate, timeout, framing, and flow
  control.

## Using the package

The [instrument-control guide](../../docs/how-to/control-instruments.md) is the canonical
user guide for registered-device sessions, symbolic experiments, routing,
roles, shared physical state, and multi-entity clients. The
[reference lab](../../testing/reference_lab/README.md) retains integration fixtures
for coupled virtual devices and bare AWG/digitizer workflows.

Generated clients preserve concrete Python names and value types across both
time models. Live `apply`, operations, and acquisitions act within a
daemon-owned session; symbolic `ensure`, operations, and acquisitions record
ordered experiment effects. Runs and interactive sessions compete for the same
instrument claims.

Successful acquisitions return named typed readback. A rejected collection
raises `InstrumentCollectFailure` with its receipt and outcome certainty.
`MeasurementUnavailable` represents a successful acquisition whose individual
value was unavailable. Raw snapshots and lower-level receipt channels remain
available for diagnostics.

A live family call may select a repeated interface on one physical device with
`component_path=("channels", "2")`. Generated members remain relative while the
client maps state, operations, acquisitions, and concrete member constraints to
that exact mount. Whole-device snapshots remain diagnostic surfaces, and
configured defaults are intentionally unavailable through a mounted client.

## Typed client source generation

Decorated Python interface declarations are the shared source for the wire
contract and typed Python surfaces. `PACKAGE_MANIFEST` is the authoritative list
of interfaces, composites, public types, provider identity, and lazy driver
registrations. The generator and provider both derive their catalogs from it.

Run the generator from the repository root after changing a supported
declaration, and use its check mode in validation or CI:

```console
uv run --locked python scripts/generate_instrument_clients.py
uv run --locked python scripts/generate_instrument_clients.py --check
```

External packages use the installed Scopecat command with their own manifest,
import prefix, and source directory. The command has no checkout-relative path
or first-party fixture behavior:

```python
from scopecat.sdk.instruments.client_manifest import (
    ClientPackageManifest,
    InterfaceSurfaceRegistration,
)

PACKAGE_MANIFEST = ClientPackageManifest(
    surfaces=(InterfaceSurfaceRegistration(MyInstrumentInterface),),
)
```

```console
uv run scopecat-generate-instrument-clients \
  --manifest example_lab.instruments.package_manifest:PACKAGE_MANIFEST \
  --package-module example_lab.instrument_clients \
  --output-root src/example_lab/instrument_clients
```

Generated modules and the package facade are committed build outputs; edit the
declarations or manifest and regenerate them. Static descriptors make imports
independent of declaration compilation. Writable interfaces receive sparse
member projections; acquisition result declarations produce both public client
carriers and the measurement-valued observations returned by drivers. Generated
clients own wire conversion and exact `PerEntity` joins. Driver dispatch is
supplied once by `ObjectInstrumentDriver` and does not generate a handler class
per device.

One group `ensure(...)` remains a coherent state intent so routing can batch
channels that resolve to the same instrument. Group operations expand to scalar
invocations after validating every entity mapping, preventing partial effects
from a missing or extra identity. Composite client families are package
presentation metadata over existing wire interfaces. When two constituents use
the same Python member name for different property identities, keep both wire
interfaces unchanged and name the package-local view explicitly:

```python
CompositeSurfaceRegistration(
    name="SourceMonitor",
    interface_types=(SourceInterface, MonitorInterface),
    member_name_overrides=(
        (SourceInterface.enabled, "source_enabled"),
        (MonitorInterface.enabled, "monitor_enabled"),
    ),
)
```

These names apply consistently to member accessors and generated patch/target
fields; recording, restoration, routing, and driver dispatch continue to use
the original interface and property identities. Operation and acquisition
collisions use the parallel `method_name_overrides` surface:

```python
method_name_overrides = (
    (TriggerInterface.fire, "fire_trigger"),
    (GateInterface.fire, "fire_gate"),
)
```

An override changes live, symbolic, and group client method names together but
not their operation or acquisition refs. Use aliases only when the methods are
genuinely different concepts. If they are the same portable capability, model
that capability once in a shared interface. Interface authors should also avoid
framework-owned client verbs such as `apply` and `ensure`; those names are a
normal authoring convention rather than a second runtime validation system.

Generated acquisition carrier names use the acquisition declaration as their
key on both individual and composite surfaces:

```python
acquisition_names = (
    AcquisitionPublicNames(
        SensorInterface.sample,
        readback="SensorSampleReadback",
        products="SensorSampleProducts",
    ),
)
```

Either carrier name may be omitted when its declaration-derived default is
already unambiguous. This is also the explicit escape hatch when different
composite acquisitions happen to reuse the same result-class name; it does not
change acquisition or result refs.

Payload-bearing operations use `CommandPayload` on live clients and
`Symbolic[PayloadValue]` on symbolic clients. The generator deliberately does
not synthesize a schema-specific decoded carrier for each payload schema; the
driver adapter receives the decoded payload declared by the interface. Reusable
instrument components compile to nested interface members; resource routes
mount root client members at their physical `component_path`.

## Driver authoring

The shortest driver workflow is:

1. Declare members, results, and a `Protocol` or ABC with the helpers in
   `interface_declarations.py`.
2. Register any new surface and the lazy driver implementation in
   `PACKAGE_MANIFEST`.
3. Run `uv run --locked python scripts/generate_instrument_clients.py`.
4. Subclass `ObjectInstrumentDriver`, bind state I/O with
   `@read`/`@write`/`@query`/`@update`, and bind operations or true acquisitions
   with `@implements`.

For example, an RF implementation does not handle member refs or generic
requests:

```python
from typing import Protocol

from scopecat.kernel.quantity import Quantity
from scopecat.sdk.instruments import (
    Change,
    DeviceMember,
    Member,
    ObjectInstrumentDriver,
    Observed,
    device_member,
    instrument_driver,
    member_constraint,
    member_policy,
    observed,
    read,
    update,
)
from scopecat.sdk.instruments.declarations import (
    instrument_interface,
    member,
    observation,
)


@instrument_interface("example.rf_output/v1")
class RFOutput(Protocol):
    frequency: Member[Quantity] = member(
        access="read_write",
        restore=True,
        unit="Hz",
    )
    output_enabled: Member[bool] = member(access="read_write")


@instrument_driver(
    "example.rf_source",
    "1",
    interfaces=(RFOutput,),
    member_constraints=(
        member_constraint(RFOutput.frequency, minimum=1.0e9, maximum=20.0e9),
    ),
    member_policies=(member_policy(RFOutput.frequency, restore=False),),
    device_schema_id="example.rf_source/v1",
)
class MyRfSource(ObjectInstrumentDriver):
    reference_locked: DeviceMember[bool] = device_member(
        access="read_only",
        capture=True,
        restore=False,
    )
    reference_source: DeviceMember[str] = device_member(access="read_only")

    def __init__(self, instrument_id: str) -> None:
        self.instrument_id = instrument_id
        self._reference_source = "external"

    @read(RFOutput.frequency)
    def read_frequency(self) -> Quantity:
        return self._query_frequency()

    @update(
        RFOutput.frequency,
        RFOutput.output_enabled,
    )
    def update_output(
        self,
        *,
        frequency: Change[Quantity],
        output_enabled: Change[bool],
    ) -> None:
        if frequency.requested:
            self._set_frequency(frequency.value)
        if output_enabled.requested:
            self._set_output(output_enabled.value)

    @read(RFOutput.output_enabled)
    def read_output_enabled(self) -> bool:
        return self._query_output()

    @read(reference_locked)
    def read_reference_locked(self) -> bool:
        return self._query_reference_lock()

    @read(reference_source)
    def read_reference_source(self) -> Observed[str]:
        return observed(
            self._reference_source,
            source="configured_fixed",
            evidence={"configured_by": "connection_profile"},
        )
```

The base class supplies `describe`, `read_state`, `apply_state`, `invoke`, and
`collect` at the worker boundary. `@read`/`@write` expose independent I/O;
`@query`/`@update` preserve hardware batching and sequencing without creating
aggregate state. A driver overrides a worker method only for routing or a
failure model that these bindings cannot express.

Use a member-derived observation when an acquisition only records a fresh read
of independently queryable state. It does not need a result schema or a driver
method:

```python
@instrument_interface("example.dc_bias/v1")
class DCBias(Protocol):
    actual_voltage: Member[Quantity] = member(access="read_only", unit="V")
    settled: Member[bool] = member(access="read_only")

    readback = observation(actual_voltage, settled, label="Read back bias")
```

`ObjectInstrumentDriver.collect` reads those members coherently through the
normal state bindings. Use `@acquisition(results=...)` with a named
`@result_schema` of `scalar_result(...)` and `array_result(...)` declarations
instead when the device performs a distinct measurement, produces an array, or
has acquisition-specific failure and evidence. After generation, the driver
returns the corresponding class from
`scopecat_instruments.driver_observations`:

```python
from scopecat.records.measurement import MeasurementScalar
from scopecat.sdk.instruments import DriverOutcome, DriverSuccess, implements
from scopecat_instruments.interface_declarations import DCMonitorInterface
from scopecat_instruments.driver_observations import DCMonitorCurrentObservation


@implements(DCMonitorInterface.measure_current)
def measure_current(self) -> DriverOutcome[DCMonitorCurrentObservation]:
    return DriverSuccess(
        DCMonitorCurrentObservation(
            current=MeasurementScalar.create(
                dtype="float64",
                unit="A",
                value=self._measure_current(),
            ),
            evidence={"integration_cycles": self.integration_cycles},
        )
    )
```

The generated observation fields mirror the declared result fields but contain
`MeasurementAcquisitionValue` envelopes. `evidence` belongs to the persisted
acquisition readback; `DriverSuccess.metadata` describes the invocation outcome.
Do not define a parallel driver-only result schema. The explicit implementation
binding is the dispatch identity, so the concrete method may use a different
Python name and equal method names from different interfaces remain distinct.

An interface declares the maximum portable member surface. The concrete
driver's I/O bindings declare what one model actually implements. If a model can
report an interface member but cannot change it, bind only `@read`; the emitted
instrument description narrows that physical member to `read_only` and disables
restoration automatically. Do not add a writer whose only behavior is rejecting
changes. Mounted drivers preserve these implementation semantics independently
at every physical component path.

The default mounted router dispatches one patch to each child. If a physical
protocol instead commits several mounted channels in one device-wide frame,
keep the ordinary interface mounted at each channel and override the containing
driver's `apply_state`. Authors combine the channel clients with
`ensure_state_targets(...)`; planning retains one coherent patch for every
physical instrument. The driver may then require a complete bank and encode it
once. This physical commit rule is driver behavior, not a new composite
interface or a framework transaction declaration.

Use `write_only_member(...)` when hardware acknowledges a setting
but cannot query it. This remains state—sparse applies, routing, validation, and
audit records still use the member identity—but it cannot enter a baseline or
restoration snapshot. Generated clients expose `set(...)`/`apply(...)`, report
`is_readable() == False`, and reject `read()` locally. Do not fabricate a
readback or promote the command to a full read/write interface merely to fit a
better instrument's contract.

For the less common case where a member remains readable and writable but one
model must not participate in an interface lifecycle policy, add an
exception-only `member_policy(..., capture=False)` or
`member_policy(..., restore=False)` to `@instrument_driver`. The policy cannot
widen the interface and does not repeat its identity, type, units, or bounds.
Generated member clients expose the resolved result through
`member.implementation()` and `member.is_writable()`; `member.set(...)` checks
the concrete endpoint rather than only the portable interface.

When one model supports only a subset of an interface member's values, add
`member_constraint(...)` with narrower numeric bounds or string choices. The
interface continues to own the member's identity, type, unit, and portable
meaning; the instrument description carries only the concrete refinement. Use
a separate interface only when behavior or meaning changes, and keep
cross-member or mode-dependent combinations in driver validation rather than
trying to encode them as per-member bounds.

`device_member(...)` records model-specific background information without
inventing a one-device interface; its `capture`/`restore` policy is independent
for every member. Return `observed(...)` when the value is configured rather
than queried, or derived from other facts, so snapshots and observations retain
that distinction without copying background metadata onto unrelated members.
`device_member_ref(Driver.member)` provides its stable reference when routing or
tests need one. All four real and four virtual first-party drivers use this
pattern.

## Configuration

The Instruments workspace reads the provider's driver catalog to add or
configure a device. It exposes only supported connection kinds and typed driver
options, can test a candidate connection before publishing it, and derives
sparse startup-default fields from the probed interface description.

Virtual instrument:

```json
{
  "id": "flux",
  "exclusivity_key": "flux",
  "driver_id": "scopecat.virtual.dc_source",
  "connection": {
    "kind": "virtual"
  },
  "default_state": [
    {
      "target": {
        "kind": "interface",
        "interface_id": "scopecat.dc_source/v3",
        "component_path": [],
        "property_id": "output_enabled"
      },
      "value": false
    }
  ],
  "run_start": "apply_default_state",
  "success_action": "restore_baseline",
  "failure_action": "abort_and_release"
}
```

TCP/IP instrument:

```json
{
  "id": "readout-lo",
  "exclusivity_key": "readout-lo",
  "driver_id": "scopecat.rohde_schwarz.sgs100a",
  "connection": {
    "kind": "tcpip_socket",
    "host": "192.0.2.10",
    "port": 5025,
    "timeout_seconds": 5
  },
  "run_start": "preserve",
  "success_action": "release",
  "failure_action": "abort_and_release"
}
```

Binary serial instrument:

```json
{
  "id": "serial-bias-source",
  "exclusivity_key": "serial-bias-source",
  "driver_id": "example.serial_bias_source",
  "connection": {
    "kind": "serial",
    "port": "/dev/ttyUSB0",
    "baud_rate": 9600,
    "timeout_seconds": 1,
    "write_timeout_seconds": 1,
    "data_bits": 8,
    "parity": "none",
    "stop_bits": 1
  },
  "run_start": "preserve",
  "success_action": "release",
  "failure_action": "abort_and_release"
}
```

Driver-managed SDK:

```json
{
  "id": "composite-controller",
  "exclusivity_key": "composite-controller",
  "driver_id": "example.composite_controller",
  "connection": {
    "kind": "driver_managed",
    "options": {
      "controllers": {
        "primary": "192.0.2.10",
        "secondary": "192.0.2.11"
      }
    }
  },
  "run_start": "preserve",
  "success_action": "release",
  "failure_action": "abort_and_release"
}
```

Every run first synchronizes the device. `preserve` retains that observed
state; `apply_default_state` then applies the saved sparse member patch. A patch
may target portable interface state or declared model-specific device state;
all unlisted members remain untouched. After authored
normal-completion state, `release` leaves the resulting state in place;
`restore_baseline` restores the writable portion of the synchronized,
run-start-adjusted baseline; and `apply_safe_state` executes the configured
safe operations followed by the sparse safe-state patch. Failure always aborts
first; `abort_then_safe_state` executes the same safe actions while the device
remains commandable. Terminal readback and ownership release follow the selected
action.

A device whose safe transition is a procedure rather than a state assignment
can name one or more provider-declared operations. Operations run in declaration
order before the state patch and cannot carry opaque payloads:

```json
{
  "success_action": "apply_safe_state",
  "safe_operations": [
    {
      "interface_id": "example.sequencer/v1",
      "operation_id": "enter_safe_state"
    }
  ],
  "safe_state_requirement": "required",
  "failure_action": "abort_then_safe_state"
}
```

`best_effort` reports a known rejection and releases the instrument after final
readback. `required` instead keeps the physical access domain quarantined when a
safe action is rejected or its final state cannot be confirmed. Unknown command
outcomes always quarantine regardless of this setting. Executor lease loss and
daemon shutdown independently attempt the configured failure actions before
discarding the connection; hardware watchdogs remain responsible for complete
host or network loss.

Returned finalization actions are committed with the run's durable instrument
state evidence. Their metadata reports command evidence and must not be treated
as analog readback unless the provider says so explicitly.

Lifecycle snapshots capture the requested public and model-specific members
independently. Experiment entity and channel bindings are routing provenance,
not fields a driver reads back.

The DC monitor exposes `measure_current()` and `measure_voltage()` as separate
acquisitions. A concrete driver rejects the call at runtime when its
source mode or hardware configuration is incompatible.

Each registered driver declares its connection kind and a strict options model.
Unknown fields and coerced scalar values are rejected during configuration
discovery:

- Yokogawa GS200: `monitor_option` requests `/MON` support (`bool`, default
  `false`) and is verified with `*OPT?` when the connection opens;
- Yokogawa GS200: `remote_sense` and `guard_enabled` (`bool`, both default
  `false`) declare the expected physical wiring profile. The driver verifies
  them and never switches either setting automatically. Remote sense rejects
  voltage ranges below 1 V;
- Keysight E5080B: `channel` and `measurement` (positive integers, default `1`);
- the other included drivers accept no options.

The package supports these driver IDs:

| Driver ID | Interface |
| --- | --- |
| `scopecat.yokogawa.gs200` | `scopecat.dc_source/v3`; optional `scopecat.dc_monitor/v4` |
| `scopecat.rohde_schwarz.sgs100a` | `scopecat.rf_output/v2`, `scopecat.reference_clock/v1` |
| `scopecat.lakeshore.372` | `scopecat.temperature_readout/v1` |
| `scopecat.keysight.e5080b` | `scopecat.network_sweep/v1` |
| `scopecat.virtual.rf_source` | `scopecat.rf_output/v2`, `scopecat.reference_clock/v1` |
| `scopecat.virtual.dc_source` | `scopecat.dc_source/v3`, `scopecat.dc_monitor/v4` |
| `scopecat.virtual.temperature_monitor` | `scopecat.temperature_readout/v1` |
| `scopecat.virtual.vna` | `scopecat.network_sweep/v1` |

The real-device implementations are deliberately minimal. Verify firmware,
installed options, limits, cabling, and interlocks for the specific laboratory
before enabling hardware outputs.

SCPI drivers depend on the transport protocol and typed query helpers in
`scopecat.sdk.instruments.scpi`; this package supplies the concrete TCP
transport. Parsing failures therefore retain the command that produced the
invalid response. Binary drivers depend on `BinaryTransport`; the serial
implementation owns one port generation. Use `send(...)` for protocols that
define no response and `exchange(...)` only when the device specifies an exact
response size; a successful send confirms transport completion, not hardware
readback.

## Application composition

Projects install one configured provider at their daemon composition root:

```python
from scopecat_instruments import ConfiguredInstrumentProvider

provider = ConfiguredInstrumentProvider(seed=7)
```

Virtual drivers created by one provider share a deterministic virtual lab
world, so bias, RF heating, temperature, and the VNA response interact across
sessions.

A project package adds drivers by composing lazy registrations rather than
reimplementing provider dispatch:

```python
from scopecat_instruments.package_manifest import PACKAGE_MANIFEST
from scopecat_instruments.provider import (
    ConfiguredInstrumentProvider,
    compose_driver_registrations,
)

provider = ConfiguredInstrumentProvider(
    provider_id="example.lab.instruments",
    registrations=compose_driver_registrations(
        PACKAGE_MANIFEST.drivers,
        PROJECT_DRIVER_REGISTRATIONS,
    ),
)
```

Each `DriverRegistration` supplies one lazy `PythonSymbol`, implementation
version, connection kind, strict Pydantic options model, and catalog metadata.
For `tcpip_socket` and `serial`, the shared provider derives schemas, creates
description-only drivers, opens transports, probes hardware, closes failed
connections, and maps provider problems. Identity-capable drivers use the
default `probe="identify"`; devices with no safe identity query may declare
`probe="connect"`, which proves only that the configured port can open. Their
constructors receive `instrument_id`, the transport, and validated option
fields.

A `driver_managed` registration instead resolves a lazy factory type with two
methods. `describe(instrument_id, **options)` must be side-effect-free;
`connect(instrument_id, **options)` constructs and probes all physical resources.
The returned driver's `disconnect` releases those resources, and the factory
cleans up partial construction before raising. This is for vendor SDKs and
composite controllers, not a generic bypass for ordinary socket or serial
devices.

Transport generations are never reopened or retried inside a driver. A
`TransportError` records the failed operation, whether the command may have
reached hardware, and that the owner must replace the driver/transport pair.
After a write or binary exchange loses confirmation, return an `unknown`
outcome; automatic retry could duplicate an effect. A later ownership epoch may
connect a fresh driver and reconcile queryable state.

## Testing

The explicit testing module provides strict text and binary transcript helpers:

```python
from scopecat_instruments.testing import (
    ScriptedBinaryExchange,
    ScriptedBinaryTransport,
    ScriptedExchange,
    ScriptedTransport,
)
```

Their docstrings define the transport contract; driver tests keep exact device
sequences beside the implementations they verify.
