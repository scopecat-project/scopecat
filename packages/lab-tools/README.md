# Scopecat application tools

Open installed **Scopecat.app** (Mac) or **Scopecat.lnk** (Windows) to enter the
workbench directly. One application home owns the runtime, scientific data,
registered devices and author sources. Source folders do not own services.

Use VS Code normally. Select the application's Python interpreter for scripts and
Notebook kernels; it is shown in **Application settings**. JupyterLab remains an
optional explicit `scopecat notebook PATH --home HOME` command.

Closing the desktop window offers **Stop and close**, **Keep running in background**
or **Cancel**. Browser tabs and Python clients do not own the service. The explicit
stop acts only on that home's recorded process.

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

Tutorial deliveries retain the explicit `python lab.py teach compute --verify`
command. It runs directly and reports a VS Code folder, without a management
service. Same-service practice scopes are the next product batch. See
[tutorials](../../docs/tutorials/teaching-sandboxes.md).
