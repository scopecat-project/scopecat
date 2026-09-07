# Resume an interrupted static run

Use resume when a notebook or executor disappeared after part of a static local
experiment became durable. The run must be `queued` or `attention_required` and
must not already have a terminal outcome.

A new `lab.run(...)` is rejected when another run or
interactive session owns its required instruments. It ends as `failed` with
`run_resources_busy`, without starting acquisition or leaving queued work behind.
Submit a new invocation after the owner finishes; this is not automatic waiting.
Explicit `lab.resume(...)` retains the existing queued run on resource contention
so a failed recovery attempt does not discard its earlier measurements.

A procedure's `context.run(...)` instead retains its unstarted child and returns
the parent to `ready` with a `resource_wait` marker. The worker exits while
resources are occupied. GUI-managed procedures wake automatically when the
resources become available, using the same child run and step attempt. Python
callers without a background host explicitly resume the procedure. Cancel the
parent to atomically cancel its waiting, unstarted child as well. Quarantined
owners still require reconciliation; waiting does not authorize replay of a
child that has already started.

Inspect a queued run with `lab.control.run_detail(run_id).resources`, or the
Resources card in the GUI. A `blocked` resource includes `blocked_by.owner_kind`
(`run` or `instrument_session`), `owner_id`, and `status`. A quarantined owner
requires reconciliation; its expired execution process does not make the device
available. These fields describe a current read snapshot, not a reservation or
permission to execute. `required` means no competing claim was observed and does
not imply that an automatic worker is waiting to start the run.

Cancelling a queued run leaves its former owner's execution unchanged. Once the
queued run is closed, its resources show `released` even if another run still
owns those devices. No historical blocker is inferred from current ownership.

First reconcile the physical instruments outside Scopecat. This means checking
that it is safe to acquire and program them again; durable measurement coverage
does not prove their current state.

Then rebuild the same invocation and resume the existing run:

```python
run = lab.get_run("01K...")
invocation = FREQUENCY_SCAN(
    device="q0",
    frequencies=frequencies,
)

run = lab.resume(run, invocation, executor_id="recovery-notebook")
```

Calling `lab.resume(...)` on an attention-required run is the explicit
authorization to leave quarantine after that external reconciliation. Scopecat
plans the invocation again against the run's accepted configuration snapshot.
The reconstructed durable request, run contract, and any initialized measurement
schema must match before attention is resolved or a new executor lease is
acquired. The run contract covers every ordered point and the complete planned
measurement schema even when the UI only shows bounded point samples and no
dataset header was written before the interruption.

Resume does not require a clean Git worktree and does not claim that source code,
imports, packages, or the Python environment are unchanged. Contract-compatible
changes are accepted. This is a deliberate recovery policy: the new execution
segment records the boundary, while the operator decides whether the current
code is appropriate.

For a supported static local run, execution reconstructs progress from both the
durable contiguous point watermark and exact completed recovery groups.
Completed effects are not replayed, and new measurements belong to a new
segment-owned acquisition fragment. The final dataset identity covers the
selected records from every segment in logical point order.

If the point schedule declares a recovery group, acquired rows enter the
physical log immediately but do not make the group resumably complete until
every member succeeds. Interruption therefore makes resume repeat the whole
unfinished group, even when one or more hardware batches from it had already
completed. Those orphan acquisitions remain available for diagnostics and are
not selected by the logical dataset. Hardware batch boundaries do not weaken
this policy. A completed reordered group can be skipped from its exact durable
proof even when it lies beyond the contiguous watermark.

Adaptive runs and domain-target runs cannot yet use this API after an execution
segment has started, even when zero points are durable. Their safe position also
depends on proposal or external-job state that point coverage alone cannot
identify. Close those runs or use target-specific recovery instead of replaying
them as static suffixes.
