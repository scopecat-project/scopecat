# Declare parameters once for editing and experiments

Use a `ParameterModel` when the same fields appear in parameter editing,
experiment inputs and analysis candidate targets. A class attribute identifies a
field; an instance reads and writes concrete values. No separate field constants,
row constants or manually assembled lookup types are needed.

```python
import scopecat as sc


class Drive(sc.ParameterModel, table="drive"):
    qubit: sc.Param[str] = sc.param(key=True)
    duration: sc.Magnitude[float] = sc.quantity(unit="ns", default=64, minimum=4)
    pi_amplitude: sc.Param[float | None] = sc.param(default=None)
```

`Param[T]` reads/writes `T`. `Magnitude[float]` reads/writes numbers in its declared
unit; its experiment reference carries a `Quantity`. Optional fields describe
unknown editing values, not nullable numeric experiment inputs. The type system
does not prove physical unit dimensions or numeric bounds; save/preview uses the
existing schema checks.

## Declare choices and initial project values

Use `Param[Literal["constant", "cosine_flat_top"]]` for a closed set of strings.
The same annotation supplies editor completion and the persisted choice constraint.
`sc.param(default="constant")` only initializes new rows. A model's docstring is
its table description.

A laboratory maintainer can build the initial catalog and snapshot directly:

```python
catalog = sc.parameter_catalog("lab-parameters", Drive)
initial = sc.parameter_snapshot(
    "lab-initial",
    tables={Drive: [Drive(qubit="q0"), Drive(qubit="q1", duration=80)]},
)
```

These return the existing durable records, ready for the laboratory configuration
builder. An empty list creates an empty table; optional unknown cells remain
absent. Unit conversion, key validation and choice/bound checks reuse the existing
configuration validation. This does not install a default or alter a project.

## Create or edit a table

Given an existing author session and named parameter context:

```python
params = author.config.workspace(context="my-working-point")
drive = params.declare_table(Drive)
drive.add(Drive(qubit="q0"))
print(params.structure_diff())
version = params.save("drive-start")

row = params[Drive]["q0"]
row.duration = 80
row.pi_amplitude = None
print(params.diff())
```

Creation is explicit. On subsequent opens use `params[Drive]`; do not add the
same row again. Bound rows share edits with `params["drive"]["q0"]`. Reads convert
units for display without rewriting stored values. Defaults only initialize new
objects; selecting an existing row never fills unknown cells.

Both dataclass and model table views support assignment and deletion:

```python
params[Drive]["q1"] = Drive(qubit="q1", duration=96)
del params[Drive]["q1"]
```

The supplied key must match the object's key. Editing an existing key directly is
rejected. Deleting a row invalidates previously selected row views. Renames, key
changes and unit conversions still use the explicit workspace structure APIs;
changing a Python class alone never migrates saved data.

## Inspect and copy rows

```python
row = params[Drive]["q0"]
print(row)
detached = row.copy()
detached.duration = 96
# The original workspace is unchanged until this explicit replacement.
params[Drive]["q0"] = detached
```

`row.copy()`, `copy.copy(row)` and `copy.deepcopy(row)` produce independent model
values. Unknown required fields display as `<unknown>` and remain unknown in a
copy; reading one still raises a useful error. Constructor defaults never fill
copied unknown cells. A bound view rejects a changed primary key rather than
silently using a different lookup from the model's experiment references.

Edit existing scalar values with `params.scalars["repetitions"] = 128`; these use
the same diff, freeze and save path as table edits. Schema additions are explicit.

## Use fields in an experiment

```python
@sc.experiment(id="lab.duration")
def duration_probe(context: sc.ExperimentContext) -> sc.ValueRef[sc.Quantity]:
    return sc.parameter_ref(Drive.duration, "q0")
```

`parameter_ref` builds the existing symbolic lookup. It does not read the active
default or capture a Notebook variable. The existing preview/submission path
binds it to the selected frozen configuration. Later edits cannot alter a prepared
run. A field is required only when consumed; an unknown pi amplitude does not
block this duration-only experiment.

For a composite key, pass values in declaration key order, for example
`sc.parameter_ref(Pair.duration, ("q0", "q1"))`. Each key component can also be an
existing supported symbolic scalar input. Inherited fields resolve against the
selected subclass's table; defining another class does not modify the old class
or its retained references.

## Target a retained analysis candidate

With a managed analysis result whose `amplitude` field is accepted as a value:

```python
candidate = author.config.stage(
    fit,
    name="pi-fit",
    table=Drive,
    key="q0",
    fields={Drive.pi_amplitude: "amplitude"},
)
```

The target field supplies its identity, not evidence. The saved analysis receipt
remains authoritative, `None` cannot be staged, and independent verification is
still required before publishing a default. A field from another table is
rejected. See [candidate verification](verify-parameter-candidates.md).

Numeric results mapped to a `Magnitude` field use that field's declared unit;
`Quantity` results retain their explicit physical unit. Editing and candidate
mapping check declarations against the stored schema using the same compatibility
rule. Managed receipts remain the source of the actual result values.

## Maintainer integrations

Use `sc.parameter_table_ref(Drive)` for a compiler/policy input that needs the
complete frozen table, and `sc.parameter_definition(Drive.duration)` when an
explicit schema edit needs that column's definition. Specialist analysis code can
build a proposed cell edit with `sc.parameter_update(Drive.duration, "q0", 96)`;
it enters the existing proposal/review path and does not activate a default.

Standard dataclass views remain available for data interoperation. Shared
laboratory declarations use `ParameterModel`. The former `parameter_field`,
`parameter_schema`, field/key/assignment/row/cell handles have been removed after
migrating their readers and writers. Existing snapshots and retained runs keep
their durable representation; removing Python handles does not migrate storage.
