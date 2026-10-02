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

## Notebook workspace and saved edits

For your own experiments, open your registered source folder in VS Code and use
the interpreter shown in Application settings. Python files and notebooks share
the application service. See the [learning path](../getting-started/learning-path.md)
for authoring and analysis; the synthetic course sources remain independent
maintainer fixtures, not separately managed tutorial installations.

The optional command-line entry uses the same installed application:
`python -m lab_tools.practice --home DATA_HOME` starts a practice; add `--list`
to list practices or `--clear ID` to clear one while preserving its files.
It prints locations without opening a browser. Per-topic environments and
`--reset` / `--stop` sandbox commands are retired.
