# Scopecat tutorial tools

Public tooling for disposable tutorial workspaces, pinned kernels, offline
installation, matching GUI assets and executable course verification.

Use `scopecat teach` in an installed teaching environment, or the generated
`lab.cmd` / `lab.py` menu. Maintainers use the repository's `teach.cmd` or
`teach.py` entry to build, verify and optionally install a fixed delivery.

See [tutorial sandboxes](../../docs/tutorials/teaching-sandboxes.md).
This package does not import private laboratory code or the reference lab.
Lower-level `scopecat-lab` project commands remain available for explicit
workspace preparation and diagnostic use; they do not migrate scientific data.
