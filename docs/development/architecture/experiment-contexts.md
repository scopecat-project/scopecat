# Experiment workbench and session contexts

Status: selected product direction and proposed implementation contracts, recorded
2026-09-19 after the teaching-host trial. The entities and APIs proposed here are
not shipped. This document governs the next implementation slices; it does not
relax current ownership checks or designate a new supported scientific-data baseline.

## Product outcome

Scopecat opens as an experiment workbench: choose what to measure, inspect the
resolved setup, execute, and find retained results. Tutorials live under Help and
open prepared simulated experiments in that same workbench. The teaching inventory
introduced in [the local host](application-host.md) is a transitional facility,
not the application's information architecture.

One default local application manages discovery, execution services and installation.
Each workbench page and Python kernel selects its own experimental context. A kernel
normally discovers its code workspace from its current repository; that discovery
must not silently choose a physical sample, replace another session's source, or
acquire devices. No application-wide mutable "current chip" or "current config"
controls all clients.

A representative context summary is:

> A + B assembly · cooldown 3 · joint-gate working point · coupler-lab · operator Li

First-class selection does not require filling every field for every run. Remember
session defaults, offer compatible choices, and show the resolved context before
submission. Changing a selection affects future requests in that session only.
An operator identity records attribution; a selected name is not authentication
or permission to control hardware.

## Audit of current ownership

Paths in this table are repository-relative. They describe the implementation at
`b9d6bb73c`, not the proposed end state.

| Surface | Current responsibility | Consequence for the next design |
|---|---|---|
| `packages/scopecat/src/scopecat/project.py` | `Project` discovers code, composition, backend entry and default daemon connection | Keep code discovery; do not let its directory define scientific grouping or all session choices |
| `packages/scopecat/src/scopecat/runtime_binding.py` | Separates workspace, data and deployment paths | This is a location/ownership foundation, not a model of chips or experimental batches |
| `packages/scopecat-server/src/scopecat_server/runtime.py` | One data writer and deployment owner; composes services for one workspace | Multi-workspace admission needs a new source/execution ownership contract, not removal of endpoint validation |
| `packages/scopecat/src/scopecat/application/author_project.py` and server `storage/sqlite/schema.py` | Short run numbers expose `scheduler_runs.sequence` within a store | User numbering scope is currently tied to storage rather than an explicit record collection |
| `packages/scopecat/src/scopecat/records/sample.py` | Stable store-local sample IDs, immutable revisions, roles and relations | Preserve provenance; relations alone do not specify executable multi-chip wiring or joint calibration |
| `packages/scopecat/src/scopecat/records/config_context.py` | Working-point context tied to one exact sample binding and registry base | Multi-sample working points need an explicit target/assembly scope |
| `packages/scopecat/src/scopecat/records/config.py` | A complete snapshot includes topology, instruments, routing, parameter definitions and values | Separate authoring/maintenance ownership while retaining one resolved execution snapshot |
| `packages/scopecat/src/scopecat/records/research_project.py` | Mutable many-to-many research organization around samples and runs | Research membership and history filters must not determine or renumber a run's permanent short address |

The [workspace binding contract](workspace-bindings.md) remains authoritative for
current execution. Its one-active-workspace limit is an implementation boundary
to replace deliberately. Existing sample IDs are store-local; a common application
must qualify imported identities by their owning catalog/store or explicitly map
them, never merge two samples merely because both are called `chip-a`.

## Concepts and independent lifetimes

| Concept | Owns | Does not implicitly select |
|---|---|---|
| Application installation | Runtime versions, discovery, service lifecycle and maintenance entry | Current chip, numbering scope, scientific identity |
| Code workspace and revision | Author files, environment requirements, immutable submitted source | Storage folder, sample or cooldown |
| Physical sample | Stable physical identity and revisioned description | A repository or one run sequence |
| Measurement target / assembly revision | Selected sample revisions, roles, member-qualified entities and interconnections | A new physical identity for each cooldown |
| Experimental batch | A named event/campaign such as mounting or cooldown, with stable ID and metadata | A database, process or mandatory wall-clock-derived identity |
| Working point | Versioned parameter state applicable to a target and compatible setup | Another session's selection or an automatic hardware write |
| Apparatus and setup revision | Maintained instrument identities, connection/routing configuration and capabilities | Which research project owns results |
| Operator | Attribution and, separately, authorized capabilities | A new environment or data directory |
| Record collection | Stable run-address namespace and history organization for acquisition | Physical file layout or arbitrary search-result numbering |
| Research project | Changeable scientific grouping of samples and evidence | Immutable acquisition addresses |
| Session context | One client's selected references and defaults | Ownership of devices or mutation of shared scientific state |

An experimental batch can contain several samples and collections. A collection
can have a default target/batch without deriving its identity from their names.
The initial convenience flow may create one collection for a selected target and
cooldown. Changing an operator, repository or working point keeps that collection
unless the user explicitly chooses another. Renaming labels never changes IDs.

An assembly is a revisioned measurement target, not a string concatenation of chip
names. It records members and their physical roles, qualified entity addresses,
and relevant interconnections. Moving from A to A+B does not reuse A's working point
as a joint working point without an explicit compatible composition. Single-chip
and joint calibration validity must remain distinguishable. The current one-sample
context and subject-role analysis rules need adaptation and consumer tests before
claiming this support.

## Stable numbering without folder ownership

A run keeps its complete durable ID. Its human address is the pair
`(record collection ID, positive run number)`, rendered with a recognizable
collection label, for example `chip A / cooldown 3 / #42`.

- Allocate the short number transactionally with successful admission. Concurrent
  sessions using one collection cannot receive the same number.
- An exact idempotent retry returns the same run and address. A cancelled or failed
  admitted run retains its number; gaps are allowed and numbers are not recycled.
- Searching by sample, working point, operator or research membership never
  recalculates numbers. A run has one original acquisition address; additional
  collections of references/bookmarks do not relocate or renumber it.
- `#42` resolves only against an explicitly bound collection. Ambiguous cross-
  collection lookups require qualification, never "whichever is currently global".
- Switching code, reopening a kernel, moving files or updating Scopecat does not
  reset the counter. Creating a new experimental collection can start at #1.
- Independent writable clones cannot both allocate under the same collection
  identity. Restore/import must retain origin addresses and establish an explicit
  new ownership/branch policy before accepting new measurements.

This is a proposed replacement for the store-sequence convenience API. Migration
must retain old run IDs, evidence, source/config hashes and existing local number
references. A possible legacy mapping is one explicit collection per original
store with the existing sequence values; validate it against supported baselines
before choosing a migration. Do not infer historical cooldown boundaries from
folder names or renumber previously retained evidence automatically.

## Session selection, preparation and admission

The intended flow is selection -> validated preparation -> admitted frozen context.
Preparation resolves compatible source, sample/assembly revisions, working-point
revision, apparatus setup, batch, collection and operator attribution. The GUI and
Python client use the same server contract. No concrete constructor syntax is
committed here.

A prepared request retains exact references. Later selection changes do not rewrite
it. If a shared head changes between preview and submission, admission must either
honor the explicitly pinned compatible revision or report the relevant conflict;
it must not silently substitute a new target, source or parameter state. A context
change affecting preview semantics requires preparation again. Admitted runs retain
the resolved snapshot and provenance, including procedure-child attribution and
numbering policy. Refresh of code follows the same rule.

A session's working-point selection is distinct from publishing a shared update.
Publication requires an expected revision, validates applicability, and records the
actor and evidence. Another client's conflicting update is surfaced rather than
overwritten. Restoring a UI selection or parameter context never dispatches hardware.
A changed operator on a shared workstation affects future requests, not old records.

Two pages and two kernels can inspect different contexts concurrently. Requests
from multiple code workspaces require source-qualified catalogs and isolated author
workers/environments; importing two packages with the same module name into one
shared interpreter is not the solution. Preserve exact environment qualification
for executing retained source; reading old data need not execute old code.

## Configuration composition and resource ownership

Maintain apparatus/setup, target description, parameter declarations, working-point
values and per-request overrides under their appropriate owners. Resolve them into
a complete immutable execution snapshot with value origins and compatibility checks.
This separates maintenance without weakening reproducibility. Parameters defined
by code must be checked against the selected target and setup. Do not automatically
merge a sample topology into accepted wiring, or treat arbitrary combinations of
otherwise valid selections as executable.

Device ownership crosses session, collection, code and storage boundaries. A common
resource authority must identify the same physical device across declarations,
coordinate claims, and retain existing generation fencing and unknown-effect
quarantine. Different collection IDs or different worker processes confer no right
to operate an occupied device. Initially one local authority can manage registered
apparatus; arbitrary independent SDK users and cross-host arbitration are separate
qualification problems. Renaming a configured device must not bypass its identity.

One application may supervise several workers and separate stores. Presentation,
scientific ownership, execution isolation and filesystem layout are independent
choices. Do not require one huge database, one interpreter, or a single process to
achieve one ordinary installation and workbench entry.

## Teaching inside the same application

Help opens a prepared exercise in the ordinary workbench with a persistent, visible
practice marker. Each exercise owns a simulated target, copied working-point state,
record collection and source sandbox. Repeated teaching labels and `#1` are qualified
by exercise identities. A real-work context and several practice contexts can remain
open side by side.

The server must enforce practice resource and publication boundaries: practice
sessions can use only simulated backends; they cannot obtain real apparatus
capabilities or advance a real working point. Merely hiding real devices in a menu
is insufficient. Initial storage and worker separation may remain underneath this
shared presentation. This is protection against accidental workflow crossover,
not an OS security sandbox for arbitrary Python code running as the same user.

Reset creates a fresh exercise generation. Cleanup can delete only owned disposable
practice state after checking active use and references; retained real evidence is
never subject to teaching cleanup. Exporting useful practice source or retaining an
exercise result is explicit and must not create a dangling real-data reference to
later-deleted practice state. Tutorial updates must not mutate a running exercise.

## Scenario acceptance

These are future executable acceptance cases, not passed tests. The existing host
checks establish lifecycle behavior only.

| Scenario | Required observable result |
|---|---|
| A, same cooldown, code repository X -> Y | New runs use Y's captured source; history, sample identity and collection numbering continue; X's old results remain readable |
| Same code, target A -> B in one page | Compatible B context is selected or a specific incompatibility is shown; A's prepared/admitted work and another page remain unchanged |
| A enters a new cooldown | Stable A identity; explicit new batch and default new collection at #1; previous cooldown remains searchable |
| A+B joint measurement | Frozen assembly members/connections and joint parameter scope; results discoverable from both samples without duplicate acquisition or rewritten ownership |
| Two kernels, same collection | Unique numbers, independent selections and preserved code revisions; resource conflict is visible when both need the same instrument |
| Working point updated during preview | Explicit revision conflict or honored pinned-compatible request; no silent substitution; no relabeling of admitted work |
| Research grouping, filtering or folder rename | Existing run IDs and human addresses remain unchanged |
| Two practice exercises plus a real-work page | Independent numbering/parameters; practice cannot dispatch real hardware or publish real calibration; reset leaves other contexts untouched |
| Application restart or code-directory move | Persistent catalog/collection identities and history; no automatic replay of interrupted or uncertain hardware operations |
| Installed application maintained by a person | One workbench entry; visible version/status/logs and supported update/recovery actions without manually choosing ports or numbering folders |

## Ordered implementation slices

1. **Context and address contracts.** Define stable target/assembly, batch and record
   collection references; audit current short-number consumers, research filters,
   sample/calibration scopes and supported-store migration. Specify frozen request
   context and conflicts. Exit: a synthetic admission/history journey demonstrates
   collection numbering and retained legacy references without new UI navigation.
2. **Session-scoped execution.** Implement GUI/Python request context, compatible
   config composition, source-qualified workspace admission and resource authority.
   Adapt analysis, procedures and calibration consumers. Exit: two real kernels and
   pages use independent contexts; shared-device conflicts and publication conflicts
   are enforced across workspaces. Hardware support requires its own qualification.
3. **Workbench as the application entry.** Integrate the existing experiment console
   with context selection and application discovery. Move exercises under Help;
   reuse host lifecycle code where suitable instead of expanding a second product
   UI. Exit: installed synthetic workbench + Help exercise + restart journey, with
   no new project directory required merely to switch a scientific context.
4. **Maintenance and deployment.** Consolidate installation/update/diagnostics and
   backups around documented scientific and runtime owners. Then evaluate tray and
   login startup. Machine services, LAN identity/authorization and remote execution
   have separate deployment contracts; a local singleton alone does not provide them.

Do not expand the teaching launcher as the primary application or relax existing
binding checks while these slices are pending. No new human feedback is needed to
write the contracts, inspect consumers and build synthetic acceptance fixtures.
Prototype defaults, labels and navigation with the small scenarios above before
committing to broad GUI polish. Real device behavior remains a separate evidence gate.
