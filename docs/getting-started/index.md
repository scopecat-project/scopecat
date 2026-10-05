# Getting started

If you have a Scopecat desktop preview, open the application. Python and application
dependencies are included; you do not need a source checkout.

On macOS, copy Scopecat from the DMG to Applications before opening it.
The free preview is ad-hoc signed, without Apple notarization. If macOS cannot
verify the developer, follow the [first-open instructions](../how-to/mac-preview.md).

- To explore without writing code, use **Help → Start peak practice**.
  [Practice in the application](../tutorials/teaching-sandboxes.md) explains the scan,
  manual decision and cleanup.
- To learn parameters and scans, use **Help → Start parameters Notebook**. Help
  prepares an editable folder and Python environment; **Continue** retains your
  source and saved edits. See [the Notebook lesson](../tutorials/teaching-sandboxes.md#parameters-and-scans-in-a-notebook).
- To write your first experiment, choose **Settings → Author code → New code folder**.
  Pick a save location and name, then **Create folder and prepare Python**. Open the
  resulting folder in VS Code, select its `.venv`, and run `notebooks/02_edit_scan.py`
  cell by cell. The example needs no devices.
- To browse a received `.scopecat` file, choose **File → Open**. No author environment
  is needed; see [open and share recorded data](../how-to/open-and-share-data.md).
- For existing Scopecat code, choose **Use existing folder**, browse to the folder,
  enter its **Execution Python**, and **Add code folder**. The application stays running.

The application stores your results independently of the code folder. Creating or
opening a folder does not acquire data. Submitting the example runs a new synthetic
measurement; reopening its saved result does not run it again.

- [Learning paths](learning-path.md) separates author, maintainer and extension tasks.
- [First experiment](quickstart.md) follows the installed application's example.
- [Project layout](../reference/project-layout.md) explains the files generated
  by `scopecat init`.
- [Edit a starter experiment](../tutorials/starter-authoring.md) adds request
  editing, scans, retained analysis and reopening.

See [application use and maintenance](../how-to/maintain-application.md) for Python
environments, updates and closing the application.
