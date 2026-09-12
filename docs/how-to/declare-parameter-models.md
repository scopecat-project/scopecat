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

## Existing declarations

Standard dataclass views remain available for ordinary data interoperation.
The new class-based entry is the preferred route for shared experiment parameter
declarations. Existing `ParameterSchema` consumers are not removed by this
integration; migrate their actual readers, initial values and candidate writers
together before removing those legacy handles. No parameter storage migration or
new database is introduced by this API.
