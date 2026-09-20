# Scopecat Server

Local FastAPI and SSE transport plus the default SQLite daemon runtime.
Published distributions include the project GUI; source checkouts may use the
Vite development server or pass `apps/scopecat-ui/dist` with `--static-dir`.

From the repository root:

```console
uv run scopecat init ./my-lab
uv run scopecat config check ./my-lab
uv run scopecat start ./my-lab --static-dir apps/scopecat-ui/dist
uv run scopecat open ./my-lab
uv run python ./my-lab/notebooks/01_first_run.py
```

`init` creates a project without replacing existing files. `config check`
validates its bootstrap source without starting a daemon or writing project
state. Configuration reconciliation commands and the complete runnable
walkthrough live in the [repository README](../../README.md).

Each `scopecat.toml` names a lightweight daemon bootstrap, declarative worker
capabilities and, when devices are configured, an instrument backend:

```toml
[lab]
bootstrap = "my_lab.application:create_bootstrap"
instrument_backend = "my_lab.backend:create_backend"

[lab.capabilities]
author_modules = ["my_lab.experiments"]
experiment_system = "my_lab.system:build_experiment_system"
```

The system builder is optional: the generated starter uses the default system.
When supplied, it accepts the accepted configuration and instrument contract
catalog as positional arguments and returns an `ExperimentSystem`. Other optional
capabilities declare procedure and schedule symbols, calibration registries,
publication policies and launch/comparison providers. These are existing values
or callables, not project-root factories.

```python
from pathlib import Path

from scopecat.application import LabBootstrap
from my_lab.configuration import build_initial_config


def create_bootstrap(project: Path) -> LabBootstrap:
    return LabBootstrap(
        bootstrap_config=lambda: build_initial_config(project),
    )
```

```python
from pathlib import Path

from scopecat.sdk.instruments import InstrumentBackend
from my_lab.drivers import LabProvider


def create_backend(project: Path) -> InstrumentBackend:
    return InstrumentBackend(provider=LabProvider.from_project(project))
```

Bootstrap and backend factories accept the resolved project `Path`. The bootstrap
config is a lazy seed used only for an empty registry. Capability symbols are
resolved in the notebook or project worker; the daemon loads only
`create_bootstrap`. A custom `lab.application` factory remains available for special
composition and cannot be combined with `[lab.capabilities]`.
Notebook planning receives the accepted snapshot and the
daemon-resolved contract catalog. Backend code, transports, codecs, and drivers
are imported and constructed only in the long-lived instrument worker.

Only one daemon owns a project. It stores SQLite and immutable objects below
`.scopecat`, records its loopback endpoint in `.scopecat/daemon.json`, and
serves the replayable event stream used by GUI and notebook clients. See the
[daemon model](../../docs/development/architecture/daemon.md) for ownership, fencing, quarantine,
and security boundaries.

## Project-store compatibility

This early implementation uses an explicitly versioned project store and does
not perform implicit migrations. The current version is defined by
`PROJECT_SCHEMA_VERSION` in `scopecat_server.storage.sqlite.schema`. A daemon
built from this revision refuses an older `.scopecat/control.sqlite3` instead
of partially reading or rewriting it.
Before switching revisions, stop the daemon and back up the complete
`.scopecat/` directory. If stored runs or configuration history matter, inspect
or export them with the revision that created the store. Rebuilding means
explicitly starting from an empty runtime store and accepting that the daemon
will seed a new registry from `create_bootstrap`; source-controlled project
files are not a substitute for persisted run history.

The daemon never deletes an incompatible store automatically. This rebuild-only
policy is intentional for the current closed development phase; a supported
migration or export/import boundary is required before project stores are
treated as long-lived user data.

For a bundled source-checkout preview:

```console
cd apps/scopecat-ui
pnpm run build
cd ../..
uv run python scripts/build_server_distribution.py
```

Tests may construct `LocalDaemonRuntime` with a temporary project or pass a
custom `DaemonApplication` to `create_app`.
