# Select a notebook's experimental context

An author session can remember the sample, saved working point, record collection
and operator for its next requests. This selection belongs to that client. Two
notebooks connected to the same daemon can make different selections without
changing the lab's active configuration or another client's defaults.

```python
session = sc.notebook()
collection = session.create_record_collection("Chip A · cooldown 3")
session.use(working_point=working_point_ref, collection=collection.id, operator="Li")

prepared = session.prepare(rabi())
run = prepared.run().wait().result()
session.history()
session.run(1)
```

Here `working_point_ref` is an existing exact `ConfigContextRef`; see
[parameter contexts](manage-configuration.md). Selecting it also selects its bound
sample. `rabi` is an experiment imported from your project. The same `use` API is
available on `project.authoring()` and `AuthorProject` clients. Selection performs
read-only validation; it does not run an experiment, activate configuration, or
establish calibration validity.

## Change one choice without resetting the others

```python
session.use(operator="Wang")
session.use(working_point=another_working_point_ref)
selected = session.selection
```

Omitted fields remain selected. Switching working points selects the new point's
sample and retains the collection and operator. To use a sample without a saved
working point, explicitly clear the old point:

```python
session.use(sample="chip-b", working_point=None)
```

A mismatched sample/working-point pair or missing collection is rejected; a failed
update leaves all previous selections intact. `selection` is an immutable snapshot.
A later `use` call affects only future preparations; already prepared requests,
submitted jobs and retained results keep their original selection. The ordinary
server preview/admission checks remain in force if shared configuration changes.

In IPython, `sc.notebook()` reuses this kernel's session. Cell-boundary source
refresh preserves the selection. Closing and reopening starts with empty defaults;
selection is not saved as an application-wide current chip. Multiple notebooks in
one shared kernel share its session, just as they share Python variables.

## Override one preparation

Existing explicit `prepare` arguments keep their meaning:

```python
session.prepare(rabi(), parameters=other_parameters)
session.prepare(rabi(), context=another_working_point_ref, actor="Guest")
session.prepare(rabi(), context=None, record_collection=None)
```

An explicit `parameters`, `candidate`, `context` or `sample` selects the whole
scientific scope for that request. It does not combine with an inherited sample
or working point. The selected operator and collection are inherited independently
unless explicitly overridden. `context=None` deliberately uses the ordinary active
configuration without the session's sample/working point. Clearing the collection
for one request uses the default record collection. None of these overrides changes
`session.selection`.

A saved recipe has its own frozen scientific scope.
`session.prepare_plan(ref)` uses that scope and inherits only the current operator
and collection. Both may be explicitly overridden for that execution.

## Number lookup follows the selected collection

With a collection selected, `history()`, `run(number)` and `run_number(run)` all use
that collection. Asking for another collection's run number raises a clear error.
Durable string run IDs always resolve independently of this selection.

Pass `collection=another_id` for a scoped lookup, or `collection=None` to use the
legacy store-wide number/history without changing the selection. A session with
no collection selected keeps the previous store-wide behavior. See
[record collections](record-collections.md) for stable addresses and migration.

This is currently a Python/Notebook selection facility for existing single-sample
working points. Batch/cooldown applicability, multi-sample assemblies, page-local
GUI selection and multiple code workspaces still require their own contracts and
implementation. A collection named “cooldown 3” is not proof that a previous
calibration applies in that cooldown.
