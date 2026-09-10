# Review and publish project configuration

A project's `src/<package>/configuration.py` is ordinary version-controlled
Python. The daemon owns the accepted configuration history; it does not watch or
rewrite that source file.

## Edit a saved working point in Python

For ordinary parameter edits, open a context that your laboratory project has
already installed. The entry name selects an exact sample revision and working
point; no Pydantic models or configuration hashes are needed. The following
example assumes a `qubits` table with a string `id` primary key and numeric
`frequency`/`amplitude` fields:

```python
params = lab.config.workspace(context="sample-a-parked")
params["qubits"]["q0"]["frequency"] = 5.2
params.diff()  # detached before/after edits
reviewed = params.preview()  # validate and freeze these edits
params["qubits"]["q0"]["frequency"] = 5.3  # does not change reviewed
version = params.save("sample-a-trial-1", note="manual trial")
reopened = lab.config.workspace(context=version)
# A new process can use context="sample-a-trial-1" with a new lab connection.
```

`save` creates an immutable named version and advances this workspace's baseline.
Its diff is then empty, and `discard()` returns to that saved baseline. Saving
never changes the laboratory's shared default. A name is a unique registry entry,
not a mutable latest-version pointer; use a new name for another version. An
unchanged workspace can also be saved under a new name. Opening another version
is explicit and does not transfer this workspace's unsaved edits.

`preview()` and `freeze()` return the existing resolved configuration accepted by
run/config APIs. They retain exact sample identity, per-cell origins and only the
edited cells as run overrides. The managed author-session API is a separate
implementation slice; freezing alone does not submit a run or bypass its normal
admission checks. Validation failures leave the buffer editable for correction.

Rows behave like mappings:

```python
params["qubits"]["q1"] = {"frequency": 5.8, "amplitude": 0.2}
row = params["qubits"]["q1"]  # live view, shared with later typed adapters
row["amplitude"] = 0.25
row_copy = dict(row)  # detached copy
another = params.copy()  # independent buffer, including unsaved edits
params.discard()  # restore the baseline
```

Replacing an existing row must retain its existing fields. Row keys cannot be
edited in place; delete and insert the row to change its identity. `del
params["qubits"]["q1"]` removes a row and permanently invalidates views of that
row, even if the same key is subsequently inserted. Views of retained rows stay
live across save, discard and rebase. Composite keys use a tuple in declared
primary-key order. Scalar parameters use `params.scalar("name")` and
`params.set_scalar("name", value)`.

To combine another editor's saved branch, choose it explicitly:

```python
params.rebase(current="sample-a-other-trial")
params.diff()  # only our remaining edits against that branch
params.save("sample-a-combined")
```

Independent-cell changes combine using the existing common-base merge rules.
Same-cell conflicts leave the workspace unchanged and report the parameter/cell
with base, local and current values in structured problem details. Set the
conflicting field to the chosen value and retry. Rebase rejects another sample
revision, working point or parameter schema. There is no automatic lookup of a
newer branch and no silent overwrite of another editor's work.

This first dictionary editor exposes existing scalar values and stored tables
with declared primary keys. It does not create parameter definitions, expose
unkeyed tables for indexed editing, or clear a field to `None`. Existing missing
cells stay missing; reading does not supply defaults. Full unknown tables and
schema evolution belong to the next parameter-structure slice. Unit reads retain
the stored representation; deliberate compatible-unit edits remain explicit.
These manual edits do not claim measurement or scientific verification.

## Review configuration source changes

Validate the source without starting the daemon:

```sh
scopecat config check ./my-lab
```

With the project daemon running, compare a freshly evaluated source snapshot
with the current daemon default:

```sh
scopecat config diff ./my-lab
```

Review the diff, then explicitly publish it with an operator identity and useful
audit note:

```sh
scopecat config apply ./my-lab \
  --actor alice \
  --note "add readout VNA and reviewed defaults"
```

Export a complete JSON snapshot for review or backup:

```sh
scopecat config export ./my-lab --output ./active-config.json
```

The exported JSON is generated state, not the primary editing format. Continue
editing the project's Python configuration source and use `diff` and `apply` for
subsequent changes.


## Propose an entity or field edit

Use an existing typed cell or keyed row update for calibration changes. For
example, the public reference lab proposes one delay with
`Q1_CHANNEL_CALIBRATION[CHANNEL_DELAY].update(1.0)`. The general form is:

```python
edit = sc.update_parameter_rows(
    "channels",
    key={"qubit": sc.EntityRef(id="q1", kind="qubit")},
    values={"frequency": sc.Quantity(5100, "MHz")},
)
analysis.result().propose("q1-frequency", edit, reason="reviewed fit")
```

The catalog validates the key, fields and compatible quantity dimensions. A
candidate retains explicitly supplied quantity units, and leaves other cells'
representations unchanged. Execution resolves quantities into catalog units
transiently. Whole-table replacement remains available for deliberate structural
changes; use cell/row updates when only a calibration field is intended.

New proposal deltas retain entity/key, field, original base and proposed values.
Decision and configuration views distinguish a **physical value change** from an
**equivalent representation**: changing `5 GHz` to `5000 MHz` is still an explicit
reviewable edit, even though its physical value is equal. Older publications
remain readable and show their retained before/after values.

Common-base proposals can compose independent cells. Identical changes to one
cell coalesce. Different representations of that cell produce a representation
conflict even when physically equivalent; different physical values produce a
physical conflict. Both report the original base and competing values. Neither
selects an automatic winner. Unchanged cells and incidental table normalization
do not become calibration changes.

Acceptance continues to require the frozen expected registry generation and
retains proposal provenance. Undo appends an activation of the previous exact
entry; it does not erase acceptance history. Refresh and review again after a
generation conflict rather than silently accepting against a newer base.


## Restore an earlier default

In **Default configuration**, select a saved version. The entry details show
**Restore default** if that exact entry was previously activated, **Accept as
default** for an unactivated derived entry, and **Set as default** for a new
source snapshot. The restore decision comes from the entry's history on the
server, even if its old activation is outside the displayed history page.

From Python, use the existing activation operation with the exact saved entry:

```python
active = lab.config.active()
receipt = lab.config.activate_entry(
    "previously-accepted-entry",
    operation_id="restore-reviewed-working-point-1",
    expected_generation=active.activation.generation,
    note="return to the previously reviewed working point",
)
print(receipt.activation.restored_from_generation)
```

Keep the operation ID with the command. Retry that same command after an
ambiguous response, or call `lab.config.activation_operation(operation_id)` to
read its original receipt. A changed default requires a new review and expected
generation for a new operation. `lab.config.undo()` uses the same operation to
restore the previous distinct entry.

Restoration reuses the exact entry ID, content hash, and original candidate,
manual-edit, or cohort provenance. It appends a new activation whose
`restored_from_generation` points to the most recent activation of that entry.
Selecting the already active entry is a no-op. A never-activated derived entry
still requires its original base to be active; copying a stale entry does not
make it eligible. Physical instrument identity checks also remain in force.

**Restoring parameters does not revalidate the sample or renew calibration
validity.** It creates no scientific acceptance, verification result, calibration
success publication, or device action. Use the experiment's normal verification
journey before relying on restored parameters at a changed working point.

## Keep sample and working-point parameters separate

A parameter context is a saved configuration revision tied to an exact physical
sample revision and an explicit working-point ID. It lives in the configuration
registry alongside other snapshots. Saving or selecting it does not change the
lab default. Two samples can both have a `parked` point and a logical `q0`; those
names do not make them the same physical sample.

In the console, open a configuration and choose **Save working point copy**.
Select the physical sample, name the working point, and edit the values you know.
Use **Mark unknown** for an unknown value. The saved copy retains the selected
sample revision. Choose **Use for next experiment** to launch with that exact
sample and working point; the launch form displays its identity and can switch
back with **Use lab default**. Compare it with another saved revision using the comparison
selector; select an older context again to recover its parameters without
rewriting either copy or any earlier run.

The Python API uses the same registry and resolver:

```python
from scopecat.records.config_context import ConfigContextRef

active = lab.config.active()
base = ConfigContextRef(
    entry_id=active.entry.id,
    content_hash=active.entry.content_hash,
)
copy = lab.config.save_context(
    entry_id="sample-a-parked-1",
    base=base,
    sample=lab.samples.handle("sample-a").selector(),
    working_point_id="parked",
    label="Sample A / parked",
    note="Starting values for an attended exploration",
)
selected = ConfigContextRef(
    entry_id=copy.entry.id,
    content_hash=copy.entry.content_hash,
)
resolved = lab.config.resolve_context(selected)
prepared = lab.prepare(my_experiment(), config=resolved)
prepared.preview()
run = prepared.run()
```

Omitting `parameters` copies the base snapshot. Pass a `ParameterSnapshot` to save
known values or omit unknown parameters and non-key table cells. Supplied values
must still satisfy their catalog types and units; table identity keys remain
required. Missing values block an experiment when its parameter expressions or
compiler actually require them. An unrelated missing field does not prevent a
run that does not use it. The normal complete-configuration path remains strict.

For a trial, pass existing typed `ParameterUpdate` edits to
`lab.config.resolve_context(selected, overrides=(edit, ...))`, then prepare or run
that resolution. Precedence is explicit: the saved base supplies inherited
values, the saved context supplies its edits, and run overrides apply in tuple
order, with a later edit winning if it updates the same cell. Overrides never
modify the saved context. The resolution lists effective values, per-value
origins, and missing values. Runs freeze the resolved snapshot, exact context
reference, typed overrides, and physical sample binding.

Admission checks the selected entry's ID and content hash separately from the
current lab generation. If the lab default changed after resolution, resolve
again before submitting; selecting a context never implicitly activates it.
Historical selection preserves parameters and provenance, and does not grant a
new claim of calibration freshness or evidence quality.

## Change a working point's parameter table structure

A table's parameter and column IDs are semantic identities. Changing an ID is
an explicit rename, not a display-label change or an alias. Structure revisions
use a content hash of the parameter catalog; this is independent of the database
schema version. Saved configuration JSON and old run snapshots are not rewritten.

In **Configuration**, select a saved working point and choose **Change table
structure**. Add an optional column, rename a column, change its type or unit, or
replace its lookup key. Leave an unknown value empty. Provided values require an
imported or estimated origin and a source note. Preview the affected rows and
parameter identities, then save a new working point revision. The lab default
remains unchanged. Select the old saved version to restore its exact structure
for a subsequent experiment.

A small Python declaration uses the same preview and save endpoints:

```python
from scopecat.config.structure import (
    ParameterStructurePlan,
    parameter_structure_version,
)
from scopecat.records.parameter import ParameterDefinition
from scopecat.records.parameter_structure import AddParameterColumn

quality = sc.parameter_field("quality", sc.FloatType())
plan = ParameterStructurePlan(
    base=context_ref,
    structure_version=parameter_structure_version(saved.config.parameter_catalog),
    edits=(
        AddParameterColumn(
            parameter_id="qubits",
            column=ParameterDefinition(id=quality.id, value_type=quality.value_type),
        ),
    ),
)
preview = lab.config.preview_structure(plan)
print(preview.missing_values)
revised = lab.config.save_context(
    entry_id="sample-a-parked-quality",
    base=context_ref,
    sample=lab.samples.handle("sample-a").selector(revision=1),
    working_point_id="parked",
    label="Sample A parked, with quality",
    structure_plan=plan,
    note="Optional analysis column; no measurement has supplied its values yet",
)
```

Missing optional cells remain absent. An existing experiment that does not read
`quality` can run with this context. An experiment that reads an unknown cell
reports that parameter identity; supply the value or revise that experiment.
Malformed supplied values and missing or duplicate primary keys are still
rejected. The ordinary complete-configuration path keeps its existing validation.

For breaking changes, `ChangeParameterColumn` requires one declared policy:
`compatible_unit`, `lossless_numeric`, `explicit_values`, `patch_values`, or
`unknown`. Automatic numeric conversion rejects rounding; unit conversion requires
compatible units.
Explicit `StructureValueDecision` records identify an existing keyed row, or a
row index for a table without a key. A measured declaration must reference an
existing source run; this records the author's evidence claim and does not grant
calibration validity. Conversion and renaming never promote evidence to measured.

Use `patch_values` to change only the cells identified by its value decisions,
including marking a selected cell unknown. Unspecified rows retain their values,
evidence and original source-cell references. In contrast, `explicit_values`
replaces the entire column: rows without a decision become unknown. Neither mode
bypasses the column's final type/unit validation; changing the type while retaining
incompatible values is rejected. For example, this declares one estimated value
without replacing other rows:

```python
from scopecat.records.parameter_structure import (
    ChangeParameterColumn,
    StructureValueDecision,
)

ChangeParameterColumn(
    parameter_id="qubits",
    column=ParameterDefinition(id="quality", value_type=quality.value_type),
    conversion="patch_values",
    values=(
        StructureValueDecision(
            key={"qubit": sc.EntityRef(id="q0", kind="logical_qubit")},
            value=0.8,
            origin="estimated",
            note="Initial estimate pending measurement",
        ),
    ),
)
```

The preview reports changed column and key identities. Python callers can pass
named `StructureConsumer` records containing existing typed parameter contracts
for experiments or analyses; the compiler's contract validator reports their
incompatibilities. This list is explicit: arbitrary Python, compiler, driver and
analysis code is not automatically inventoried. Preview the affected experiments
before using the new context.

Operations are ordered and run against an exact base entry and content hash.
Saving revalidates the declaration; a changed or conflicting base cannot silently
replace the draft. Per-cell metadata distinguishes the current column/key from
the exact old source entry, column and key, including successive renames.
For a split or merge, explicitly compute and supply the intended new values in
Python and preview the resulting additions and type changes. There is no generic
split/merge engine or automatic source-column deletion in this release; retain
the old columns until dependent author code has been updated.
