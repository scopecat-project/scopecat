# Project layout and manifest

`scopecat init my-lab` creates this minimal project:

```text
my-lab/
├── README.md
├── scopecat.toml
├── notebooks/
│   ├── 01_first_run.py
│   └── 02_edit_scan.py
└── src/scopecat_lab/
    ├── __init__.py
    ├── application.py
    ├── backend.py
    ├── configuration.py
    └── authored/
        ├── __init__.py
        └── signal.py
```

Projects may add a `config/` directory for external, version-controlled
infrastructure inputs. Runtime state lives in `.scopecat/` and is owned by the
daemon rather than edited by users.

The runtime directory contains `control.sqlite3`, its SQLite side files, the
immutable `objects/` store, daemon metadata, and logs. The daemon refuses schema
versions other than its own and never implicitly migrates or deletes state.
Use the supported [stopped-project snapshot workflow](../how-to/backup-and-restore.md)
to preserve the database, objects, application/configuration source and installed
version record together. Retain the matching reader and external dependencies
when upgrading or starting a new project.

## Manifest

`scopecat.toml` identifies daemon bootstrap, project application, and instrument
backend factories:

```toml
[lab]
bootstrap = "scopecat_lab.application:create_bootstrap"
application = "scopecat_lab.application:create_application"
instrument_backend = "scopecat_lab.backend:create_backend"

[authors]
source_roots = ["src"]
refresh_roots = ["src/scopecat_lab/authored"]
```

The three factory values use `MODULE:CALLABLE` syntax. Project discovery searches at or above
the supplied path and makes the project's `src` directory importable.

## Source ownership

- `application.py` exports a lightweight bootstrap factory for the daemon and a
  separate full application factory for notebooks and the project worker.
- `backend.py` composes worker-only instrument providers and drivers.
- `configuration.py` builds the bootstrap configuration used only while the
  daemon registry is empty.
- `authored/` contains locally editable experiment and analysis declarations. Source
  refresh captures these edits; it does not upgrade installed shared packages.
- `notebooks/` contains user-owned interactive workflows and scripts.

After initialization, these are application source files: edit, test, and
version them with the rest of the lab project. Use the
[configuration review workflow](../how-to/manage-configuration.md) to publish
configuration changes explicitly.

Keep procedure, schedule, calibration, publication, and system-builder imports
inside the full application factory. Importing the bootstrap factory must not
load those user execution callbacks into the daemon process.

## Bind a workspace to persistent local data

A local deployment binding is optional and separate from captured scientific
source. Without it, data and runtime state remain in `<workspace>/.scopecat/`.
To replace code directories while retaining one data space, put this file beside
`scopecat.toml` in each selected workspace:

```toml title="scopecat.runtime.toml"
[runtime]
data_root = "../laboratory-data"
deployment_root = "../bench-owner"
```

Both paths are required and resolve relative to the workspace; absolute paths
are also accepted. `data_root` directly owns `control.sqlite3`, immutable objects,
source materializations, author-job receipts and diagnostics. `deployment_root`
holds the local deployment identity and exclusive ownership lock. Use one
maintained deployment location for every launch path to the same bench. Separate
invented locations cannot detect that their SDKs address the same physical device.
Do not put either directory inside an authored source root.

Only one service owns a data space, and only one data space at a time owns a
configured deployment. Another workspace cannot implicitly take over the mutable
source or stop its service. Stop the selected workspace explicitly, then start the
replacement. Do not edit bindings while its service or clients are running.
Notebook endpoint overrides must match workspace, data and deployment paths.
Opening a project does not install dependencies or start acquisition.

The store identity persists independently of paths; old runs, parameters and
source hashes are retained. Refresh applies to the selected workspace. Original
code execution still requires its recorded environment and maintained composition;
retaining data does not promise execution of arbitrary old code in a new runtime.
This is local execution, not an independent remote client/server environment.

The current schema is 69. Existing schema 68 and earlier data needs its matching reader;
opening it performs no migration. Keep old evidence and environments. An explicit
supported-baseline migration policy is tracked separately from this binding.
See [backup and restore](../how-to/backup-and-restore.md) for relocation and receipts.
