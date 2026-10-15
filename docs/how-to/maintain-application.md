# Maintain the application

The native Scopecat entry opens the workbench directly. There is no laboratory
selector or separate management page. **Application settings** contains software,
source folders and data locations. **Devices and drivers** maintains shared devices.

## Daily use

If you only want to inspect or share saved results, start with
[Open and share recorded data](open-and-share-data.md); no code folder is needed.

1. Open Scopecat, then open your experiment code folder in VS Code.
2. Select your prepared or self-managed Python for scripts and Notebook kernels.
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

## Back up application data

In the native app, choose **Settings → Software and data → Back up application data…**.
Choose an existing folder outside the application home, save and stop editing practice
files, then confirm. Cancelling either dialog changes nothing. Scopecat stops the
service only when idle; active work or file transfers must finish first.

The result page shows the uniquely named snapshot directory and whether verification
succeeded. Keep the whole directory. The service stays stopped; choose **重新打开应用**
to continue, or **退出** to leave it stopped. On failure, original data and environments
remain in place; reopen and retry after correcting the reported cause. Do not treat a
failed or unverified destination as a usable backup.

This reuses the current-format snapshot: scientific records, immutable source bundles,
practice files and author registrations are retained. External editable author folders,
Python environments, SDKs and the application installer are **not** included; preserve
those separately. Backup does not migrate data or archive the complete environment.
Restore eligibility follows the supported snapshot and data-format compatibility
contract, not the application version number alone. Restoring remains an explicit
maintainer operation into a **new** application home, described in
[backup/restore](backup-and-restore.md); there is no in-place restore
button. The native installation does not require or install a global `scopecat` command
for this backup workflow.

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

For your first Notebook experiment, follow [the Help lesson](../getting-started/quickstart.md).
Its folder is already registered and can become your own working source.
To create a separate author directory, open **Settings → Author code → New code folder**.
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

For a registered folder, `sc.notebook()` accepts a compatible user-selected kernel
while an older `.venv` remains in place. It still checks the selected source's
Scopecat API versions and installed author-package contents before importing it;
registration does not waive those checks. The Notebook kernel and execution worker
need not have the same executable path. Keep old environments for retained-source
execution and recovery.

If an existing folder has no local Python, choose **Create local Python environment**.
Select `.venv/bin/python` on macOS or `.venv/Scripts/python.exe` on Windows.
This is your environment: installing
plotting or analysis packages there does not modify the application. Do not use an
interpreter inside the installed application as a Notebook kernel. The generated
environment uses an independent uv-managed Python. Keep that Python installed while
its environments are in use. Replacing or removing the application does not remove it.

First preparation needs network access for Python and third-party dependencies.
The application itself still starts offline. The author resources carry only the
required unpublished Scopecat wheels and the matching dependency recipe.
A folder's `.python-version` overrides the recipe's fixed Python version;
`requires-python` and declared dependencies are checked. An existing `uv.lock`
is exported with `--locked`: a stale lock fails without being rewritten.
The application does not choose `latest`, change PATH or install global packages.

The application CLI preparation actions accept `--python /path/to/python` for
an existing independent interpreter. `UV_PYTHON` is also checked when set.
For an already prepared background environment, **Execution Python** validates
the environment before selection. Failed preparation preserves the previous one.

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
environment. Packages supplied by the delivery stay constrained to its complete
dependency lock, the same baseline used to create the local Notebook environment.
Online resolution can add declared packages but cannot silently upgrade that baseline.
Conflicting requirements fail without changing the running application
or the selected source environment. Existing prepared work and plans retain their
recorded dependency versions; the application remains running. Local pip installs
alone do not change background execution.

Client imports check the source's declared dependency ranges and the matching
Scopecat API version. They do not require every analysis-library version or Python
patch release to match the execution environment. Retained execution and recovery
still check the exact recorded environment; changing a user environment in place
can invalidate work prepared with that environment.

For a broken local environment, close its terminals and kernels, then choose
**Rebuild local Python environment**. The old directory is retained as
`.venv-retained-…`; a failed rebuild restores it. The replacement starts from the
selected delivery, so reinstall your local additions afterwards. This operation
does not alter source files, measurements or managed execution environments.

For a Help course with failing imports, use **Repair Notebook environments** in
that course after closing its notebooks and Python terminals. Repair keeps the
same folder, source edits, Notebook notes and saved results. It retains the old
local environment and prepares the background environment against the delivery
lock; previously prepared work keeps its recorded environment. Restart the
Notebook with the displayed `.venv` kernel. Reinstall local additions if needed.
Help windows observe the same preparation operation: wait while it is running;
after a failure or interruption, retry continues in the same folder.

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
Installed capability packages belong to the source's selected execution environment.
Declare their manifest in that source's `scopecat.toml`, as described in
[installed laboratory adapters](../development/architecture/public-application.md#installed-laboratory-adapters).
Do not install them into or configure them on the application itself.

## Develop a capability in VS Code

Use an isolated foreground development application and edit ordinary source in
VS Code:

```sh
uv run --locked python -m lab_tools.dev --home /path/to/development
```

Run this from a public checkout with Python 3.14+, uv, Node.js and pnpm. It opens
the native desktop with Vite and an isolated backend; Ctrl-C requests exit after
work becomes idle. Register the capability source folder through Settings.
See [daily source development](../development/public-preview.md#daily-source-desktop).
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

原生日志位于 `HOME/desktop/desktop.log`。新建应用的科学数据位于 `HOME/data/`，
daemon 日志位于 `HOME/state/daemon.log`；`state/` 还保存安装登记、作者目录登记、
解释器引用和运行协调信息，**不是可丢弃的缓存**。已识别的旧布局（legacy）保留
`HOME/runtime/.scopecat/` 下原有的数据和 daemon 日志，不会自动搬迁。
记录准确错误和应用路径；历史目录不会自动删除。完整边界见
[安装布局](../development/installation-layout.md#installed-application-directories)。

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

## Reset data when an unsupported format prevents startup

The native error page offers **删除应用数据并重新初始化…**. By default, choose
an external backup folder, then confirm the exact data range. The host remembers
only this folder. Each recovery starts with backup enabled; **本次跳过备份** skips
it only for this confirmation. Cancelling either dialog deletes nothing. A failed
backup never falls back to deletion.

原件备份逐字节保留数据库、WAL 和对象文件，不读取或归档可重建的
`control.sqlite3-shm` 索引；清单会注明这一项。该索引不含数据库内容，
SQLite 可从数据库和 WAL 重建它。备份期间仍持有写入保护，不执行 checkpoint。
参见 [SQLite WAL 文件说明](https://www.sqlite.org/walformat.html#the_wal_index_or_shm_file)。

操作只删除确认的数据目录中的 `control.sqlite3`、其 SQLite sidecar 文件和 `objects/`，
再在原路径初始化当前格式的空存储。新布局的数据目录为 `HOME/data/`；已识别的
legacy 布局仍为 `HOME/runtime/.scopecat/`。不会删除整个数据目录或应用 home。
这会移除实验、参数、草稿、设备配置和已存证据；原始作者目录、Python 环境、
应用文件、作者目录登记及其他文件保留。新布局的 `state/` 也保留，不属于科学数据重置范围。
不会自动运行任务或连接设备，也不会清理环境、物化源码或未知辅助文件。
此操作不创建新数据空间或空间管理入口。

The backup is a uniquely named directory in the chosen folder, containing raw
store files and a SHA-256 manifest. It preserves the original format; it is **not**
a current-version snapshot/restore promise and does not include source or Python
environments. Supported-format daily backup uses the Settings entry above; explicit
restore remains a separate maintainer command.
All archive files are copied and verified before deletion. Interrupted deletion
can be partial and cannot be rolled back automatically. Retry requires explicit
confirmation; an existing verified archive is reused and its location displayed.
If an earlier attempt skipped backup, a new backup can contain only what remains.

Recovery refuses custom runtime bindings/composition, uncertain or active owners,
redirected paths, unknown files inside the object store, and unresolved rollback
journals. These are explanations to preserve the data, not permission to guess.
