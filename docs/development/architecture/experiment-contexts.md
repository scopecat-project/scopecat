# Experiment workbench and session contexts

Status: selected product direction and proposed implementation contracts, recorded
2026-09-19 after the teaching-host trial. The entities and APIs proposed here are
not all shipped; implementation status is recorded below. This document governs the next implementation slices; it does not
relax current ownership checks. The [prebaseline data policy](../data-compatibility.md)
retires the schema 68–74 migration exercises; current format 78 is not a supported
baseline. All retained-evidence and recovery contracts below concern the current
format, not a promise to read or upgrade earlier development stores.

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

## Current capability and remaining boundaries

The workbench has collection-qualified run addresses, declared batches, Python
session selection and independent GUI page selection. These are usable single-
sample features; they do not yet establish the complete target/setup model below.

| Area | Implemented | Next boundary |
|---|---|---|
| Scientific identity | Immutable sample revisions; exact single-member target selection through Python, preview, plans and parent/child admission | Graphical target picker (#643); executable assembly validation |
| Applicability | Declared batch guards; shared target/batch/setup content comparison used by parameter rebase | Use resolved applicability in preparation, admission, working-point publication and calibration |
| Working points | Exact single-sample scope, value provenance, explicit estimate copies and revision conflicts | Separate object-scoped parameter publication from executable setup and shared active-config defaults |
| Apparatus history | Descriptive object/revision and observation slice (#644); separate from executable target selection | Useful history navigation and explicit evidence links; no live wiring or calibration-validity claim |
| Execution setup | Complete retained config; setup-content projection for strict comparison | Independent maintained setup revisions and one authoritative resolver; descriptive documentation is not required to be a complete wiring model |
| Session and addressing | Per-page/kernel choices, frozen target plans, collection numbering and same-environment source-qualified workspace execution | Graphical workspace selection, heterogeneous environments and setup selection |
| Application | Registered services open the experiment workbench; Help entry and local lifecycle controls | Further Help integration and server-enforced practice boundaries |

The apparatus-history row describes the bounded slice introduced with this change,
not a completed physical-state or calibration model. Its scope and the next
configuration-ownership work are specified in [apparatus history](apparatus-history.md).

This table is the current work list. The implementation sections below explain
prior decisions; their historical limitations are not additional independent TODOs.

Current source/workspace ownership is described in [workspace bindings](workspace-bindings.md).
Sample and target identities remain catalog-qualified; a common application must
not merge physical samples merely because both are named `chip-a`.

## Concepts and independent lifetimes

| Concept | Owns | Does not implicitly select |
|---|---|---|
| Application installation | Runtime versions, discovery, service lifecycle and maintenance entry | Current chip, numbering scope, scientific identity |
| Code workspace and revision | Author files, environment requirements, immutable submitted source | Storage folder, sample or cooldown |
| Physical sample | Stable physical identity and revisioned description | A repository or one run sequence |
| Measurement target / assembly revision | Selected sample revisions, roles, member-qualified entities and interconnections | A new physical identity for each cooldown |
| Experimental batch | A named event/campaign such as mounting or cooldown, with stable ID and metadata | A database, process or mandatory wall-clock-derived identity |
| Working point | Versioned parameter state applicable to a target and compatible setup | Another session's selection or an automatic hardware write |
| Descriptive apparatus object and observation | Stable identity, recorded descriptions and historical evidence under declared conditions | Current wiring, executable targeting or calibration validity |
| Executable setup revision | Maintained execution connections/routes, drivers and capabilities | A complete physical inventory or proof that the real wiring matches |
| Operator | Attribution and, separately, authorized capabilities | A new environment or data directory |
| Record collection | Stable run-address namespace and history organization for acquisition | Physical file layout or arbitrary search-result numbering |
| Research project | Changeable scientific grouping of samples and evidence | Immutable acquisition addresses |
| Session context | One client's selected references and defaults | Ownership of devices or mutation of shared scientific state |

An experimental batch can contain several samples and collections. A collection
can have a default target/batch without deriving its identity from their names.
The initial convenience flow may create one collection for a selected target and
cooldown. Changing an operator, repository or working point keeps that collection
unless the user explicitly chooses another. Renaming labels never changes IDs.

Starting a new cooldown or changing mounting/wiring does not automatically qualify
the previous working point or calibration for the new physical conditions. A
working point's applicability includes the relevant batch/setup scope. Previous
values may be explicitly copied as starting estimates with retained provenance;
that copy is not evidence of fresh calibration. Batch selection and calibration
freshness therefore need a shared validation rule, not just matching chip names.

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

These address invariants apply to current-format acquisition and recovery. No
prebaseline store mapping or upgrade path is required. Do not infer historical
cooldown boundaries from folder names or rewrite files as part of a code refactor.

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
| A enters a new cooldown | Stable A identity; explicit new batch and default new collection at #1; previous cooldown remains searchable; old calibration is not silently treated as valid in new conditions |
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
   sample/calibration scopes and current-format storage. Specify frozen request
   context and conflicts. Exit: a synthetic admission/history journey demonstrates
   collection numbering and frozen evidence without new UI navigation.
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

## First implementation: record addresses

Record collections provide atomic admission-time numbering and collection-qualified
Python/HTTP history/lookup. Store-wide sequence lookup remains a convenience in
the current format. The earlier development migration exercise is retired. See
[record collections](../../how-to/record-collections.md) for the supported surface.

Automated scenarios cover independent collections, concurrent allocation, retry
conflicts, rename/restart stability, authored execution, saved-plan destinations
and current-format recovery. These cover the numbering portion of slice 1;
they do not complete target/assembly and batch contracts, per-session selection,
resource authority, or the application/workbench integration in later slices.

## Second implementation: client-local selection

Author/Notebook clients now keep an immutable selection of the existing single
sample, exact working-point reference, collection and operator. A validated partial
update affects future preparation only. Explicit scientific preparation inputs
replace the inherited scientific scope; saved recipes retain their own scope.
History and numeric lookup follow the selected collection. Source refresh preserves
selection, while a fresh client starts unselected. The existing LaunchRequest and
preview/admission contracts carry the frozen values; no daemon-global selection or
new storage schema is introduced. See [session context](../../how-to/select-session-context.md).

At this stage the following consumer constraints remained. Declared batch support
was subsequently delivered; assembly/setup scope still requires the replacement
contracts described below:

| Consumer | Current constraint | Required next contract |
|---|---|---|
| `SampleBinding.entity_scope` | Physical identity is the sample ID; `context_id` denotes the working point | Catalog-qualified sample identity and assembly-member entity scope; a cooldown must not redefine physical identity |
| `ConfigContextMetadata`, `ContextRunConfigSource` | Exactly one sample revision, one working-point ID and a base configuration | Target/assembly revision plus explicit batch/setup applicability; independent scope validation before parameter composition |
| `CalibrationTargetRef` in `automation/calibrations.py` | Targets use sample/context plus local entity identity | Carry applicable target/batch/setup scope into requirement matching, freshness and publication; do not reuse old evidence solely by sample name |
| Candidate launch in `application/launch_config.py` | Requires one exact subject from the source run | Assembly-aware subject/evidence validation; distinguish copied estimates from current-condition evidence |
| `AuthorProject.prepare_plan` and procedure admission | Recipe pins scientific configuration and source; execution chooses collection/operator | Extend frozen request/child provenance when batch and target references exist; session defaults must not override retained recipe scope |

The dual-client journey checks independent samples/working points/operators and
collection numbering, frozen old preparation, rejected partial updates, explicit
overrides and unchanged shared active configuration. The actual IPython journey
checks reuse, source refresh and reopening. These tests do not qualify shared-device
arbitration across separate deployments or multi-chip calibration.

## Third implementation: declared batch applicability

The batch catalog and indexed run/batch association retain declared event identity.
Sample selectors and immutable bindings can carry a batch identity; physical sample
identity and collection numbering remain independent. An unspecified event is not
inferred from a name or directory. No prebaseline batch migration is provided.

Working points pin this binding. Session preparation checks its selected batch
against working points, candidates and saved plans. In-place workspace advance
cannot change scope; explicit new-workspace copies preserve value origins as
estimates. Server admission rejects mismatched context bindings and batch-scoped
candidate reuse with changed samples/batches. Calibration target keys, freshness
and procedure selectors carry batch identity, so an old or unscoped success cannot
satisfy a target declared for the new event. Cross-batch dependency evidence is
also rejected. Integrations must actually declare
that event; existing unscoped integrations are not silently reclassified.

Automated checks cover one chip across two batches, independent retained numbering,
copy versus in-place relabeling, direct candidate admission, scoped calibration
status, metadata rename/restart, and current-format recovery. See
[experimental batches](../../how-to/experimental-batches.md).

The earlier audit remains open for assembly/member identities, setup applicability,
explicit cross-scope dependency policy, shared-active-configuration composition,
multi-workspace source ownership and physical resource authority. GUI selection
was subsequently delivered in the fourth implementation.
This slice does not establish hardware qualification or an event's physical truth.

## Fourth implementation: workbench page selection

The existing experiment console now exposes page-local sample, batch, record
collection and operator choices through the same preview/admission request used
by Python. Working-point selection adopts its exact sample/batch. Switching
experiments or refreshing author source retains page selection; resetting inputs
retains it too. Separate tabs and fresh projects start independently.

Catalog browsing is paginated and offers explicit metadata creation. Saved recipes
keep their scientific scope, reject another selected batch, and use the page's
collection/operator for execution. Selection edits invalidate previews without
rewriting retained submission attempts. Configuration copies preserve batch
bindings and can explicitly choose a new event as an estimate.

A real-daemon browser journey exercises two pages with different samples/batches
and one collection, saved-plan rejection, acquisition and retained numbering.
This connects the already shipped single-sample contracts to the workbench; it
does not add multi-workspace source ownership, assembly/setup composition, shared
physical resource authority, or the final installation-level entry and Help flow.

## Fifth implementation: shared applicability contract and rebase boundary

`records/scientific_scope.py` defines target content within one owning catalog:
exact sample-revision members, member-qualified entity addresses, and declared
undirected interconnections. Member and connection ordering do not change the
content identity. A single-member target and an A+B target remain distinct; A/q0
and B/q0 do not collide. This is not a persisted target catalog, cross-catalog
identity, assembly execution, or a declaration that referenced local entities and
physical connections have been validated.

`ScientificApplicability` combines that target with an explicit declared/unscoped
batch variant and a setup-content hash. Unscoped is not a wildcard. Strict reuse
requires all three scopes to match. The current single-sample projection reads
current records without adding stored fields. This projection is not a prebaseline
reader or a cross-version hash-compatibility promise.

The first consumer is parameter-workspace rebase. Previously it checked the sample
and parameter declarations but could take values from a working point whose
routing, topology or instrument connections differed. Rebase now checks shared
applicability before merging; rejection leaves the original base and local edits
unchanged. It still requires the same working-point identity and parameter schema.
Explicit copying into another working point remains the path for starting estimates;
that operation does not assert calibration validity.

The setup-content projection excludes profile/system names, entity descriptive
metadata, role descriptions, parameter declarations and parameter values. It keeps
execution topology, primary entity, routing, instrument connections/drivers,
lifecycle policies and domain configuration. Logical IDs matter because existing
plans address them. Equality is conservative declared-configuration equality,
not physical-device identity, live state verification, or hardware qualification.

### Next coordinated implementation slices

The target catalog and unified authored selection described below have shipped.
The remaining priority is ownership and applicability, not another independent
selection model:

1. Deliver lightweight [apparatus history](apparatus-history.md) for observations,
   documents and linked runs. Recording a line must not require a fake sample or
   a complete live topology. Descriptive evidence does not qualify calibration.
2. Separate maintained executable setup from object-scoped parameter state,
   retaining one complete frozen execution snapshot. Audit and replace shared
   active-config scientific defaults while preserving authoritative inventory,
   runtime fencing and quarantine. Parameter publication checks the relevant
   working-point revision, not an unrelated object's publication.
3. Extend executable subject/applicability where needed, then carry it through
   working-point/candidate publication and calibration dependency matching. Scope
   must be retained at measurement/publication time; do not fabricate apparatus
   bindings from historical links or sample names. Explicit estimate adoption and
   qualified calibration remain different operations.
4. Qualify A/B independent calibration, a no-sample physical-line measurement and
   a chip calibration depending on a qualified line calibration. Assembly execution
   and the graphical target picker follow their explicit contracts. Neither requires
   a universal object graph or implicit room-temperature/low-temperature reuse.

Within the current format, preserve scientific objects and acquisition addresses.
During incompatible prebaseline redesigns, leave original files intact but update
current writers/readers together rather than add old-format codecs or migrations.
Long-term compatibility starts only with an explicitly designated future baseline.


## Sixth implementation: local target catalog

The current catalog persists target heads and immutable revisions. Python/HTTP create,
compare-and-swap revise, paginated list, latest/exact get and qualified resolve
reuse the existing `project_identity` as catalog identity. Registration validates
members against retained local sample revisions and connection endpoints against
their topology, atomically. Labels/audit data are retained per revision but excluded
from scientific target-content hashes. Foreign-catalog references are rejected;
restoring the existing data space preserves its identity and references.

Prebaseline target-store migrations are retired; new builds do not infer target
references for earlier data. See [target registration](../../how-to/register-measurement-targets.md).
This completes catalog registration only: launch selection, exact target freezing
at admission, working-point/calibration migration and assembly execution remain
pending. No new target field was appended to the existing launch/run JSON contracts.


The next execution slice is specified in [frozen target selection and admission](target-execution.md),
including single-member/subject projection, the new intent and retained-evidence
boundary, procedure-child propagation and concrete acceptance scenarios. That
contract is prospective; catalog registration alone still does not enable execution.


## Unified authored scientific selection (#641)

The coordinated execution contract is now implemented for single-member targets.
One selection and reviewed envelope replaces the old flat launch fields. Notebook
selection, preview, immutable plans, typed authored parent procedures and child runs
retain exact target/configuration evidence. The GUI preserves target plans; its
catalog picker remains #643. Generic saved multi-stage sample workflows may change
configuration while admission preserves their exact sample scope. Maintained setup,
calibration applicability and assembly execution remain open, as shown in the table.
