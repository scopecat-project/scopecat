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

## Collect and find results in the application

The experiment workbench keeps **Record collection** visible in measurement
context. Create a collection once, explicitly choose **Use**, then keep that
selection for successive acquisitions. **Return to preparation** keeps the
page's destination; opening retained data in another window does not change it.

In **Data**, choose **Record collection** to browse its runs, including failed or
cancelled admissions. **All collections** includes the default and named
collections; it is not the default collection itself. Select the actual default
collection from the list to restrict browsing to it. Enter **Collection run
number** and choose **Find run** to resolve an exact address, independently of
the project, sample, time and advanced source filters. A missing number is not
replaced with a global-number lookup. Imported results remain separate below.

Rows, acquisition receipts and run details show the collection ID with its short
number and label the scheduler number **Global**. Details also show the current
collection name. **Rename collection** changes only that label; research
associations also leave acquisition addresses intact. **Copy run reference**
copies the collection ID, short number and stable run ID for the current data
space. **Copy read-only code**
includes the explicit `session.run(number, collection="collection-id")`
alternative and executes the stable run ID, so a different Notebook selection
cannot silently open another run. Connect the Notebook to the same data space.

Working point and bench deployment are exact technical identifiers under
**Advanced source filters**. Collections are flat numbering namespaces; directory
hierarchies and scientific-condition tags are not provided by this workflow.
