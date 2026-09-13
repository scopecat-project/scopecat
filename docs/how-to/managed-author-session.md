# Run an experiment from a notebook

Use the laboratory's installed environment and start its daemon as described by
its setup guide. The lab maintainer supplies an existing sample/workpoint context
and registered experiment. This example uses the reference laboratory's `qubits`
table and `signal` experiment; substitute your laboratory's names.

```python
import scopecat as sc

project = sc.open_project("/path/to/lab")
with project.authoring() as author:
    parameters = author.config.workspace(context="my-sample-start")
    parameters["qubits"]["q0"]["drive_carrier_frequency"] = sc.Quantity(5.1, "GHz")
    checked = author.prepare(
        "signal",
        parameters=parameters,
        fixed={"gain": 1.0},
        scans={"frequency": [sc.Quantity(f, "GHz") for f in (5.0, 5.1, 5.2)]},
    )
    print(checked.preview)
    job = checked.run()
    receipt = job.receipt
    job.wait(timeout=60)
    run = job.result()
    values = run.measurements()["result"].require_values()
    saved = parameters.save("my-sample-next")
```

`fixed` supplies scalar controls. `scans` supplies sequences, Python ranges or
NumPy arrays. Numeric values use the control's declared unit; explicit quantities
retain their units and undergo the existing unit/bounds checks. Structural
experiment options go in `inputs={...}`. Supplying the same control twice is an
error. None of these adapters changes which controls an experiment permits.

Preparing validates and freezes unsaved parameter edits, the selected author
revision and the preview's manual-state fence. Editing the workspace afterwards
does not alter `checked`. Running after a relevant laboratory change may require
a new preview. Saving a named parameter version does not publish a shared default.

## Edit a request before preparing

When the laboratory's author package is installed in the notebook environment,
use its experiment declaration to create a request. Request creation checks the
function's call signature and captures supplied values; it does not build the
program, import a device SDK or acquire data. Importing a laboratory module still
executes that module's ordinary Python top-level code.

```python
from reference_lab.workflows.authored.signal import signal

request = signal(gain=1.0, polarity="positive")
request.values["frequency"] = sc.Scan(sc.Quantity(f, "GHz") for f in (5.0, 5.1, 5.2))
with project.authoring() as author:
    parameters = author.config.workspace(context="my-sample-start")
    checked = author.prepare(request, parameters=parameters)
    alternative = request.copy()
    alternative.values["frequency"] = sc.Quantity(5.1, "GHz")
    alternative.values["polarity"] = "negative"
    next_checked = author.prepare(alternative, parameters=parameters)
    plan = checked.save_plan("Positive resonance scan", saved_by="operator")
    reopened = author.prepare_plan(plan.ref, actor="operator")
```

`values` is one mutable dictionary for structural inputs, runtime inputs and
editable numeric controls. Assign `sc.Scan(...)` to select a scan; assign a scalar
to return to a fixed point. Only declared scannable controls accept scans. `Scan`
captures its iterable immediately, so changing a NumPy array later cannot change
its values. Requests capture their initial inputs and `copy()` deep-copies edited
data while retaining the same declaration. Arrays assigned directly are ordinary
input values, never implicit scans; managed scalar forms reject them.

Function arguments have the declaration's static types. Dictionary edits have
value type `object` and are checked at preparation, including unknown names,
required inputs, units, bounds and scannability. This does not provide generated,
statically checked attributes such as `request.frequency`. A request has no
`output` or cached program: each preparation rebuilds from the selected managed
source. Structural edits therefore produce the corresponding new result tree.
Already prepared launches and saved plans keep their captured inputs and code.
Pass configuration/parameters and the execution actor to `prepare`; do not also
pass `inputs`, `fixed`, `scans` or `control_edits` when supplying a request.

### Optional typed field editing

Keep the dictionary for quick exploration. For a repeatedly edited request, an
ordinary mutable dataclass gives your editor explicit field types:

```python
from dataclasses import dataclass
from typing import Literal


@dataclass
class SignalInputs:
    gain: float
    frequency: sc.Quantity | sc.Scan
    polarity: Literal["positive", "negative"]


request = signal(gain=1.0).typed(SignalInputs)
request.values.frequency = sc.Scan(sc.Quantity(f, "GHz") for f in (5.0, 5.1))
request.values.polarity = "negative"
alternative = request.copy()
alternative.values.frequency = sc.Quantity(5.1, "GHz")
with project.authoring() as author:
    checked = author.prepare(request)
```

`typed(InputClass)` creates an isolated request with a dataclass as its sole
value store; `copy()` preserves that concrete type. Its fields must cover the
current request values exactly, including editable controls. Omit class defaults:
selected values, including defaults, come from the ordinary experiment call.
Use `Quantity | Scan` only for scannable controls. Structural fields retain their
ordinary Python types. `snapshot()` returns an isolated dictionary and preserves
`Quantity` and `Scan` objects instead of recursively converting them to records.

This is a manually maintained editing type, not another experiment declaration.
As with ordinary dataclasses, its annotations do not validate Python values at
runtime; keep them aligned with the experiment signature. Editors check subsequent
field assignments, while `prepare` enforces the original declaration's values,
units, bounds and scan capabilities through the same path as dictionary requests.
No controls, defaults, source registry or program are generated from this class.
Changing the experiment's input names requires updating this optional editing type.
Saved plans retain the prepared values, not the notebook's Python dataclass type.

### Inspect the prepared experiment

```python
checked = author.prepare(request, parameters=parameters)
facts = checked.inspection
print(facts.total_point_count, facts.points_truncated)
print([(record.id, record.dims) for record in facts.records])
print([(step.implementation, step.placement) for step in facts.computes])
for parameter in facts.parameters:
    if parameter.kind == "lookup":
        print(parameter.table_id, parameter.column_id, parameter.key_columns)
    else:
        print(parameter.parameter_id)
print(checked.preview.code_revision, checked.preview.config_source)
```

`inspection` reads an isolated copy of facts captured by that successful prepare.
It performs no I/O, compilation or acquisition, and remains readable after the
session closes. Later request, parameter or source edits cannot update these
facts. Prepare again to inspect a changed experiment; keep the enclosing preview
with its exact request hash, source revision and configuration identity when
sharing evidence. Saved plans are revalidated by the existing prepare-plan path.

`parameters` lists compiler-declared scalar or table-column dependencies. Table
and column identifiers remain separate; `key_columns` names lookup keys, not
resolved row values. This is not a per-point parameter-value trace or a new
parameter store. `bindings` and `binding_edges` explain invocation inputs and scan
center/overlay relationships; they are not a complete operation dependency graph.
Missing parameters still fail preparation with the existing field diagnostics.

`points` contains at most the compiler's sampled point limit (currently 64),
with `points_truncated` indicating omitted points. `total_point_count=None` means
an adaptive total is unknown; `point_limit` remains the upper bound.
`domain_inspections` describes only `selected_point`, initially the first point.
Its target-owned `content` retains waveform/program limits and truncation metadata.
These sampled facts do not prove every point or establish scientific validity.

Summary collections are capped at `item_limit` (currently 256) each. Inspect
`item_counts` and `truncated` before treating a list as complete. No full scan or
configuration table is copied into this section. Existing maintained launch
providers may omit inspection; `checked.inspection` then reports that capability
is unavailable. This release adds read-only Python/HTTP facts, not a graphical
editor or point-selection UI.

An imported declaration retains its source identity from load time. Preparation
compares that contract with the selected revision and rejects a mismatch. After
editing a declaration, explicitly refresh the session and reload the notebook's
experiment module, or select the original revision. Helpers execute from the
selected managed revision; importing a request does not pin notebook helper
objects or claim to fingerprint their transitive dependencies.

Calling `experiment(...)` creates an editable request without executing its body.
`author.prepare(request)` builds and validates it against the selected managed
revision. There is no separate `.request(...)` factory.

Maintainers who need a local immutable program use `experiment.build(...)`.
It preserves checked function arguments and the typed `invocation.output` tree;
it does not acquire data or select a managed source revision. Existing low-level
`.bind(...)` supports partial runtime inputs while assembling an invocation;
structural inputs must still be complete. These built objects are separate from
editable requests. Request dictionary edits do not synthesize statically checked
attributes; concrete typed editing remains separate work.

## Declare controls next to their inputs

In the laboratory's author module, use standard `Annotated` metadata rather than
separate control constants. The parameter name owns the ID; its Python default
owns the starting value. The body receives a symbolic input or scan coordinate.

```python
from typing import Annotated
import scopecat as sc


@sc.experiment
def amplitude_probe(
    experiment: sc.ExperimentContext,
    amplitude: Annotated[
        sc.Input[float], sc.ControlSpec(minimum=0, maximum=0.9, scannable=True)
    ] = 0.1,
    gain: Annotated[sc.Input[float], sc.ControlSpec(minimum=0)] = 1.0,
) -> sc.Input[float]:
    return amplitude * gain
```

`amplitude_probe()` includes both defaults. Edit
`request.values["amplitude"] = sc.Scan([0.1, 0.2])` to scan; assigning `0.15`
returns to a fixed coordinate. `amplitude_probe.build(amplitude=0.15)` explicitly builds
one fixed coordinate, with no duplicate runtime input source. `gain` remains a
scalar runtime input and cannot be scanned. GUI controls, bounds, units and saved
plans come from the same derived `ControlSet`.

`ControlSpec` supports `float` and `Quantity` inputs, with optional `unit`,
`minimum`, `maximum`, `title`, `group` and `scannable`. A `Quantity` default supplies
its unit when the metadata omits it. For example,
`Annotated[sc.Input[sc.Quantity], sc.ControlSpec(minimum=4, scannable=True)] = sc.Quantity(16, "ns")`.
Quantities are immutable and safe as Python defaults. The maintained lint config
recognizes them; author files also disable basedpyright's optional blanket
`reportCallInDefaultInitializer` diagnostic locally, preserving other type checks.

Omit a Python default when the operator must choose a value. Required quantity
controls must specify `unit` in `ControlSpec`. Discovery still succeeds; the GUI
shows an empty required field, and preparation rejects missing values. A required
scan can be supplied directly via `author.prepare("name", scans={...})` without
inventing a scalar starting value. The typed experiment call continues
to require the function's creation arguments before dictionary editing.

`Input[T]` honestly describes concrete caller values and symbolic body references;
it does not make symbolic values behave like ordinary numbers in Python control
flow. Keep target names, integer shot counts and structural choices as normal
Python parameters. A structural edit rebuilds the program during preparation.

Use one metadata owner: do not repeat a field in both `ControlSpec` and an explicit
`ControlSet`, or combine `ControlSpec` with a second `ValueType` declaration.
Maintainers can still supply explicit owned/derived controls and a project
validator through `ControlSet`; the metadata-derived fields join that same set.
It is the existing validation and execution contract, not another control registry.

## Restart Python and read the same result

The receipt path is printed/stored by your notebook, and receipts are also kept
under the project's `.scopecat/author-jobs/` directory. Keep that file alongside
your notebook's run references. It contains the exact reviewed request and an
identity for finding its durable procedure, not a copy of the run data.

```python
with project.authoring() as author:
    job = author.reopen(receipt)
    run = job.result()
    values = run.measurements()["result"].require_values()
    parameters = author.config.workspace(context="my-sample-next")
```

`run`, datasets and variables use the open session for lazy reads. Already
materialized arrays, tuples, tables and `run.snapshot` remain usable after close.
Use `job.reconnect(new_author).result()` or `new_author.run(run_id)` to fetch more
data. A new notebook imports only Scopecat: project modules load in revision-aware
workers, so no `sys.path` edits or notebook-global project imports are needed.

## Waiting and uncertain submission

`job.wait(timeout=60)` returns the same job on successful procedure completion.
It raises `AuthorJobTimeout` when waiting expires, `AuthorJobAttention` when input
or operator attention is needed, and `AuthorJobCancelled` or `AuthorJobFailed` for
those durable closure outcomes. These exceptions are available from
`scopecat.application.author_project`. A timeout does not cancel execution.
`job.cancel(reason="...")` requests a stop after the current step settles; inspect
or wait for the eventual closure. There are no automatic acquisition retries.

A lost submission response raises `AuthorSubmissionUncertain`. Its `.job` holds
the receipt written **before** sending the request. Call `error.job.recover()` or
reopen that receipt after restarting. Recovery only queries the original request
identity. `None` means admission has not been found; it is not permission to
acquire again. Inspect the daemon before deciding what to do. A dispatch failure
raises `AuthorJobAttention` with its admitted `.job` for inspection.

Calling `checked.run()` again intentionally requests another acquisition. Retain
the job and its receipt when continuing an interrupted notebook cell.

## Editing author code

`author.state()` shows the active revision. `author.refresh()` explicitly
validates current source and selects it for future prepares. It hides the normal
generation bookkeeping while preserving concurrent-update conflicts. Source edits
are not automatically selected. Existing previews and runs keep their code.

For deliberate old-code selection, retain `checked.preview.code_revision` and use
`author.prepare(..., code_revision=old_revision)`. The catalog is loaded from that
same revision. Saved experiment plans also retain exact code/configuration.
Registered analysis still uses `author.analyze(..., code_revision=...)`; ordinary
analysis-function convenience belongs to the next implementation slice.


### Return a typed point coordinate

A signature input annotated with `Input[T]` and `ControlSpec(scannable=True)` already has
an automatic point coordinate, including when its value is fixed. Use
`context.coordinate(amplitude)` in the experiment body to obtain a
`CoordinateRef[T]` for a typed result dataclass without a cast. Returning that
handle uses the existing result field name and recording path; the helper does
not add another record or change the scan.

The helper also accepts coordinates returned by `context.scan(...)`. It rejects
ordinary runtime inputs and computed expressions: those are values, not direct
point coordinates. Return computed values as ordinary results instead.

### Preparation latency

The first prepare for a source revision loads its isolated author environment.
Repeated prepares reuse that worker while still checking the current request
and configuration. Refresh selects a separate version; old prepared experiments
keep their original identity. Maintainers can reproduce and diagnose latency
with the [author performance benchmark](../development/author-performance.md).

### Observe an experiment before it finishes

Use the same retained job for read-only progress and bounded data previews:

```python
job = checked.run()
progress = job.progress()
print(progress.state)

preview = job.preview(limit=10)
child = preview.progress.current_child
if child is not None:
    print(child.step_key, child.run.run_id)
    print(child.run.control.completed_point_count)
    if preview.latest is not None:
        print(preview.latest.point_index, preview.latest.observables)
    if preview.live is not None:
        print(preview.live.received_record_count, preview.live.durable_record_count)

job.wait()
run = job.result(step="experiment")
```

Each call performs bounded reads; it starts no polling thread and does not submit
or resume anything. Reopen the receipt with `author.reopen(receipt)` in another
Python session and call the same methods while the experiment is still running.

`progress.state` distinguishes waiting for dispatch, starting, waiting for an
acquisition/resources, acquiring, settling, running another step, paused dispatch,
attention/input, cancellation in progress, and the final succeeded/failed/cancelled
outcomes. The underlying procedure, dispatch and child run snapshots retain the
exact reasons and identities. A missing admission still raises
`AuthorSubmissionUncertain`, rather than guessing that it is queued.

`preview()` selects the **current unsettled acquisition**, with its exact step key,
attempt and admitted run ID. In multi-step procedures successive calls may name
different steps; label plots by that identity. Retry attempts of one step retain
its admitted effect identity. The active child does not depend on history paging;
`progress.steps` contains at most 50 attempts and `steps.next_cursor` selects the
next page via `job.progress(cursor=...)`.

`latest` is the latest daemon-received record, possibly not yet persisted. It is
absent before data arrives or after the live buffer closes. `live` carries the
received/durable record counts and whether that buffer is active. Before dataset
initialization it is None; the next explicit read can discover the schema.

`durable` separately contains a bounded prefix of stored point records. The limit
is 1–100 records; `truncated` indicates omitted records. With no current acquisition,
including after the step completes, `durable` and `live` are None. This is a small
preview, not a continuous stream or full dataset export. Reading never forces a
flush. Completed-point coverage may remain zero while a live record is visible;
received, durable and recoverable coverage are different facts.

Every preview is explicitly `provisional`: partial coverage is not a sealed
analysis dataset. Use the child's completed count and accepted point plan to
interpret progress; an adaptive plan's accepted size need not be its final size.
Progress and records are successive observations, not an atomic snapshot, and
execution may finish between the reads. Even then use `job.result(step=...)` for
the retained successful acquisition and normal analysis/provenance APIs.
