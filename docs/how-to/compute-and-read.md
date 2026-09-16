# Compute a value and read its typed result

Start with a working experiment. A function decorated with `@sc.compute` records
a calculation when called in an experiment or module definition; it does
not execute the function while defining the experiment. At execution, the function
receives native Python or NumPy values. Ordinary Python control flow belongs inside
that function; a measurement reference is not a value you can inspect during
experiment definition.

## Infer a scalar result

```python
from dataclasses import dataclass
from typing import Annotated

import numpy as np
from numpy.typing import NDArray
import scopecat as sc


@sc.compute
def mean_iq(
    iq: NDArray[np.complex128],
) -> Annotated[complex, sc.ScalarType(sc.ComplexType(unit="ratio"))]:
    return complex(iq.mean())


@dataclass(frozen=True)
class MeanResult[T]:
    iq: T
```

Declare the experiment's return type as `MeanResult[sc.DataRef[complex]]`.
Inside its body, after obtaining `shots`:

```python
mean = mean_iq(shots)
return MeanResult(mean)
```

To test the numerical function with real arrays, call its explicit native entry:

```python
assert mean_iq.eager(np.array([1 + 2j, 3 + 4j])) == 2 + 3j
```

Calling `mean_iq(...)` outside a definition raises an error directing you to
`.eager(...)`. Inside a definition, even an all-literal call records a node; it
does not silently switch to immediate execution. A native function body receives
real values and can use ordinary NumPy/SciPy code. Calls to other decorated
helpers inside that body should use their `.eager(...)` entry too.

There is no separate `output_type` or author-side `cast`. Without a unit, the
return annotation can simply be `-> complex`. The same inference supports `bool`,
`int`, `float` and `str`. Arrays need an `Annotated` `ArrayType` with their dtype,
unit and local dimensions. Native and declared types must agree; a real array
annotation paired with a complex array schema is rejected during construction.

`DataRef[T]` describes a value of type `T` that will exist during execution.
The framework chooses the appropriate computation placement from the inputs;
ordinary result declarations do not need to choose `ValueRef` or `ProductRef`.
Direct calls support positional-or-keyword and keyword-only parameters, including
defaults. Positional-only parameters, `*args` and `**kwargs` are rejected when
decorating. The result type is statically preserved, and `.eager` retains full
native argument checking. Symbolic argument names and required arguments are
checked during graph construction; symbolic calls do not yet statically transform
each native argument type into its corresponding reference type.

`experiment.compute(fn=ordinary_function, ...)` remains available for explicit
node IDs, output schemas, structured bundles and axis inheritance. When its
`output_type` is omitted it also preserves the native return type. If reusing a
decorated function there, pass `fn=mean_iq.eager`.

Calculations should return data without changing inputs or controlling devices.
This decorator does not trace individual Python operations, compile NumPy for an
instrument, or provide data-dependent device control flow.

This mean reduces all axes of the array supplied at **one experiment point**. It
does not average the experiment's scan points together. If the array contains
multiple local axes, select the intended NumPy axis in the function explicitly.
Saving only the mean discards the shot distribution: retain raw shots or sufficient
statistics when later analysis needs uncertainty or significance estimates.

## Keep the type when reading

Given the symbolic result returned by the matching experiment invocation:

```python
view = run.result(output)
means = view.rows(lambda point: point.value(view.output.iq))
```

Here `output` is the invocation's `.output`, a `MeanResult[sc.DataRef[complex]]`, and `means` has type
`tuple[complex, ...]`. Binding checks the references against the saved dataset.
Unavailable values raise a diagnostic; use `where_available` when deliberately
selecting usable points. A complex value cannot be assigned to a `float` without a
type-checking error.

A dataclass containing references describes values that will be produced. For
native rows, including after a restart, select the reading type explicitly:

```python
rows = session.run(run_id).result().rows_as(MeanResult[complex])
print(rows[0].iq.real, rows[0].iq.imag)
```

`rows` has type `tuple[MeanResult[complex], ...]`. The same generic dataclass gives
the symbolic and native versions their field names without pretending a reference
is already a complex number. An independent native dataclass also works; there is
no requirement to import the experiment's original Python module. The reader
validates the complete persisted field paths and dtype, including nested dataclasses,
before constructing rows. A renamed field, missing field or scalar/array mismatch
raises instead of filling defaults or silently discarding data.

Plain `complex` reads the recorded magnitude in its stored unit. To assert the
unit as well, reuse an annotated type:

```python
type MeanIQ = Annotated[complex, sc.ScalarType(sc.ComplexType(unit="ratio"))]
rows = session.run(run_id).result().rows_as(MeanResult[MeanIQ])
```

For arrays, use `NDArray[np.complex128]` (or another supported concrete dtype).
`Annotated[..., sc.ArrayType(...)]` additionally checks the exact stored unit,
local axis names, rank and requested extents. Array axis names may be full persisted
dimension IDs or their local names. An omitted dimension kind or extent is
unconstrained. `Quantity` reads a real numeric scalar together with its stored unit.
This API does not convert units or narrow numeric types; request a compatible
reader or explicitly transform values afterwards.

`rows_as` materializes the selected rows. Arrays remain read-only; missing values,
partial array masks and segmented arrays cannot become apparently complete native
rows. Use `result.where_available().rows_as(...)` when deliberately selecting
complete points, or the labeled dataset interface when you need masks and ragged
segment diagnostics. Supported readers use ordinary init fields, nested/parameterized
dataclasses and supported native leaf types; arbitrary unions, object fields and
recursive dataclasses are not inferred.

Do not rebuild a changed experiment and assume its output describes an older run.
See [measurement data](use-measurement-data.md) for historical reading and projections.

## Edit and diagnose

Follow [author refresh](refresh-author-code.md) after editing calculation code.
Refreshing server source does not replace declarations already imported into a
Notebook. Use `experiment = authors.refresh(experiment)` to explicitly rebind a
typed declaration and its local helpers to the newly admitted revision.

## Fixing a failed computation

Exceptions from an author computation identify the compute operation, exception
type and message in the run failure shown by the Notebook. The full traceback
remains in the worker log. Fix the function, call
`experiment = session.refresh(experiment)`, and prepare a new request. The failed
job and earlier successful results retain their original identities; refreshing
does not resubmit a failed acquisition or rewrite its data.
