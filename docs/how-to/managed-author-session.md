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

request = signal.request(gain=1.0, polarity="positive")
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

An imported declaration retains its source identity from load time. Preparation
compares that contract with the selected revision and rejects a mismatch. After
editing a declaration, explicitly refresh the session and reload the notebook's
experiment module, or select the original revision. Helpers execute from the
selected managed revision; importing a request does not pin notebook helper
objects or claim to fingerprint their transitive dependencies.

The explicit `.request(...)` factory is the current managed editing API.
`experiment(...)` and `.bind(...)` still construct immutable invocations for local
composition. Making the ordinary call create a request, consolidating control
metadata into function inputs, and adding a concrete typed editing model remain
separate convergence work; they are not implemented by this factory.

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
