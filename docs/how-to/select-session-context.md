# Select an experimental context

An author session can remember the sample, experimental batch, saved working point, record collection
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
sample and batch. `rabi` is an experiment imported from your project. The same `use` API is
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
or working point. When a batch is selected, that explicit scope must still belong
to the selected batch (or the request must explicitly select its original batch).
The selected operator and collection are inherited independently
unless explicitly overridden. `context=None` deliberately uses the ordinary active
configuration without the session's sample/working point. Clearing the collection
for one request uses the default record collection. None of these overrides changes
`session.selection`.

A saved recipe has its own frozen scientific scope.
`session.prepare_plan(ref)` uses that scope and inherits only the current operator
and collection. Both may be explicitly overridden for that execution. The plan
must match the selected batch; a different batch requires a new scientific scope.

## Number lookup follows the selected collection

With a collection selected, `history()`, `run(number)` and `run_number(run)` all use
that collection. Asking for another collection's run number raises a clear error.
Durable string run IDs always resolve independently of this selection.

Pass `collection=another_id` for a scoped lookup, or `collection=None` to use the
legacy store-wide number/history without changing the selection. A session with
no collection selected keeps the previous store-wide behavior. See
[record collections](record-collections.md) for stable addresses and current-format recovery.

Python/Notebook clients and the workbench support existing single-sample
working points. Declared batch/cooldown applicability is covered by
[experimental batches](experimental-batches.md). Multi-sample assemblies, setup
applicability and multiple code workspaces remain pending. A collection named
“cooldown 3” is not proof that a previous
calibration applies in that cooldown.

## Select context in the workbench

In **Experiments**, **Measurement context · this page** holds the sample and
operator. Open **Browse samples, batches and collections** to choose registered
samples, an experimental batch and a record collection. Lists have explicit
**Load more** controls. **New batch** and **New collection** create named catalog
entries; use the newly created entry when ready. Creating metadata never starts a
measurement or changes another page's selection.

These selections survive switching experiments, refreshing author code and moving
between console views. **Reset launch draft** resets experiment inputs and detaches
a saved recipe while keeping the page's sample, batch, collection and operator.
Reloading the browser or connecting to a different project starts a new selection;
separate tabs do not share mutable defaults. This is still one connected code
workspace, not a multi-workspace application selector.

Select a saved working point through **Configuration → Use for next experiment**.
Its exact sample revision and batch become the selected scope. Those fields are
read-only while bound; **Use lab default** releases the working point while keeping
sample/batch selection. When saving a working-point copy, **Choose experimental
batch** can explicitly bind the copy to a new event. Its parameter values remain
starting estimates, not new calibration evidence.

Opening a saved plan retains its sample revision and batch, while collection and
operator remain the destination page's choices. A plan from another batch is
rejected without replacing the current draft. To intentionally revisit it, release
the working point or reset the recipe draft as needed, then select its original
batch (or clear the batch constraint) before opening it. Imported analysis
suggestions retain the current operator and collection; without an explicit
working point they clear the old scientific scope.

Every selection edit invalidates the preview. **Preview** validates it through the
same launch contract used by Python; only **Start acquisition** admits a procedure.
The original request remains available if a submission response is lost, even after
subsequent edits. None of these selections publishes a new shared default config.

Declared batch and collection selection currently require an authored experiment
(the standard `@experiment` path). Legacy custom launch providers report an
unsupported-selection error at preview; the console never drops these choices to
make such a launch proceed. Their procedure adapters need explicit support before
using this scope.
