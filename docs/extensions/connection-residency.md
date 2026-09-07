# Qualify connection-local residency

A successful upload proves state on the current device connection. It does not
prove that a replacement worker, reset device, or newly opened connection still
has that state. Target residency fingerprints describe device-effective content;
they are not durable evidence of hardware readiness. See
[opaque target setup](quantum.md#cache-opaque-target-setup-by-content).

Scopecat retains successful setup within one run. Equal content in later target
batches can skip setup while still triggering and collecting every requested
point. Failed setup never enters that cache. A fresh independently admitted run
establishes setup again, even if the daemon retains its physical connection after
releasing run ownership. Drivers must invalidate their own lower-level caches
when reconnecting or resetting a device. An adapter may perform an idempotent
ensure; it must not silently repeat a non-idempotent trigger after an unknown
response.

## Use the shared hardware-free contract

The workspace package `scopecat-testkit` exposes
`scopecat_testkit.connection_residency`. Install it alongside the matching
Scopecat checkout in your adapter's test environment. The module imports public
SDK contracts and does not require the server extra. It provides:

- `ResidencyProbe(root)`: a file-backed event log and one-shot fault switches that
  survive worker replacement. `counts()` returns setup, trigger and collect counts.
- `VolatileProgramProvider`, `VolatileProgramTarget`, `residency_config()` and
  `residency_experiment(points)`: a working reference with one-point target
  batches, stable content fingerprints and a fresh empty program on each connection.
- `check_connection_residency(run, probe, *, assert_unknown=None)`: reusable checks
  around your adapter's supported execution entry point. `run(points)` admits a
  new run, returns its `RunSnapshot`, and preserves public failure exceptions.

Use your real adapter and target with a fake native client. Give each new fake
connection a distinct ID and an empty loaded-program slot. Call
`probe.record(connection_id, operation, content)` for `connect`, `setup`,
`trigger`, `collect` and `disconnect`; count attempted setup and physical trigger
calls, not merely successful receipts. Record `None` for contentless operations.
Honor `probe.consume("setup_rejected")` by rejecting that setup before any trigger.
Honor `probe.consume("trigger_unknown")` by performing and counting one trigger,
then reporting its response as unknown. Collection must require successfully
loaded content and a trigger. Use equal device content across the requested
points and one-point target batches so setup reuse is exercised.

```python
from scopecat_testkit.connection_residency import (
    ResidencyProbe,
    check_connection_residency,
)

probe = ResidencyProbe(tmp_path / "device")
# Configure your fake native client to use probe, then create your normal lab.
check_connection_residency(
    lambda points: lab.run(adapter_experiment(points)).snapshot,
    probe,
    assert_unknown=assert_adapter_unknown,
)
```

`assert_adapter_unknown(execute)` must call `execute()` exactly once and assert
its actual public error and uncertainty evidence. For example, a daemon-backed
adapter can compare `DaemonClient.list_runs()` before/after, read `get_run()`,
`get_run_execution_segments()` and `replay_events(run_id=...)`, and assert the
run requires attention, resources are quarantined and the original unknown
hardware problem remains recorded. Do not turn an arbitrary exception into an
invented successful or indeterminate result. If your runner raises the public
`RunIndeterminate` error directly, omit the callback; that is the default check.

The shared sequence checks these cumulative physical counts:

| Scenario | Setup | Trigger | Collect |
|---|---:|---:|---:|
| Two equal-content points in one connection/run | 1 | 2 | 2 |
| Fresh run with equal content | 2 | 3 | 3 |
| Rejected setup in a new run | 3 | 3 | 3 |
| Fresh supported run after rejection | 4 | 4 | 4 |
| Lost response to the first trigger of two requested points | 5 | 5 | 4 |

Also exercise your supported explicit disconnect and worker-replacement actions.
Connection counts depend on that policy; ownership release alone need not close
the connection. Assert the new connection begins empty and reloads before its
first trigger. Never use durable setup rows as a reason to skip this reload.
Device-specific reconnect and reinitialization remain adapter responsibilities.

## Reference evidence and current failure surface

The core integration contract closes its connection after each run and verifies
five distinct connections, each beginning with setup. The real daemon/worker
contract retains one connection across those runs while repeating run-local
setup. Separate worker tests replace the worker between successful runs and
verify setup before the next acquisition, reject a predecessor generation's
loaded handle, and kill a worker after setup before acquisition. The killed
worker causes zero triggers and zero collections; the durable execution segment
records `run_instrument_acquisition_prepare_unknown` with indeterminate certainty.

For a lost trigger response, the daemon records the original
`fixture_trigger_response_lost` code in `run_hardware_batch_unknown`, fences the
executor and quarantines its resources. If subsequent finalization cannot commit
with the revoked lease, `RunFinalizationFailed` preserves the original execution
problem first, appends the terminal persistence problem and retains the lease
conflict as its exception cause. Its local `execution_outcome` is explicitly not
a promise of a durable terminal result. The reference test checks both that
exception and the actual saved attention/uncertainty evidence. See
[worker failure diagnostics](instruments.md#inspect-worker-failures-and-vendor-output).
