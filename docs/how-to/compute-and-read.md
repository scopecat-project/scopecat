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
class MeanResult:
    iq: sc.DataRef[complex]
```

Inside an experiment, after obtaining `shots`:

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

Here `output` is the invocation's `.output`, a `MeanResult`, and `means` has type
`tuple[complex, ...]`. Binding checks the references against the saved dataset.
Unavailable values raise a diagnostic; use `where_available` when deliberately
selecting usable points. A complex value cannot be assigned to a `float` without a
type-checking error.

A dataclass containing references is a result schema, not a dataclass containing
already materialized values. Explicit row construction is still needed when you
want a native dataclass for every point. Fully automatic dataclass materialization
is not provided by this interface.

After a restart, `session.run(run_id).result()` reads the persisted return paths
without importing the old experiment. That source-independent view is not given a
static Python dataclass type automatically. Do not rebuild a changed experiment
and assume its output describes an older run. See
[measurement data](use-measurement-data.md) for historical reading and projections.

## Edit and diagnose

Follow [author refresh](refresh-author-code.md) after editing calculation code.
Refreshing server source does not replace declarations already imported into a
Notebook. Unified Notebook import refresh is not yet part of this guide's available API.
