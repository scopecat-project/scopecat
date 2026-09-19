# Scopecat application and tutorial tools

Public tooling for disposable tutorial workspaces, pinned kernels, offline
installation, matching GUI assets and executable course verification.

Use `scopecat app` or the installed `lab.cmd` / `lab.py` entry to open registered
experimental services. Register an existing project from its Python environment
with `scopecat app PATH`; use `--python` for an explicit interpreter and
`--static-dir` for a source-built GUI. Registration does not start devices.

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
