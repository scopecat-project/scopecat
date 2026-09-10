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
    parameters["qubits"][sc.EntityRef(kind="logical_qubit", id="q0")][
        "drive_carrier_frequency"
    ] = sc.Quantity(5.1, "GHz")
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
