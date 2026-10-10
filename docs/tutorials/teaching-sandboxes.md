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

## Choose a Notebook route

Start with **Parameters and scans**, then **Edit and refresh experiments** if you
want to keep authoring. The first lesson already lets you run an experiment, open
its result in Scopecat, edit Python and run again. You can stop there and use the
same editable folder for your own work; seven topics are not seven required steps.

Choose **Mean IQ and typed results** when you need calculated outputs, or
**Grouped analysis and history** when you need to summarize several curves. They
are independent options after the basics, not prerequisites for one another.
Move to **Parameter calibration and recovery** when you need to decide whether a
proposed parameter correction should be adopted. Follow it with **Joint
calibration and coupled checks**, then **Background calibration and publication**
only if your work needs combined corrections and multi-stage tasks.

The preparation steps below are shared: a running Scopecat desktop and VS Code
with Python/Jupyter support. The lessons supply synthetic inputs and editable
source; no device or previous course's data is required. The prerequisites in this
table are suggested knowledge, not a requirement to import another lesson's runs.

| Topic | Be comfortable with | Learn and finish able to |
| --- | --- | --- |
| **Parameters and scans** (`parameters`) — start here | Running a Python Notebook cell and saving a file | Preview and acquire a seven-point scan; read the same run in Notebook and Runs; change shots from 64 to 32 and compare the retained results. Defining a new parameter table is optional. |
| **Edit and refresh experiments** (`refresh`) — continue authoring | The first lesson's request, preview and run cycle | Save an edit, distinguish old and new requests, add an experiment from the supplied source, and reopen an exact run. |
| **Mean IQ and typed results** (`compute`) — optional calculation | Scan points, shots and basic NumPy arrays | Compare per-shot IQ with its mean, read typed rows, and reopen the chosen mean run. Recognize that averaging removes the shot distribution. |
| **Grouped analysis and history** (`groups`) — optional analysis | Scans and retained run IDs | Analyze two amplitude groups across 42 frequency-scan points; add a third group for 63 points; reopen the saved analysis by its publication ID. |
| **Parameter calibration and recovery** (`calibration`) — advanced adoption | Saved parameter branches, measurements and analysis; reading Python functions | Trace a fitted candidate through fresh verification to publication; compare acceptance with rejection that leaves its branch unchanged. Health checks and capability reports are a later extension. |
| **Joint calibration and coupled checks** (`joint-calibration`) — advanced composition | Candidate, verification and publication concepts from calibration | Explain why individually accepted corrections can fail together, inspect the coupled decision and required target coverage, and find the original procedure again. |
| **Background calibration and publication** (`task-calibration`) — advanced tasks | Joint verification and parameter branch versions | Inspect accepted, scientifically rejected and branch-conflict tasks; distinguish passed stages, final verification and an actual publication receipt. |

## Learn with Notebooks

In desktop **Help → Learn with Notebooks**, select a topic, then choose **Start**. Scopecat creates an ordinary
code folder and prepares its local Python kernel and registered execution
environment. It uses a new folder under the application home by default; use
**Choose another save location…** before starting if you prefer another parent.
No source-registration or dependency forms are needed for the supplied lesson.

Help opens the selected course’s Notebook in VS Code. Install its Python and Jupyter
extensions and select the displayed `.venv` interpreter as the Notebook kernel.
If the editor cannot open automatically, Help retains the exact folder, Notebook
and interpreter paths so you can open them manually. If preparation fails, use
**Retry preparation**; already-created files and edits are retained.

For **Parameters and scans**, run the shipped cells in order: connect with `sc.notebook()`, open the lesson's
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
separately. All supplied topics use this preparation and continuation flow.

## Grouped analysis and history

Select **Grouped analysis and history** in Help, then **Start groups Notebook**.
The same preparation flow opens `notebooks/groups.ipynb` in its own editable
folder. Its `groups-…-setup` and `groups-…-parameters` names identify this course’s
synthetic inputs in the shared application; the parameters course retains its
separate `parameters-…` inputs. Existing course folders and edits are preserved.

Connect, open the teaching parameters, and preview the 42-point scan. The next
cell explicitly acquires two amplitude groups and publishes their frequency-curve
analysis. `src/my_experiment/group_analysis.py` is editable. The result cell reads
the saved analysis; it does not collect again or publish another analysis.
Add an amplitude to explore three groups and 63 points in a new run.

**Continue groups Notebook** reopens the same files. After restarting the kernel,
run the connection cell, open the intended run in **Runs**, and expand its grouped
analysis. Use the full run ID in the run header and the **Publication ID** in the
expanded analysis (not its reusable key) in the Notebook’s read-only example.
The saved acquisition output also records this pair of IDs.
Do not run all cells to reopen results. The application retains both courses’
runs, while each course’s source and parameter edits remain independent.

## Continue editing, computation or calibration

The same Help selector also offers these existing capabilities:

| Topic | Explicit new work | Continue after a restart |
| --- | --- | --- |
| Edit and refresh experiments | Save source edits, preview a new request and run the changed or added experiment | Connect, select a retained run ID from Runs and read its result; source edits stay in the folder |
| Mean IQ and typed results | Acquire shot and mean-IQ examples | Connect and select the exact saved run number or ID before the typed result cell |
| Parameter calibration and recovery | Submit a procedure that proposes, verifies and conditionally publishes a correction | Connect and run the read-only lesson history cell |
| Joint calibration and coupled checks | Propose individual and joint corrections, inspect coupled verification | Connect and run the read-only lesson history cell |
| Background calibration and publication | Start a bounded task whose finalization verifies and conditionally publishes its result | Connect and inspect the retained task and its history |

Each topic uses its own setup and parameter names in the same application. Starting
or continuing a Notebook never submits a procedure or task. **Run All** repeats
explicit scientific work; use the marked connection/history cells to inspect existing
evidence. Preserve the exact run, procedure or task identity when comparing attempts.
No topic needs another course's results.

Choose the next topic by the task you want to accomplish. The route above is a
reading guide for the supplied material, not a claim of completed unfamiliar-user
evaluation.

## Notebook workspace and saved edits

You can keep writing in the course folder: it is already an ordinary registered
author directory. Save your changes in `src/my_experiment` and construct a new
request as shown in the Notebook. No graduation,
copying, publication or second registration step is required. Earlier runs retain
their original source and parameters.

Help's **Manage this code folder in Settings** selects that exact directory. Check
its execution Python there; if its local `.venv` is missing, use **Create local
Python environment**, or the explicit repair control for a damaged environment.
If the source folder or Notebook is missing, restore the original files to the
shown location; Continue deliberately does not recreate them over your work.

To start separately, use **Settings → Author code → New code folder**, choose a
save location and a new name, then **Create folder and prepare Python**. Follow
[starter authoring](starter-authoring.md) in that folder. Keep the course folder
and retained results for reference; no transfer of teaching parameters is implied.

Save editor changes before closing. Return to the same course in Help and choose
**Continue**; reconnect and use its history cells to reopen the exact run,
procedure or task. The selected course stays in the page URL when navigating
away and returning or reloading that page. A fresh application window can select
the course again; its saved folder is independent of this page selection.

For your own experiments, open your registered source folder in VS Code and use
the interpreter shown in Application settings. Python files and notebooks share
the application service. See the [learning path](../getting-started/learning-path.md)
for authoring and analysis.

The optional command-line entry uses the same installed application:
`python -m lab_tools.practice --home DATA_HOME` starts a practice; add `--list`
to list practices or `--clear ID` to clear one while preserving its files.
It prints locations without opening a browser. Per-topic environments and
`--reset` / `--stop` sandbox commands are retired.
