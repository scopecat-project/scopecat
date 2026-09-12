# Review and publish project configuration

For declarations shared by parameter editing and experiments, start with
[parameter models](declare-parameter-models.md). The same workspace also supports
ordinary dataclass and dictionary views described below.


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
row = params["qubits"]["q1"]  # live view, shared with typed views
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
primary-key order. Edit existing scalar parameters with `params.scalars["name"] = value`.
Adding or removing a declaration remains an explicit schema change.

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

The dictionary editor exposes existing scalar values and keyed tables; it does
not expose unkeyed tables for indexed editing. Unknown cells read `None`, and
reads never supply defaults. The dataclass declaration workflow below creates
new tables and stages explicit schema changes. Unit reads retain the stored
representation; deliberate compatible-unit edits remain explicit.
These manual edits do not claim measurement or scientific verification.

## Use ordinary dataclass rows

A lab maintainer can declare familiar Python row types. This example binds an
existing `drive` table whose columns, string key and quantity bounds match the
declaration:

```python
from dataclasses import dataclass
from typing import Annotated
import scopecat as sc


@dataclass
class Drive:
    id: str
    frequency: Annotated[float, sc.ParameterSpec(unit="GHz", minimum=0, maximum=10)]
    amplitude: float | None = 0.25
    enabled: bool = True


params = lab.config.workspace(context="sample-a-parked")
drives = params.table("drive", row_type=Drive)
q0 = drives["q0"]  # editor knows this is a Drive
q0.frequency = 5.2
params["drive"]["q0"]["enabled"] = False
assert q0.enabled is False  # the dictionary and attributes share edits
params.preview()  # validate before saving or running
version = params.save("sample-a-drive-trial")
```

The returned row is a live instance of the declared dataclass type. Plain scalar
fields support `bool`, `int`, `float`, `str` and `sc.EntityRef`; `float | None`
exposes an existing unknown cell as `None`. Reading an unknown required field
reports its table, row and column instead of inventing a value. Use mutable,
data-only dataclasses; selection does not execute constructors or `__post_init__`.
Normal dataclass methods, repr and attribute completion remain available.

Naked float attributes use their declared units: `q0.frequency` above returns GHz
even if the stored value is represented in MHz. A compatible MHz row declaration
must also express its bounds in MHz. Reads never rewrite the stored quantity or
its origin. Naked dictionary edits use the underlying table's units; use
`sc.Quantity(5200, "MHz")` in the dictionary to specify another representation.
Both interfaces are edit buffers: normal assignments do not promise immediate
runtime type or range validation. Use `preview()` or `save()` to validate edits.
`Annotated` metadata does not make float arithmetic dimensionally type-safe.

Defaults apply only through an explicit new-row constructor:

```python
new = drives.add(Drive(id="q1", frequency=5.8))  # amplitude default is 0.25
# Existing missing amplitudes still read None, never 0.25.
if q0.amplitude is not None:
    q0.amplitude *= 0.9
```

`add` refuses to replace an existing key. Declaring an Optional field does not
clear existing values. Explicit `q0.amplitude = None`, or the equivalent dictionary
assignment, clears that cell and its former evidence without changing other
cells' origins. Dictionary rows iterate every declared column; unknowns read
`None`. Stored snapshots continue to omit unknown atoms.

For maintainers, `sc.dataclass_table_schema(Drive, primary_key=("id",))` is a pure
adapter to the existing table schema. It performs no project installation or
migration. Binding requires matching columns, scalar types, units and bounds;
mismatches report the field and require an explicit schema change. Metadata can
also be written as `field(metadata={"parameter": sc.ParameterSpec(unit="GHz")})`.
No Pydantic base class or checker plugin is needed. Editors catch misspelled
attributes, assigning strings to float fields, and arithmetic on an Optional
before checking for `None`.

## Declare a new table and evolve its structure

Start from the lab's supplied sample/workpoint context. You can declare a table
without writing a catalog or Pydantic models:

```python
@dataclass
class Probe:
    id: str
    duration: Annotated[float, sc.ParameterSpec(unit="ns")]
    pi_amplitude: float | None = None


probes = params.declare_table("probes", Probe, key="id")
probes.add(Probe("q0", duration=40))
assert params["probes"]["q0"]["pi_amplitude"] is None
params.structure_diff()  # added table, affected rows, explicit consumer actions
params.save("sample-a-probes")
```

Re-executing an identical declaration is a no-op. A new optional dataclass field
adds a column, leaving every existing row unknown even if the field has an
initializer default. Existing column removal, type/unit changes and key changes
are rejected with the old/new declarations; they are never inferred as renames.

Use explicit operations for supported changes:

```python
params.convert_unit("probes", "duration", "us")
params.rename_column("probes", "duration", "pulse_length")
params.rename_column("probes", "id", "qubit")
params.change_key("probes", key="qubit")
params.structure_diff()
params.save("sample-a-probes-v2")
```

Compatible unit conversion converts stored values and bounds. Key changes must
leave every row with a complete, unique key. Structural operations invalidate
old row views: select a fresh row or bind the updated dataclass after changing
its fields. A removed table created in an unsaved draft disappears on discard.

Save or discard pending **value** edits before staging a structural operation.
After staging structure, you can add rows or edit values and save them together.
Review `structure_diff()` and save a named version **before previewing/running an
experiment** with the new schema. `freeze()` refuses pending structure changes;
it never creates hidden saved versions. Ordinary value edits still freeze
without saving. Reopen earlier versions and runs to read their original schema,
values and source addresses.

Unknowns are permitted at bootstrap and registry import; supplied values, row
keys and infrastructure remain validated. Compiled parameter imports validate
only their declared dependencies. A lookup that needs an unknown column reports
table, key and field and asks you to supply/calibrate it; an experiment using
another column can proceed. This is explicit contract validation, not arbitrary
Python dependency discovery. Known literal-key lookups specialize the selected cell, so another row
with the same unknown column does not block that lookup. Dynamic key expressions
conservatively validate all rows of the imported column; use a concrete key for
a single-sample probe. This slice adds no domain-aware lazy dependency discovery.
Ordinary Python/dataclass consumers must check Optional values themselves.

For maintainers: storage remains project schema 65 with absent cells representing
unknowns. The wire adds `add_table` structure edits and permits `null` in keyed
row **updates**, meaning clear that cell; snapshots do not store null atoms.
New readers still read old snapshots and runs. Client and daemon must use the
same current build; older clients cannot consume the new structure records or
clear intents. This change rewrites no historical records and supplies no
cross-version migration.

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

quality = ParameterDefinition(id="quality", value_type=sc.ScalarType(sc.FloatType()))
plan = ParameterStructurePlan(
    base=context_ref,
    structure_version=parameter_structure_version(saved.config.parameter_catalog),
    edits=(
        AddParameterColumn(
            parameter_id="qubits",
            column=quality,
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


## Notebook and console views

Displaying `params`, `params["drive"]`, or `params["drive"]["q0"]` in a notebook
shows a compact live view. Unknown cells are distinct from zero; quantity values
retain their stored units. HTML views show the saved cell origin where available.
An edited cell is marked as an unsaved manual value, never as a new measurement.
The views cap rows and columns for readability and report the full table size.
Use dictionary access to inspect the complete contents. Rendering does not save,
validate or change a value.

In **Configuration**, **Save working point copy** edits an isolated candidate for
an explicit sample and working point. **Save context** saves a version;
**Use for next experiment** selects that version in the launch form;
**Set as default** explicitly publishes a laboratory default. Refreshing author
code does not publish parameters.

Parameter and structure drafts survive navigation between console sections and
saved entries. Finish or cancel an existing draft before opening another of the
same kind. Reloading or closing the page discards unsaved browser drafts. Failed
saves retain the draft; validation messages name the relevant field and expose
raw diagnostics on demand. An origin label records where a value came from; it
is not a statement that the calibration is valid for the selected sample.
