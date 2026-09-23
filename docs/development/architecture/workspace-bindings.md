# Workspace, data space and execution binding

Current format: schema **96** supports workspace-scoped author publication within
one qualified local service. It is a development format, not a compatibility
baseline. Earlier schema/migration exercises are retired; this page describes
current behavior only. See the [data policy](../data-compatibility.md),
[project layout](../../reference/project-layout.md#bind-a-workspace-to-persistent-local-data)
and [current-format restore](../../how-to/backup-and-restore.md#separately-located-data).

## Current ownership contract

1. **Workspace:** the source directory discovered by `open_project()`. Its location
   supplies authored code; combined projects also supply maintained composition.
   An author-only folder inherits an explicitly registered installed laboratory;
   its location is not scientific identity.
2. **Data space:** SQLite, immutable objects, retained source bundles and durable
   receipts share one persistent identity independent of paths. One writer owns it.
3. **Execution deployment:** one configured owner of backend composition/device
   bindings and one selected data space. Its canonical ownership location is shared
   by authorized launch paths; moving code does not release that ownership.
4. **Registered author source:** each registered workspace has its own publication
   head, preparations and qualified worker binding. Opening a folder or connecting
   a client does not register code, acquire hardware or replace another source.

`scopecat.runtime.toml` resolves workspace, data and deployment paths. It is local
machine configuration, excluded from captured source and snapshots. Runtime paths
come from the live binding, never a retained manifest. Hardware deployments select
their maintained owner explicitly. Different dependency environments and remote
execution remain outside this local qualification.

## Ownership and recovery

- Acquire deployment ownership, then data-writer ownership, before constructing a
  hardware backend. Failed startup releases both reservations.
- Endpoint and health checks validate registered source, data and deployment owners;
  an explicit URL does not authorize rebinding or source substitution.
- Existing device generations, leases and unknown-effect quarantine remain in
  force. Unrelated declarations that secretly share an instrument are not solved
  by data-directory locks or registration.
- Within the current format, switching source or moving its registered location
  retains scientific identity, source/config hashes and run addresses. Publication
  is explicit and cannot relabel another source's retained receipts.
- Reading retained results is distinct from executing their source. Execution still
  checks captured environment and maintained composition and never automatically
  replays uncertain hardware work.
- Current-format backup restores into a fresh location without an endpoint, local
  source binding or device dispatch. Independent writable clones and cross-store
  imports require separate identity/ownership policy.

These invariants do not require new software to decode prebaseline formats. Leave
unsupported files untouched; owner-maintained historical environments are archival
arrangements, not a Scopecat migration service.

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
The daemon now composes registered workspaces with one qualified interpreter and
maintained composition, scoped author heads, and endpoint/data/deployment checks.
Environment qualification and deployment/data writer locks remain in force.
Different dependency environments remain outside this qualified deployment. Reading retained evidence remains independent
of qualifying its original code for execution.

## Workspace-scoped author publication

The current format implements the vertical contract below for
[#630](https://github.com/scopecat-project/scopecat/issues/630), within
[#613](https://github.com/scopecat-project/scopecat/issues/613). The application host
registers whole deployments; this catalog registers author sources within one.

Stop the service and run, using its installed environment:

```shell
scopecat register-workspace /path/to/second --service /path/to/service
```

Then start from the service workspace. The returned stable workspace ID selects
independent publication state. Opening `Project`/`AuthorProject` from a registered
source uses that owner automatically; connecting never registers a path. Only the
service source can start the daemon or snapshot the shared store. The GUI defaults
to its service owner. Its **Code workspace** selector lists registered source
identities and their current execution availability; retained sources without a
qualified local binding remain visible as unavailable. Listing performs no source
publication, registration or environment installation.

The selected code workspace belongs to the page's draft. Switching it discards
old experiment inputs, preview and pinned plan/source while preserving scientific
selection, operator and record collection. Same-name experiment definitions remain
qualified by their workspace. Refresh operates on that workspace only; completing
an explicitly requested refresh invalidates an unpinned preview even when the
experiment declaration is unchanged. Saved plans
and comparison handoffs retain their exact source owner and revision; **Use current
source** is an explicit choice that requires a fresh preview. An uncertain original
submission remains separately recorded under its original owner and is never
rewritten into the newly selected workspace.

Machine-local bindings live in the data directory's `author-workspaces.json`,
separate from persistent source membership. They are excluded from snapshots.
After a current-format restore, retained runs and bundles remain readable without
those paths. For
executable access, stop the service and explicitly register qualified source with
`--identity EXISTING_WORKSPACE_ID`; unknown IDs and `legacy` are rejected. Moving
an identity revokes its old location. Portable multi-source installation bundles
and different dependency environments remain outside this slice.

Focused process coverage uses two clients/workspaces with the same package name,
one daemon, independent refresh, shared numbering, old-version execution, saved
plan execution and original-source analysis across clients. It also checks
restored evidence without secondary source, registration locks and identity moves.
Full installed/Windows and interactive-kernel qualification remains in closeout.

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
it must not change a retained current-format bundle's content hash.

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
Current-format retained results remain readable when that workspace is unavailable. Different working
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

The service workspace has the explicit reserved identity `legacy` in the current
format. This is a valid current owner, not a rule for interpreting missing fields
in old data. Source ownership must be recorded explicitly; do not invent it from
paths, labels, a missing field or today's selected workspace. Machine-local
locations must be rebound before execution after restore.

### Dependencies, owned files and exit evidence

| Work | Main consumers | Required completion |
|---|---|---|
| Catalog and current-format recovery | `records/author_revision.py`, SQLite author repository/schema/snapshots | Two workspace heads/preparations are independent; current-format backup/restore preserves source membership |
| Service qualification | `services/author_revisions.py`, `services/application.py`, `runtime.py` | Registered owner resolves baseline/binding; incompatible maintenance/environment is rejected before publication |
| Connection and requests | `daemon/client.py`, `daemon/endpoint.py`, HTTP transport, `LaunchRequest`, retained worker calls | No arbitrary path admission or silent workspace fallback; preview/submission bind exact source |
| Python and workbench callers | `Project`, `AuthorProject`, Notebook workspace, catalog/refresh UI and generated API | Existing single-workspace flows use the explicit owner; switching one client leaves another untouched |
| Execution consumers | Author/validation/retained workers, saved plans and procedure launch | Exact original source and child provenance survive refresh/restart; same-name packages remain process-isolated |

Own changes to this vertical chain in one coordinated worktree;
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

## Author-only installed-laboratory binding

A manifest with `[authors]` and no `[lab]` is an author-only folder. Discovery without
adapter resolution can inspect it before registration. Loading or capturing it for
execution requires explicit registration with a laboratory whose `[lab]` contains
only an installed adapter reference. It cannot become a second application service.

Source registration and daemon startup compare the laboratory adapter reference
and installed adapter content, rather than requiring identical source manifests.
Each author's maintenance hash still covers its own source boundaries, dependency
requirements, installed content and non-refreshable files. The service's interpreter,
data binding and deployment binding remain shared and explicitly checked.

Captured sources include an internal `scopecat.laboratory.toml` containing the exact
adapter reference. Qualified workers load it from the verified revision, never from
current local source registration. Installed artifact hashes and environment checks
remain required. Changing adapter selection requires stopped maintenance/restart;
old revisions cannot execute under a different maintained baseline. The existing
combined-project contract remains for project-local bootstrap and driver code.
