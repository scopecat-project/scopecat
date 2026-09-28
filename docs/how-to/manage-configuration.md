# Edit parameters for an experiment

Use **Configuration** in the workbench, or `session.parameters` in VS Code.
Parameters can be edited before selecting a sample or connecting a device.
Saving values does not establish that a calibration is valid.

## In the workbench

Choose a **Saved parameter version**, then **Edit a copy**. Enter known values
and leave unknown values empty; zero is a known value. Review the units.

Give the copy a new version name. Save it on its own, or choose a parameter branch
to advance its history. If another editor has changed that branch, your draft stays
open. Review the new head and decide whether to keep your changes.

**Use for next experiment** changes only this page's selection. Choose an
**Experiment setup** and preview again before running. Editing device addresses
belongs in **Devices and drivers**, not the parameter form.

## In VS Code

Create a named history from an existing parameter revision once:

```python
initial = session.parameters.get("initial-estimates")
session.parameters.create_branch("chip-a/daily", revision=initial)
session.use(parameter_branch="chip-a/daily")
params = session.params
```

Reopening the branch captures its current version for this editor:

```python
params["drive"]["q0"]["frequency"] = 5.1
print(params.diff())
version = params.save(note="Frequency estimate from the latest scan")
```

Table and field names come from the author's declarations. Quantity fields accept
values with units, for example `sc.Quantity(5.1, "GHz")`. Reading a value through
a compatible unit view does not rewrite its stored representation.
`None` marks an unknown non-key cell; it does not fill a default.

Use `params.discard()` to discard local edits. `params.copy()` makes an independent
draft. `params.save("chip-a/trial")` saves a new branch and selects it in that editor;
the original branch and other sessions keep their selections.

For typed fields and new tables, see
[parameter declarations](declare-dynamic-parameters.md).
For file or pandas editing, see [table exchange](exchange-parameter-tables.md).

## Prepare and run

```python
setup = session.setup.get("bench-v1")
session.use(parameter_branch="chip-a/daily", setup=setup)
prepared = session.prepare(experiment, parameters=session.params)
print(prepared.preview)
run = prepared.run()
```

Preparation captures exact setup, device connections and parameter inputs.
Changing another page's selection does not change this request. Changing a device's
connection requires a fresh setup resolution and preview; already submitted work
must finish or be cancelled before that maintenance.

A preview checks whether the inputs can be used by this experiment. It is not
independent calibration verification. Publish a verified candidate with
[the parameter-branch workflow](publish-working-point-calibration.md).

## Conflicts and history

If a save reports that the branch changed, the local draft remains available.
`params.rebase()` merges independent cell changes. A conflicting cell or changed
table declaration requires review; the editor does not silently choose a value.

`session.parameters.history("chip-a/daily")` lists exact revisions.
`session.parameters.get(version.id)` reopens one without changing any branch.
Saving, copying and restoring values do not transfer calibration validity to a new
sample, batch or setup.
