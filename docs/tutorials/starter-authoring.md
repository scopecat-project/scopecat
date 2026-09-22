# Edit and analyze a starter experiment

Use the small project created by `scopecat init` in the
[pilot quickstart](../getting-started/quickstart.md). You do not need the reference
lab, its device topology, quantum compiler, or calibration policies.

Start the same project and open its GUI:

```sh
scopecat start ./my-lab
scopecat open ./my-lab
python ./my-lab/notebooks/02_edit_scan.py
```

For a source checkout, use the built GUI directory with `--static-dir` as described
in the quickstart's build section. Both scripts and GUI discover the project's
actual daemon endpoint; do not copy an old port number.

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

The thermometer experiment lives in `authored/thermometer.py`; the first Notebook
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
workspaces and saved plans, or choose another [tutorial sandbox](teaching-sandboxes.md).
Device and quantum extension authors can follow the
[extension guides](../extensions/index.md).
