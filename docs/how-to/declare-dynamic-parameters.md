# Start with a dynamic parameter table

An exploratory Notebook can declare a table without a Python class. Given an
existing author session and named working context:

```python
import scopecat as sc

params = author.config.workspace(context="my-working-point")
drive = params.declare_table(
    "drive",
    key="qubit",
    columns={
        "qubit": str,
        "duration": sc.column(float, unit="ns", minimum=4),
        "amplitude": float | None,
    },
)
drive["q0"] = {"duration": sc.Quantity(64, "ns"), "amplitude": None}
print(params.structure_diff())
print(params.diff())
version = params.save("drive-start")
```

Types are explicit even for empty tables and entirely unknown columns. Use
`bool`, `int`, `float`, `str`, string `Literal` choices, or `EntityRef`;
`sc.column` adds units, bounds or an entity kind. Composite keys use a tuple of
column names. Primary keys cannot be optional. Dictionary rows retain explicit
`Quantity` values; declaring a unit does not turn their reads into bare numbers.

On subsequent opens select `params["drive"]`. Repeating an identical declaration
is harmless, but inserting the same key replaces that row. Review before saving.

## Extend an exploration

Save pending value edits before changing the structure:

```python
params.add_column("drive", "quality", float | None)
params.declare_scalar("attempts", sc.column(int, minimum=1), value=3)
print(params.structure_diff())
params.save("drive-extended")
params.scalars["attempts"] = 5
```

Added columns must be optional: existing rows start unknown, never at an invented
zero. A new scalar requires an explicit initial value; declaring an existing
scalar is rejected. Renames, unit conversion and key changes use the existing
workspace structure operations. Changing a declaration does not implicitly
rewrite data. Saves create named contexts and do not publish the laboratory
default. [JSON and pandas exchange](exchange-parameter-tables.md) can populate a
declared table; imports do not infer a new schema.

## Build symbolic references

```python
duration = params["drive"].ref("duration", "q0", as_type=sc.Quantity)
```

The reference carries the table's column type, unit and key metadata. It does not
read the current cell, so constructing a reference to an unknown value is allowed;
consuming that unknown value still fails the existing experiment validation.
The selected run configuration supplies the value. Later schema edits do not
change an already constructed reference. Pass references into experiment-building
code rather than capturing a live workspace in portable experiment definitions.

`as_type` checks and narrows the Python result type. Without it the static type is
`ValueRef[object]`, because a dynamic column name cannot give a type checker its
result type. Composite keys use a tuple in declared key order; supported symbolic
key inputs remain symbolic.

## Adopt a model when the experiment stabilizes

```python
class Drive(sc.ParameterModel, table="drive"):
    qubit: sc.Param[str] = sc.param(key=True)
    duration: sc.Magnitude[float] = sc.quantity(unit="ns", minimum=4)
    amplitude: sc.Param[float | None] = sc.param(default=None)
    quality: sc.Param[float | None] = sc.param(default=None)


row = params[Drive]["q0"]
row.duration = 80
ref = sc.parameter_ref(Drive.duration, "q0")
```

A matching model binds to the same stored table without copying or migration.
Existing unknowns remain unknown; constructor defaults do not fill saved cells.
The model adds attribute completion and statically typed references. See
[parameter models](declare-parameter-models.md) for shared laboratory declarations.
