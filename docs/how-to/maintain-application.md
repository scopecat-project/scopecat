# Maintain the application

The native Scopecat entry opens the workbench directly. There is no laboratory
selector or separate management page. **Application settings** contains software,
source folders and data locations. **Devices and drivers** maintains shared devices.

## Daily use

If you only want to inspect or share saved results, start with
[Open and share recorded data](open-and-share-data.md); no code folder is needed.

1. Open Scopecat, then open your experiment code folder in VS Code.
2. Select your code folder's `.venv` interpreter for Python and Notebook kernels.
   Choose your code, setup and parameters in the current task.
3. Save edits and refresh author code. This captures source without rebuilding
   the application or installing dependencies.
4. Closing the last window keeps Scopecat in the background on both Mac and Windows,
   with a menu-bar/system-tray entry for **Open** and **Quit**. Closing an additional
   window only closes that view. Choose **Quit** to exit the application: if idle,
   it exits directly; with unfinished work, choose to wait, stop the work and quit,
   or continue in the background.
   An idle Python session alone does not prevent quitting.

Reopening restores access to retained records; it never repeats a measurement.
Closing a browser tab or a Python client does not stop the application.

## Install a newer application

Quit Scopecat, install the newer native package for your platform, then reopen it.
The package already contains Python and application dependencies. Startup runs that
version directly; it does not install another execution environment. You do not
need system Python or uv, or an environment selection in Settings.

The window appears while the application starts. A failed startup stays in that window
with retry and quit controls. If an existing service is still running, finish its
work before retrying, or explicitly stop it to complete the update. Startup never
silently substitutes an older desktop version.
Application updates preserve user Python environments and retained task environments.
Updating client packages is a separate operation; close kernels before rebuilding them.

## Author folders

For your first experiment, open **Settings → Author code → New code folder**.
Choose a save location and folder name, then **Create folder and prepare Python**.
Scopecat creates a device-free example and an independent Python environment.
It does not connect devices or start a measurement. Existing folders are never overwritten.

After preparation, Settings shows your folder and Python path. Open that folder
in VS Code, select its `.venv` interpreter, and run `notebooks/02_edit_scan.py`
cell by cell. The example submits a synthetic scan, analyzes it and reopens the
saved result. Running the submission again creates another measurement.

For existing Scopecat source, choose **Use existing folder → Browse for code folder…**,
enter its **Execution Python**, then **Add code folder**. Select registered folders
under **Your code folders**. Adding a folder keeps the application running. Multiple
folders share one service, device registry and data authority.

If an existing folder has no local Python, choose **Create local Python environment**.
Select `.venv/bin/python` on macOS or `.venv/Scripts/python.exe` on Windows.
This is your environment: installing
plotting or analysis packages there does not modify the application. Do not use an
interpreter inside the installed application as a Notebook kernel. The generated
environment retains its own base Python in the folder's `.scopecat-python` directory;
keep that directory with `.venv`. Replacing or removing the application does not
remove this interpreter.

The generated environment contains the Scopecat Python API, a notebook kernel and
their dependencies. Desktop, service and teaching packages are not copied into it.
VS Code can use it directly. For optional JupyterLab editing, install `jupyterlab`
with this environment's pip; the `scopecat notebook` maintainer command also uses
this folder's `.venv`, never application Python.

If a package is also needed by background experiments, declare it in the folder's
`pyproject.toml`, for example:

```toml
[project]
name = "my-experiments"
version = "0.1.0"
dependencies = ["humanize==4.13.0"]
```

Choose **Prepare execution environment from pyproject.toml**,
then refresh and preview your experiment.
Preparation resolves the source requirements with compatible Scopecat packages in a separate managed
environment. Conflicting requirements fail without changing the running application
or the selected source environment. Existing prepared work and plans retain their
recorded dependency versions; the application remains running. Local pip installs
alone do not change background execution.

For a broken local environment, close its terminals and kernels, then choose
**Rebuild local Python environment**. The old directory is retained as
`.venv-retained-…`; a failed rebuild restores it. The replacement starts from the
selected delivery, so reinstall your local additions afterwards. This operation
does not alter source files, measurements or managed execution environments.

Managed execution environments live under `HOME/environments`; retained environments
are not disposable caches. To use an existing environment instead, enter its Python
and choose **Use this execution Python**; no packages are installed. Driver updates
use the registered source environment unless explicitly overridden in the driver
panel. Changing source execution Python does not replace connected drivers.

Maintainers can use the same operations without opening a browser:

```sh
scopecat app --home /path/to/Scopecat --action register-source --workspace /path/to/code
scopecat app --home /path/to/Scopecat --action start
scopecat app --home /path/to/Scopecat --action status
scopecat app --home /path/to/Scopecat --action stop
```

Only `--action open` opens a browser; `--action desktop` opens the native window.
A source checkout can initially configure an isolated development home with
`--action configure --source /path/to/scopecat`, after building its GUI.
Explicit installed capabilities use paired `--distribution` and `--manifest`
arguments during configure/update. The capability distribution owns its manifest;
author folders do not copy driver or application composition.

## Develop a capability in VS Code

Use an isolated foreground development application and edit ordinary source in
VS Code:

```sh
python -m lab_tools.dev --home /path/to/development --workspace /path/to/author-source --source /path/to/scopecat
```

The development environment needs the source dependencies installed first. This
command starts its own backend and Vite without opening a browser; Ctrl-C stops
both. It does not install a desktop entry or share the daily application's home.
Registering the source folder does not enable its drivers. Experiment and
analysis edits use ordinary author refresh. Initial driver selection and edits use
**Update from source** in **Devices and drivers**, after finishing active work and
releasing manual sessions. Saving a file alone does not replace a live driver.
Dependency changes require explicit preparation of the development environment.
Application updates use a newly built native package, not a candidate interpreter
or a capability-wheel switch inside the running application.

## Recovery

If startup detects another interpreter, use the desktop's **Stop background and
restart** recovery action, or run the explicit stop command above. Stopping uses
the recorded process identity, even when its old interpreter or optional adapter
is unavailable. It never kills unrelated processes by name.

After a force-kill, valid stale ownership is reconciled under the runtime locks.
A lock file's existence alone does not mean a process is alive. Ambiguous ownership
remains an error rather than deleting records or guessing a PID.

After changing local settings, choose **Stop work and restart** in Application
settings. Restart checks the current application and settings before reopening.
If it fails, the window retains the error and retry controls. Install a corrected
application package to repair application dependencies; do not use pip inside it.
**Technical diagnostics** contains interpreter and capability details for maintainers.

The native log is `HOME/desktop/desktop.log`; runtime logs and data are under
`HOME/runtime/.scopecat` unless explicitly bound elsewhere. Record the exact error
and installation path. Historical directories are never automatically deleted.

## Ownership and limits

A development home is independent of a daily home. Use temporary homes for tests;
never borrow the daily service. Different application homes do not coordinate
access to the same physical hardware.

The application retains existing daemon/worker isolation and shutdown checks.
Updates are explicit interruptions, not permission to resume an acquisition.
Supported persistent-data compatibility begins only at a designated baseline;
none is designated yet. See [data compatibility](../development/data-compatibility.md)
and [backup/restore](backup-and-restore.md).

Help practice uses the same application and its ordinary data-cleanup controls.
