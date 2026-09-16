# Workspace, data space and execution binding

Status: design and code audit for [#572](https://github.com/scopecat-project/scopecat/issues/572),
reviewed against `a12fe550a` on 2026-09-16. The contract below is an implementation
target, not a supported manifest syntax or a completed migration. The first
implementation is local, with one writer per data space and one service per
configured execution deployment.

## Existing contracts and actual gaps

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
deployment identity and ownership location. Manifest parsing must distinguish
these locations explicitly; the field spelling is finalized with typed producer
and consumer fixtures, before templates adopt it. Local synthetic projects may
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

This audit does not pass those acceptance gates. #572 stays open until the
integrated journey succeeds. Do not publish consumer configuration examples
that imply the proposed binding is already accepted by `scopecat.toml`.
