# Instrument control and authoring

Scopecat presents one typed capability vocabulary in two time models: a live
client performs work now, while a symbolic client records the same device verbs
inside an experiment. Drivers implement that capability without receiving run,
entity, point, product, or dataset concepts.

Daemon ownership and failure handling live in the
[lab daemon architecture](../development/architecture/daemon.md).
Execution ordering and physical authority live in the
[execution semantics](../development/architecture/execution.md). Provider registration,
generation, configuration, and driver tests live in the
[instrument provider README](https://github.com/scopecat-project/scopecat/blob/main/packages/scopecat-instruments/README.md).

## Use a configured device now

Create a typed physical reference from the configured instrument id, then open
it through a lab session:

```python
import scopecat as sc
from scopecat_instruments import network_sweep


READOUT_VNA = network_sweep("readout-vna")

with sc.open_project(".").connect(operator="alice") as lab:
    with lab.instruments.open(READOUT_VNA) as devices:
        vna = devices[READOUT_VNA]
        vna.apply(
            start_frequency=sc.Quantity(4.9, "GHz"),
            stop_frequency=sc.Quantity(5.1, "GHz"),
            points=751,
            s_parameter="S21",
        )
        trace = vna.sweep()
```

`apply(...)` performs a sparse state transition during the call. `sweep()`
triggers hardware and returns typed readback with receipt evidence. The session
owns synchronization and the same exclusive claim used by experiment runs; it
does not create a one-point experiment.

Closing a session ends ownership and applies its configured finish policy, while
keeping the connection available for subsequent work. To hand a configured device
to another program after all runs and sessions have ended, disconnect it explicitly:

```python
with sc.open_project(".").connect(operator="alice") as lab:
    lab.instruments.release(READOUT_VNA)  # Configured id strings also work.
```

Release rejects devices reserved by unfinished runs or sessions. It leaves the
daemon running, discards the connection and its cached state, and reconnects on
the next session or experiment. Release applies to the whole physical instrument,
including when given a component reference. It does not reserve the device for
another program: pause new submissions until that program has finished. Release
itself does not apply new hardware settings; the preceding run or session's finish
policy determines the final state.

When one inventory entry owns several implementations of the same interface,
select the physical mount on the live reference:

```python
from scopecat_instruments import rf_output


PUMP_2 = rf_output("pump-source", component_path=("channels", "2"))

with sc.open_project(".").connect(operator="alice") as lab:
    with lab.instruments.open(PUMP_2) as devices:
        source = devices[PUMP_2]
        source.apply(frequency=sc.Quantity(6, "GHz"), output_enabled=True)
```

Generated members stay relative to the selected mount; the live channel maps
state, operations, and acquisitions onto the physical component path and checks
that the interface is mounted there. `describe()`, `observed_state()`, and
`refresh()` still describe or return the owning physical instrument for
diagnostics. Configured defaults also belong to that owner and therefore cannot
be applied through a component-scoped client.

A genuinely temporary diagnostic device can use a session-only binding without
publishing configuration or defining entity routes:

```python
import scopecat as sc
from scopecat.records.config import TcpipSocketInstrumentConnection
from scopecat_instruments import network_sweep


BENCH_VNA = sc.temporary_instrument(
    network_sweep("temporary-bench-vna"),
    driver_id="scopecat.keysight.e5080b",
    connection=TcpipSocketInstrumentConnection(
        host="192.0.2.40",
        port=5025,
    ),
)

with sc.open_project(".").connect(operator="alice") as lab:
    with lab.instruments.open(BENCH_VNA) as devices:
        trace = devices[BENCH_VNA].sweep()
```

The daemon probes the installed driver, owns the connection, and claims a stable
identity for the session. The attachment disappears when the session closes and
does not enter experiment routing. Keep transient cable and operator intent in
the notebook cell. When a diagnostic should become a reproducible run, add the
device to inventory, write a small named experiment, and record its meaningful
result.

## Return from manual work to an experiment

In the console, connect a device, query the scientific values you need, apply
changes, then disconnect the manual session before starting an experiment.
Queries retain their observation source and time; an observed value is not a
promise about the device's later state. Configured defaults produce a receipt
and can be rejected when the maintained inventory has no default state. In that
case, choose explicit scientific settings instead of assuming a reset occurred.

Preview records the complete compiled instrument footprint and the manual-event
cursor captured before compilation. A later apply, invocation, acquisition or
explicit abort or connection release on a relevant physical instrument invalidates that
preview. The console explains the affected instrument and offers Preview again,
while retaining your scientific inputs. Pure queries and changes to unrelated
physical instruments leave the preview usable. Operations that fail or stop
partway through also invalidate it because they may already have changed state.

The revision-aware notebook entry uses the same check:

```python
with sc.open_project(".").authoring() as authors:
    prepared = authors.prepare("ramsey")
    # Finish any relevant manual changes before preparing again.
    submission = prepared.submit(request_key="ramsey-after-manual-adjustment")
```

Admission checks the server-recorded preview against the exact configuration and
code revision. Retrying an already admitted request key returns the original
procedure even if manual work occurred afterward. A fresh preview has a fresh
check and uses a new submission key; an unresolved original submission retains
its original payload for recovery. A preview is not a permanent permission to
execute a saved plan.

Runtime ownership still governs acquisition after admission. A manual session and
a run cannot own the same physical instrument concurrently. A stop request is
pending until the run finishes its cleanup; wait for the stopped result before
connecting manually. This ordering does not freeze hardware or detect changes
made outside the daemon.

Maintained launch adapters pass the preview's `manual_state` into their durable
intent and as `expected_manual_preview` to `lab.procedures.submit(...)`. The
standard author adapter already does this. Resource aliases are compared through
the inventory's physical exclusivity keys; maintainers own those mappings.

## Declare the same work for an experiment

Passing an experiment context to the same factory creates its symbolic client:

```python
import scopecat as sc
from scopecat_instruments import network_sweep


@sc.experiment
def capture(experiment: sc.ExperimentContext):
    vna = network_sweep(experiment)
    vna.ensure(
        start_frequency=sc.Quantity(4.9, "GHz"),
        stop_frequency=sc.Quantity(5.1, "GHz"),
        points=751,
        s_parameter="S21",
    )
    return vna.sweep()
```

`ensure(...)` records coherent desired state. Fixed values, inputs, parameters,
and other permitted symbolic values may supply its fields; omitted fields remain
unspecified. Consecutive ensures retain effect order. The symbolic acquisition
returns a typed product bundle, and defining the experiment touches no hardware.

An experiment can coordinate several instrument clients, reusable modules, and
domain calls. `@module` is an extraction boundary for work that is genuinely
reused or composed, rather than a prerequisite for device use.

Product namespaces derive from the capability and effect occurrence. Supply an
explicit acquisition `id=` when an occurrence is part of a durable data
contract. Returning a complete result bundle preserves its declared coordinate,
observable, axis, and acquisition-group relationships. The
[experiment dataflow model](../concepts/experiment-dataflow.md#return-the-result-you-mean-to-keep)
is the canonical durable-result reference.

### Select equivalent resources by role

Logical resource identities are allocated automatically. A stable lab role
expresses purpose when one entity has several resources implementing the same
interface:

```python
from scopecat_instruments import rf_output


drive = rf_output(
    experiment,
    for_=sc.one("q0", kind="logical_device"),
    role="drive",
)
readout = rf_output(
    experiment,
    for_=sc.one("q0", kind="logical_device"),
    role="readout",
)
```

The accepted configuration attaches each role to a selectable route. A route
groups all endpoints chosen together:

```json
{
  "roles": [
    {"id": "readout", "description": "source used for qubit readout"}
  ],
  "routes": [
    {
      "id": "readout-source",
      "instrument_id": "readout-source-0",
      "role_id": "readout",
      "entity_ids": ["q0"],
      "endpoints": [
        {
          "interface_id": "scopecat.rf_output/v2",
          "entity_id": "q0",
          "channel_id": "readout-q0",
          "component_path": ["outputs", "readout-q0"]
        }
      ]
    }
  ]
}
```

A string selects that exact cataloged role. Omitting `role` selects an unlabelled
route. Code that intentionally accepts the unique compatible route regardless
of role may pass `sc.ANY_RESOURCE_ROLE`.

`entity_ids` lists the logical entities served by the complete route. An
endpoint `entity_id` narrows one channel or command binding to an entity. A
shared LO, sequencer, or waveform endpoint omits the endpoint entity while the
route still lists every entity it serves. Entityless routes support bench work
selected by role or an unscoped experiment.

### Mount shared state on its physical owner

Mutable state belongs to its physical owner: device-wide properties at the
interface root, bank-wide properties on a bank component, LO frequency on an LO
component, and channel properties on channel components. Endpoint
`component_path` mounts a logical interface member at that owner.

When several routes resolve a property to the same physical address, they form
one effective shared-state group for that property. Equal requirements coalesce
into one command; unequal requirements produce
`experiment_conflicting_desired_state` before execution. Group membership is
capability-specific, so channels may share an LO while belonging to different
clock or trigger domains. Components such as `lo_groups/0`, `clock_domains/1`,
and `trigger_domains/0` express those distinct owners.

Roles select hardware by purpose. Component paths express shared mutable state.
Logical entity ids remain provenance and do not create copies of a physical
state slot.

An interface contains behavior that callers can rely on across compatible
devices. A repeated physical implementation uses `interface_mounts`; one
model's narrower access, capture, restore, or value domain stays in its
interface-property implementation. Model-specific facts and settings that are
useful for diagnosis, recording, or restoration but are not portable behavior
belong to a versioned `device_schema`. They are independently readable and
cacheable state members and do not require inventing a one-device interface.

### Use signed IF for specialized LO scans

Common experiments express physical carrier intent and use reviewed lab
frequency composition. A specialized fixed-IF LO scan can stay lab-local with
the single convention `RF = LO + signed IF`; positive and negative IF values
select the IQ sideband directly.

The scanned LO is already a durable point coordinate. Record the derived carrier
as another coordinate so plots and exports retain both the requested control and
its physical meaning:

```python
@dataclass(frozen=True)
class SpectrumResult:
    rf_frequency: Annotated[
        sc.ValueRef[sc.Quantity],
        sc.Result(
            role="coordinate",
            metadata={
                "relation": "rf_frequency = lo_frequency + signed_if",
                "signed_if_hz": -100_000_000.0,
            },
        ),
    ]


lo = experiment.scan("lo_frequency", (4.9, 5.0, 5.1), unit="GHz")
rf = lo + sc.Quantity(-100, "MHz")
return SpectrumResult(rf_frequency=rf)
```

The reference lab's
[`xy_drive` workflow](https://github.com/scopecat-project/scopecat/blob/main/examples/reference_lab/src/reference_lab/workflows/xy_drive.py)
shows this composition, and the
[`fixed-IF quantum sweep`](https://github.com/scopecat-project/scopecat/blob/main/examples/reference_lab/notebooks/36_q0_fixed_if_lo_sweep.py)
shows an LO host effect bounding domain batches.

Routing determines which source is changed. Equal requests to one resolved LO
owner coalesce; distinct LO owners remain independently schedulable. Only a lab
that deliberately coordinates a scan per physical owner needs additional local
grouping logic.

### Address one or many entities

Entity cardinality is selected at the factory boundary:

```python
single = network_sweep(
    experiment,
    for_=sc.one("q0", kind="logical_device"),
)

targets = sc.each("q0", "q1", kind="logical_device")
many = network_sweep(experiment, for_=targets)
```

`one(...)` creates one scalar symbolic client. Its entity may be concrete or
point-resolved. `each(...)` holds concrete authoring-time identities and returns
a group client with the same verbs. Scalar arguments broadcast; `PerEntity[T]`
supplies values by exact `(kind, id)` identity:

```python
points = sc.PerEntity((entity, 501 if entity.id == "q0" else 801) for entity in targets)

many.ensure(
    start_frequency=sc.Quantity(4.9, "GHz"),
    stop_frequency=sc.Quantity(5.1, "GHz"),
    points=points,
    s_parameter="S21",
)
traces = many.sweep()
return traces
```

The group still expands to independently routed scalar resources and
acquisitions, but the homogeneous returned products are recorded field-by-field
with a `logical_device` entity axis. Dataset schema width therefore follows the
number of result fields, not the number of selected devices. Per-entity product
sources and acquisition evidence remain aligned to the durable entity index.

Alignment is an identity join, so mapping order is irrelevant and missing,
extra, or duplicate identities fail before effects are recorded. Group authoring
does not ask a driver to perform an implicit vector operation.

## Compact rule set

1. Declare device meaning once with concrete Python state, operations, and
   results.
2. Use `apply` for an immediate sparse transition and `ensure` for symbolic
   desired state; use the same operation and acquisition verbs in both modes.
3. Let generated clients add time and cardinality: live or symbolic, one entity
   or `PerEntity`.
4. Mount mutable state at its physical owner and select equivalent resources by
   stable lab purpose.
5. Return typed values or product bundles and let dependencies determine order.
6. Record values and bundles directly; declared roles, axes, groups, and entity
   identity carry the dataset mapping.

To add a capability or driver, continue with the
[instrument extension guide](../extensions/instruments.md).
