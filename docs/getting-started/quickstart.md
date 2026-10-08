# First experiment

Open the installed Scopecat application. This example uses synthetic data and
needs no devices. To inspect a received file instead, use
[Open and share recorded data](../how-to/open-and-share-data.md).

1. In **Settings → Author code → New code folder**, choose a location and name,
   then **Create folder and prepare Python**. Wait for the folder and Python path
   to appear. This explicit preparation is separate from application startup.
2. Open that folder in VS Code. With the Python and Jupyter extensions installed,
   select the displayed Python and open `notebooks/02_edit_scan.py`.
3. Run **Imports**, **Connect** and **Preview** one cell at a time. Inspect the
   preview before running **Submit**, which creates one acquisition. **Result and
   analysis** reads `[0.5, 1.0, 0.5]`. Keep the printed receipt path with your notes.
   Do not use Run All when continuing an existing experiment.
4. Inspect the saved run in **Runs**, or open the exact URL printed by **Reopen**.
   Change the scan positions or save edits to `src/scopecat_lab/authored/signal.py`,
   then run **Preview** again to refresh and validate source. Correct any reported
   error before **Submit**. Submit only when you want another measurement. A wait
   timeout means wait again on the same job, not submit it again.
5. Save your code. Closing the last Scopecat window hides it to the menu bar or
   tray. Choose **Quit** to exit; active work presents a decision. Reopen the
   application and select the retained run in **Runs**. To continue in a fresh
   Python kernel, run **Imports** and **Connect**, set `receipt = Path("the printed
   receipt path")`, then run **Reopen** and **Close**. This reads the same run
   without executing **Preview**, **Submit** or another analysis.

Install extra analysis packages in your selected Python, never application Python.
For existing laboratory code or background dependency changes, follow
[application maintenance](../how-to/maintain-application.md#author-folders).

If connection fails, open Scopecat and check the selected interpreter. If preview
fails, correct the reported input or source before submitting. Startup failures
retain retry and quit controls; preserve their details when asking for help.

Framework contributors use [source development](../development/public-preview.md#run-without-installation).
The old per-project pilot environment is not a desktop installation prerequisite.
