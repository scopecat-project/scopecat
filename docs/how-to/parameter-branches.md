# Work on a parameter branch

A revision is immutable declarations and values. A branch gives a sequence of
revisions a stable name. Neither owns equipment, establishes calibration validity
nor changes the laboratory default.

With an initial parameter revision and a selected executable setup:

```python
lab.parameters.create_branch("chip-a/daily", revision=initial)
session.use(parameter_branch="chip-a/daily", operator="Alice")

values = session.parameter_branch.revision
# Edit values.catalog / values.parameters using your parameter-authoring code.
session.parameter_branch.save(
    catalog=values.catalog,
    parameters=updated_parameters,
    note="Update readout calibration",
)
# Future preparations use the revision just saved.
prepared = session.prepare(experiment())
```

Saving generates the immutable revision ID and advances only the checked-out
branch. No setup reference or version name is needed for each save. The previous
revision remains available. Inspect history with
`lab.parameters.history("chip-a/daily")`.

Checkout captures an editing base. Another notebook may advance the same branch,
but it cannot silently replace this notebook's values. A save from a stale base
reports a conflict and creates no revision. Inspect the other change before
checking out again with `session.use(parameter_branch="chip-a/daily")`; merge
decisions are explicit, and automatic table merging is not implemented.

The same checkout can be used without an author session:
`draft = lab.parameters.checkout("chip-a/daily")`, followed by `draft.save(...)`.
An unchanged retry after a lost response reuses the pending save command.
Create a separate experiment branch with `create_branch("chip-a/trial",
revision=draft.head.revision)`.

Session branch selection preserves the current subject, batch, operator and
record collection, except fields explicitly changed in the same call. Selecting
another sample/target drops the checkout unless a branch is also explicitly
selected. Selecting `parameters=revision` or a working point exits branch mode.
Branches currently have no enforced sample, cooldown or working-point scope:
their names are labels, not scientific applicability checks.

Prepared requests and saved plans contain exact parameter references. Later
branch changes cannot alter those inputs. Run provenance currently retains the
exact parameter/setup revisions, not a branch-name attribution. Branch following
across sessions, automatic merging and a graphical branch editor are not provided.

A working point describes an experimental condition, while branches organize
parameter editing. A default should select a branch for new sessions, not be
changed as a side effect of saving. Existing working-point/default APIs remain
transitional; this feature does not rename or convert their old records.
