# Number runs within a record collection

A record collection is a persistent numbering namespace. For example, create one
for a cooldown and keep using it when changing experiments or source files. Its
name can change; its ID and each admitted run's address cannot. A research project
can group runs from several collections without moving or renumbering them.

With an existing author session (`session = sc.notebook()` in a configured project):

```python
collection = session.create_record_collection("Chip A · September cooldown")
prepared = session.prepare("signal", record_collection=collection.id)
run = prepared.run().wait().result()

session.history(collection=collection.id)
first = session.run(1, collection=collection.id)
address = session.get_run(first.id).address
```

Create the collection once. On a later notebook visit, use
`session.record_collections()` to find it and
`session.record_collection(collection_id)` to reopen it. Calling
`create_record_collection` again creates a distinct collection even if the name
matches. For an automation-owned stable ID, use
`session.save_record_collection("chip-a-cooldown-2026-09", RecordCollectionEdit(...))`
with `RecordCollectionEdit` imported from `scopecat.records.record_collection`.
Rename with the same ID and `expected_revision=collection.revision`; refresh
metadata if another client has changed it.

Selection belongs to the prepared request. Two notebooks can select different
collections, or share one without duplicate numbers. An accepted submission keeps
its original number on retry; reusing that submission identity with a different
collection is a conflict. Numbers are assigned on admission, so queued, cancelled
and failed runs retain their numbers. Numbers are not list positions or counts of
successful measurements.

`session.prepare_plan(ref, actor="alice", record_collection=collection.id)` selects
the destination for one execution of a saved experiment plan. Saving the recipe
does not freeze its destination collection. Preview and submission do freeze the
selection; changing it requires a new preview.

Direct `LabClient.run(..., record_collection=collection.id)` and
`LabProcedureContext.run(..., record_collection=collection.id)` also support this
selection. Custom launcher providers must explicitly forward selection into their
child requests; the built-in authored launcher rejects an unsupported maintained
provider instead of silently placing runs in the default collection.

## Existing notebooks and retained data

Without a selected session collection, unqualified `session.run(number)`,
`session.run_number(run)` and `session.history()` continue using the store's existing global scheduler numbers. New runs without a
collection selection belong to the default collection and keep those same numbers.
The default collection can therefore have gaps when runs use other collections.
New named collections start at 1 and allocate independently. Use the collection ID
alongside its number when sharing a short address; retain `run.id` for existing
machine references. Collection names are labels, not identifiers.

The default collection ID derives from the current store identity.
[Current-format backup/restore](backup-and-restore.md) retains its addresses;
there is no supported import or upgrade of earlier development stores. Keep old
files separately under the [data policy](../development/data-compatibility.md).

This is the storage and Python/HTTP foundation for the
[experiment workbench context](../development/architecture/experiment-contexts.md).
[Notebook session selection](select-session-context.md) can remember a collection
per client/kernel, and the experiment workbench has page-local selection. There
is no cross-store catalog. A collection alone neither isolates hardware
nor copies working-point parameters; teaching still uses its existing isolation.
