# Keep cooldowns and other experimental batches distinct

An experimental batch identifies one physical event, such as a cooldown or
mounting. It is independent of chip identity, parameter branch, record collection
and code directory. Create a new batch when those physical conditions change;
renaming a label does not change the scope of retained evidence.

```python
batch = session.create_experimental_batch("Chip A · cooldown 3")
```

Use `session.experimental_batches()` to browse retained batches and
`session.experimental_batch(batch_id)` to reopen one. Names need not be unique;
IDs are stable. The catalog provides bounded pages and optimistic metadata edits,
like [record collections](record-collections.md). Batches have no delete operation.
The workbench provides batch selection in
[Measurement context](select-session-context.md#select-context-in-the-workbench).

## Select parameters and batch independently

Given an existing sample `chip` and parameter branch `chip-a/daily`:

```python
session.use(
    sample=chip.id,
    batch=batch.id,
    parameter_branch="chip-a/daily",
)
params = session.params
```

A branch stores parameter versions; it does not own a sample or certify values
for a cooldown. You may select the same saved values for another batch without
copying a working point. Those values are starting estimates until appropriate
measurements establish their validity. To keep subsequent trial edits separate,
fork a branch with `params.save("chip-a/cooldown-3-trial")`, then explicitly select
that branch. See [parameter branches](parameter-branches.md).

Selecting a new sample starts a new scientific scope. Supply the intended batch
and parameter branch alongside it, as above. Changing only the batch preserves
the current parameter selection:

```python
next_batch = session.create_experimental_batch("Chip A · cooldown 4")
session.use(batch=next_batch.id)
```

The laboratory's executable setup is a separate choice. Preview validates the
selected equipment and inputs; selecting a batch or saving parameters does not
activate equipment or prove calibration applicability.

## Prepare and retain the actual scope

```python
collection = session.create_record_collection("Continuous chip study")
session.use(batch=batch.id, collection=collection.id)
prepared = session.prepare("signal")
plan = prepared.save_plan("Cooldown 3 signal", saved_by="operator")
run = prepared.run().wait().result()
assert run.samples[0].batch_id == batch.id
```

Changing the session later does not relabel this preparation or its result.
Changing batch also does not reset numbering; select another collection if you
want a new sequence. Names and folder paths do not infer these choices.

An independent parameter editor supplies values while retaining the session's
subject and batch. It can also be combined with an explicit sample/batch for one
preparation. A saved plan instead keeps its frozen scientific scope:

```python
session.use(batch=next_batch.id)
reopened = session.prepare_plan(plan.ref)
assert reopened.request.selection.batch.id == batch.id
```

An explicit conflicting `batch=next_batch.id` on `prepare_plan` is rejected.
Likewise, a candidate retains its source evidence's sample and batch; it cannot be
relabelled for another cooldown. Preparing with a candidate selects that retained
scope, and direct run admission rejects a conflicting sample/batch selector.
To explore new conditions, select independent parameter estimates and acquire new
evidence. None of these operations retroactively changes old results.

## Calibration evidence and history

Calibration checks assess exact measurement inputs, including parameter or
candidate identity, subject, setup and execution scenario. Batch information in
the retained sample binding is part of that evidence scope. A branch name or a
successful save is not a reusable calibration result. Use the check/applicability
and publication workflow described in
[automated parameter calibration](automate-parameter-calibration.md); changing a
batch label does not create fresh evidence. Operator attribution is separate from
execution authorization.

To filter retained runs without changing their addresses:

```python
from scopecat.records.research_project import RunHistoryFilter

page = session.list_runs(history=RunHistoryFilter(batch_id=batch.id))
```

The current format retains the batch catalog and run/batch association. Unspecified
batches stay unspecified; none is guessed from a branch name, directory or time.
Current-format [backup/restore](backup-and-restore.md) retains this evidence.
Earlier development stores have no supported migration or read path in new builds;
see the [data policy](../development/data-compatibility.md).
