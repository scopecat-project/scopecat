# Maintain the application

The native Scopecat entry opens the workbench directly. There is no laboratory
selector or separate management page. **Application settings** contains software,
source folders and data locations. **Devices and drivers** maintains shared devices.

## Daily use

1. Open Scopecat, then open your experiment code folder in VS Code.
2. Select your code folder's `.venv` interpreter for Python and Notebook kernels.
   Choose your code, setup and parameters in the current task.
3. Save edits and refresh author code. This captures source without rebuilding
   the application or installing dependencies.
4. Close Scopecat when finished. If it is idle, it exits directly. With unfinished
   work, choose to wait, stop the work and quit, or continue in the background.
   Background mode retains a menu-bar/system-tray entry with **Open** and **Quit**.
   An idle Python session alone does not prevent quitting.

Reopening restores access to retained records; it never repeats a measurement.
Closing a browser tab or a Python client does not stop the application.

## Install a newer application

Quit Scopecat, install the newer native package for your platform, then reopen it.
The installed application owns the desktop version; startup prepares its matching
execution environment automatically. No delivery-directory selection or separate
environment activation is required in Settings. You do not need system Python or uv.

The window appears while preparation runs. A failed preparation stays in that window
with retry and quit controls. If an existing service is still running, finish its
work before retrying, or explicitly stop it to complete the update. Startup never
silently substitutes an older desktop version. An interrupted selection can be retried;
this is not a scientific-data migration.
Application updates preserve user Python environments and retained task environments.
Updating client packages is a separate operation; close kernels before rebuilding them.

## Author folders

Author folders contain editable source and an author-only `scopecat.toml`.
Register a folder in Application settings. Registration currently restarts the
application, so finish active work first. Multiple folders share one service,
device registry and data authority; their source identities remain independent.

In the native application's Settings, enter the registered folder's full path and
choose **Create local Python environment**. Select `.venv/bin/python` on macOS or
`.venv/Scripts/python.exe` on Windows in VS Code. This is your environment: installing
plotting or analysis packages there does not modify the application. Do not use an
interpreter from `releases` as a Notebook kernel.

If a package is also needed by background experiments, declare it in the folder's
`pyproject.toml`, for example:

```toml
[project]
name = "my-experiments"
version = "0.1.0"
dependencies = ["humanize==4.13.0"]
```

Choose **Prepare background dependencies**, then refresh and preview your experiment.
Preparation resolves against the fixed delivery's dependencies in a separate managed
environment. Conflicting requirements fail without changing the running application
or the selected source environment. Existing prepared work and plans retain their
recorded dependency versions; the application remains running. Local pip installs
alone do not change background execution.

For a broken local environment, close its terminals and kernels, then choose
**Rebuild local Python environment**. The old directory is retained as
`.venv-retained-…`; a failed rebuild restores it. The replacement starts from the
selected delivery, so reinstall your local additions afterwards. This operation
does not alter source files, measurements or managed execution environments.

The initial execution environment can share the immutable delivery with the app.
Additional environments live under `HOME/environments`; retained environments and
releases are not disposable caches. Driver-process dependencies still belong to the
application delivery; this operation changes experiment workers, not connected drivers.

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

Use an isolated development home with the capability's complete delivery installed.
Edit its package normally in VS Code, then prepare a snapshot of that source:

```sh
scopecat app --home /path/to/development --action prepare-capability --package /path/to/capability-package
```

This rebuilds only the selected capability wheel, reuses the installed delivery's
GUI and dependency wheelhouse, and qualifies a separate immutable candidate.
Unchanged candidate contents reuse the retained environment. The running application
keeps its selected software until you explicitly stop and apply the candidate through
Settings or `--action apply-update`. Restart Python kernels afterwards.

This is an editable-source workflow, not a mutable `pip install -e` runtime: saving
a driver file cannot silently change a live connection or an admitted task's identity.
Changed dependencies or build backends require a complete delivery; candidate failure
leaves the selected environment intact. Author-only edits still use ordinary source
refresh and do not need this operation.

## Recovery

If startup detects another interpreter, use the desktop's **Stop background and
restart** recovery action, or run the explicit stop command above. Stopping uses
the recorded process identity, even when its old interpreter or optional adapter
is unavailable. It never kills unrelated processes by name.

After a force-kill, valid stale ownership is reconciled under the runtime locks.
A lock file's existence alone does not mean a process is alive. Ambiguous ownership
remains an error rather than deleting records or guessing a PID.

In-place edits to installed capability code or local settings invalidate the
qualified identity. Stop the application, fix the environment, then run
`scopecat app --home HOME --action update --python PYTHON --static-dir GUI`
to qualify and select it again. Prefer preparing a new delivery for ordinary updates.

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
