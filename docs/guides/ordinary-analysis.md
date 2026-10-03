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

## Analyze an exported file in your own Python environment

Use a Python environment with the matching Scopecat version and your numerical
tools installed. The application, device drivers and original author environment
do not need to run. With the `mean_response` function above:

```python
with sc.open_capture("raw.scopecat", output="analyzed.scopecat") as capture:
    print(capture.run_ids)
    run_id = capture.run_ids[0]  # Choose the intended run if the file contains several.
    publication = capture.analyze(run_id, mean_response(minimum=0.2), key="mean")
    fitted = publication.result_as(Mean)
    print(fitted.value.value, fitted.value.reason)
```

Each successful analysis save writes the result file atomically. Later failures
preserve earlier saved results. The source is unchanged; choose a new destination
when opening the capture. This handle can then update its own output with further
analyses, but it cannot overwrite a pre-existing destination. Open `analyzed.scopecat` from the
application’s **File → Open** command to inspect its retained results and inputs.

The source measurements stay unchanged. Repeating identical analysis in the
session reuses its publication; changing arguments or implementation creates
a new revision under the same key. The result retains input identities, effective
arguments, output identities, a local implementation fingerprint and Python
interpreter description. It does not bundle your entire environment or promise
to reconstruct it. Reading a saved conclusion does not rerun its code:

```python
with sc.open_capture("analyzed.scopecat") as capture:
    fitted = capture.published_analysis("mean").result_as(Mean)
    print(fitted.value)
```

For interactive work, `capture.analysis(run_id, key="fit")` returns the ordinary
`AnalysisContext`: use `measurements()`, `trace()`, and `result()` to publish facts,
datasets, tables, figures or attachments. Its `save()` persists the publication
before returning. Analysis loads the captured logical-point selection
into memory; a file without that selection cannot silently substitute physical
acquisition order. Use the lower-level `MeasurementSnapshot` reader for bounded
record processing. A changed publication currently writes a complete portable
file, including the original recordings. Group related outputs in one result and
save it once; repeated saves of large captures require corresponding disk I/O.

## Analyze a run in the application

In an author session, use the retained run's original source by default:

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

`arguments` accepts JSON values and `Quantity` (also inside lists and string-keyed
mappings). A declared `expected_frequency: sc.Quantity` can receive
`arguments={"expected_frequency": fitted.value.frequency}` directly. The client
encodes values and units as JSON; the worker validates and restores them using the
**selected source version's** annotations and defaults. No arbitrary objects are
pickled. Supported annotations are scalar/optional/literal values, `Quantity`,
and typed lists/string-keyed dictionaries of those values. Unannotated arguments
remain JSON values; annotate quantity inputs to receive native quantities.

The same managed argument rules apply to `@sc.analysis_step` functions taking an
`AnalysisContext`. Use that form when composing several analyses or retaining
additional evidence. An ordinary invocation can run in the supplied context:
`mean_response(minimum=0.2).run(context)`. Both forms retain requested arguments
and effective arguments including the selected source's defaults. Direct local
Python calls can still use native objects such as run handles; the remote argument
boundary does not serialize those objects.

Invalid types identify the function and argument instead of failing at the raw
JSON request boundary. Numeric strings are not silently converted. Requested
units are retained as supplied; the analysis can explicitly convert with `.to(...)`.
Equivalent magnitudes supplied in different units remain distinct requested
arguments and do not silently share a publication identity.

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
[quadratic peak fit](../../examples/reference_lab/src/reference_lab_authors/authored/ordinary_analysis.py)
for a dataclass conclusion, table, plot and flat/no-response rejection.
