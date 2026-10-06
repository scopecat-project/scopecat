# Edit parameters for an experiment

Use **Configuration** in the workbench, or `session.parameters` in VS Code.
Parameters can be edited before selecting a sample or connecting a device.
Saving values does not establish that a calibration is valid.

## In the workbench

For an existing daily table, enter its **Working parameter branch** and choose
**Open working table**. Reopening resumes that branch's active work, including
unfinished text such as `-` or `1e`. Alternatively choose a **Saved parameter
version**, then **Edit a copy**. **Edit another copy** starts independent work.

Values, units and notes save automatically to application storage. Wait for the
saved status before quitting; input not yet acknowledged is not guaranteed after
forced termination. Closing the editor only hides it. Navigation, reopening the
application and restarting its service recover acknowledged input. Empty numeric
text remains unfinished input; use **Mark unknown** to deliberately clear a value.
Zero is a known value. Closing the editor or switching its saved version waits for
saving; if saving fails, the editor stays open with the error and a retry button.
A service disconnection keeps your input visible. Ordinary browser close/reload
requests a leave warning while input is unsaved; staying lets you retry. Choosing
to leave anyway can lose that unconfirmed input. Native application Quit behavior
has not been separately verified for this warning.

Choose **Use working inputs for next experiment** when ready. This validates and
captures the current inputs without saving a parameter version or moving a branch.
Choose an **Experiment setup** and preview before running. If the working table
changes, explicitly use its current inputs and preview again. Already submitted
experiments keep their exact captured values.

**Save a parameter checkpoint** is optional. Give it a version name and save on
its own or to an explicit branch. Successful saving completes the draft and retains
its history; failed saving leaves the draft active. Opening the working branch
after completion starts from its saved head. Removing a whole known parameter
requires a checkpoint because experiment overrides cannot represent that removal.

Concurrent writes retain conflicting input for review. If a branch changes,
review its new values before choosing to keep your table; this keeps your whole
table and does not merge cells automatically. Draft history lets you inspect and
copy earlier input. Explicit discard ends that draft but retains its history.

**Use for next experiment** on a saved version selects that exact version.
Editing device addresses belongs in **Devices and drivers**, not the parameter form.

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
