# Scopecat application and tutorial tools

Public tooling for the experiment application, first-run connection, offline
installation, tutorial workspaces and Notebook environments.

Use `scopecat app`, installed `lab.cmd` (Windows), or `Scopecat.command` (Mac).
`lab.py` remains available for scripted launches. First use offers
creation of an ordinary experiment directory or connection to a trusted laboratory
code directory. Data can be placed in a separate new location; existing bindings
and records are preserved. The project `.venv` is used when present, otherwise the
application environment is used. Missing declared dependencies are reported before
service startup; setup does not install laboratory dependencies.

Successful setup opens and remembers the primary workbench. Daily launch restores
it directly; `--manage` opens maintenance. Starting can initialize devices according
to laboratory policy, but never submits a measurement. Maintainers can still use
`scopecat app PATH --python ... --static-dir ...` for explicit environment choices.

Open the installed `Notebook.cmd` / `Notebook.command` beside the application entry
to use the preferred laboratory's registered interpreter. A sole author workspace
is selected automatically; a software starter opens its own workspace. Multiple
author workspaces require an explicit `scopecat notebook PATH` selection. From an
author directory, `scopecat notebook` continues to use that directory. Notebook
launch does not start the laboratory or acquire data; open the workbench first.

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
