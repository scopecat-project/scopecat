# Open and share recorded data

You only need the Scopecat application to browse a `.scopecat` file. You do not
need the original experiment code, laboratory adapter, devices or a Python setup.

## Open a file

Choose **File → Open** (Cmd-O on Mac, Ctrl-O on Windows), then select the file.
Scopecat checks it and opens its records on **Data**. The record ID and source
identify what you are viewing. Opening keeps a copy in the application's data
store; it does not edit the original file or repeat the experiment.

Choose a run if the file contains several. **Measurements** lets you switch
between acquisition history and the retained analysis selection, when available.
Acquisition history includes repeated measurements; the analysis selection shows
the exact points retained for analysis. Large arrays appear as summaries in the
table. Use **Waveform record** to inspect an individual trace; the plot states
when it displays sampled data. Expand **Retained analyses** to read saved
conclusions, their inputs and any tables, figures or attachments.

To compare files, choose **File → New Window** and open the other file there.
Each window keeps its own selected record and zoom. Use Cmd/Ctrl-F to find text
in the current view, Cmd/Ctrl-C to copy selected text, and the **View** menu to
change zoom. Closing the last window leaves Scopecat in the menu bar or system
tray; choose **Quit** there when you want to exit.

## Share a record or save a copy

For a run in your application, select it in **Experiments** and choose
**Export Scopecat file…**. Choose a destination and wait for the saved-path
confirmation. The file includes retained measurements and their scientific
dependencies, which can include other runs used by the saved analyses.

For an imported file, choose **Save a copy…** on **Data**. This preserves its
original contents, including retained analyses. Share the resulting file with
someone using a compatible Scopecat version. This development format is not yet
a promised long-term archival format; keep the application version with important
exports. A portable file is separate from a [whole-store backup](backup-and-restore.md).

If opening or saving fails, the error remains visible and you can retry after
correcting the cause. Cancelling a file dialog preserves the current view.
Repeatedly opening the same data does not create another measurement. A file with
conflicting content for an already imported source/run is rejected rather than
replacing the earlier data.

## Analyze with your own Python tools

Install the matching public Scopecat Python package in your own environment,
alongside your analysis tools. Do not use the interpreter inside the application.
Follow [ordinary Python analysis](../guides/ordinary-analysis.md#analyze-an-exported-file-in-your-own-python-environment)
to read the file and save conclusions to a new `.scopecat` file. Open that result
through **File → Open** to inspect its measurements and new analysis together.
The original file is unchanged; opening the result does not rerun its analysis.
