# Platform status and remaining work

Audited against the local implementation on 2026-09-23, after exact saved
parameter selection reached the sample workbench. This is the current work list;
earlier delivery notes are historical context, not additional pending work.
No new hardware or installed Windows qualification is claimed by this audit.

## Delivered boundaries

| Area | Implemented | Current boundary |
| --- | --- | --- |
| Parameters/setup | Independent immutable revisions, branches, session setup pins and exact context resolution | Some consumers still use combined configurations and optional global parameter defaults |
| Scientific context | Common `MeasurementContext`; subject separated from `TargetSetupBinding`; retained mappings checked at admission and for applicability | Common context covers saved parameter revisions without overrides, not candidates or unsaved inputs |
| Targets | Catalog definitions and a general pure topology mapping checker | Registered execution remains single-member, no target connections, identity mapping |
| Candidates | Retained proposals, independent verification, compatible candidate composition and fenced branch publication | No parameter flow between background task stages |
| Calibration | Declared checks, indexed history, exact-context applicability, immutable profiles and prerequisite-aware focused reports | Whole-input matching; no resolved parameter-read dependencies or selective cross-revision reuse |
| Automation | Durable fixed tasks, dependency-checked admission, sequential background advancement, controls and restart recovery | Calls and check contexts are fixed at task creation; no repair or stage output binding |
| Workbench | Task controls and drill-down; sample capability reports from retained runs, branches or exact revisions | Explicit bounded queries, not a continuously maintained health dashboard |
| Application | Ordinary workbench first use, installed adapters, registered author folders, notebook interpreter selection and stopped environment replacement | Native installers and physical authority shared across separate services remain separate work |
| Recovery | Current-format backup/restore and non-mutating rejection of unsupported formats | Schema 94 is not a supported persistent-data baseline |

See [configuration ownership](configuration-ownership.md),
[target execution](architecture/target-execution.md),
[automation tasks](architecture/automation-tasks.md) and
[the public application contract](architecture/public-application.md).

## Implementation order

### 1. Establish the parameter-flow contract

Build on existing candidate verification and branch publication, without a second
proposal/evidence store. Audit their maintained consumers without a global
parameter default before connecting them to tasks.

The next complete workflow captures an initial branch head, retains a first
stage's candidate, lets a later stage consume those exact values, verifies the
final combination, then publishes against the captured head. Intermediate
progress survives failure/restart without becoming the daily branch.
See [task parameter flow](architecture/task-parameter-flow.md) for acceptance and
the remaining design decisions. Fixed check tasks remain usable during this work.

### 2. Remove old configuration dependencies along that workflow

Track actual callers, not names containing `config`. Complete resolved snapshots
are valid execution evidence. Global parameter defaults and combined editing or
bootstrap APIs must not be prerequisites for independent parameter/setup users.
Replace fixtures with explicit owner initialization while preserving scientific,
conflict, resource and recovery assertions. Retire old entries after their
maintained consumers have moved.

### 3. Qualify a small simulated calibration workflow

Use several control entities with shared readout and coupling edges. Exercise
two parameter-producing stages, final verification, local drift, a failed resource,
unaffected progress, restart and a concurrent daily-branch edit. Show retained
partial results and missing coverage. This initially need not claim multi-member
target execution or automatic selective repair.

### 4. Extend addresses, applicability and execution deliberately

- Converge capability addresses with member-qualified target entities.
- Connect explicit target/setup maps to admission before enabling connected or
  multi-member targets. The pure mapping checker is not execution authority.
- Capture resolved parameter reads, query membership and capture completeness;
  supplement them with declared physical interactions before selective reuse.
- Add bounded check-first repair after dependencies can explain invalidation.

Setup still aggregates control topology, instrument registry, routing, domain
target and software scenario. One active setup remains deployment authority.
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
- **Other platform work:** live grouped analysis, symbolic argument typing,
  heterogeneous environments and installation qualification remain separate.
  Do not count delivered compute or native readers as missing.
- **Later contracts:** native install/uninstall, tray/login startup, LAN
  authorization, physical authority spanning services, selected-run exchange and
  a supported data baseline. Descriptive apparatus history does not authorize
  apparatus execution or transfer room-temperature calibration to low temperature.

Follow the [data policy](data-compatibility.md): retain historical files, add no
prebaseline readers or migrations, and keep current-format recovery tested.
Private Actions remain disabled. This work continues locally on the current branch
with coherent commits and targeted checks, without a PR or remote CI per slice.
