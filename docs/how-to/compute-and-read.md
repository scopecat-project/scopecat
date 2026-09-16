# Compute a value and read its typed result

Start with a working experiment. `context.compute` records a calculation; it does
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


def mean_iq(
    *, iq: NDArray[np.complex128]
) -> Annotated[complex, sc.ScalarType(sc.ComplexType(unit="ratio"))]:
    return complex(iq.mean())


@dataclass(frozen=True)
class MeanResult:
    iq: sc.DataRef[complex]
```

Inside an experiment, after obtaining `shots`:

```python
mean = experiment.compute(fn=mean_iq, iq=shots)
return MeanResult(mean)
```

There is no separate `output_type` or author-side `cast`. Without a unit, the
return annotation can simply be `-> complex`. The same inference supports `bool`,
`int`, `float` and `str`. Arrays need an `Annotated` `ArrayType` with their dtype,
unit and local dimensions. Native and declared types must agree; a real array
annotation paired with a complex array schema is rejected during construction.

`DataRef[T]` describes a value of type `T` that will exist during execution.
The framework chooses the appropriate computation placement from the inputs;
ordinary result declarations do not need to choose `ValueRef` or `ProductRef`.
This inference applies when `output_type` is omitted. Explicit output schemas and
structured compute bundles retain their existing interfaces. Input keyword names
and contracts are checked during graph construction; this API does not yet provide
static checking of every compute input against the native function signature.

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
Notebook. A direct-call compute decorator and unified Notebook import refresh are
not part of this guide's available API.
