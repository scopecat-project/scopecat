# Edit parameters on a branch

A revision is immutable declarations and values. A branch names an editing
history. Neither owns equipment nor establishes calibration validity.

With an initial parameter revision and a selected executable setup:

```python
lab.parameters.create_branch("chip-a/daily", revision=initial)
session.use(parameter_branch="chip-a/daily", operator="Alice")
params = session.params

# The same dictionary/typed-table editor used in the tutorials.
params["drive"]["q0"]["frequency"] = 5.15
params.diff()
version = params.save(note="Update readout calibration")
prepared = session.prepare(experiment(), parameters=params)
```

For editing alone, `params = lab.parameters.workspace("chip-a/daily")` needs no
setup, sample or working point. Typed row views, scalars, declarations, schema
changes, table import/export, `diff()`, `copy()` and `discard()` share the existing
parameter editor implementation.

Ordinary `params.save()` generates a revision ID and advances the current branch.
An unchanged ordinary save returns the existing version. Saving never changes
the lab default. `params.save("chip-a/trial")` forks and selects a new branch,
leaving the original branch unchanged. Inspect history with
`lab.parameters.history("chip-a/daily")`; read an exact version with
`lab.parameters.get(version.id)`.

Checkout captures an editing base. If another writer advances the branch,
saving reports a conflict without losing the local draft or saving an orphan
revision. Call `params.rebase()` to merge non-conflicting cells from the latest
head. Conflicting cells leave the entire draft unchanged. A changed catalog or
pending structure edits require explicit review rather than automatic schema
merging. Re-selecting the branch through `session.use(...)` starts a fresh checkout;
it does not merge the old editor's unsaved values.

An unchanged retry after a lost response reuses the pending save command.
Independent copies retain their editing base and do not move the original
session's selection when saved.

## Prepare without saving value edits

`session.prepare(request, parameters=params)` captures unsaved **value** changes as
run-only overrides. Previewing does not write a revision or advance a branch.
Later edits to `params` do not change an existing preview. Runs and saved plans
retain the exact base parameter/setup references and overrides.

Save **structure** changes before preparing an experiment, as with the earlier
editor. When `parameters=params` is omitted, session preparation uses the selected
saved revision; it does not silently include an editor's unsaved buffer.

## Scientific context remains separate

Session branch selection and explicit branch-editor preparation preserve the
subject, batch, operator and record collection. Selecting another sample/target
drops the checkout unless a branch is also explicitly selected. Choosing a saved
parameter revision or working point exits branch mode. Branch names currently
carry no enforced sample/cooldown applicability or calibration acceptance.

The low-level `checkout(...).save(catalog=..., parameters=...)` remains available
for programmatic full-snapshot producers; ordinary authors use `params.save()`.
The old `session.config.workspace(context=...)` still serves maintained
working-point consumers, but new teaching uses the independent branch backend
without fabricating samples or working points.

Default branch selection, scientific working-point consolidation, graphical
branch management and equipment/target/binding separation remain follow-up work.
No prebaseline data migration or historical-file rewriting is introduced.
