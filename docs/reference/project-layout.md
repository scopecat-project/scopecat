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
version record together. This supports only the current format. Owners may retain
old environments separately for archival reading; new builds do not promise to
read or migrate earlier development stores.

## Manifest

`scopecat.toml` identifies daemon bootstrap, declared execution capabilities,
and the instrument backend:

```toml
[lab]
bootstrap = "scopecat_lab.application:create_bootstrap"
instrument_backend = "scopecat_lab.backend:create_backend"

[lab.capabilities]
author_modules = ["scopecat_lab.authored"]

[authors]
source_roots = ["src"]
refresh_roots = ["src/scopecat_lab/authored"]
```

Bootstrap and backend factories use `MODULE:CALLABLE` syntax. Capability symbols
use `MODULE:SYMBOL`; `author_modules` lists discoverable Python modules.
Project discovery searches at or above
the supplied path and makes the project's `src` directory importable.

## Source ownership

- `application.py` exports the lightweight initial configuration bootstrap.
- `[lab.capabilities]` declares notebook and worker execution capabilities without
  requiring a custom application factory.
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

Declare procedure, schedule, calibration, publication, and system-builder symbols
in `[lab.capabilities]`. They are resolved in the notebook or project worker, not
while the daemon loads its bootstrap. Importing the bootstrap factory must not
load user execution callbacks. A custom `lab.application` factory remains an
alternative for special composition; it cannot be combined with the capabilities
table.

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
configured deployment. Opening a project does not install dependencies or start
acquisition. Notebook endpoint overrides must match the registered source owner,
data space and deployment; a URL alone does not select another codebase.

Within the current format, store identity persists independently of paths; runs, parameters and
source hashes are retained. Original code execution still requires its recorded
environment and maintained composition. Retaining data does not promise execution
of arbitrary old code in a new runtime. This is local execution, not an independent
remote client/server environment.

The current schema is **75**, a development format rather than a compatibility
baseline. [Backup and restore](../how-to/backup-and-restore.md) supports that current
format only. Earlier formats are rejected without mutation; use fresh state for
new development and keep original files separately. See the
[data compatibility policy](../development/data-compatibility.md).

## Register another author workspace

Use this when two codebases should publish and execute against the same local
service and scientific records. Their maintained composition and Python environment
must match; authored experiments may differ and use the same Python package names.
Register from the service's installed environment while both deployments are stopped:

```shell
scopecat register-workspace /path/to/second --service /path/to/service
```

Replace the two paths with your source directories; quote paths containing spaces.
The command writes the second workspace's local runtime binding and returns its
stable workspace ID. It refuses to redirect a workspace with its own scientific
store or a conflicting explicit binding. Keep starting, stopping and snapshotting the shared deployment from its service
workspace.

Open each codebase normally from its notebook:

```python
from scopecat.project import open_project

project = open_project()
session = project.authoring()
```

The source location selects its registered owner automatically. Refresh updates
only that owner's publication head; prepared work and saved plans retain their
exact source. Both sessions share the service's data and may select the same record
collection. They do not need separate daemons. The workbench currently defaults to
the service source and preserves a saved plan or analysis handoff's source owner;
a general workspace selector is not included yet.

Registration is local machine configuration, separate from retained scientific
membership. Snapshots preserve source references and bundles but exclude local
workspace paths. After a current-format restore or move, read retained data without recreating
those paths. To execute again from a new qualified location, stop the service and
explicitly rebind an existing identity:

```shell
scopecat register-workspace /new/source/location --service /path/to/service --identity EXISTING_WORKSPACE_ID
```

Use the ID from the original registration. This keeps its publication history and
revokes the old source location. Unknown IDs and the service owner's reserved
`legacy` identity cannot be rebound this way. Portable multi-codebase installation
bundles and different dependency environments require later qualification.
