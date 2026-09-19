# Keep cooldowns and other experimental batches distinct

An experimental batch identifies one physical event or campaign, such as a cooldown
or mounting. It is independent of the chip identity, working-point name, record
collection and code directory. Create a new batch when those physical conditions
change; renaming a label never changes applicability.

```python
batch = session.create_experimental_batch("Chip A · cooldown 3")
```

The workbench also provides batch creation and selection in
[Measurement context](select-session-context.md#select-context-in-the-workbench),
and an explicit batch choice when copying a working point.

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

Automatic calibration takes its exact sample revision, working point and batch
from the selected saved working-point scope. Definition authors select logical
members; the evaluator binds them to the workspace before status lookup. The
stable workspace owner and declared batch participate in calibration identity,
while parent and child runs retain the exact sample revision. Two same-named
working-point branches do not share successes merely because their labels match.
Renaming a batch keeps its evidence in the original scope.

Dependencies must belong to the same workspace owner and batch. Cross-owner,
cross-batch and unscoped dependency reuse needs an explicit applicability policy
and is rejected by this slice. Catalog-scoped nonpublishing checks remain possible
without manufacturing a sample. This does not certify copied values or physical
hardware applicability. An operator name remains attribution, not authorization.

To filter retained runs without changing their addresses:

```python
from scopecat.records.research_project import RunHistoryFilter

page = session.list_runs(history=RunHistoryFilter(batch_id=batch.id))
```

The current format stores the batch catalog and an indexed run/batch association.
Unspecified batches remain unspecified; no event is guessed from a working-point
name, directory or timestamp. Current-format [backup/restore](backup-and-restore.md)
retains that evidence. Earlier development stores have no supported migration or
read path in new builds; see the [data policy](../development/data-compatibility.md).
