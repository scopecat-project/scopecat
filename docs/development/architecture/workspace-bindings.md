# Workspace, data space and execution binding

Status: the first local binding implementation uses `scopecat.runtime.toml`,
store schema 68 and separate deployment/data locks. See the supported
[project layout](../../reference/project-layout.md#bind-a-workspace-to-persistent-local-data)
and [restore behavior](../../how-to/backup-and-restore.md#separately-located-data).
This page retains the pre-implementation audit against `a12fe550a` and explains
the design boundaries. No historical-store migration or remote execution is
included. [#572](https://github.com/scopecat-project/scopecat/issues/572) tracks
integration acceptance.

The [experiment-context direction](experiment-contexts.md) proposes replacing the
one-active-workspace restriction with explicit session/source ownership. It does
not relax this implemented binding contract before that replacement is delivered.

## Pre-implementation contracts and gaps

Paths below are repository-relative implementation locations.

| Concern | Existing implementation | Required change |
|---|---|---|
| Workspace discovery | `packages/scopecat/src/scopecat/project.py`: `open_project()` walks upward; `Project.root` supplies composition and mutable source | Preserve discovery and the workspace root; resolve a separate explicit runtime binding |
| Persistent storage | `packages/scopecat-server/src/scopecat_server/runtime.py`: SQLite and immutable objects live under `<root>/.scopecat`; `SQLiteProjectStore` already owns their physical paths | Resolve these paths from the data space; retain existing run, sample, parameter and object identities |
| Service identity | `runtime._project_id()` hashes the absolute workspace path; `services/application.py` exposes it through health | Persist a data-space identity independent of code paths; expose and validate deployment and selected workspace identity separately |
| Discovery and lifecycle | `packages/scopecat/src/scopecat/daemon/endpoint.py` and server `lifecycle.py`: endpoint, process identity and shutdown token are workspace-local; explicit URL/environment overrides bypass record checks | Resolve the selected binding and compare it with service health, including explicit endpoints; do not silently attach to another workspace's author service |
| Writer ownership | Runtime holds `<root>/.scopecat/daemon.lock`; SQLite coordinates work within that owner | Lock the canonical data space, not each launch directory; also reserve the configured execution deployment before backend construction |
| Instrument ownership | Instrument generations, leases, claims and unknown-effect quarantine operate within one daemon/store | Preserve these mechanisms; they do not establish exclusion across independently configured stores |
| Author source | `project_sources.py` captures relative files, environment and installed-package content; `services/author_revisions.py` publishes content-addressed revisions and a single active generation | Bind exactly one mutable workspace to the service; preserve retained revisions and explicit refresh/publication |
| Source loading | Server `author_worker.py` and `services/author_revisions.py` materialize under `<root>/.scopecat/code`; workers separately use the runtime root and captured code root | Carry resolved runtime paths through workers; never resolve live storage from the archived manifest or infer it from the materialized source directory |
| Receipts and auxiliary state | `Project.authoring()` stores `author-jobs` under the workspace; procedure workers, diagnostics and logs also derive paths from it | Classify each as durable evidence or regenerable runtime state and route it to its owner; moving only SQLite is insufficient |
| Snapshot and restore | Server `snapshots.py` locks a stopped project, copies SQLite plus objects, validates references and restores without executing code | Extend inventory/path ownership for external data spaces and durable receipts; current snapshot format excludes `author-jobs`, caches, endpoint records and diagnostics |

The path-derived `project_id` is currently a health identity, not a reason to
rewrite every run or sample record. Reuse existing content hashes and IDs.
Research-project grouping belongs to [#573](https://github.com/scopecat-project/scopecat/issues/573);
format migration and supported baselines belong to
[#574](https://github.com/scopecat-project/scopecat/issues/574).

## Minimal binding contract

1. **Workspace:** the directory discovered by `open_project()`, containing local
   composition, authored source and its manifest. `Project.root` continues to
   mean this directory. Its absolute path is a location, not scientific identity.
2. **Data space:** the owner of SQLite, immutable objects, retained source bundles
   and durable author receipts. A newly initialized store receives a persistent
   ID under exclusive ownership; it is not generated from a workspace or storage
   path. Opening existing state does not silently assign a new scientific history
   or perform a format upgrade.
3. **Execution deployment:** a locally configured owner of backend composition,
   device binding and one selected data space. It has a stable identity and a
   canonical local ownership location shared by every authorized launch path.
   Changing software directories does not change that ownership location.
4. **Active author binding:** the running service selects exactly one workspace.
   Another workspace may replace it only after an explicit stop and validated
   restart. Merely opening a folder or connecting a notebook does not switch
   mutable source, install dependencies, refresh revisions or acquire instruments.

The resolved internal binding carries workspace root, data root and identity,
deployment identity and ownership location. The separate runtime file requires
`[runtime].data_root` and `deployment_root`; it is excluded from source capture
and snapshots. Runtime consumers resolve it from the live workspace, never the
retained code directory. Local synthetic projects may
use a colocated default. Hardware consumers select their deployment explicitly.
No general remote execution or independent client/server dependency matrix is
introduced by this contract.

## Ownership and transition rules

- Acquire deployment ownership, then data-space writer ownership, before loading
  a hardware backend. Release already-acquired resources if either reservation
  fails. Two workspaces targeting one data space must not start two services;
  two data spaces targeting one configured deployment must also conflict.
- Health/discovery must identify the data space, deployment and selected mutable
  workspace, in addition to the existing process identity checks. A mismatched
  author connection fails before submission or refresh. Endpoint overrides do
  not authorize rebinding.
- The deployment mapping is maintained configuration. This does not detect two
  unrelated deployment declarations that secretly refer to the same physical
  instrument; arbitrary cross-host/SDK arbitration remains out of scope.
- Switching A to B retains the same data-space ID and old run/parameter/sample
  references. A changed workspace supplies new source only through the existing
  validated publication path. It cannot relabel old receipts or replace retained
  source with today's files.
- Read access to original results and saved analyses is distinct from executing
  old code. `require_environment()` and maintenance-hash checks remain relevant
  to re-execution. A new software version does not automatically qualify an old
  revision for execution or resume interrupted hardware work.
- Captured `scopecat.toml` currently contributes to the maintenance hash. Separating
  deployment binding from code must preserve the captured bytes and old hashes;
  runtime location resolution must not follow stale paths in retained manifests.
  Do not weaken maintenance checks merely to make a moved workspace pass.
- Backup includes all declared durable owners and restores to a fresh location
  without a live endpoint or device dispatch. A recovery copy retains lineage;
  independent writable copies and their identity/import semantics require the
  explicit policy in #574/#575, not accidental cloning by a folder copy.

## Implementation slices and acceptance

| Slice | Owned changes | Required observable result |
|---|---|---|
| Binding and store identity | Typed project binding, store metadata, path resolution and schema policy | Two workspace locations resolve one explicit data space; identity survives a code-directory move; unsupported old stores are rejected without mutation |
| Lifecycle and ownership | Endpoint/health models, start/stop, deployment and data locks, config-command clients | Same-data and same-deployment conflicts fail before backend construction; explicit endpoint mismatch fails; failed startup releases its reservations |
| Author and worker consumers | Receipts, revision service, validation/analysis/procedure workers, diagnostics | A publishes and runs; after stopping A, B attaches to the same history; old results and source refs remain readable, new refresh uses B, competing bindings fail |
| Recovery and integrated journey | Snapshot inventory, restoration and installed starter | Snapshot external data and durable receipts; restore elsewhere and read/verify retained evidence without original paths or dispatch; restart and source/environment mismatch behavior remain explicit |

Use `packages/scopecat/tests/project/test_project.py`, daemon endpoint tests,
server `test_lifecycle.py`/`test_runtime.py`, author revision tests and
`test_snapshot_integration.py` as existing coverage seams. Add one installed,
synthetic A-to-B journey covering actual processes, receipts and restart. Keep
run-like checks serial locally. Linux/Windows CI and self-review precede public
merge; hardware admission is a separate consumer qualification.

The installed acceptance fixture is `scripts/verify_workspace_binding.py`, also
run by `verify_pilot_bundle.py` and the runtime-binding journey tests. It checks
competing author/service bindings, A-to-B history and config, original/current
analysis, receipts and restoration after removing the original directories.
Scientific and physical-device qualification remains external to that fixture.

## Revision worker binding

Revision workers now receive an explicit `AuthorWorkerBinding`: the live workspace
and the Python executable selected by the service composition. Pool ownership,
serialization and validation-worker adoption are keyed by that binding **and** the
immutable source revision. Two bindings with identical source hashes cannot reuse
one another's interpreter, live endpoint or imported application. Launch and
retained analysis/comparison pass the same binding. The executable path preserves
virtual-environment symlinks; resolving it to the base interpreter would lose the
selected environment.

This is an internal local process contract, not a persistent workspace catalog.
The current daemon still composes one workspace with its own interpreter, stores
one active author head, and validates endpoint/data/deployment bindings. Existing
environment qualification and deployment/data writer locks remain in force.
Different dependency environments and simultaneous workspace admission require
explicit runtime qualification and workspace-scoped publication before the public
connection contract can admit them. Reading retained evidence remains independent
of qualifying its original code for execution.

## Next vertical slice: workspace-scoped author publication

Design for [#613](https://github.com/scopecat-project/scopecat/issues/613), not a
shipped multi-workspace API. Implement after the target-catalog schema change;
allocate the next migration centrally. Do not expose a second workspace until the
whole registration → preparation → submission → retained-read path below works.

### Ownership and qualification

| Owner | Identity and state | Boundary |
|---|---|---|
| Application host deployment registry | Registered service ID, local startup location/interpreter, observed daemon identity/status | Finds and starts a service; never owns its scientific catalog or author head |
| Daemon workspace catalog | Store-local stable workspace ID, label, registered source membership | Identifies author publication within one scientific store; paths are locations, not IDs |
| Machine-local workspace binding | Workspace ID → canonical source root and exact interpreter path | Explicit trusted registration; excluded from portable scientific snapshots and never restored as permission to execute |
| Workspace author service | Baseline, head generation/revision, preparation operations | Refresh and cancellation affect that workspace only |
| Existing data/deployment authority | One data writer, maintained composition, device claims and fencing | Registration does not replace the backend, active scientific configuration or another workspace |
| Page/kernel session | Selected workspace plus existing scientific context | Refresh defaults are local; prepared work retains exact source and scientific references |

The first supported pair of workspaces must share the daemon's qualified Python
and maintained composition. Their author files and module names may overlap, but
run in separate revision workers. Reject another environment or maintenance hash
with an actionable qualification error; do not install dependencies or restart the
backend as a side effect of connecting or refreshing. Different environments and
apparatus compositions need their own qualification slice.

The local interpreter path selects a process; it is not an environment content
hash. Reuse the captured Python/package/installed-author checks before execution.
Moving a registered workspace updates its location explicitly and preserves its ID
and publication head. Reading existing results needs neither that location nor an
executable environment. A copied scientific store is not automatically authorized
as another writable deployment.

### Source and request boundary

Keep `AuthorRevisionRef` as the immutable content hash: identical bundles can share
stored content. Workspace identity qualifies publication and execution ownership;
it must not change the bundle's historical content hash.

- Make workspace selection explicit on the daemon client connection. The workbench
  and Notebook bind that identity before catalog browsing or refresh. Unregistered
  paths cannot become registered merely by sending an HTTP request.
- Scope author state/preparation routes to a workspace ID. Preparation requests
  carry that workspace's expected generation. State and operation responses identify
  their owner; cancelling or retrying another workspace's operation is rejected.
- Carry the workspace ID alongside the exact `code_revision` through launch
  preview, submission, saved recipes, retained analysis/comparison and procedure
  child provenance. One shared request model is authoritative; do not add a second
  competing source field with precedence rules. Update all current consumers.
- An unpinned catalog/preview resolves only the selected workspace's head.
  Submission uses the prepared revision; it never substitutes a later head.
  Expected-generation conflicts stay local to the selected workspace.
- The revision lookup remains content-addressed, but execution also checks that
  the revision belongs to the selected workspace and passes its maintenance and
  runtime qualification. Reading a retained bundle is distinct from executing it.
- Resolve workers from the registered local binding, not an arbitrary request
  executable/root. The worker receives the selected workspace's connection
  identity; endpoint/health verification checks catalog membership plus the same
  data/deployment ownership. Replace the one-root match with that explicit check,
  rather than deleting the existing protection or treating a URL as permission.

For a run's original-source analysis, recover the retained source owner/revision;
`source="current"` explicitly selects a qualified workspace's current revision.
Old results remain readable when that workspace is unavailable. Different working
points, batches or record collections do not select another source implicitly.

### Replace the singleton and route lifecycle by owner

Keep the immutable `author_revisions` content table. Replace the mutable singleton
head with `author_workspace_heads(workspace_id, generation, content_hash)` and
explicit workspace/revision membership. `AuthorRevisionRepository` is constructed
with a workspace ID so every state, publication and preparation query is scoped.

Preparation identity is `(workspace_id, operation_id)`, not operation ID alone.
This also prevents identical `initialize-{hash}` operations and generation-zero
lookups from crossing workspaces. Store updates, publication CAS and completion of
that preparation stay in one transaction. A workspace refresh must not interrupt,
adopt or report another workspace's preparation. On service restart, mark only
actually owned unfinished operations interrupted; never replay them automatically.

Replace `application.author_revisions` as the one selected service with an
owner-resolving catalog/service manager. Reuse `AuthorWorkerBinding` and the existing
binding/revision pool key. Keep the bounded process budget and per-key serialization;
qualifying another workspace must not introduce an unbounded worker pool.
Do not retain an implicit globally active workspace for old routes after all
current callers have moved to the explicit contract.

The copy migration gives the old singleton and preparations one explicit legacy
workspace identity, copies generation and content references, then retires the
singleton bookkeeping in the destination. Preserve preparation JSON/evidence,
source bundles, receipts, run IDs, source/config hashes and record addresses.
Historical records without a workspace field resolve to this designated legacy
owner; do not infer identity from paths, labels or today's selected workspace.
This is a supported-data read rule, not a parallel mutable legacy author service.
Machine-local locations must be rebound before execution after restore.

### Dependencies, owned files and exit evidence

| Work | Main consumers | Required completion |
|---|---|---|
| Catalog and copy migration | `records/author_revision.py`, SQLite author repository/schema/migrations/snapshots | Legacy retained data opens without evidence/hash changes; two workspace heads and preparations are independent |
| Service qualification | `services/author_revisions.py`, `services/application.py`, `runtime.py` | Registered owner resolves baseline/binding; incompatible maintenance/environment is rejected before publication |
| Connection and requests | `daemon/client.py`, `daemon/endpoint.py`, HTTP transport, `LaunchRequest`, retained worker calls | No arbitrary path admission or silent workspace fallback; preview/submission bind exact source |
| Python and workbench callers | `Project`, `AuthorProject`, Notebook workspace, catalog/refresh UI and generated API | Existing single-workspace flows use the explicit owner; switching one client leaves another untouched |
| Execution consumers | Author/validation/retained workers, saved plans and procedure launch | Exact original source and child provenance survive refresh/restart; same-name packages remain process-isolated |

Own this vertical chain in one worktree after the target-catalog migration lands;
coordinate edits to launch/source records, transport and schema with the target
and application-host owners. The host registry work can proceed independently;
it registers a deployment, not an author workspace inside that deployment.

The focused exit journey uses two actual kernels/workspaces with the same package
name and one qualified composition. It checks independent catalogs and refresh
CAS, identical-hash worker isolation, old prepared/source execution, failures and
cancellation scoped to one workspace, shared collection numbering, restart and
retained reads after source removal. Add explicit unknown-workspace, wrong-binding
and incompatible-environment/maintenance rejection cases. Existing shared-device
claims remain enforced; this slice does not qualify cross-deployment physical-device
aliases or multiple maintained apparatus compositions. Keep full installed/Windows
and recovery acceptance in the integration closeout tracked by the parent issue.
