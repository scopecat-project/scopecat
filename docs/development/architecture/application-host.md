# Local application host

Status: an application entry for explicitly registered experiment services, with
teaching under Help. The selected product direction remains the
[experiment workbench with session contexts](experiment-contexts.md).
The host opens each service's real workbench; it does not replace that workbench
with tutorial inventory or establish shared physical-device ownership.

## Register and open an existing experiment service

Run the following in the project's environment, with `scopecat-lab-tools` installed:

```sh
scopecat app /path/to/existing-project
```

For a separate target interpreter or a source GUI build:

```sh
scopecat app /path/to/existing-project --python /path/to/environment/bin/python --static-dir /path/to/gui/dist
```

On Windows the interpreter is typically `environment\Scripts\python.exe`.
The paths are trusted local CLI inputs. The browser only submits a registered
service ID; it cannot register an arbitrary path or interpreter. Registration
validates the actual project root, environment and GUI without starting its
daemon. Starting/checking the workbench runs the existing startup lifecycle in
that environment. After successful startup and a fresh matching running-service
check, an explicit **Open workbench (new tab)** link opens the service GUI while
keeping the authenticated manager page available. The link carries no manager
credential and isolates opener/referrer state. Startup may initialize the project's configured
instruments; it does not submit a measurement. Existing hardware startup policy
remains the project's responsibility.

`scopecat app` without a project reopens the manager. Installed `lab.cmd` / `lab.py`
launchers use this entry. **Help** contains managed teaching exercises;
`scopecat teach` opens it directly. Explicit installed tutorial automation uses
`python lab.py teach compute --verify`. A normal installation without a tutorial
delivery can still manage experiment services; Help reports teaching unavailable.
Source users pass `--source CHECKOUT` to enable source-backed teaching.

The service registration records an absolute interpreter path without resolving
venv symlinks, its prefix/Python/package versions and selected GUI directory.
Startup checks that identity and refuses a running daemon from another interpreter,
an API-only service or a mismatched GUI. It leaves an existing service running so
the maintainer can stop it explicitly. Before changing an environment, finish active management work and stop the service.
After an in-place update, **Recheck environment** validates the existing registered
interpreter, project and GUI and atomically updates its environment identity.
It retains the deployment ID and paths, records the operation, and leaves the service
stopped. A failed probe retains the old registration. Rechecking rejects any state
other than stopped and shares the registration/operation lock; it cannot overlap
re-registration or another management operation. New interpreter or GUI paths still
require explicit trusted local registration. See the [maintenance guide](../../how-to/maintain-application.md). This version check
is not a lockfile/content attestation of every dependency or editable source byte.
Failed startup stays visible in the operation log; there is no fallback interpreter.

**Stop service** asks for confirmation because it can interrupt active work and
Notebook connections. It uses the registered interpreter and existing daemon
lifecycle, including graceful shutdown and its existing timeout policy. The
process identity and interpreter must match; a failed check leaves the service
untouched and retains the operation log. Stopping does not require GUI assets.
It never automatically restarts the service or resumes measurement.

**Remove registration** is available after the service is stopped, with no other
pending management operation. It only removes the deployment catalog row. Project
files, scientific data and operation history remain, and the CLI can register that
project again. Retrying the same successful removal operation returns its retained
result. A stopped service with an unreadable/missing project remains an explicit
maintenance error, rather than guessing that its process is safe to forget.

The experiment GUI has a **Help and maintenance** page. It uses the existing health
response to identify the current service and provides supported documentation,
teaching and maintenance directions. Reopen the original installed `lab.cmd` /
`lab.py` entry to return to that installation's manager; a qualified source
installation can use `scopecat app` with its original host-home options. Help does
not register directories or start another service merely by being opened.

There is no automatic reopening of the last selected service or authenticated
one-click return to a particular manager. The daemon is not paired with an
installation host: deployment/source identities do not identify its manager port,
home or credentials. Do not expose `host.json`, infer a manager address from a
project path, or add an unauthenticated cross-origin management endpoint to make
a return link work. A future direct return flow requires an explicit pairing
contract.
It retains separate child daemons; it does not establish one shared executor or
cross-service hardware exclusion. Those remain tracked in the [current platform work](../platform-status.md) and
runtime design; #614 only delivered the earlier application-entry slice. Notebook/page scientific selections are unchanged.

## Ownership

The host owns a local deployment catalog, the teaching inventory and serialized
lifecycle operations. `host/services.sqlite` assigns stable deployment IDs to
canonical project roots and explicit runtimes. Registration and operation admission
share a lock; queued/running startup prevents rebinding its environment. This is
separate from the implemented daemon workspace-source catalog and from scientific
applicability identity.
The managed directory UUID identifies a teaching workspace; API requests use that
identity rather than accepting arbitrary filesystem paths. Existing managed copies
are discovered from their teaching metadata, version and current-generation record.
All mutation paths revalidate membership and reject symlink escape. Current copies
and copies with running processes are protected from deletion. Real experiment
workspaces and data spaces are not admitted to this disposable inventory.

Each exercise retains its installed Python environment and project daemon. The host
never imports its author package or acquires its devices. This preserves dependency,
module-name, source-revision, data-writer and failure boundaries during the migration.
A host restart does not stop exercise services, rewrite retained data or redirect
Notebook requests. This is one application entry with managed child services, not
a claim that every execution now shares one Python process.

## Process and operation lifecycle

A launch lock serializes concurrent clients; a separate owner lock protects the
host lifetime. A private endpoint record carries process creation time, instance
identity, protocol, runtime content identity and a random token. The host listens
only on loopback, validates Host/Origin and requires the token for API access.
The browser receives it in a URL fragment and removes the fragment immediately;
API replies and logs do not expose it. This is a trusted local-user service, not
an isolation boundary against arbitrary code running as that same OS user.

Operations persist in `host/operations.sqlite3`; output is retained per operation.
Client-generated IDs make resubmission idempotent. Only one management operation
runs at a time, and admission/claim transitions use SQLite writer transactions.
A short-lived worker runs in the manager's installed environment and uses each
exercise's interpreter for execution. Its process identity and completion are
persisted independently of the HTTP process. After restart, live workers remain
observable; absent workers become interrupted, with files and logs preserved.
Interrupted work is never replayed automatically. Creating a new copy publishes
the current pointer only after its environment is ready.

Graceful host shutdown and runtime replacement reject active management work.
Replacing the manager does not stop an already running exercise. Fixed releases
live in separate installed directories. Per-home installation serializes preparation,
stages and checks the copied bundle, and preserves incomplete runtime attempts before
retrying. Virtual environments are built at their final path. Complete launchers are
published atomically, with `lab.py` as the release-selection point; neither retained
releases nor registered service paths are redirected by manager installation.
Selecting a prepared release does not itself replace a running host: the next launch
still requires management work to be idle. Source-development entry stops its manager
before replacing the source runtime. UI and CLI both use the operation API, so
there is one lifecycle owner rather than two competing filesystem implementations.

## Development and acceptance

Tests use explicit temporary homes and dynamic loopback ports. They do not install
an OS service or depend on the user's default installation. Real-process tests
cover concurrent launch, identity reuse, authenticated access, interrupted HTTP
ownership and operation reconnection. Installed Windows/Linux acceptance creates
all four topics through the same host, runs the shipped Notebooks, resets a copy,
deletes the selected old copy and shuts down the host.

## Further deployment decisions

Follow the context, session execution, workbench integration and maintenance order
in the [implementation slices](experiment-contexts.md#ordered-implementation-slices).
Lifecycle reuse is useful, but further teaching-launcher expansion and tray work
are not the next architectural step.

The existing workspace/data-space/deployment binding remains authoritative for
project execution. Before generalizing this host to laboratories, define stable
workspace and deployment IDs independent of paths, cross-workspace physical-device
ownership, session authorization and compatible runtime upgrade rules. Independent
stores currently do not establish exclusion for two declarations targeting the same
physical instrument. A management wrapper does not solve that problem by itself.

Tray integration and login startup can be added to this entry after its lifecycle
has been exercised. Unattended machine services need an explicit OS identity and
device-access policy. LAN access needs authenticated clients and distinct rights for
reading results, publishing source, changing parameters and controlling devices.
These features are not enabled by this initial local teaching implementation.
