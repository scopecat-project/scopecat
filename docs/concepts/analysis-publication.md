# Analysis publication

This page records the evolving durable-publication contract. The primary design
test is the [reference-lab calibration workflow](../tutorials/reference-lab.md):
users should be able to publish, review, accept, use, and undo a proposal without
managing storage identities or revision mechanics directly.

An analysis publication provides a durable boundary for fitted values, reusable
datasets, plots, reports, and proposed parameter changes that might otherwise
remain in run-adjacent scripts or personal file layouts. The atomic publication
is owned by one source run, one stable sample, or the project. Sample- and
project-owned publications may span runs, but always retain explicit immutable
inputs. It is not a dataframe API, compute runtime, or workflow scheduler.

Users should keep doing numerical work with NumPy, pandas, Polars, Xarray,
PyArrow, SciPy, and domain libraries. Scopecat owns the durable boundary after
that work: identities, scientific meaning needed to interpret an output,
relations between outputs, content hashes, provenance, and attachment to the
owning run, sample, or project scope.

## Durable output ontology

Every output has a stable, analysis-local `id`. An analysis publishes five
kinds of output:

- **Fact** is a small typed conclusion, such as a fitted resonance, quality
  metric, classification, or structured fit summary. Scalars and quantities
  use first-party schemas. A structured fact requires an `AnalysisFactSchema`
  that validates its canonical JSON shape; it is not an unlabelled JSON dump.
- **Dataset** is reusable structured scientific data. Its durable schema retains
  column identities, physical types, nulls, units, coordinates, and the native
  metadata that can be represented without ambiguity.
- **Artifact** is an exact file or byte sequence, such as a report, native Xarray
  store, model checkpoint, image, or vendor format. It retains a filename, media
  type, content hash, and producer rather than depending on a user's directory
  convention. Published artifacts can be reopened through Python or downloaded
  from the run view.
- **View** is a bounded table or figure projection for inspection. The author
  supplies dataset sources and projections; publication generates the
  canonical preview, total row/point count, and truncation flag. The cache is
  presentation, not another authoritative scientific result.
- **Proposal** is a decision output that proposes parameter changes and retains
  its validation and acceptance lineage. It may cite authoritative fact,
  dataset, or artifact outputs from the same analysis as structured evidence.

The analysis record is the root provenance record for this publication. Large
datasets and artifacts live as separate content entries under the same run,
sample, or project owner; the record stores typed references. This keeps one atomic
publication without embedding every payload or content index in one JSON
record.

## Publish ordinary Python results

Exploratory analysis starts from a run context, performs numerical work in its
native library, and publishes only the outputs worth retaining:

```python
context = run.analysis("Resonator fit", key="resonator-fit")
measurements = context.measurements()

frame = measurements.project(
    {"bias": "dc_bias", "response": "signal"},
    units={"bias": "V"},
    identity=False,
).to_polars()
fits = fit_with_polars(frame)

published = (
    context.result()
    .dataset(
        "fits",
        fits,
        fields={
            "bias": sc.AnalysisField(role="coordinate", unit="V"),
            "resonance": sc.AnalysisField(unit="GHz"),
        },
    )
    .table(dataset="fits", columns=("bias", "resonance"))
    .figure(dataset="fits", kind="line", x="bias", y="resonance")
    .fact("fit-quality", float(fits["quality"].mean()))
    .artifact("report", text=render_report(fits), filename="fit.md")
    .save()
)
```

Tables and figures project the published dataset; they do not own another copy
of the scientific values. Reopen the same typed boundary by logical key or exact
record ID:

```python
published = run.published_analysis("resonator-fit")
fits = published.dataset("fits").to_polars()
quality = published.fact("fit-quality").value
report = published.artifact("report").text()
```

A reusable step returns the same declarative result and lets `run.analyze(...)`
publish it:

```python
@sc.analysis_step(id="resonator-fit")
def resonator_fit(context: sc.AnalysisContext) -> sc.Analysis:
    fits = fit_with_scipy(context.measurements())
    return context.result("Resonator fit").dataset("fits", fits)


published = run.analyze(resonator_fit())
```

Both paths return `PublishedAnalysis`; there is no separate immediate outcome
model.

## Layered scientific figures

A figure contains one to sixteen explicitly ordered layers. The existing
`.figure(...)` convenience method creates a single layer; `.figure_layers(...)`
combines measured points, a fitted curve, and declared uncertainty in the same
axes. Fitting methods and scientific acceptance thresholds remain project code.

```python
from scopecat.records.analysis import (
    AnalysisDatasetViewSource,
    AnalysisFigureLayerSpec,
    AnalysisFigureProjection,
    AnalysisUncertaintyProjection,
)

review = (
    context.result()
    .dataset("fit-curve", fitted_curve)
    .figure_layers(
        layers=(
            AnalysisFigureLayerSpec(
                id="measured",
                source=measured_publication.dataset_view_source("observations"),
                projection=AnalysisFigureProjection(
                    kind="scatter", x="bias", y="signal"
                ),
            ),
            AnalysisFigureLayerSpec(
                id="fit",
                source=AnalysisDatasetViewSource(output_id="fit-curve"),
                projection=AnalysisFigureProjection(
                    kind="line",
                    x="bias",
                    y="signal",
                    uncertainty=AnalysisUncertaintyProjection(
                        lower="lower",
                        upper="upper",
                        style="band",
                        meaning="95% confidence interval from the project fit",
                    ),
                ),
            ),
        ),
    )
    .save()
)
```

A sibling source names an output in this atomic publication. An already
published source freezes its exact analysis revision, output identity, dataset
hash, and codec as an input; a later source revision does not change the figure.
The source accessor reads metadata, without loading the full dataset. Each
layer exposes its actual source and projection in the review UI.

All layers use the first layer's axis units. Publication converts compatible
units and rejects incompatible dimensions or a mixture of declared and unknown
units. Uncertainty columns are absolute lower and upper bounds in compatible
y-axis units, with a required project-authored meaning. `band` joins adjacent
bounds in source order; `bars` draws vertical bounds at each point. Neither
implies a confidence level, fit quality, nor permission to accept a proposal.

The entire figure has a 4,096-point preview budget, divided equally among the
declared layers; earlier layers receive any remainder. Each layer selects its
first rows in source order within that share, before conversion to Python
values. Unused shares are not reassigned. This guarantees every non-empty layer
has a visible share, and the UI reports both returned and total counts. It is a
bounded preview, not a representative statistical sample. Full source datasets
remain separately available. External Arrow inputs still use the existing
whole-blob storage read: record batches are counted without building a full
table, and only selected columns and bounded rows enter the preview table.
This is not storage predicate pushdown or a bound on a single decoded batch.

The layered figure format was introduced in project store version **63**. The
current runtime uses version **64**, adding immutable author revisions. Older
projects and snapshots require their pinned matching reader; this runtime rejects
them before modification. Preserve originals when starting a separate version 65
project. See [backup and restore](../how-to/backup-and-restore.md).

## Compare completed runs

A comparison, candidate verification, drift estimate, or cohort summary does
not belong to an arbitrary member run. Start it from the project and bind each
completed-run input with a local role:

```python
context = lab.analysis(
    "Candidate verification",
    key="candidate-verification",
)
baseline = context.measurements(
    baseline_run,
    id="baseline",
    role="baseline",
)
candidate = context.measurements(
    candidate_run,
    id="candidate",
    role="candidate",
)

decision = compare_candidate(baseline, candidate)
published = (
    context.result()
    .dataset("comparison", decision.rows)
    .fact("accepted", decision.accepted)
    .artifact("report", text=decision.report, filename="verification.md")
    .save()
)

reopened = lab.published_analysis("candidate-verification")
```

The durable subject is `project`, while every input freezes its binding ID,
source run ID, content target, hash, codec, role, and optional source-analysis
revision. Project analysis has its own immutable revision stream and content
namespace. Its outputs therefore do not appear in any input run content catalog, and
deleting the notion of a “primary run” does not lose provenance.

Only completed runs are valid inputs. A project analysis may also consume a
dataset published by a run analysis:

```python
fits = context.analysis_dataset(
    "resonator-fit",
    "fit-by-bias",
    run=baseline_run,
    id="baseline-fits",
    role="baseline",
)
```

The explicit binding ID prevents collisions when several runs expose the same
dataset ID. Project analysis does not currently publish parameter proposals:
changing configuration still requires one run-scoped analysis with one
unambiguous base configuration. A project verification may gate whether that
existing proposal is accepted.

## Logical keys and immutable revisions

The author supplies one logical analysis `key`, not a version number. Durable
record IDs always make the revision explicit: the first publication uses
`analysis-<key>-r1`, followed by `-r2`, then `-r3`. This keeps record identity
unambiguous even when a logical key itself ends in revision-shaped text.
Repeating the same logical content is an idempotent notebook or step retry and
returns the existing publication; changing an input snapshot, output content,
title, metadata, or other durable meaning appends a new record without replacing
an earlier one.

Datasets, artifacts, and parameter proposals use the allocated analysis record
ID as part of their durable identity, so a revision never leaves a supposedly
immutable payload pointing at overwritten content. Proposal timestamps are not
part of logical content identity: rebuilding the same proposal in a later
notebook execution resolves to the already-published proposal and its original
timestamp.

Reading by logical key returns the latest revision. Reading by an exact analysis
record ID returns that historical revision. `PublishedAnalysis.revision` and
`publication_hash` expose the allocated revision and the content identity, while
`published_at` reports the stable server-assigned time of the first successful
publication. Ordinary authoring code does not manage these values.

History reads return bounded summaries instead of fetching every publication
body. Use `run.analysis_summaries(...)` for one run or
`lab.analysis_summaries(...)` for project-level multi-run publications, then
open a selected item through `published_analysis(...)` when its outputs are
needed.

`Analysis.save()` and `run.analyze(...)` return that same `PublishedAnalysis`
handle after persistence. There is no separate immediate outcome model: code run
in the publishing cell and code run after reopening the project use the same
typed output access and load parameter proposals from the same durable records.

## Conversion policy

Adapters may perform an automatic conversion only when Scopecat can preserve
the source semantics required to reconstruct or faithfully expose the data. In
particular, an adapter must not silently discard or reinterpret:

- numeric width, complex values, timestamps, categorical values, or extension
  types;
- the distinction between null, unavailable, and absent;
- index and coordinate identity, order, dimensions, shapes, or variable roles;
- units and other scientific field metadata;
- column identity when a library requires names to be rewritten.

Column names in a native object remain the default durable IDs. A sparse
`fields={source_name: AnalysisField(...)}` publication mapping may rename them
once at the boundary and assign role, unit, and label together. The durable
schema records both source names and stable IDs rather than letting every
downstream adapter infer them differently. `AnalysisField.unit` is the durable
target unit, matching its meaning for typed table rows. A source field with a
known compatible unit is numerically converted; a field without unit metadata
treats it as the author's explicit declaration. Incompatible units and units on
non-numeric fields are rejected rather than relabeling values silently.

There are three intentional outcomes:

1. lossless normalization into a first-party dataset;
2. an explicit projection chosen by the user, such as selecting scalar columns
   for a table view; or
3. exact preservation as an artifact when no lossless first-party mapping
   exists yet.

An unsupported native shape must not be flattened implicitly merely because a
dataframe conversion is available. Adding a first-party mapping requires a
round-trip contract and tests against the native library.

### Native dataset contract

The first-party adapter intentionally supports a small, explicit matrix:

| Source | Accepted boundary | Durable guarantees | Explicit limit |
| --- | --- | --- | --- |
| PyArrow | A `Table` with unique, non-empty field names | Arrow types, nulls, nested column values, schema metadata, and recognized role/unit/label metadata | A table view must deliberately select scalar columns; nested values are not stringified |
| pandas | A two-dimensional `DataFrame` with string column names | Values enter the same Arrow schema; categorical, timezone, and nullable numeric identities remain durable there; meaningful indexes become coordinate columns | The default `RangeIndex` is dropped; index/column collisions and non-string columns are rejected |
| Polars | A `DataFrame` through its Arrow representation | Column order, Arrow-representable physical types, nulls, and explicit `AnalysisField` semantics | Polars-only metadata with no Arrow representation is not promised |
| Xarray | Exactly one named dimension, with every coordinate and data variable using that dimension | Coordinate roles, physical dtypes, dimension identity, and finite JSON dataset/variable attributes round-trip | Multi-dimensional, scalar-mixed, or multi-index layouts must be projected deliberately or stored as artifacts |
| Annotated rows | A non-empty homogeneous sequence of dataclass rows with `Annotated[..., AnalysisField(...)]` fields | The annotation supplies stable field ID, role, label, and target unit; `Quantity` values are converted once and the selected values enter Arrow without a dataframe adapter | Unannotated fields are private implementation details and are omitted; empty or mixed row types have no inferable durable schema |
| NumPy | Arrays inside an explicitly named dataframe/Xarray field | The owning container supplies field identity and topology | A bare ndarray is not a dataset because it has no durable field names or coordinate meaning |

Annotated rows enter the same bounded preview projector during publication, but
dataset publication is not constrained by the preview row limit. Because the
dataclass already owns its field semantics, a second `fields=` mapping is
rejected. The annotated Python scalar type also fixes the Arrow physical type,
including for an optional column whose current rows are all null. Authors
publish the rows once and let tables or figures refer to that durable dataset
by output ID.

`DerivedDataset.to_pandas()` defaults to familiar pandas/NumPy dtypes. Use
`dtype_backend="pyarrow"` when nullable integer and other exact Arrow dtype
identity matters more than NumPy-native behavior. This choice affects only the
in-memory pandas view; the durable dataset always retains its Arrow schema.

## Provenance and scope

The default analysis path is ordinary Python followed by an explicit durable
publication:

```python
def resonator_fit_analysis(context: sc.AnalysisContext) -> sc.Analysis:
    fits = fit_with_scipy(context.measurements())
    return (
        context.result("Resonator fit")
        .dataset("fits", fits)
        .fact("selected-fit", select_fit(fits), schema=FIT_SCHEMA)
        .artifact(
            "report",
            text=render_report(fits),
            filename="resonator-fit.md",
        )
    )
```

Accessing the measurements records their exact snapshot as an analysis input;
publishing the results does not require an execution wrapper, registry, codec
declaration, or provenance handle. Numerical work remains normal NumPy,
pandas, Polars, Xarray, PyArrow, SciPy, or domain-library code.

An analysis may consume a dataset already published by an earlier analysis on
the same run:

```python
fits = context.analysis_dataset("resonator-fit", "fit-by-bias")
frame = fits.to_polars()
```

The logical analysis key is resolved while authoring. The saved input freezes
the exact analysis record revision, output ID, dataset content entry, hash, and
codec. Consequently, publishing a new source revision is a provenance change
for the consumer even when its Arrow bytes happen to be identical. The source
must already exist. A run context restricts sources to that run; a project
context requires the source run explicitly. Neither form schedules steps or
creates hidden workflow state.

### Optional execution evidence

A traced call retains optional evidence about one ordinary eager Python call:
its named inputs, captured names, diagnostic implementation identity, and the
content identity of one native return value. `context.trace(...)` returns that
value but does not publish, cache, batch, replay, or remotely deploy it.

When a published fact, dataset, or artifact exactly matches the traced output,
Scopecat records `produced_by`. First-party dataset normalization, such as a
field mapping or pandas index policy, records `derived_from` instead. Publication
remains explicit, and numerical code never passes provenance handles.

A parameter proposal may cite authoritative facts, datasets, or artifacts from
the same publication with `evidence=("selected-fit", "fit-quality")`. Views are
bounded presentation caches, so cite their source dataset instead.

Experiment `compute(...)` is a different lifecycle: it is a node in the formal
experiment program and may run before or during acquisition. Analysis
`trace(...)` is an optional record of ordinary eager code over a run snapshot.
The similar function call does not make them one placement or deployment model.

## Structured fact contract

A structured fact has a versioned domain `schema_id`, canonical JSON content,
and a fingerprint of Scopecat's structural schema. Dataclass and Pydantic types
are local validation and reconstruction adapters; the durable record never
depends on their import paths. The exact structural identity rules belong to
`AnalysisFactSchema` itself.

Define the local type and its schema descriptor together, then reuse the
descriptor for publication and typed reading:

```python
@dataclass(frozen=True, slots=True)
class ResonatorFit:
    resonance: sc.Quantity
    quality: float


RESONATOR_FIT_SCHEMA = sc.AnalysisFactSchema(
    "lab.resonator-fit.v1",
    ResonatorFit,
)

analysis = context.result("Fit review").fact(
    "selected-fit",
    fit,
    schema=RESONATOR_FIT_SCHEMA,
)

published = run.published_analysis("fit-review")
fit = published.fact_as("selected-fit", RESONATOR_FIT_SCHEMA)
```

`fact_as(...)` checks the schema ID, structural codec, and fingerprint before
reconstructing the caller's dataclass or Pydantic model. Changing the durable
shape requires a new versioned schema ID. Scopecat does not maintain a global
Python type registry or import application types while reopening a run; code
that only needs generic inspection can continue to use `fact(...)` and read its
JSON value directly.

Analysis belongs to one completed run, one stable sample, or a project
publication over explicit completed-run snapshots. A sample publication accepts
only input runs bound to that sample; see [Chips and physical samples](samples.md).
Live checkpoints, orchestration retries, schedules, recurring calibration
state, and continuously changing cohorts belong to a future workflow model.
They should not be encoded as hidden state in an analysis. Future streaming
analysis may reuse the same execution and publication primitives, but its
cursors, windows, checkpoint state, and finalization policy belong to that
workflow rather than to the analysis record.
