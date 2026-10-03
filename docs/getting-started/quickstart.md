# First experiment

Open the installed Scopecat application. This example uses synthetic data and
needs no devices. To inspect a received file instead, use
[Open and share recorded data](../how-to/open-and-share-data.md).

1. In **Settings → Author code → New code folder**, choose a location and name,
   then **Create folder and prepare Python**. Wait for the folder and Python path
   to appear. This explicit preparation is separate from application startup.
2. Open that folder in VS Code. With the Python and Jupyter extensions installed,
   select the displayed Python and open `notebooks/02_edit_scan.py`.
3. Run the cells in order. Preview describes the three scan points without
   acquiring; the submission creates a run. Its result is `[0.5, 1.0, 0.5]`.
4. Inspect the saved run in **Experiments**. Change the scan positions, preview
   again, and submit only when you want another measurement. A wait timeout means
   wait again on the same task, not submit it again.
5. Save your code. Closing the last Scopecat window hides it to the menu bar or
   tray. Choose **Quit** to exit; active work presents a decision. Reopen the
   application and select the retained run without executing the submission again.

Install extra analysis packages in your selected Python, never application Python.
For existing laboratory code or background dependency changes, follow
[application maintenance](../how-to/maintain-application.md#author-folders).

If connection fails, open Scopecat and check the selected interpreter. If preview
fails, correct the reported input or source before submitting. Startup failures
retain retry and quit controls; preserve their details when asking for help.

Framework contributors use [source development](../development/public-preview.md#run-without-installation).
The old per-project pilot environment is not a desktop installation prerequisite.
