# Tour the reference lab

The reference lab is Scopecat's runnable gallery: a deterministic four-qubit
project with virtual RF and DC sources, temperature monitor, VNA, shared LOs,
AWGs, digitizer, timing controller, and oscilloscope.

The [pilot quickstart](../getting-started/quickstart.md) covers the installed
starter project. This gallery uses the source workspace: run `uv sync --locked`
and build its GUI with `pnpm --dir apps/scopecat-ui install --frozen-lockfile`
and `pnpm --dir apps/scopecat-ui run build` first.

## Start the lab

From the repository root:

```sh
uv run scopecat config check examples/reference_lab
uv run scopecat start examples/reference_lab --static-dir apps/scopecat-ui/dist
uv run scopecat open examples/reference_lab
```

Every gallery script discovers this daemon through the project manifest.

Run the lab tour to establish the shared starting state:

```sh
uv run python examples/reference_lab/notebooks/00_lab_tour.py
```

Its summary should list the configured instruments, their availability, and the
reviewed parameter-table row counts. Users should not need daemon URLs or
database identities to establish this context.

Then run the physical-subject workflow:

```sh
uv run python examples/reference_lab/notebooks/05_sample_workflow.py
```

It registers and revises a stable chip, binds the active revision to a Ramsey
run, and publishes a sample-owned conclusion over that exact run. In the
**Samples** workspace, the run opens the historical revision it actually used,
while the active sample remains independently visible.

## Keep a notebook connection open across cells

Create one connection in a setup cell and keep it for interactive reads. If you
rerun this cell, close the old connection before replacing it:

```python
import scopecat as sc
from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.workflows.temperature_diagnostic import temperature_diagnostic

project = sc.open_project(EXAMPLE_ROOT)
if "lab" in globals():
    lab.close()
lab = project.connect(operator="notebook")
```

Run one virtual diagnostic in another cell and explicitly capture its state:

```python
run = lab.run(temperature_diagnostic(), name="Notebook thermometer")
snapshot = run.snapshot
run_id = snapshot.run_id
print(snapshot.status)
```

`run` is a live handle. Each `run.status` or `run.snapshot` access reads current
state through its original connection. `snapshot` is an immutable local value;
it performs no HTTP and remains readable after closing. A terminal snapshot has
an outcome. A snapshot captured earlier stays unchanged as the run progresses.

Dataset objects also load lazily. Materialize the values needed locally before
closing, for example `records = run.measurements().records`; an unread dataset
still needs its connection. Close the kernel's session explicitly when finished:

```python
lab.close()
print(snapshot.status)  # Captured state remains usable.
```

A lazy read through the old handle or an unread dataset now raises
`SessionClosedError` from `scopecat.kernel.errors`, with reconnection guidance.
Closing a connection does not stop the daemon or delete the run. To read the
retained results in a new kernel or after restart, keep the project path and run
ID and attach through a new connection:

```python
with sc.open_project(EXAMPLE_ROOT).connect() as lab:
    retained_run = lab.get_run(run_id)
    records = retained_run.measurements().records
```

`get_run` attaches a new handle; it does not execute or resume acquisition. The
old handle keeps its original lifetime. Supplying a `DaemonClient` directly to
`LabClient` retains caller ownership: closing that wrapper does not close the
supplied connection. `lab.is_closed` reports the underlying connection's state.

For a complete executable close/reattach example:

```sh
uv run python examples/reference_lab/notebooks/02_session_lifetime.py
```

Its summary confirms the same terminal snapshot and retained measurement after
both connections have closed. Short scripts should continue using `with` blocks
so exceptions also release their connections.

## Inspect and control instruments

Open the **Instruments** workspace, then run in another terminal:

```sh
uv run python examples/reference_lab/notebooks/10_direct_control.py
```

The script reserves typed virtual devices and changes their state outside an
experiment. The virtual world is coupled: enabled flux bias moves the VNA notch
and changes mixing-chamber telemetry. These clients use the same interfaces as
real providers.

Success means the summary contains successful temperature and trace receipts,
and the source output is disabled again even if acquisition fails. The GUI may
change, but it must make instrument availability and session failure attributable
to the affected device.

## Complete a calibration

Run the supported DRAG-beta workflow:

```sh
uv run python examples/reference_lab/notebooks/30_drag_calibration.py
```

In the project console, inspect the new runs, measurement data, analysis, and
configuration history. The workflow records a baseline, publishes a candidate,
runs the same scan with that candidate, and publishes a project-level
baseline/candidate verification. Only a passing verification accepts the
candidate; the workflow then uses the new default in production and demonstrates
undo. Durable revisions and decisions remain visible without exposing storage
identifiers in the ordinary notebook flow.

The structured summary verifies the design outcomes directly:

- the baseline run completes and records the previewed point count;
- analysis publishes outputs, evidence, a report, and one proposal;
- the candidate run identifies the analysis proposal as its config source;
- project analysis freezes both run datasets and records the acceptance metric;
- the production run uses the accepted default;
- undo resolves the previous distinct immutable entry and reactivates it through
  the same idempotent operation ledger without deleting history.

If users must manually transfer revision IDs between these steps, or cannot tell
which configuration a run used, treat that as workflow design feedback rather
than an explanation to add to the tutorial.

## Continue through the gallery

The [complete reference-lab gallery](https://github.com/scopecat-project/scopecat/blob/main/examples/reference_lab/README.md#gallery)
maps each tested script to its intended scenario. Useful next steps include:

- `20_flux_spectroscopy.py` for scan data, fitting, and parameter proposals;
- `21_scan_shapes.py` for point clouds, repeat, and traversal;
- `40_measurement_workbench.py` for selection, Xarray, Arrow, and paged reads;
- `50_ragged_scope_capture.py` for variable-length waveforms.

Stop the lab when finished:

```sh
uv run scopecat stop examples/reference_lab
```
