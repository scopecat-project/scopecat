# Keep cooldowns and other experimental batches distinct

An experimental batch identifies one physical event or campaign, such as a cooldown
or mounting. It is independent of the chip identity, working-point name, record
collection and code directory. Create a new batch when those physical conditions
change; renaming a label never changes applicability.

```python
batch = session.create_experimental_batch("Chip A · cooldown 3")
```

Use `session.experimental_batches()` to find retained batches and
`session.experimental_batch(batch_id)` to reopen one. Names need not be unique;
IDs are stable. The catalog provides bounded pages and optimistic metadata edits,
like [record collections](record-collections.md). Batches have no delete operation.

## Bind a working point to its batch

When saving a working point, put the batch on its sample selector:

```python
saved = lab.config.save_context(
    entry_id="chip-a-cooldown-3-parked",
    base=base_ref,
    sample=chip.selector(batch_id=batch.id),
    working_point_id="parked",
    label="Parked in cooldown 3",
)
working_point = ConfigContextRef(
    entry_id=saved.entry.id, content_hash=saved.entry.content_hash
)
session.use(working_point=working_point)
```

Here `chip` is an existing `SampleHandle`, `lab` is a connected `LabClient`, and
`base_ref` is an exact configuration reference. Import `ConfigContextRef` from
`scopecat.records.config_context`. See [parameter contexts](manage-configuration.md)
for saving and editing parameter values.

Selecting a working point selects its sample and batch. Selecting a different batch
while retaining that working point is rejected. To start a new event, create a new
working point for it. A batch can contain several samples; it is not a new physical
identity for the chip. Assembly and setup applicability are not implemented yet.

You can explicitly use a previous working point as `base` when saving a new one
with the new batch's sample selector. This preserves the source reference and value
origins. The copied values are starting estimates, not newly acquired calibration
evidence. Advancing an existing parameter workspace cannot change its sample,
working point or batch in place.

## Prepare and retain the actual scope

```python
session.use(working_point=working_point, collection=collection.id)
prepared = session.prepare("signal")
run = prepared.run().wait().result()
run.samples[0].batch_id
```

Changing the session later does not relabel this preparation or its result.
Changing batch also does not reset numbering; explicitly select a new collection
if you want a new sequence. Batch names, collection labels and folder names do not
infer one another.

An explicit `context`, `parameters` or `candidate` argument to `prepare` must still
match the selected batch. A saved plan retains its original batch and cannot be
executed under another selected batch. To intentionally revisit its original
conditions, select that batch or pass `batch=original_batch_id` for that preparation.
Passing `batch=None` removes the local selection constraint; it never strips a
batch from a retained working point, candidate, or saved plan.

Without a saved working point, use
`session.use(sample=chip.id, working_point=None, batch=batch.id)`. This uses the
ordinary active configuration as the starting parameter set; it does not certify
those values for the selected event. Direct clients can pass
`sample=chip.selector(batch_id=batch.id)` to `lab.run(...)`.

The server validates declared batch IDs and stores them with the exact sample
bindings. It rejects forged context bindings and batch-scoped candidate reuse under
a different sample/batch, including through direct run submission. A candidate
belongs to its original evidence scope; copy parameter estimates explicitly when
moving to new conditions.

## Calibration evidence and history

Calibration integrations must pass the declared event when creating
`CalibrationTargetRef(..., sample_id=chip.id, context_id="parked", batch_id=batch.id)`.
The batch participates in the calibration key, freshness fingerprint, parent
procedure selectors and inherited child-run bindings. A success in batch A cannot
satisfy the corresponding target in batch B; an unscoped legacy success is distinct
from both. Renaming a batch keeps its existing evidence usable in its original scope.
Dependencies must belong to the same batch. Cross-batch and unscoped dependency
reuse is rejected until an explicit applicability policy is implemented.

This does not automatically infer a batch for existing calibration integrations,
certify copied values, or separate the existing shared active configuration into
per-batch apparatus and parameter owners. A selected operator is still attribution,
not authorization. Hardware and multi-chip applicability need separate qualification.

To filter retained runs without changing their addresses:

```python
from scopecat.records.research_project import RunHistoryFilter

page = session.list_runs(history=RunHistoryFilter(batch_id=batch.id))
```

Schema **72** adds the batch catalog and an indexed run/batch association. The
[explicit copy migration](migrate-data.md) preserves all old tables, payloads and
run numbers. Historical batches remain unspecified; no event is guessed from a
working-point name, directory or timestamp. Optional batch fields are omitted from
old serialized identities, so existing content hashes and calibration keys remain
unchanged.
