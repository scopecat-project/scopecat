# Edit and analyze a starter experiment

For your first Notebook, start with [Parameters and scans in Help](../getting-started/quickstart.md).
You can keep using that course folder. This guide is for a **separate author folder**
with the Settings starter, which uses different source and Notebook files.

In the application, choose **Settings → Author code → New code folder**, select
a save location and create it. Open the new folder in VS Code and select its `.venv`
interpreter. Keep Scopecat open and run `notebooks/02_edit_scan.py` cell by cell.
The example uses synthetic data and needs no devices.

Framework contributors can use the [source development launcher](../development/public-preview.md#daily-source-desktop).
It starts blank; create or register an author folder in Settings before following this guide.
The separate `scopecat init` starter remains available through the
[CLI reference](../reference/cli.md#project-lifecycle); it is not required for the
desktop lesson above. Use the endpoint recorded for the selected application,
rather than copying a historical port number.

## Your editable files

`src/scopecat_lab/authored/signal.py` contains a synthetic response, one experiment,
and one ordinary analysis function. `notebooks/02_edit_scan.py` supplies setup,
creates a request and prints a run link. This is a framework lesson, not a model
or calibration of a real qubit.

```python
params = open_parameters(author)
request = signal(center=0.0)
request.values["position"] = sc.Scan([-1.0, 0.0, 1.0])
prepared = author.prepare(request, parameters=params)
job = prepared.run()
run = job.wait(timeout=120).result()
```

Change the positions and rerun the preparation and acquisition. A `Scan` explicitly
selects an axis; a plain array does not. `prepare` captures the request. Changes
made later require another preparation. `request.copy()` creates an independent
request for comparison; creating or editing a request never starts acquisition.

The supplied three points produce `(0.5, 1.0, 0.5)`. The script publishes a
`Summary(mean=2/3, points=3)` using `analyze_as`, records the analysis source and
opens the retained run again through its receipt. Inspect the same run in the GUI.

`authored/parameters.py` owns the parameter model and opens the `starter` branch.
The `scale` column has a real effect on the response:

```python
params["response"]["signal"]["scale"] = 2.0
params.save(note="Compare a doubled response")
```

Repeating the same scan now gives `(1.0, 2.0, 1.0)`. Reopening a Notebook preserves
the saved branch values; it does not reset them to the source defaults. For a
disposable comparison, `params.save("trial")` forks the branch. Previous previews,
runs and analyses retain their original inputs. Startup, editing and running
create no global parameter default or artificial working point.

The separate CLI starter also includes `authored/thermometer.py`; its first Notebook
uses the same author-session preparation path. Both scripts load the project's
local package before refreshing its author modules, so ordinary imports work
when running a generated script from outside its source directory.

## Edit code and retain evidence

Request value changes need another prepare, not a source refresh. After editing
`authored/signal.py`, refresh and repeat the normal import:

```python
author.refresh()
from scopecat_lab.authored.signal import signal
```

Use the same steps for newly added author modules. There is no kernel restart or
manual reload step. Previously imported aliases and existing requests retain their
original definitions; create a new request from the newly imported declaration.
Keep the original run ID or receipt and reopen it instead of rerunning acquisition.

`analyze_as(run.id, "scopecat_lab.authored.signal:summarize", Summary)` selects the
run's original source by default. After refresh, `source="current"` explicitly
reanalyzes using the edited source. The registered analysis selector is still a
qualified string; this lesson does not invent an unreleased analysis shortcut.

A wait timeout ends this wait. Call `job.wait(...)` again or reopen the receipt;
calling `prepared.run()` again creates a new acquisition.

## Where to go next

Use [managed author sessions](../how-to/managed-author-session.md) for parameter
workspaces and saved plans, or choose another [Notebook lesson](teaching-sandboxes.md).
Device and quantum extension authors can follow the
[extension guides](../extensions/index.md).

## Rebuild this folder's client environment

After **Settings → Author code** prepares the local environment, keep
`requirements-client.txt` and `author-environments/` with your source. The lock
names its source snapshot: framework wheels copied from the selected delivery,
`client.in` (Scopecat and ipykernel inputs), `delivery-constraints.txt`,
`delivery.json` (delivery/source provenance), and `python.json` (Python version,
ABI and binary identity). `pyproject.toml` declares additional experiment
requirements; it is not by itself the complete client environment.

Close the Notebook. From the author directory, create a venv with the matching
Python version/ABI recorded in `python.json`, then install the exact client lock:

```sh
uv venv --python <matching-python> .venv
uv pip sync --python .venv --require-hashes requirements-client.txt
```

On Windows this creates `.venv/Scripts/python.exe`; choose that interpreter/kernel
when reopening the Notebook. These commands do not activate a shell environment
or install into the application. A different OS/ABI needs its own preparation;
this lock is not a cross-platform or fully offline distribution. Registry packages
need network access or a populated cache. Custom indexes and third-party local
sources remain explicit prerequisites. Retaining the framework wheels allows the
folder to move without retaining the original application installation path.

To change dependencies, edit `pyproject.toml`, then explicitly prepare/rebuild in
Settings. Preparation checks the selected delivery constraints and retains a new
client lock; incompatible requirements fail rather than upgrading shared framework
dependencies. If you own a `uv.lock`, update it deliberately first: stale locks
are rejected. Ad hoc pip installs are not represented by the saved lock. Keep a
copy of the previous lock when you need to reproduce that client later.

This rebuild affects the editable client only. Published experiments retain fixed
source and application-owned execution environments; changing the client does not
replace their dependencies or reinterpret old results. Prepare and select the
execution environment through the existing application workflow for new source.

## From the lesson to a first real experiment

Start with Help's **Parameters and scans** lesson, then create the separate
Settings author folder described above. Run its synthetic three-point scan once
and keep its receipt. The ordinary template uses `sc.notebook(...)`, the existing
unified session entry; select the correct application through the supported
registration/connection workflow. Application identity and locator improvements
are tracked separately in [#1127](https://github.com/scopecat-project/scopecat/issues/1127).

| Keep the learned workflow | Replace for the laboratory |
| --- | --- |
| Requests, explicit scans, prepare/preview, submit, wait, receipt reopen | The analytic response function with a supported acquisition/driver implementation |
| Parameter workspaces, units, saved branches and analysis publications | Scientific parameter schema, units, limits, scan range, timing, averaging and calibration assumptions |
| Experiment/analysis API structure | `configuration.py`'s synthetic setup: real topology, instrument registry, routing, capabilities and lifecycle policy |
| Editable source and declared dependencies | Required adapters, SDK versions, native drivers and site-specific prerequisites in the appropriate client/execution environments |

Changing an address cannot turn this synthetic response into a physical
measurement. Review the setup and driver contract with the equipment owner,
validate units and small bounded scan inputs, and preview before explicit
acquisition. Choose device exit/failure policy according to the actual equipment;
this lesson does not supply a laboratory default. Keep private addresses,
credentials, SDKs and site parameters outside the public source repository.
After the first intended acquisition, record its receipt and reopen the same
result; do not submit again merely to find it.

The bounded Windows user observation remains **unperformed**. Record the app
version, Python/ABI, selected environment lock, assistance needed and concrete
blockers while a target user rebuilds, edits, runs, then exits and reopens the exact
result/source using the existing recovery workflow. Automated Linux rebuild and
kernel checks do not establish this observation or real-device compatibility.
