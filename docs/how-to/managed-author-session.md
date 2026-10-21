# Run an experiment from a notebook

For a first device-free experiment, start with the installed application and
[First experiment](../getting-started/quickstart.md) in Help. For your own laboratory
code, create or select a folder in **Settings → Author code**, then choose its Python
in your editor. Keep Scopecat open; registered folders connect to its application service
and share application-owned data. No per-folder daemon startup is needed.

For an ordinary Notebook, use the shared session entry from the author folder:

```python
import scopecat as sc

session = sc.notebook()
```

Registration stores the author-to-application association in per-user local
metadata, outside your source folder. An independently launched VS Code kernel
can therefore find the same application without inherited launcher variables.
New registration does not write `scopecat.runtime.toml` or create `.scopecat`
locks in an otherwise fresh author folder. Application updates keep this
association because it names the data instance, not its executable or version.
The association pins the existing scientific `project_id` and author registration
ID; its application path is only a local address. Replacing data at that same
address fails the identity check. Explicit registration may initialize an empty
selected application using the normal store bootstrap under ownership locks;
Notebook discovery never initializes a store.

Interrupted registration leaves a pending owner record. Repeat registration with
the same application to finish it; a different application cannot claim it.

After an application restart, close the old session and call `sc.notebook()` again
to read its current endpoint. Retained jobs can use `job.reconnect(session)`.
If the application is stopped, unavailable or its registration conflicts, the
call fails: it never selects another running/default application or creates a
new data store. Start the associated application and retry. A moved author folder
requires explicit registration; moving an application requires deliberate owner
recovery rather than silently changing the association.

Advanced callers may pass `daemon=` to `sc.notebook()` or use
`SCOPECAT_DAEMON_URL`; both still verify the registered owner. Development kernels
can use `SCOPECAT_APPLICATION_ROOT` for an exact registered instance. This override
must agree with any persistent association. Without registration or inherited
configuration, reopening an editor does not guess a development worktree.

The locator directory is `%LOCALAPPDATA%/Scopecat/author-locations` on Windows,
`~/Library/Application Support/Scopecat/author-locations` on macOS, and
`$XDG_DATA_HOME/Scopecat/author-locations` (default `~/.local/share`) on Linux.
`SCOPECAT_LOCATOR_HOME` selects an absolute directory for isolated development or
tests; all participating processes must use the same value. Do not copy these
machine-local records into a portable author template.

Existing explicit runtime files remain readable. Explicit registration adds an
association only when all effective runtime locations and settings match the
selected owner; the original file stays untouched. Custom or conflicting bindings
must be resolved deliberately. Registration never moves scientific data, deletes
old lock files, or takes over an active independent runtime. This connection
change does not migrate old scientific store formats or automatically transfer
an author between applications.

This API guide is for an already prepared laboratory author folder. Its maintainer
supplies the execution environment, parameter branch and registered experiments;
use the existing-folder preparation in [application maintenance](maintain-application.md#author-folders).
The examples assume a `bench-a` setup, a laboratory `qubits` table and a `signal`
experiment; substitute your laboratory's names. These are not the analytic
starter's declarations. Sample/target selection is a separate choice, not a
prerequisite for editing parameters.

```python
import scopecat as sc

project = sc.open_project("/path/to/lab")
with project.authoring() as author:
    author.use(parameter_branch="experiment", setup=author.setup.get("bench-a"))
    parameters = author.params
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
    saved = parameters.save()
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

## Open retained objects in the desktop

With the connected application desktop running, call `job.open()` to inspect the
admitted task or `run.open()` to inspect its measurement. A procedure handle also
supports `task.open(view="decision")`. A published analysis supports
`publication.open()` and retains its exact project, run or sample owner. These
calls request a native window and wait for the host's acknowledgement; they do
not submit work or launch an application. The host chooses its installed or
development UI address, so notebook code does not construct URLs.

If the desktop is unavailable, open that application's desktop and retry. If an
opening times out, retain the exception's request and wait on the same request:

```python
from scopecat.api.application_open import ApplicationOpenTimeout

try:
    run.open()
except ApplicationOpenTimeout as error:
    error.request.wait(timeout=10)
```

Do not issue a new `open()` merely to retry an uncertain request. Requests expire
after one minute and cannot replay into a restarted host; inspect existing windows
before making a fresh request after expiry or a host change. Portable analysis
files must first be imported into the application before opening there.

## Refresh shared code and compiler code

Keep ordinary laboratory Python modules under the project's declared
`authors.source_roots`; a separate `refresh_roots` declaration is not required.
After saving edits to experiments, analysis, recipes, shared helpers or compiler
factories, use the same author connection:

```python
state = author.state()
author.refresh_authors(expected_generation=state.generation)
checked = author.prepare("signal")
```

Refresh validates the captured source in a fresh process while the application
stays running. Each prepared request and saved plan keeps its own source version;
editing shared code does not alter a request that is already prepared. If a
compiler error appears for the selected setup, correct the source, refresh and
prepare again. Previously retained source remains available for older tasks.

Driver changes use the separate [idle-device update](control-instruments.md#update-driver-source-while-the-application-is-running).
Author refresh keeps the current device driver version. Dependencies must already
be available in the application's environment.

## Edit a request before preparing

When the laboratory's author package is installed in the notebook environment,
use its experiment declaration to create a request. Request creation checks the
function's call signature and captures supplied values; it does not build the
program, import a device SDK or acquire data. Importing a laboratory module still
executes that module's ordinary Python top-level code.

```python
from my_lab.authored.signal import signal

request = signal(gain=1.0, polarity="positive")
request.values["frequency"] = sc.Scan(sc.Quantity(f, "GHz") for f in (5.0, 5.1, 5.2))
with project.authoring() as author:
    author.use(parameter_branch="experiment", setup=author.setup.get("bench-a"))
    parameters = author.params
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
print(checked.preview.code_revision, checked.preview.reviewed.config_source)
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
editing a declaration in an explicit `project.authoring()` session, refresh and
repeat its ordinary import before constructing a new request, or select the original
revision. With `sc.notebook()`, new requests select saved source automatically as
explained below. Helpers execute from the selected managed revision; importing a request does not pin notebook helper
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
under the bound data root's `author-jobs/` directory. For application-registered
folders this is application data, not a new data owner inside the code folder.
Keep the printed receipt path alongside your notebook's run references. The receipt
contains the exact reviewed request and an identity for finding its durable
procedure, not a copy of the run data.

```python
with project.authoring() as author:
    job = author.reopen(receipt)
    run = job.result()
    values = run.measurements()["result"].require_values()
    parameters = author.parameters.workspace("experiment")
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
generation bookkeeping while preserving concurrent-update conflicts. In an explicit
`project.authoring()` session, saving edits alone does not select them.

For Notebook/IPython, `author = sc.notebook()` defaults to selecting saved source
before each cell and when creating new requests. Both normal import styles work;
new modules become available in the next cell. Existing requests, prepared
experiments and runs keep their original source. Arbitrary Python objects and
helper aliases are not hot-reloaded. See the
[source selection guide](refresh-author-code.md#notebook-path) for the
`live=False` option and the boundary with explicit refresh.

For deliberate old-code selection, retain `checked.preview.code_revision` and use
`author.prepare(..., code_revision=old_revision)`. The catalog is loaded from that
same revision. Saved experiment plans also retain exact code/configuration.
Ordinary analysis is available through `author.analyze_as(run_id, "module:function",
ResultType, arguments={...})`. It uses the run's original source by default;
`source="current"` explicitly selects the session's current source. Read a saved
publication without executing again via
`author.run(run_id).published_analysis(publication_id).result_as(ResultType)`.
The lower-level `author.analyze(..., code_revision=...)` supports exact revision
selection. See [ordinary analysis](../guides/ordinary-analysis.md) for the supported
arguments and publication contract.


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

### Prepare a background calibration task

`author.calibration_tasks` (also available on `sc.notebook()`) uses the existing
`CalibrationTaskPlan`, `task_call`, repair budget and finalization contracts:

```python
prepared = author.calibration_tasks.prepare(
    "calibration-round-1",
    plan,
    calls=calls,
    finalization=finalization,
    repairs=repairs,
    repair_budget=repair_budget,
)
view = prepared.submit()
view = author.calibration_tasks.start(view, actor="author", reason="Run this round")
```

Build the calls after selecting the author revision and importing its declarations.
Preparation captures one `ProcedureSource` and detached normalized call intents for
all stages, repairs, candidate verification and finalization. Session experiment
defaults do not change those inputs. The application validates the templates and
resolved invocations against the retained source before admission. It owns worker
execution and task advancement; no second client or Notebook polling loop is needed.

`submit()` saves the specification but does not start it. Retry the same prepared
object and task ID after an uncertain response. Changed source files and mutations
to the caller's input dictionaries or the exposed command copy cannot change that
retry. `prepared.reconnect(new_session)` checks workspace, data store and deployment
before reattaching; submission rechecks the application's identity.

Use `author.calibration_tasks.get(task_id)` or bounded `list()` after reopening.
`wait(task_id, timeout=90)` only observes: it returns a stopped task, admission error,
or procedure attention/input state. A timeout stops waiting without cancellation.
Inspect the returned view before explicitly calling `start(view, actor=…, reason=…)`
to continue admission. Existing procedure controls still handle human input and
reconciliation. Pause/cancel prevent future stage admission; already admitted
procedures retain their own controls.

Stage evidence is in `view.progress.stages`; attempts and their phases are retained
in `view.task.attempts`. Read each procedure with `author.procedures.get(id)` and
its outputs through `author.run(...)` or `author.published_analysis(...)` on the same
session. Finalization is a separate procedure in `view.finalization`. Task mode
`finished` and successful stage checks do not imply scientific acceptance or branch
publication: inspect the final decision and publication receipt. These reads never
retry a stage, refresh source, advance a task or publish a branch.
