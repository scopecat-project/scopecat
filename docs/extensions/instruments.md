# Extend instruments

The `scopecat-instruments` package demonstrates the supported provider pattern:
interface declarations define typed capabilities, generated live and symbolic
clients expose them to notebooks and experiments, and drivers implement the
capability without receiving run or dataset concepts.

An interface author declares portable members directly on a Python `Protocol`
or abstract base class. A distinct acquisition names an explicit result schema:

```python
from typing import Protocol

from scopecat import Quantity
from scopecat.sdk.instruments import Member
from scopecat.sdk.instruments.declarations import (
    acquisition,
    array_result,
    axis,
    instrument_interface,
    member,
    result_schema,
)


@result_schema
class NetworkSweepResults:
    frequency = array_result(
        dtype="float64", role="coordinate", unit="Hz", axes=("frequency",)
    )
    s_parameter = array_result(dtype="complex128", unit="ratio", axes=("frequency",))


@instrument_interface("example.network_sweep/v1")
class NetworkSweep(Protocol):
    start_frequency: Member[Quantity] = member(
        access="read_write", restore=True, unit="Hz"
    )
    stop_frequency: Member[Quantity] = member(
        access="read_write", restore=True, unit="Hz"
    )
    points: Member[int] = member(access="read_write", restore=True, minimum=2)
    s_parameter: Member[str] = member(access="read_write", restore=True)

    @acquisition(
        results=NetworkSweepResults,
        axes={"frequency": axis(size=points, unit="Hz")},
    )
    def sweep(self) -> None: ...
```

Members are independently queryable and cacheable state facts. Generated sparse
patch and target carriers represent omission. `scalar_result(...)` and
`array_result(...)` explicitly own measurement dtype, role, unit, and axes;
the schema class is a declaration namespace rather than a runtime value carrier.
Generation produces the wire contract, typed clients, member references, state
projections, and measurement-valued driver observation carriers.

An acknowledged setting that the device cannot query is the narrow exception:
declare it with `write_only_member(...)`. It remains an independently
addressed sparse state command. A successful apply can return explicit
`command_confirmed` observations in its `DriverSuccess(DriverStateReadback(...))`
receipt. Each observation names the confirmed member and its canonical-unit value;
this records command confirmation, not independently queried hardware state:

```python
# Only after the device-specific write/acknowledgement completed successfully.
return DriverSuccess(
    DriverStateReadback(
        observations=(
            DriverStateObservation(
                target=SETPOINT,
                value=confirmed_value,
                source="command_confirmed",
            ),
        )
    )
)
```

The driver owns the evidence supporting `confirmed_value`; the framework never
infers it from the request. A receipt must confirm every assigned member with the
requested value. Missing confirmation, unsupported members, invalid values/units,
and write-only observations claiming `hardware_query` are rejected. Rejected or
unknown writes produce no confirmed state and are not retried automatically.
A write-only driver's ordinary `read_state` can still return no observations.
Command confirmation does not make that property independently observable:
reconciliation, baseline capture and restoration retain their existing restrictions.

A driver subclasses `ObjectInstrumentDriver` and binds typed methods to member
declarations with `@read`, `@write`, `@query`, or `@update`. The base class
handles dispatch and wire conversion; the driver owns command ordering, device
limits, temporary setup and restoration, and response interpretation. A true
operation or acquisition is attached to its interface declaration with
`@implements(...)`; acquisition methods return their generated class from
`scopecat_instruments.driver_observations`. Class creation rejects missing,
duplicate, or signature-incompatible behavior bindings.

When requested results affect hardware setup, override
`prepare_acquisitions(plan: DriverAcquisitionPlan)`. Scopecat calls this hook
before each hardware batch, and every `DriverAcquisition` exposes its selected
`results`. The driver can therefore enable raw retention, choose a capture
mode, or allocate buffers only when the experiment requests the corresponding
result. Aggregate demand across every acquisition sharing a physical resource
in the plan—for example, raw retention remains enabled if any acquisition on a
channel requests raw samples. `collect(...)` must return only the selected
results; preparation is batch-scoped and must not depend on the order of later
collection calls. Drivers that need no demand-dependent setup inherit the
no-op implementation.

When the values to record are already members, declare an `observation(...)`
on those members instead of repeating their schema. The framework performs a
fresh coherent state read and records it as acquisition
products. Reserve `@acquisition` for a distinct measurement procedure, arrays,
or acquisition-specific failure and evidence.

Model-specific background state belongs on the concrete driver as a
`device_member(...)`; it can be captured or restored without inventing a
single-device portable interface. A reader may return `observed(value,
source="configured_fixed")` or `source="derived"` to retain how that member was
known; plain values mean a hardware query. Keep such provenance on the member
it describes instead of duplicating it in every other observation. An operation
that disturbs persistent state lists the affected members in `invalidates`; a
later `ensure` establishes a new guarantee.

Concrete models may narrow a portable member's numeric range or string choices
with `member_constraint(...)`. This is an endpoint refinement, not a new
interface declaration. Relational constraints such as “remote sense requires a
voltage range of at least 1 V” remain explicit driver behavior because they
depend on several independently queryable members.

Configured providers distinguish physical connection from device protocol.
`tcpip_socket` supplies a line-oriented SCPI transport; `serial` supplies a
binary transport with explicit framing. `driver_managed` delegates construction
to a lazy factory only when a vendor SDK or composite controller owns multiple
physical resources: its `describe` path is side-effect-free, while `connect`
owns probing, partial-failure cleanup, and the returned driver's disconnect
lifecycle. Drivers use `send` when the protocol has no acknowledgement and
exact-size `exchange` when it does. A transport registration also chooses
identity or connection-only probing. Transport failures retire that generation:
never retry an unconfirmed write in a driver, because the command may already
have reached hardware.

Use the package's source-adjacent guides for the exact extension workflow:

- [driver authoring](https://github.com/scopecat-project/scopecat/blob/main/packages/scopecat-instruments/README.md#driver-authoring)
- [typed client source generation](https://github.com/scopecat-project/scopecat/blob/main/packages/scopecat-instruments/README.md#typed-client-source-generation)
- [configuration](https://github.com/scopecat-project/scopecat/blob/main/packages/scopecat-instruments/README.md#configuration)
- [testing](https://github.com/scopecat-project/scopecat/blob/main/packages/scopecat-instruments/README.md#testing)

The [instrument control guide](../how-to/control-instruments.md) shows the
resulting live and symbolic user experience. Keep driver-specific command and
transport details beside their code; only cross-cutting user concepts belong in
the main documentation site.

## Inspect worker failures and vendor output

Instrument workers capture Python stdout/stderr and native bytes written to file
descriptors 1 and 2 before loading project driver code. RPC frames use a separate
channel. Display text decodes UTF-8 with replacement; the downloadable raw JSONL
preserves original bytes as base64, including invalid UTF-8. Native output carries
its worker generation, stream and **sampled** active request contexts. A native
read may happen after its originating call finished, so these samples do not
prove which overlapping request emitted a chunk. Python output and structured
failures carry the current request and instrument context when available.

Each project retains at most eight generation files, each containing the first
256 KiB of encoded diagnostics. Display responses are capped at 64 KiB. Completed
old generations are pruned under a retention lock; active generations are kept.
If every slot is in use, the new worker drains output without retaining it and
reports that quota condition without an evidence link. Reaching the byte limit
also stops retention, not draining, so a noisy vendor cannot block merely because
its log quota was reached. A diagnostic write I/O failure also switches to
discarding bytes while continuing to drain; the retained prefix may be incomplete. The display identifies truncation and links to the
bounded raw representation with `?raw=true`.

Only bytes delivered during capture are retained. Vendor-owned C stdio or custom
memory buffers that have not been flushed are not saved evidence. Drivers remain
responsible for their native buffer policy. Worker shutdown redirects late
native/atexit output to the null device so it cannot leak into daemon output; it
does not infer vendor-specific flushing or repeat operations.

The run inspector shows the causal operation problem first and additional cleanup
failures separately, with links to retained diagnostics. The public daemon client
provides `get_run_failure_evidence(run_id)` and
`get_worker_diagnostics(generation, raw=False)`. Diagnostic URLs accept only the
controlled generation identifier, never a filesystem path. A pruned generation
returns HTTP 410 rather than implying its bytes are still retained.

A failed terminal write must not replace the original acquisition problem or
claim the local result was saved. Callers can catch
`RunFinalizationFailed` from `scopecat.kernel.errors`, inspect its
`execution_outcome` and `finalization_problems`, then query the saved run. Its
`terminal_persistence` is `"unconfirmed"`; the original terminal-write exception
remains chained as `__cause__`. Ordinary `RunFailure` subclasses retain their
existing durable-outcome meaning. No diagnostic or finalization error grants
permission to retry an unknown trigger, invoke, or other non-idempotent effect.

### Measured operation costs

A driver may attach `OperationCostMeasurement` (from `scopecat.sdk.instruments`)
to `DriverSuccess`, `DriverRejected`, or `DriverUnknown`. The backend preserves it
in the operation receipt, including collect's binary transport. Supply facts from
the actual operation and a meaningful `source`; leave unsupported fields `None`.
A preflight estimate is never a measured cost. `unavailable_reason` describes the
missing counters, not a failure of the operation.

`transfer_seconds` and `acquire_seconds` are adapter-observed intervals. They may
overlap each other and the server's enclosing backend-call wall interval, so they
must not be added into a total run duration. Uploaded bytes count transferred
content; reused bytes count content used without another upload. Rendered bytes
count generated content, while retained bytes describe live storage at that
observation, a gauge that must not be summed over operations. A byte count of zero
is a measured zero; `None` is unavailable. Do not count JSON, worker framing or
spool bytes as device transfer unless that is the explicitly named measurement.

The server retains these facts once per physical operation in its existing batch
event, even when that batch fails or becomes indeterminate. Saved batches remain
readable when terminal publication is unconfirmed. Initial client planning wall
time is separate terminal evidence; it excludes lazy target compilation, which is
currently unavailable. Successful hardware cleanup/readback/release has its own
server wall interval. The terminal evidence commit cannot measure its own durable
completion and is explicitly unavailable. These observations introduce no retry
or connection-lifetime policy.

The run detail view and `DaemonClient.get_run_measured_costs(run_id)` show the
latest 128 events, with a partial-history marker when that window is full.
Connection identity is derived from the actual worker endpoint and connection
handle. Cold means the first observed connection for that instrument in this
daemon lifetime; warm means reuse of an existing connection; reconnect means a
new connection after an earlier observation in that same daemon lifetime. A new
daemon begins a new context. Connection reuse does **not** promise setup residency
across independently admitted runs.
