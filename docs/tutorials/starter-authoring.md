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
request = signal(center=0.0)
request.values["position"] = sc.Scan([-1.0, 0.0, 1.0])
prepared = author.prepare(request)
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

## Edit code and retain evidence

Request value changes need another prepare, not a source refresh. After editing
`authored/signal.py`, call `author.refresh()`. In an interactive notebook, restart
its Python kernel and re-run setup before importing the changed declaration;
source refresh does not mutate objects already imported into your kernel. Keep
the original run ID or receipt and reopen it instead of rerunning acquisition.

`analyze_as(run.id, "scopecat_lab.authored.signal:summarize", Summary)` selects the
run's original source by default. After refresh, `source="current"` explicitly
reanalyzes using the edited source. The registered analysis selector is still a
qualified string; this lesson does not invent an unreleased analysis shortcut.

A wait timeout ends this wait. Call `job.wait(...)` again or reopen the receipt;
calling `prepared.run()` again creates a new acquisition.

## Where to go next

Use [managed author sessions](../how-to/managed-author-session.md) for parameter
workspaces and saved plans. The [reference lab](reference-lab.md) is an optional
integration laboratory for physical routing, quantum compilation, and calibration
recovery. It is not a project template and need not be copied into your workspace.
