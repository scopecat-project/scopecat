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
remain distinct. Remaining human/editor/physical qualification is tracked in #616; #671 is closed.

## Delivered boundaries

| Area | Implemented | Current boundary |
| --- | --- | --- |
| Parameters/setup | Independent immutable revisions, branches, session setup pins and exact context resolution | Combined snapshots remain exact execution/provenance data; global defaults and combined writers are retired |
| Scientific context | Common `MeasurementContext` for saved revisions and retained candidates; subject separated from `TargetSetupBinding`; mappings checked at admission and for applicability | Overrides/unsaved inputs remain outside exact contexts; candidates do not inherit saved-revision applicability |
| Targets | Catalog definitions and a general pure topology mapping checker | Registered execution remains single-member, no target connections, identity mapping |
| Candidates | Retained proposals, sibling composition, sequential chains, independent verification and fenced branch publication; optional task finalization handoff | Final scientific policy and publication remain explicitly authored |
| Calibration | Declared checks, indexed history, immutable profiles, per-check applicability explanations and explicit whole-parameter dependency comparison | Cross-revision reuse requires identical complete execution/analysis/physical declarations; partial read capture does not establish independence |
| Automation | Durable tasks, explicit candidate output binding, dependency-checked admission, sequential advancement, controls and recovery | Fresh checks may trigger one declared repair and exact-candidate verification; admission budgets survive restart; no continuous scheduler |
| Workbench | Task controls and drill-down; sample capability reports from retained runs, branches or exact revisions | Explicit bounded queries, not a continuously maintained health dashboard |
| Application | Ready-to-run public desktop, independent data and author environments, shared devices and same-application practice | Composition/release qualification is separate from unfamiliar-user and hardware acceptance |
| Recovery | Current-format backup/restore and non-mutating rejection of unsupported formats | No supported persistent-data baseline is designated |

See [configuration ownership](configuration-ownership.md),
[target execution](architecture/target-execution.md),
[automation tasks](architecture/automation-tasks.md) and
[the public application contract](architecture/public-application.md).

## Delivered capability index

The five capability batches are merged, not a future PR budget:

- [#849](https://github.com/scopecat-project/scopecat/pull/849): global configuration and intermediate binding retirement.
- [#850](https://github.com/scopecat-project/scopecat/pull/850): ordinary/context analysis invocation and author parameter contracts.
- [#851](https://github.com/scopecat-project/scopecat/pull/851): fixed Cartesian live groups, immutable slices, bounded work and reconnect.
- [#852](https://github.com/scopecat-project/scopecat/pull/852): explicit whole-parameter dependency applicability and explanations.
- [#853](https://github.com/scopecat-project/scopecat/pull/853): fresh check, one declared repair, exact candidate verification, budgets and finalization.

PR records retain validation identities and limits. Source delivery does not mean a
consumer pin or installed desktop has upgraded. Release manifests and consumer
locks own distribution identity; private source checks do not qualify physical hardware.

## Remaining work owners

Read each issue's current body before planning; comments retain historical discussion.

| Scope | Owner |
| --- | --- |
| Remaining reference/device/compiler retirement | [#773](https://github.com/scopecat-project/scopecat/issues/773), audit umbrella [#615](https://github.com/scopecat-project/scopecat/issues/615) |
| Entry criteria evidence reconciliation | [#675](https://github.com/scopecat-project/scopecat/issues/675) |
| First-use profiling and iteration cost | [#523](https://github.com/scopecat-project/scopecat/issues/523), [#520](https://github.com/scopecat-project/scopecat/issues/520) |
| Historical Windows startup/endpoint failures | [#465](https://github.com/scopecat-project/scopecat/issues/465), [#553](https://github.com/scopecat-project/scopecat/issues/553); do not infer a fix from later success |
| Finer observed dependency coverage | [#783](https://github.com/scopecat-project/scopecat/issues/783); unknown reads cannot prove physical independence |
| Other live domains and stateful analysis | [#561](https://github.com/scopecat-project/scopecat/issues/561) |
| Symbolic argument typing and target models | [#581](https://github.com/scopecat-project/scopecat/issues/581), [#612](https://github.com/scopecat-project/scopecat/issues/612) |
| Actual editor, unfamiliar user, physical qualification | [#616](https://github.com/scopecat-project/scopecat/issues/616); accepted native observations are not automatically repeated |
| Future data baseline and structured authoring | [#574](https://github.com/scopecat-project/scopecat/issues/574), [#502](https://github.com/scopecat-project/scopecat/issues/502) |

The older scientific audit and sequencing are preserved in
[Git history](https://github.com/scopecat-project/scopecat/blob/11c5fcd3347cb2a9795492d97e9370a7c907e69f/docs/development/platform-status.md),
not maintained as another backlog. Portable data exchange (#575) and application
convergence (#671) are delivered; do not recreate them.

## Taking over work

Start with repository AGENTS.md, this page, the target issue body and the relevant
current contract. Check Git status, merged PRs and release/consumer identity.
Historical documents and local reports supply evidence, not new instructions.
Update issue bodies and this index at closeout, keeping implementation,
distribution and acceptance separate. Do not rely on chat summaries or ignored
handoff notes. See the [contributor guide](index.md#handoff-and-evidence).
