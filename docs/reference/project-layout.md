# Project layout and manifest

Author code can live in a separate folder whose `scopecat.toml` contains only
`[authors]`. Register it with the running application and select its execution
Python. Registration shares data and device ownership, not laboratory code.
Declare needed scientific capabilities in that folder's `[lab.capabilities]`.
See [author folders in the application](../how-to/maintain-application.md#author-folders).
The combined layout below remains the form for laboratory-owned local composition.


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
        ├── parameters.py
        ├── signal.py
        └── thermometer.py
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
```

Bootstrap and backend factories use `MODULE:CALLABLE` syntax. Capability symbols
use `MODULE:SYMBOL`; `author_modules` lists discoverable Python modules.
Project discovery searches at or above
the supplied path and makes the project's `src` directory importable.

## Source ownership

Author source and refresh roots are relative subdirectories; equivalent `./src/`
spellings are normalized. The whole project directory (`.` and its aliases) is
not a source root. Captures exclude `scopecat.runtime.toml` at every depth: local
application/data bindings are deployment metadata, not scientific source or
configuration-sharing attachments. Existing retained originals are not rewritten.

- `application.py` exports the lightweight initial configuration bootstrap.
- `[lab.capabilities]` declares notebook and worker execution capabilities without
  requiring a custom application factory.
- `backend.py` composes worker-only instrument providers and drivers.
- `configuration.py` declares equipment for first use, without parameter values.
- `authored/parameters.py` declares the starter's parameter model and opens its
  independent editing branch. Existing saved branch values are not reseeded.
- `authored/` contains locally editable experiment and analysis declarations. Source
  refresh captures these edits; it does not upgrade installed shared packages.
- `notebooks/` contains user-owned interactive workflows and scripts.

After initialization, these are application source files: edit, test, and
version them with the rest of the lab project. Use the
[setup management](../how-to/maintain-executable-setup.md) to activate reviewed
equipment changes and [parameter branches](../how-to/parameter-branches.md) for
ordinary author edits. The generated starter publishes no global parameter default.

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
environment and retained source snapshot. Retaining data does not promise execution
of arbitrary old code in a new runtime. This is local execution, not an independent
remote client/server environment.

The current schema is a development format rather than a compatibility
baseline. [Backup and restore](../how-to/backup-and-restore.md) supports the current
format only. Earlier formats are rejected without mutation; use fresh state for
new development and keep original files separately. See the
[data compatibility policy](../development/data-compatibility.md).

## Register another author workspace

Use this when two codebases should publish and execute against the same local
service and scientific records. Each registered author source has a qualified
execution environment, independent of the application Python. Experiments, shared
helpers and compiler source may differ and use the same Python package names.
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
revokes the old source location. Unknown IDs cannot be rebound this way; every
source uses an explicit registered identity. Retained execution requires its captured dependency identities, even
when the application and author environments are separate.

## Submit an ordinary procedure from an author session

Register the procedure in the source manifest's `[lab.capabilities].procedures`,
using its `module:attribute` name. In a script, explicitly select source before
importing its definition and intent model:

```python
with project.authoring() as session:
    session.refresh()
    from my_lab.workflow import ReviewIntent, review

    prepared = session.procedures.prepare(
        review, ReviewIntent(label="Review scan"), request_key="review-scan-1"
    )
    task = prepared.submit()
    print(task.id, task.dispatch_error)
```

`sc.notebook()` exposes the same `session.procedures` API and selects source at
cell boundaries. Preparing a procedure never refreshes imports or executes its
body. Changed source or an old definition/model alias requires an explicit
refresh and reimport. Intent supplies the procedure's scientific choices; session
experiment defaults are not implicitly applied.

Retain `prepared` (or its serialized `command`) before submission. After an
uncertain response, retry that exact command with the same request key; a changed
command must not reuse the key. `prepared.reconnect(session).submit()` preserves
its original source and identity, and rejects a different data-store or deployment
identity even if its workspace ID matches. The application validates the exact registered
definition and canonical intent in the retained revision, then owns dispatch.
Closing the client or kernel leaves the managed task with the application.

Reconnect with `session.procedures.get(task_id)`, inspect `task.progress()`, or
request `task.cancel(actor="author", reason="Stop")`. Answer a waiting step using
`session.submit_procedure_step_input(...)`, then call `task.resume()` to dispatch
ready work. Existing revision fences and unknown-outcome gates still apply.
A dispatch error retains the admitted task ID for inspection and explicit resume.

`Project.connect()` and `LabClient.procedures.start/resume` retain their explicit
local Python execution semantics, including internal worker use. They are not
aliases for this managed API. Existing calibration, joint and task teaching
consumers need a separate migration of their source binding and scientific intent;
this first slice does not change their return values or merge the client classes.
