# First experiment

Follow [download and installation](install.md), then open Scopecat. Start with
**Help → Learn with Notebooks → Parameters and scans**. This lesson uses synthetic
data and needs no devices. To inspect a received file instead, use
[Open and share recorded data](../how-to/open-and-share-data.md).

1. Select **Parameters and scans**, then **Start parameters Notebook**. Help creates
   an editable author folder and prepares its Python. You can choose another save
   location before starting; no source-registration form is needed.
2. Open the displayed `notebooks/parameters.ipynb` in VS Code, with its Python and
   Jupyter extensions installed. Select the displayed `.venv` as the Notebook kernel.
   Help retains the paths if the editor cannot open automatically.
3. Follow the cells to connect, open the lesson's parameter branch and preview the
   seven-point scan. Execute the acquisition cell to collect 64 shots per point.
   The separate result cell reads that saved run and links to it in **Runs**;
   repeating the result cell does not acquire again.
4. Follow the [editing steps in the lesson guide](../tutorials/teaching-sandboxes.md#learn-with-notebooks)
   to change the shots in `src/my_experiment/teaching.py`, reconstruct the request
   and acquire another run. Earlier results retain their original source and inputs.
5. Save your edits and return through **Continue parameters Notebook**. After a
   kernel restart, reconnect and open the lesson's parameters, then select an exact
   run ID from **Runs** as shown in the lesson guide before reading its result.
   Do not use Run All to reopen saved results. A wait timeout means wait on the same
   job, not submit another acquisition.

Keep writing in this course folder: it is already registered with the application.
You do not need to copy or register it again. For a separate author directory, use
**Settings → Author code → New code folder** and follow
[starter authoring](../tutorials/starter-authoring.md); that starter uses different files.

Install extra analysis packages in your selected Python, never application Python.
For preparation failures or existing laboratory code, see
[application maintenance](../how-to/maintain-application.md#author-folders).
Closing the last application window hides it to the tray or menu bar; choose
**Quit** to exit. Reopen Scopecat and **Runs** to inspect the same retained result.

Continue with [Notebook topics](../tutorials/teaching-sandboxes.md#choose-a-notebook-route)
or [continuing an experiment across sessions](../how-to/continue-experiment.md).
Framework contributors use [daily source development](../development/public-preview.md#daily-source-desktop).
