# Scopecat application and tutorial tools

Public tooling for the experiment application, first-run connection, offline
installation, tutorial workspaces and Notebook environments.

Open installed `Scopecat.lnk` (Windows) or `Scopecat.app` (Mac).
Experiments and settings share one window. Opening the application reconnects to
running services; use **启动 / 检查工作台** to start a stopped laboratory.
Closing the window offers to stop services started by this application session or
keep them in the background. Independent services remain running and are listed.
Stopping can interrupt measurements and connected VS Code kernels.

Use VS Code to open the experiment code folder and select its laboratory interpreter
for Python and Notebook execution. JupyterLab is optional.

`lab.py` remains available for scripts. It defaults to status output; specify
`--action start`, `stop`, `open`, `desktop`, or `quit` explicitly. Only `open` opens
a browser. Source commands require `--home`, keeping development separate from the
daily installation. For example, `scopecat app --home /tmp/my-test --action status`.
First use offers
creation of an ordinary experiment directory or connection to a trusted laboratory
code directory. Data can be placed in a separate new location; existing bindings
and records are preserved. The project `.venv` is used when present, otherwise the
application environment is used. Missing declared dependencies are reported before
service startup; setup does not install laboratory dependencies.

Successful setup opens and remembers the primary workbench in the same window.
Starting can initialize devices according
to laboratory policy, but never submits a measurement. Maintainers can still use
`scopecat app PATH --home ... --action start --python ... --static-dir ...`
for explicit environment choices.

Open the installed `Notebook.cmd` / `Notebook.command` beside the application entry
to use the preferred laboratory's registered interpreter. A sole author workspace
is selected automatically; a software starter opens its own workspace. Multiple
author workspaces require an explicit `scopecat notebook PATH` selection. From an
author directory, `scopecat notebook` continues to use that directory. Notebook
launch does not start the laboratory or acquire data; open the workbench first.
`Manage.cmd` / `Manage.command` opens service management without starting an
experiment service, including when the preferred workbench is already selected.

After updating an existing environment, stop the service and use **Recheck environment**
in the manager to validate its registered paths without rebuilding a CLI command.
See [application maintenance](../../docs/how-to/maintain-application.md).

Teaching is under **Help**. `scopecat teach` opens that section directly;
`python lab.py teach compute --verify` keeps tutorial automation explicit. Maintainers use the repository's `teach.cmd` or
`teach.py` entry to build, verify and optionally install a fixed delivery.

See [tutorial sandboxes](../../docs/tutorials/teaching-sandboxes.md).
This package does not import private laboratory code or the reference lab.
Lower-level `scopecat-lab` project commands remain available for explicit
workspace preparation and diagnostic use; they do not migrate scientific data.
