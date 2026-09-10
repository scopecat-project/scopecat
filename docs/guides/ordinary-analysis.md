# Ordinary Python analysis

Keep a normal analysis function in your project's configured author source directory.
It receives the existing `Dataset` and returns a standard dataclass. NumPy and SciPy
can do the numerical work; Scopecat publishes the conclusion and its provenance.

```python
from dataclasses import dataclass
import numpy as np
import scopecat as sc
from scopecat.measurements.dataset import Dataset


@dataclass(frozen=True)
class Mean:
    value: float | None
    reason: str


@sc.analysis_function
def mean_response(data: Dataset, *, minimum: float = 0) -> Mean:
    y = np.asarray(data["result"].require_values(), dtype=float)
    selected = y[y >= minimum]
    if not selected.size:
        return Mean(None, "No points pass the selection")
    return Mean(float(selected.mean()), "Selected mean")
```

In the managed notebook session, use the retained run's original source by default:

```python
with project.authoring() as author:
    fitted = author.analyze_as(
        run_id,
        "lab.analyses:mean_response",
        Mean,
        arguments={"minimum": 0.2},
    )
    print(fitted.value.value, fitted.value.reason)
    publication_id = fitted.publication.id
```

After changing a function or its helpers, explicitly call `author.refresh()` and
choose `source="current"` in `analyze_as`. This selects the refreshed source;
it does not change the run or overwrite earlier analysis. A run without retained
author source requires this explicit current-source choice. The low-level `analyze`
method also supports an exact revision for maintenance tools.

The returned `AnalysisResult` contains a materialized `value` and an authoritative
`publication`. A plain `Mean(...)` created locally is only a value. The publication
retains the run input, source revision, requested arguments, effective trace
arguments (including defaults), output identities and publication hash. Repeating
identical content may return the same receipt; changed source or arguments cannot
silently reuse that content. A candidate remains an estimate, not verified calibration.

Reopen without executing analysis again:

```python
with project.authoring() as author:
    fitted = author.run(run_id).published_analysis(publication_id).result_as(Mean)
```

Use the same dataclass structure to read a result. Changed structures are rejected
rather than silently coerced. Supported conclusion fields are the existing fact
schema's scalar, optional, literal, quantity and structured JSON-compatible types;
arbitrary objects, functions and arrays in a conclusion are not pickled. Use
`sc.Quantity` for unit-bearing conclusions. Dataclass field metadata is used for
table columns, not for implicit conversion of conclusion fields.

For aligned arrays, reuse `data.project({...}, units={...}).to_xarray()` or
`data.bind(...)`. These paths preserve point/shot/entity dimensions, units and
missing-value diagnostics. Choose an appropriate layout for ragged data; do not
flatten independent arrays and join by position. `require_values()` rejects missing
values. Your scientific function should distinguish unusable input (raise an
explanatory error) from a scientifically rejected fit (return a status and `None`
candidate). `analyze_as` includes worker diagnostics in raised HTTP error notes.

To analyze locally after disconnect, call `data.materialize()` while the session is
open. This explicitly loads the measurements into memory. A materialized numerical
result remains usable after disconnect; loading publication datasets or artifacts
needs an open session. `mean_response.function(data, minimum=0.2)` is an ordinary
local call and creates no publication or claim of captured helper source.

For extra tables and plots, return `sc.AnalysisProducts(result, datasets=...,
plots=...)`. Tables reuse Arrow, pandas, Polars, one-dimensional Xarray datasets,
or homogeneous dataclass rows; NumPy arrays can be supplied as labeled Xarray
columns. Standard row fields (`bool`, `int`, `float`, `str`, `Quantity`, optionally
`None`) are inferred. Use `dataclasses.field(metadata={"unit": "GHz",
"role": "coordinate"})` for explicit column semantics. Existing `AnalysisField`
annotations continue to select their explicitly annotated columns.

`sc.AnalysisPlot(dataset="curve", x="frequency", y="response")` creates a retained
line view; `kind="scatter"` is also supported. These are views of published data,
not arbitrary serialized plotting objects. See the executable
[quadratic peak fit](../../examples/reference_lab/src/reference_lab/workflows/authored/ordinary_analysis.py)
for a dataclass conclusion, table, plot and flat/no-response rejection.
