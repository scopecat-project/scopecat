# Measurement data workflows

Scopecat exposes recorded measurements as one labeled `Dataset`. Its schema
comes from the experiment result: variable identities, roles, data types,
units, point-domain layout, local dimensions, and recording groups are retained
without asking notebook code or the GUI to infer them from values.

The [experiment dataflow model](../concepts/experiment-dataflow.md) explains what becomes
durable and how to declare grids, point clouds, repeat, and traversal. This guide
starts after records exist and explains how to inspect, select, and convert
them.

## Understand the recorded shape

Every dataset has a `point` dimension. Scalar variables have one value per
logical point. An acquisition trace or waveform adds its declared local
dimensions after `point`; coordinates and observables in the same recording
group stay aligned on those dimensions.

The schema also records whether points came from a `product_grid` or a
`point_cloud`. Consumers use that declaration rather than guessing a grid from
coincidentally regular coordinate values. Repeat is an ordinary typed point
coordinate when present. Physical traversal never changes logical point
identity or durable row order.

Unavailable data retains its declared dtype, unit, and shape together with a
typed reason such as `missing`, `invalid`, or `overload`. It is not replaced by
an arbitrary sentinel. Point-local experiment compute propagates unavailable
measured inputs without invoking the user kernel; filtering those rows remains
an explicit analysis decision.

### Entity-indexed results

Homogeneous results returned for several entities are recorded as one variable
with an entity dimension, rather than as one variable per entity. For example,
a returned `PerEntity[NetworkSweepProducts]` produces one `frequency` variable
and one `s_parameter` variable. Their shape begins with
`(point, logical_device, ...)`, and the durable dimension index stores the
ordered `(kind, id)` identities and the product source corresponding to each
position.

`invocation.output` preserves the experiment function's authored return type,
including `PerEntity` identity mappings. `invocation.result_ref(path)` exposes
the durable handle for a returned path; use `entity_result_ref(path)` for a
statically typed array `RecordRef`. This keeps static authoring types honest
while the recording boundary turns homogeneous product mappings into data axes.
Heterogeneous mappings continue to expand as structured result paths.
When a grouped value is not part of the return tree, select the same layout
explicitly with
`experiment.stack_entities(products, record_id="readout", axis="qubit")`.

Select entities by identity rather than position:

```python
q1 = data.sel(logical_device="q1")
xds = data.to_xarray()
assert xds.coords["logical_device"].values.tolist() == ["q0", "q1"]
```

Entity identity is always the complete `(kind, id)` pair. A homogeneous axis
uses readable ids as its Xarray coordinate labels; a mixed-kind axis uses a
collision-free canonical identity key and keeps the readable ids separately in
`scopecat_entity_labels_json`. The ordered axis also has a stable fingerprint.
Authored entity `RecordRef` handles bind that fingerprint, so a handle cannot be
silently reused after an entity axis has changed.

Align datasets before comparing entity-indexed variables:

```python
left, right = left.align_entities(right, "logical_device", join="inner")
# join="outer" keeps the union in left-first order and masks absent leaves.
```

The default `join="exact"` requires the same identities in the same order.
`inner` keeps shared identities in the left dataset's order. `outer` appends
right-only identities, records `missing` availability for absent fixed-shape
array leaves, and stores null product-source and acquisition-evidence entries
for those positions. Use `reindex_entities(dimension, entities)` when one
explicit target order is already known. For an absent entity-local ragged
value, outer alignment inserts a `MeasurementUnavailable` segment with an
unknown local extent. It contributes no observations while preserving the
requested entity position and its missing-data reason.

For unit-bearing scalar or array variables, `magnitudes("mV")` performs the
same linear conversion while preserving array shape and partial-value masks;
`require_magnitudes("mV")` additionally rejects whole unavailable rows.

When only some entity, shot, or sample leaves fail, the value remains a
`MeasurementArray` with a read-only boolean availability mask. Native Dataset
access returns a NumPy masked array, Xarray emits `<variable>__valid` and
`<variable>__unavailable_reason` arrays on the same dimensions, and Arrow uses
null leaves inside the nested array. A whole-value failure still uses
`MeasurementUnavailable`; all-success values carry no mask or diagnostic
sidecar.

### Variable-length results

An acquisition axis with no fixed extent is ragged:

```python
from scopecat.sdk.instruments.declarations import (
    acquisition,
    array_result,
    axis,
    result_schema,
)


@result_schema
class CaptureResults:
    samples = array_result(dtype="float64", axes=("sample",))


@acquisition(results=CaptureResults, axes={"sample": axis()})
def capture_until_trigger(self) -> None: ...
```

Each point may return a different number of samples. Scopecat still validates
rank, dtype, and unit; only the ragged dimension's extent may vary. Use
`axis(size=1024)` for a fixed extent or `axis(size="points")` when instrument
state determines one fixed extent.

Ragged arrays preserve each point-local shape and are never padded. The native
Xarray view carries parent-point and local-index coordinates, pandas point
layout keeps each array in one cell, and an observation projection emits one
row per local sample.

An entity-indexed ragged variable is represented by
`MeasurementSegmentedArray`. Its `segments` stay aligned to the entity axis;
each segment is either one rectangular `MeasurementArray` or a
`MeasurementUnavailable` value with its own local shape. The aggregate `shape`
keeps the entity count and reports `None` where segment extents differ or are
unknown. Flattened `values` and `availability` remain available for native
observation processing, while `segments` preserve the boundaries needed for
per-entity inspection and Arrow round trips.

Available `MeasurementArray.values` are read-only, C-contiguous NumPy arrays
with the declared dtype. Available scalar values are normalized to the declared
Python type. Complex values are native `complex` in Python and `{real, imag}`
objects in JSON and Arrow structures.

If unavailable data hides a ragged extent, an aligned coordinate or observable
in the same recording group may supply it. When no sibling knows the extent,
the point contributes zero observations and retains an unknown local extent.
Variable-length axes currently belong to typed instrument acquisitions;
domain-program result axes remain fixed-size.

## Read and select measurements

`run.measurements()` returns a schema-backed, lazy facade:

```python
data = run.measurements()

data.coords
data.data_vars
data.point_indices
data["readout.dc_bias"].values

near_zero = data.sel(
    **{"readout.dc_bias": sc.Quantity(0.0, "V")},
    method="nearest",
)
subset = data.isel(point=slice(0, 20), sample=slice(10, 50))
ragged_window = data.isel_ragged(
    sample=slice(10, 50),
    group="readout",
)
valid = data.where(data["temperature"].is_available())
groups = data.groupby("amplification")
```

The facade binds the manifest schema immediately and loads exact records only
when an operation needs them. Schema inspection and projected Arrow reading do
not materialize the complete run. Once row access materializes a snapshot, the
facade detaches it from the session and reuses it. Until then, row access and
Arrow iteration require the project connection that created the run handle to
remain open.

Materializing records does not construct an Xarray view. That view is built once
on demand by `to_xarray()`, variable `.xarray`, or selections that use Xarray;
Arrow exports and ordinary record access avoid that allocation. Public Xarray
exports still return detached copies. Checks specific to Xarray alignment, such
as matching point-local extents within a ragged recording group, occur when the
view is requested.

Exact coordinate selection retains every match, including duplicate
point-cloud coordinates. Numeric selection accepts unit-aware quantities and
an optional nearest tolerance. `isel(...)` accepts `point` and fixed local
dimensions without dropping dimensions. `isel_ragged(...)` applies an indexer
inside each point and requires either an aligned recording group or one
ungrouped variable. Boolean masks compose with `&`, `|`, and `~`.

When code starts from the experiment's returned value, use the corresponding
typed view:

```python
result = run.result(spectrum().output)
complete = result.where_available(result.output.temperature)
rows = complete.rows(build_fit_row)
```

`run.result(authored_output)` binds original typed handles when recording keeps
the same tree shape. If automatic entity stacking transformed the return tree,
use `invocation.entity_result_ref(path)` for direct dataset access or
`run.result()` and its persisted result paths. The latter does not rebuild the
experiment. All variants expose the same dataset as `.dataset`; use
`run.measurements()` directly when work starts from
dataset variables instead of the experiment's return tree.

Analysis receives this same facade through `context.measurements()`. Accessing
it records the exact measurement snapshot dependency. Run artifacts and JSON
entries are separate published content, not an alternative measurement API.

## Project into ecosystem libraries

Before handing data to another library, bind external names and target units
once with `project(...)`. Selectors may be durable variable IDs, typed result
handles, or persisted result paths:

```python
projected = result.project(
    {
        "bias_v": result.output.dc_bias,
        "temperature_mk": result.output.temperature,
        "s21": result.output.trace.s_parameter,
    },
    units={"bias_v": "V", "temperature_mk": "mK", "s21": "ratio"},
    diagnostics="reason",
)

assert projected.units == {
    "bias_v": "V",
    "temperature_mk": "mK",
    "s21": "ratio",
}
arrow = projected.to_arrow()
pandas_frame = projected.to_pandas()
polars_frame = projected.to_polars()
xarray_dataset = projected.to_xarray()
```

This projection is an adapter boundary, not an analysis framework. Fit, filter,
group, and plot with pandas, Polars, Xarray, NumPy, SciPy, or domain libraries
after conversion. `ProjectionSchema` retains every external name's durable
variable ID, typed-result path, dtype, dimensions, role, unit, label, and
recording group. Arrow carries those semantics in schema and field metadata.

The conversion rules are fixed:

| Scopecat value | Arrow / Polars | pandas `numpy` backend | Xarray |
| --- | --- | --- | --- |
| point scalar | declared scalar type | native scalar column | `point` variable |
| point-local array | fixed or variable list | NumPy array per row | named local dimensions |
| partially available array | nested values with null leaves | NumPy masked array | fill values plus element validity/reasons |
| `complex128` | `{real, imag}` struct | native complex scalar or array | native complex array |
| unavailable | null plus optional diagnostics | missing value plus optional diagnostics | fill value plus diagnostics |

`to_pandas()` defaults to familiar NumPy/native magnitudes. Pandas does not
give those values a reliable unit dtype, so choose the output unit of every
unit-bearing field explicitly at the projection boundary. Selecting the same
unit as the source is meaningful: it records that the magnitude unit was an
intentional public analysis choice. If a unit is inherited instead,
`to_pandas()` warns and lists the affected columns with their actual units.
`projected.implicit_units` exposes the same check without materializing a
frame. Use `dtype_backend="pyarrow"` when pandas should retain Arrow extension
dtypes.

The declared coordinate unit is the experiment-facing scientific unit, not a
hardware transport preference. For example, a T1 axis may remain in `us` while
a target converts each value to integer `ns` samples during binding. Do not
normalize an authored axis to a backend unit merely to simplify lowering;
doing so also changes the default measurement and projection unit.

`diagnostics="reason"` emits a stable `<name>__unavailable_reason` column for
every selected field; `"full"` also emits JSON metadata. Identity columns are
included by default and may be omitted with `identity=False`.

Projection options are supplied together so names are checked against final
identity, diagnostic, and layout columns:

```python
observations = result.project(
    {
        "bias": result.output.dc_bias,
        "frequency": result.output.trace.frequency,
        "s21": result.output.trace.s_parameter,
    },
    units={"bias": "V", "frequency": "Hz", "s21": "ratio"},
    diagnostics="reason",
    identity=True,
    layout="observations",
)
```

`layout="points"` keeps one row per experiment point and represents local
arrays as nested Arrow columns or NumPy arrays in pandas. The explicit
`"observations"` layout expands one aligned local-array group into scalar rows:
point scalars are broadcast, local dimensions receive `<dimension>_index`
columns, and array coordinates and observables remain aligned. Selected arrays
must have identical local dimensions and one recording group; incompatible
selections are rejected instead of independently exploded.

For the pandas NumPy backend, scalar integers, booleans, and strings use pandas
nullable extension dtypes even when one batch contains no nulls. Floating
values use `float64`; diagnostics distinguish an unavailable NaN from an
available scientific NaN. Complex values stay native Python or NumPy complex
values, with `None` for unavailable scalar observations.

### Read large projections incrementally

Use the same projection to create a finite Arrow reader without materializing
the complete run:

```python
reader = (
    run.measurements()
    .project(
        {"bias_v": "readout.dc_bias", "s21": "readout.s_parameter"},
        units={"bias_v": "V"},
        diagnostics="reason",
    )
    .to_record_batch_reader(batch_size=500)
)
for record_batch in reader:
    analyze_arrow(record_batch)
```

For a run-backed, unsliced dataset, the reader requests projected Arrow pages
from the daemon. For a materialized or sliced dataset, it splits the local
projected table. An empty dataset retains its schema and yields no record
batches.

`batch_size` bounds stored experiment points. Observation layout may expand one
point into many rows; returned Arrow batches remain bounded. The first page pins
the currently durable point count, so later appends belong to a later reader.
This is a finite snapshot, not a live subscription. Live cursors, checkpoints,
retries, and finalization belong to a future workflow streaming contract.

Small pages can revisit the same stored Arrow chunk. The daemon's run repository
reuses verified chunk bytes with a 16 MiB payload limit and at most 32 entries;
least-recently-used entries are evicted and oversized chunks are not retained.
This bounds retained cache payload, not total process memory or a caller's decoded
arrays. A cold read still loads a whole immutable blob and its native Arrow chunk.
Row, variable, and entity projection happen before copying selected values into
NumPy; this is not storage-level entity pushdown.

Select one declared entity dimension before opening a reader:

```python
from scopecat.kernel.entity import EntityRef

wanted = (EntityRef(kind="qubit", id="q7"), EntityRef(kind="qubit", id="q0"))
for run in runs:
    reader = (
        run.measurements()
        .project({"signal": "signal"}, diagnostics="full")
        .select_entities("entity", wanted)
        .to_record_batch_reader(batch_size=10)
    )
    for batch in reader:
        analyze_arrow(batch)
```

Each run is read independently. Selection preserves requested `(kind, id)` order
regardless of the stored order. Existing entities retain their stored descriptions and source products. Native
selected records and the trace response also retain acquisition evidence; the Arrow
table uses its existing availability diagnostics and schema provenance, rather than
adding acquisition receipt columns. Missing entities retain the requested
description, produce unavailable values with `entity_alignment="absent"`, and have
no fabricated acquisition evidence. This uses the same alignment as
`Dataset.reindex_entities`; it is a single-dimension selection, not a multi-run join.
The HTTP Arrow and trace interfaces accept at most 32 selected entities per query,
including missing identities. Point page size is a separate bound.

Arrow schema metadata carries the selected measurement schema, `scopecat.run_id`,
`scopecat.config_content_hash`, and the first page's `scopecat.snapshot_size`.
A reader pins that count for subsequent pages. Refreshing the operator trace panel
issues another finite query; it does not subscribe to live updates.

Selection bounds copied value arrays and serialized response width. Whole blob
bytes, native Arrow buffers, and wide metadata/evidence sidecars can still be read.
The `entity-reads` component benchmark reports those costs separately from selected
value bytes, serialized IPC payload bytes, and sampled process RSS. Buffer sizes
are not peak RAM measurements.

Reuse is keyed by the currently resolved content digest, not a run-local path.
Cache hits check file identity, size and change timestamps; misses use the normal
SHA-256 verification. Cached bytes are immutable. This relies on the object store's
immutable-file contract, not on timestamps as a cryptographic substitute for the
digest. Reference lookup and the reader's pinned point-count semantics are
unchanged. There is no process-global cache shared between projects.

### Use native labeled arrays

Each variable exposes values, labels, validity, failure reasons, and units
through one Scopecat-owned facade:

```python
temperature = data["temperature"].dense
assert temperature.layout == "dense"
valid_temperature = temperature.values[temperature.valid]
temperature_mk = temperature.to("mK")

waveform = data["waveform"].observations
assert waveform.layout == "ragged"
assert waveform.declared_dims == ("point", "sample")
parent_points = waveform.coords["readout__sample__parent_point_index"]
```

`Variable.labeled` works for either layout. `Variable.dense` rejects ragged
data, while `Variable.observations` rejects dense data, so code cannot silently
confuse a rectangular tensor with a flattened indexed-observation stream.
`LabeledMeasurementArray.isel(...)` and `.sel(...)` keep labels and diagnostics
aligned. Its NumPy values, validity mask, reasons, and coordinates are read-only.

### Use the Xarray view

`Dataset.to_xarray()` preserves Scopecat's labeled point-domain and ragged
layout without crossing the tabular projection boundary:

```python
xds = data.to_xarray()
another = data.to_xarray()
assert xds is not another

grid = data.to_xarray(layout="grid")
```

The default layout keeps the durable `point` dimension and works for point
clouds, partial selections, and ragged results. For a complete product grid,
`layout="grid"` restores authored axes as dimensions in product order. It
rejects partial grids, duplicate or missing ordinals, and coordinates that do
not match their declared grid axes.

Ragged variables in one recording group share an observation dimension with
parent-point and local-index coordinates. Observation-validity masks and
unavailable-reason coordinates keep integer, boolean, and string fill values
unambiguous. Select points through the facade before exporting: direct
`data.to_xarray().isel(point=...)` does not cascade to a separate ragged
observation dimension.

Measurement values and metadata are deeply immutable. Every `to_xarray()` call
returns an independent snapshot, so caller edits cannot affect later selections
or exports. Complete Arrow, dataframe, and Xarray conversions materialize their
selected snapshot; use the projected record-batch reader when it does not fit
in notebook memory.

## Select traces by entity identity

For entity-indexed acquisitions, analysis code can select traces by durable
identity without finding array offsets:

```python
traces = dataset.traces("iq", entities=(sc.EntityRef(id="q1", kind="qubit"),))
```

The selection matches `(kind, id)`, preserves stored entity metadata and
acquisition evidence, and raises for missing identities. Trace point indices
remain the original dataset indices, including after filtering or reordering.
This extracts selected traces from a materialized dataset; it does not imply
server-side filtering or bounded-memory streaming.

## Let the GUI use the same schema

The canonical schema is registered before the first append, so the GUI can
render live and empty datasets without guessing from JSON values. Roles,
dimensions, recording groups, labels, units, dtype, and point-domain layout
determine safe table, scalar, trace, scatter, and product-grid views. Complex
values expose magnitude, phase, real, and imaginary modes. During execution,
the newest received point reaches the browser as Arrow IPC; large traces do not
take a JSON-array detour before rendering.

Automatic plots request bounded projections and label incomplete live surfaces.
When no safe automatic visual exists, the typed table remains available. Exact
candidate selection and browser limits are presentation implementation details
owned by the UI and its tests rather than part of the scientific data contract.

## Publish analysis outputs separately

Measurement datasets are immutable run inputs. Fitted datasets, facts, figures,
reports, and parameter proposals become durable only through an explicit
analysis publication attached to that run. See
[Analysis publication](../concepts/analysis-publication.md) for its output ontology,
lossless native-library boundary, revisions, and optional execution evidence.
