# Runnable tutorial sandboxes

Start from one complete Notebook, make a small change, then discard or reset the
exercise. No reference-lab checkout, laboratory configuration or AI assistant is
required. These tutorials use synthetic computation and never connect devices.

## Install once

Obtain the tutorial delivery for your operating system, CPU and Python 3.14 ABI.
Prepare Python 3.14, uv and VS Code with its Python/Jupyter extensions. From the
received delivery directory run `python install.py`. The installer verifies and
copies the complete offline delivery into `~/Scopecat-Lab`. Install newer deliveries
into that same home. Failed preparation keeps the previous default entry; after
fixing the reported cause, rerun the installer without manually deleting incomplete
managed environments. See [fixed delivery maintenance](../how-to/maintain-application.md#install-a-newer-fixed-delivery).

On Windows, open `Scopecat-Lab/lab.cmd`; elsewhere run
`python ~/Scopecat-Lab/lab.py`. This opens the local Scopecat management page.
Expand **Help · Teaching and practice**, then choose a topic and **Open / continue**; the service prepares the exercise and opens
its folder and Notebook in VS Code. The installed command `scopecat teach` opens
the Help section directly. The default page lists experimental services, which
are registered separately with `scopecat app PATH`. There is no need to activate environments, choose ports or number
project folders. Repeated launching connects to the same management service.

Each topic has its own environment and one complete Notebook:

| Topic | Runnable starting point |
| --- | --- |
| `parameters` | Edit a typed parameter table and run a seven-point scan |
| `compute` | Return average complex IQ and read typed rows using a shared unit alias |
| `refresh` | Edit defaults, refresh author code and import a newly added experiment |
| `groups` | Analyze two groups from a retained scan and reopen their summaries |
| `calibration` | Fit, verify and publish a synthetic zero offset; recover paused work and retain check-only evidence without changing parameters |
| `joint-calibration` | Compose two candidates, remeasure the joint settings and distinguish coupled failure from missing target coverage |

Open the generated project folder and select its `.venv` kernel. The first cell
rejects the wrong interpreter. Run All before making one small change. The current
Notebook instructions are in Chinese; this page explains the common entry in English.
Detailed API explanations follow the [learning path](../getting-started/learning-path.md).

Repeated opening continues the same exercise. To reset, first close its Notebook
kernel and use **Stop exercise service**, then **Reset exercise**. A fresh copy is
created; the old copy and its edits remain in the list. **Delete old copy** requires
confirmation and is unavailable for the current copy or a copy with running
processes. Old copies are never deleted automatically. Copy any source or Notebook
you want to retain elsewhere. Cleanup does not delete release bundles or manage
real projects.

The page shows operation progress, results and logs. Closing the browser does not
interrupt preparation or verification. Reopen the launcher to reconnect. If an
operation process exits unexpectedly, its status becomes interrupted; inspect its
retained files and logs before retrying. It is never silently replayed. Exiting the
management service leaves exercise services running; stop those explicitly from
their cards when finished. Installing a newer release selects the new manager on
next launch; active management work must finish first.

Real scientific projects and retained experimental evidence do not belong in this
resettable directory and continue to use [current-format backup/restore](../how-to/backup-and-restore.md).
Earlier development data follows the [prebaseline policy](../development/data-compatibility.md).

## Source development without GUI builds

From a Scopecat checkout, run `uv run python teach.py source`, or double-click
`teach.cmd` and choose 1 on Windows. The entry installs local wheels and locked
teaching dependencies and opens the local management page. Exercise services use
the API-only runtime.
It does not build the GUI or require Node. Only the exercise's author package is
editable; new framework/tutorial contents receive a new sandbox identity.

Stop old services and close kernels before selecting an updated source version.
Each exercise retains its original installed runtime. Source trials provide quick
API feedback; fixed deliveries provide reproducible installation acceptance.

## Maintainer delivery

`teach.cmd` choices 2 and 3 build and verify a fixed local delivery, respectively
with and without installing it on the build computer. The equivalent commands are
`uv run python teach.py release --install` and `uv run python teach.py release`.
A release requires a clean checkout, Python/uv, Node/pnpm and build-time network
access. Output directories are generated automatically. A failed stage prevents
subsequent stages from running.

The build exports the reviewed `uv.lock`, builds public wheels and a matching GUI,
and records hashes and source identity. Verification uses an empty cache and
runs the actual shipped notebooks outside the checkout, including source refresh,
new modules, history, recovery, duplicate installation and reset. Windows and Linux
run this in the manually dispatched public Full acceptance workflow. CI artifacts expire; retain accepted
release bundles separately. Successful CI is software evidence, not human or
physical-device acceptance.

An explicit standalone tutorial workspace can also be generated with
`scopecat init PATH --topic compute` in a teaching installation. The management page is the
default for disposable exercises; `init` without a topic still creates the small
virtual-instrument project used for integration and application development.

Laboratory-specific package selection, SDKs, addresses, bindings, acceptance
policies and scientific records remain owned by the consuming laboratory.

## What belongs to the exercise

Starting a new exercise installs only its synthetic setup. It does not publish a
global parameter default. The Notebook's `open_parameters(session)` creates or
opens `teaching-table`, then selects that branch in the session. Saving parameters
advances that branch; previews retain exact parameter/setup references, and
reopening a result does not require selecting today's branch again.

Before the setup cell has selected parameters, a preview reports how to select a
branch or pass `parameters=...`. Run the setup cell, or use
`session.use(parameter_branch="teaching-table")` when reopening a saved branch.
In the workbench, select the saved branch in **Measurement context**. The tutorial
does not need **Use lab parameter default**.
Existing exercise data is retained; upgrading does not delete defaults saved by
older versions or rewrite their runs. Use a fresh sandbox to try the new starting
state.

Each topic includes editable `my_experiment/parameters.py`, `response.py`,
`teaching.py` and `setup.py`. The first declares local parameter models; the next
two define the synthetic response and experiments. The last prepares the example
sample and parameter workspace using normal public APIs. The support package owns
the templates and environment tools, not the learner's scientific declarations.
The parameter topic demonstrates adding a second table alongside `Drive`.

## Notebook workspace and saved edits

The topic Notebooks initialize one default workspace:

```python
import scopecat as sc

session = sc.notebook()  # live=True by default
session  # project, source revision, mode and refresh status
```

The entry checks the project kernel when a local `.venv` exists, connects to the
service prepared by the launcher and admits author source. It never launches an
experiment. Repeating initialization reuses the session and its one cell hook.
A kernel has one default workspace; multiple documents sharing a kernel share it.
Close `session` before switching projects. Scripts keep `project.authoring()` and
explicit source selection; background threads do not inherit live request selection.

Both ordinary import styles support saved edits:

```python
from my_experiment.teaching import teaching_rabi
import my_experiment.teaching as experiments

first = teaching_rabi()
second = experiments.teaching_rabi()
```

Save an edited source file and call the experiment again. Each new request selects
the saved definition, including new default arguments and helper code. Existing
requests, previews and running jobs retain their original source. The Notebook's
module aliases update at cell boundaries; new modules and new experiment attributes
are available in the next cell, without manual refresh or re-import. If a cell
writes a new module itself, put its import in the following cell.

Invalid source is reported in the current output. Repair and history cells remain
usable, but new experiment requests retry the refresh and fail until it is fixed;
there is no old-code fallback. Reading a historical revision does not change the
source selected for subsequent live requests.

Use `sc.notebook(live=False)` to hold the selected source, or
`sc.notebook(live=True)` to resume saved edits. This is a source policy: parameter
edits still need their ordinary save operation. Ordinary Python functions, class
instances and aliases hidden inside containers are not rewritten. Static editor
signatures may need the language server to notice a changed file.

After a kernel restart, run only initialization and `session.history()` to find a
retained run, then reopen its number with `session.run(number)`. Do not rerun the
experiment just to restore a Python handle. `session.close()` removes the cell hook
and default selection; it closes the client connection, not a running experiment.

The lower-level `session.live(declaration)` remains useful for selectively live
callables outside a Notebook workspace. It does not install a kernel default.

## Outputs and history

Simple experiments return string-keyed dictionaries of deferred data. Keys become
recorded field paths; dataclasses remain available for typed composition and
field-specific recording policies. Neither form makes the deferred references
into already-computed Python values. A reader may use `rows_as(...)` to validate a
native row schema, including units. The output dataclass is not that native row.

Both synthetic response and mean computations use `@sc.compute`. The response uses
`@sc.compute(output_type=shot_array)`: the schema factory receives the structural
`shots` argument and declares a fixed shot axis for this request. Unknown-length
arrays are a different contract and are not a substitute for a fixed dense axis. Call `.eager(...)` for an ordinary NumPy computation.
Both this syntax and `experiment.compute(fn=...)` allocate node IDs automatically;
explicit IDs are optional, and array/unit semantics remain explicit.

`session.history()` displays local timestamps, names, status and stable project-local
run numbers. Reopen a selected run with `session.run(12)`; the number is not a row
position and does not change when newer runs arrive. `session.run_number(run)`
provides the number for a current handle. Complete run IDs remain the portable
identity; short numbers only make sense within their original project. Pagination
uses `session.history(before=page.next_cursor)`.

## Development and automated acceptance

The ordinary installation uses one host at `~/Scopecat-Lab`. Development and tests
may select an isolated home with `--home PATH`; each gets its own lock, endpoint,
operation database and logs. Topic arguments such as
`scopecat teach parameters --verify --home PATH` call the same management API as
the page. `--status` reports managed state; `--shutdown` closes only that host.
Source development stops its manager before updating the source runtime, preserving
exercise processes and rejecting the update while management work is still active.

The manager also opens explicitly registered experiment services. Teaching remains
synthetic and separately managed. Child services retain their own execution and
physical-device ownership; the manager does not establish cross-service exclusion,
enable LAN access, install a tray icon or register system autostart.
See the [application host architecture](../development/architecture/application-host.md).
