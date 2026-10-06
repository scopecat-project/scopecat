# Practice in Scopecat

Open **Help → Start peak practice** in the workbench. You do not need a device,
another application, a Python environment or a notebook kernel.

## Try a scan and a manual decision

1. Start the practice and choose **Open practice task**.
2. The task generates a synthetic frequency scan. When it asks for your judgment,
   choose **Review curve and enter frequency**.
3. Inspect the curve in Decisions. Select **Peak selected** and enter a frequency
   in MHz, or choose **No clear peak** / **Unsure** and leave the frequency empty.
4. Record your answer, then explicitly continue the task. A practice choice is
   not a published calibration.

You can close the page and return through Help. The existing task retains its
scan and decision; opening it does not measure again. Start a new practice when
you want a fresh attempt.

## Keep notes and finish

The optional notes folder shown in Help opens directly in VS Code. It contains
ordinary Markdown files; editing notes does not require a kernel or another
service. **Export practice files** downloads a copy.

Choose **Clear practice…** to stop that practice and remove its measurements,
analyses and decisions. The default keeps your notes and edited files. Choose
**Delete all files in this practice folder** only when you want those removed too.

An unfinished cleanup stays visible with **Retry cleanup**. Other practices,
ordinary measurements, device settings and source folders are not selected.
Closing the workbench page alone does not clear anything.

## Clean up ordinary measurements

Open a measurement, project analysis or completed task and choose
**Review data cleanup…**. Inspect the selection,
estimated file size and any retaining references before confirming deletion.
A task must finish or be cancelled through its normal controls first. If another
analysis, calibration or published parameter revision still needs the evidence,
cleanup explains what retains it; it does not silently delete dependent results.

**Data → Data cleanup history** shows unfinished operations and lets you retry
file reclamation after an interruption. Record removal and file removal are
reported separately. [Back up scientific records](../how-to/backup-and-restore.md)
before deleting anything you intend to retain.

## Parameters and scans in a Notebook

In desktop **Help → Start parameters Notebook**, Scopecat creates an ordinary
code folder and prepares its local Python kernel and registered execution
environment. It uses a new folder under the application home by default; use
**Choose another save location…** before starting if you prefer another parent.
No source-registration or dependency forms are needed for the supplied lesson.

Help opens `notebooks/parameters.ipynb` in VS Code. Install its Python and Jupyter
extensions and select the displayed `.venv` interpreter as the Notebook kernel.
If the editor cannot open automatically, Help retains the exact folder, Notebook
and interpreter paths so you can open them manually. If preparation fails, use
**Retry preparation**; already-created files and edits are retained.

Run the shipped cells in order: connect with `sc.notebook()`, open the lesson's
independent parameter branch, preview seven points, then explicitly acquire
64 shots per point. The separate result cell only reads the retained run and
links to it in this application; you can repeat that cell without acquiring again.
Save a change from `shots: int = 64` to `32` in `src/my_experiment/teaching.py`,
then reconstruct the request and run it: the new result has seven points and
32 shots, while the old result retains its original source and 64 shots.
Change the scan to five points and update its preview assertion for a five-point
result. Already prepared requests retain their original code.

**Continue parameters Notebook** opens the same files and environment. Reconnect
and reopen parameters, or use **Runs** to read previous results.
After a kernel restart, copy a run ID from Runs and use
`run = session.run("your run ID")` before executing the result cell to continue
analysis in the Notebook. Do not use Run All just to restore results. Opening Help,
a Notebook or history does not acquire data; executing the acquisition cell
explicitly creates a new run. Saved parameter edits and source edits are retained.
Continue does not replace an existing Notebook with newer lesson material. In an
older copy with acquisition and display together, move the result-reading lines
to a separate cell before repeating them.
Each newly generated folder has its own setup, initial revision and branch names.
The Notebook and `teaching.py`, `parameters.py`, `response.py`, `setup.py` and
`workspace_app.py` are editable local source. You can inspect and change the
parameter declarations, response and scan rather than importing hidden experiment
definitions from the installed teaching package.

This lesson is persistent ordinary author work, not the resettable peak practice
scope above. The supplied source computes a synthetic response without devices;
it has the ordinary author permissions, not a sandbox for arbitrary edited Python.
Use normal Data cleanup for retained runs and keep or remove your source files
separately. Other teaching topics remain maintainer material; this entry does not
create a course manager, a separate application service or an embedded editor.

## Notebook workspace and saved edits

For your own experiments, open your registered source folder in VS Code and use
the interpreter shown in Application settings. Python files and notebooks share
the application service. See the [learning path](../getting-started/learning-path.md)
for authoring and analysis; the other synthetic course sources remain independent
maintainer fixtures, not separately managed tutorial installations.

The optional command-line entry uses the same installed application:
`python -m lab_tools.practice --home DATA_HOME` starts a practice; add `--list`
to list practices or `--clear ID` to clear one while preserving its files.
It prints locations without opening a browser. Per-topic environments and
`--reset` / `--stop` sandbox commands are retired.
