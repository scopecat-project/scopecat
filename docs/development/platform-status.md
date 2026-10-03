# Platform status and remaining work

Scientific audit baseline: 2026-09-24, including task finalization, six-target
qualification and independent author consumers. The scientific sections below
retain that audit; they are not an additional desktop implementation queue.

Desktop status is governed by the [application contract](architecture/public-application.md)
and [product decision](architecture/desktop-product.md). The manager, per-source
services and per-lesson environments have retired. PR #829 delivered ready-to-run
installation, #837 independent data and the bounded desktop journey, and #844
ordinary sources with independent execution/SDK environments. #845 completed their
composition and release; actual editor, unfamiliar-user and hardware observations
remain distinct. Track the software gate in #610/#616/#671.

## Delivered boundaries

| Area | Implemented | Current boundary |
| --- | --- | --- |
| Parameters/setup | Independent immutable revisions, branches, session setup pins and exact context resolution | Combined snapshots remain exact execution/provenance data; global defaults and combined writers are retired |
| Scientific context | Common `MeasurementContext` for saved revisions and retained candidates; subject separated from `TargetSetupBinding`; mappings checked at admission and for applicability | Overrides/unsaved inputs remain outside exact contexts; candidates do not inherit saved-revision applicability |
| Targets | Catalog definitions and a general pure topology mapping checker | Registered execution remains single-member, no target connections, identity mapping |
| Candidates | Retained proposals, sibling composition, sequential chains, independent verification and fenced branch publication; optional task finalization handoff | Final scientific policy and publication remain explicitly authored |
| Calibration | Declared checks, indexed history, immutable profiles, per-check applicability explanations and explicit whole-parameter dependency comparison | Cross-revision reuse requires identical complete execution/analysis/physical declarations; partial read capture does not establish independence |
| Automation | Durable tasks, explicit candidate output binding, dependency-checked admission, sequential advancement, controls and recovery | Candidate edges require passing source checks; no repair-on-failure or general adaptive flow |
| Workbench | Task controls and drill-down; sample capability reports from retained runs, branches or exact revisions | Explicit bounded queries, not a continuously maintained health dashboard |
| Application | Ready-to-run public desktop, independent data and author environments, shared devices and same-application practice | Composition/release qualification is separate from unfamiliar-user and hardware acceptance |
| Recovery | Current-format backup/restore and non-mutating rejection of unsupported formats | Schema 107 is not a supported persistent-data baseline |

See [configuration ownership](configuration-ownership.md),
[target execution](architecture/target-execution.md),
[automation tasks](architecture/automation-tasks.md) and
[the public application contract](architecture/public-application.md).

## Current development batches

The next budget is five public PRs, distinct from the completed desktop four:

1. Retire global configuration and combined editing authority, including consumers,
   storage and misleading reference fixtures. This batch removes activation/history,
   workspace heads/rebind and intermediate binding saves. Preserve exact evidence
   reads and current-format recovery.
2. Converge the ordinary edit/prepare/acquire/analyze/reopen author loop, analysis
   invocation semantics, stable/trial parameter contracts and actionable feedback.
   Use measurements for performance changes; do not rebuild delivered typed reads.
3. Reuse ordinary analysis for completed live groups, bounded background work and
   reconnect, extending the delivered offline API.
4. Explain calibration applicability with qualified dependency coverage; unknown
   dependencies cannot justify cross-revision reuse.
5. Add bounded check-first repair with explicit budgets, retained partial progress
   and final verification/publication. Start with exact contexts if necessary.

Batch 5 now uses fresh checks, one declared fit and exact candidate verification
per stage. Tasks retain ordered attempts, total fit budgets and a restart-stable
admission deadline. Failed execution is not retried as scientific repair. All
stages must pass before the existing finalizer may verify the combined candidate
and publish against its captured branch. Historical evidence reuse, iterative
repair loops and continuous scheduling are outside this first bounded policy.

Prioritize 1–2, then 3. Batches 4–5 are subsequent scientific automation, not new
prerequisites for supervised hardware acceptance. Private consumers/docs move with
relevant public capabilities; native/manual checks repeat only for changed contracts.
Actual editor, unfamiliar-user and physical observations remain separate in #616.

Batch 1 is merged in [#849](https://github.com/scopecat-project/scopecat/pull/849).
Batch 2 ([#850](https://github.com/scopecat-project/scopecat/pull/850)) unifies
managed ordinary/context analysis arguments, defaults and
publication, and lets both use completed-run groups. The shared two-source journey
also checks separate stable/trial parameter branches, incompatible units, preserved
driver identity and original-source analysis. Existing preparation stage reporting,
preview diagnostics, admission receipts and typed reopen paths remain authoritative;
this batch does not add a second progress or result model. Live grouping is batch 3.

Batch 3 ([#851](https://github.com/scopecat-project/scopecat/pull/851)) implements
fixed Cartesian group completion from committed logical-point
projections, immutable acquisition-reference slices, bounded author-worker execution,
durable event cursors and restart reads. It reuses ordinary/context analysis and
existing publications. The run view reports progress and refreshes results; stopping
analysis leaves acquisition running. Cleanup waits for active analysis, and exported
captures retain independently readable slices. The reference scenarios cover power
spectroscopy with instrument-returned frequency arrays and repeated synthetic T1.
Paired, adaptive and point-cloud live completion, stateful reducers and feedback
gating remain separate capabilities. Schema 108 remains a prebaseline development
format. These software scenarios do not replace physical acceptance.

The sections below retain the earlier scientific audit and its evidence inventory;
current configuration authority is governed by the linked ownership contract.

## Implementation order

The following scientific slices remain valid within the application convergence
gate above. They are not prerequisites for delaying direct entry or retiring old
service topology; complete the software journey before scheduling physical trials.

### 1. Establish the parameter-flow contract

Build on existing candidate verification and branch publication, without a second
proposal/evidence store. Audit their maintained consumers without a global
parameter default before connecting them to tasks.

The supported procedure workflow captures an initial branch head, retains a first
stage's candidate, lets a later stage consume those exact values, verifies the
final combination, then publishes against the captured head. Intermediate
progress survives failure/restart without becoming the daily branch. Tasks can
now hand all adopted stage evidence to one explicit final procedure. The
`task-calibration` sandbox connects that handoff to final measurement and branch
publication, including scientific rejection and concurrent edits. The six-target
software scenario below extends this qualification beyond two targets.
See [task parameter flow](architecture/task-parameter-flow.md) for acceptance and
the remaining design decisions. Fixed check tasks remain usable during this work.

### 2. Remove old configuration dependencies along that workflow

Track actual callers, not names containing `config`. Complete resolved snapshots
are valid execution evidence. Global parameter defaults and combined editing or
bootstrap APIs must not be prerequisites for independent parameter/setup users.
Replace fixtures with explicit owner initialization while preserving scientific,
conflict, resource and recovery assertions. Retire old entries after their
maintained consumers have moved.

The experiment-plan/comparison and registered-target journeys now share
equipment-only startup and select independent parameters/setup, including branch
edits after preview. Everyday-author acquisition also no longer needs default
configuration startup; its explicit low-level snapshots remain valid inputs. Session
isolation and record numbering also use independent parameters/setup. Generic
unknown-column and structural-history coverage lives in the tutorial fixture;
the duplicate working-point structure test is retired. The
batch/context journeys now also select independent inputs: parameter reuse does
not imply calibration validity, while plans and candidates retain exact scope.
The old unknown-parameter context test is folded into tutorial coverage. Managed
notebook recovery now uses parameter branches, including stale edits and recovery
in a fresh Python process. Exploration/reanalysis also runs without global defaults.
The separate copied-author suite likewise uses equipment-only startup and explicit
inputs across source refresh, request editing, HTTP launch and live preview recovery.
Analysis recovery shares independent startup and preserves its no-reacquisition
and provenance checks.
The launcher suite now selects independent inputs too, retaining foreign-endpoint
isolation and rejecting new work when executable setup authority changes.
The standard reference bootstrap now initializes equipment only; its temporary
equipment-only replacement and manifest rewrites are retired, including acceptance
capture and snapshot recovery. The combined editing/default-selection writers and working-point heads have
since retired. Retained exact snapshot/context reads remain evidence consumers. Their presence does not imply that new author workflows require those owners.
The retained device gallery now uses equipment-only startup and explicit fixture
parameter revisions, while keeping its physical-behavior assertions. Use the
[fixture ownership inventory](reference-fixtures.md) to retain each useful behavior
before retiring its obsolete entry point.

The transitional `config diff/apply/export` CLI commands are retired. They only
compared, published or exported the daemon-wide combined default. Source checking
remains read-only; author edits use parameter branches, equipment changes use setup
operations, and scientific retention uses snapshot backup/restore. The old command
mock tests are removed with their implementations; current-format parsing,
bootstrap validation and backup/restore coverage remain. That CLI retirement preceded the subsequent removal of low-level global activation
and combined working-point writers. Immutable registry evidence is retained.

Author guides now consistently start from independent parameter branches. The
batch guide reflects the tested distinction between reusing values and reusing
evidence, including saved-plan/candidate scope. The combined-configuration guide
is explicitly legacy documentation; its continued existence is not a recommended
author entry point.

### 3. Small simulated calibration workflow qualified

A [six-target daemon journey](array-maintenance-qualification.md) now exercises
three logical readout groups, neighboring coupling, six candidate-producing fits,
aggregate verification and fenced publication. Four cases cover success, local
drift, a synthetic readout failure with unaffected progress, and a concurrent
daily-branch edit. Each restarts after retained partial progress. It uses declared
software computation; real resource failure/concurrency, multi-member target
execution and automatic selective repair are not established by this scenario.

### 4. Extend addresses, applicability and execution deliberately

- Converge capability addresses with member-qualified target entities.
- Connect explicit target/setup maps to admission before enabling connected or
  multi-member targets. The pure mapping checker is not execution authority.
- Declarative recipe queries now retain resolved key/value cells, indirect key
  dependencies and explicit limited coverage through the execution ledger.
  Public comparison detects changed inputs and lost/ambiguous row membership.
  Scalar evaluation and constant specialization now share opt-in context-local
  capture hooks, with checked-context propagation, effective-point isolation and
  explicit incomplete coverage. Domain program/compiler input materialization
  now captures per-point reads automatically; the public preparation builder
  retains them in invocation intents without private adapter assembly. Frontend
  static evaluation and constant specialization now retain whole-program,
  base-configuration reads alongside point-local input reads, including across
  repeated specialization and ledger reopening. Host compute/state/invocation and
  resource entity-selection inputs now persist under the executor lease, retaining
  point scope through state coalescing and segment scope across restart. Successful
  completion state retains a separate base-configuration record. Analysis config
  access retains the complete run snapshot as a validated publication input.
  These observations retain their base/point/segment scope. Explicit reviewed
  dependency contracts now permit whole-parameter comparison with full table
  membership, whole-catalog schema and exact setup/subject/scenario fences.
  Missing execution, analysis or physical declarations remain unknown; arbitrary
  Python reads are not automatically qualified. Row-selective inferred reuse is
  outside this bounded contract.
  Track this boundary in [#783](https://github.com/scopecat-project/scopecat/issues/783).
- Add [bounded check-first repair](https://github.com/scopecat-project/scopecat/issues/784)
  after dependencies can explain invalidation.

Setup still aggregates control topology, instrument registry, routing, domain
target and software scenario. Registered device heads and explicit setup resolutions own execution authority.
Independent execution must follow resource overlap, not source folders or target
names. Avoid an exhaustive physical inventory without a concrete consumer.

### 5. Add continuous maintenance and scale

Build sample health refresh on the existing evidence service. Keep capability
policy dependencies separate from task ordering. Add resource/scientific grouping,
fairness, budgets and duplicate-request coalescing with measured workloads.
Concurrent jobs and one batched acquisition are different contracts.

## Separate qualification and deferred work

- **Human trials:** run the complete calibration authoring/recovery journey on
  Windows after the parameter-flow slice, rather than repeating tutorial setup.
  Historical reliability reports and timings do not establish current behavior;
  see [test feedback](test-feedback.md) and [author performance](author-performance.md).
- **Fixtures:** use the [ownership map](reference-fixtures.md) and
  [gallery retirement](reference-gallery-retirement.md). Preserve demonstrated
  routing, compiler, resource and recovery coverage; moving files is not cleanup.
- **Other platform work:** open-domain live completion, stateful reducers, symbolic argument typing,
  heterogeneous environments and installation qualification remain separate.
  Do not count delivered compute or native readers as missing.
- **Later contracts:** native install/uninstall, tray/login startup, LAN
  authorization, physical authority spanning services, selected-run exchange and
  a supported data baseline. Descriptive apparatus history does not authorize
  apparatus execution or transfer room-temperature calibration to low temperature.

Follow the [data policy](data-compatibility.md): retain historical files, add no
prebaseline readers or migrations, and keep current-format recovery tested.
Private Actions remain disabled. The accumulated calibration branch now closes as
one public integration PR; it does not include analysis-author redesign, online
group scheduling, automatic selective repair or installation qualification.
See [the integration scope and validation record](calibration-branch-closeout.md).
