# Scopecat application tools

This wheel owns its Python environment's `scopecat` console through
`lab_tools.public_cli`. Native installation does not currently add a global PATH
console. An optional application-supplied CLI/launcher targeting the same data
owner is the confirmed direction, not yet implemented; see [installation identities](../../docs/development/installation-layout.md#application-command-and-data-identities).
It composes server commands with application, Notebook and practice operations.
`scopecat init --topic` creates editable author source for the current application;
prepare and register it in Settings. The old `scopecat-lab` lifecycle entry is removed.
Native bootstrap, desktop and `ApplicationRuntime` are long-lived runtime code;
this package also contains delivery and acceptance tools. See the
[repository map](../../docs/development/repository-map.md) for those boundaries.

Open installed **Scopecat.app** (Mac) or **Scopecat.lnk** (Windows) to enter the
workbench directly. One application home owns the runtime, scientific data,
registered devices and author sources. Source folders do not own services.

Use VS Code normally. Select the author folder's independent `.venv` for scripts
and Notebook kernels. Installing analysis packages there does not modify the
application's runtime. The generated environment includes Scopecat and ipykernel.
Install optional JupyterLab in that environment before using
`scopecat notebook PATH --home HOME`; this command also uses the folder's `.venv`.

Closing the last desktop window keeps the application running in the tray/menu
bar. Explicit **Quit** exits when idle; active work offers stop, background
retention, wait until idle or cancel. Browser tabs and Python clients do not own
the service. The explicit stop acts only on that home's recorded process. See the
[desktop lifecycle](../../docs/development/architecture/desktop-lifecycle.md).

Maintainer commands require an explicit home:

```sh
scopecat app --home /tmp/scopecat-dev --action configure --static-dir /path/to/gui/dist
scopecat app --home /tmp/scopecat-dev --action register-source --workspace /path/to/code
scopecat app --home /tmp/scopecat-dev --action start
scopecat app --home /tmp/scopecat-dev --action stop
```

Only `--action open` launches a browser. Status, builds, tests and installation stay
headless. Software preparation and selection are separate operations; see
[application maintenance](../../docs/how-to/maintain-application.md).

Help practice uses the current application and its data cleanup. The equivalent
headless command is `python -m lab_tools.practice --home DATA_HOME`; it reports
the practice link and optional notes folder without opening a browser. See
[tutorials](../../docs/tutorials/teaching-sandboxes.md).

Platform installation locations and the delivered native packaging boundary are
described in [installation layout](../../docs/development/installation-layout.md).
