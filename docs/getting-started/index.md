# Getting started

If you have a Scopecat desktop preview, open the application. Python and application
dependencies are included; you do not need a source checkout.

- To explore without writing code, use **Help → Start peak practice**.
  [Practice in the application](../tutorials/teaching-sandboxes.md) explains the scan,
  manual decision and cleanup.
- To write your first experiment, choose **Settings → Author code → New code folder**.
  Pick a save location and name, then **Create folder and prepare Python**. Open the
  resulting folder in VS Code, select its `.venv`, and run `notebooks/02_edit_scan.py`
  cell by cell. The example needs no devices.
- For existing Scopecat code, choose **Use existing folder**, browse to the folder,
  and **Add code folder**. Finish active work before adding a folder.

The application stores your results independently of the code folder. Creating or
opening a folder does not acquire data. Submitting the example runs a new synthetic
measurement; reopening its saved result does not run it again.

- [Learning paths](learning-path.md) separates author, maintainer and extension tasks.
- [Source preview quickstart](quickstart.md) is the framework-development path.
- [Project layout](../reference/project-layout.md) explains the files generated
  by `scopecat init`.
- [Edit a starter experiment](../tutorials/starter-authoring.md) adds request
  editing, scans, retained analysis and reopening.

See [application use and maintenance](../how-to/maintain-application.md) for Python
environments, updates and closing the application.
