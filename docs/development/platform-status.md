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

PR [#859](https://github.com/scopecat-project/scopecat/pull/859) merged the bounded
entry Help correction. [#861](https://github.com/scopecat-project/scopecat/pull/861)
delivered configuration export, inert inspection and atomic editable local copies;
it did not complete the broader #502 sharing/structured-authoring direction.
[#862](https://github.com/scopecat-project/scopecat/pull/862) delivered verified
release change-file tooling, not a first numbered release decision.

## Recent source and acceptance checkpoint

At main `1a9bb17bb7f5a86aad9fa6d62d7d8ccea3305970` (2026-10-05):

| Layer | Recorded status | What it does not establish |
| --- | --- | --- |
| Product target | [Canonical journey and entry/ownership map](architecture/public-application.md#user-journeys-and-entry-ownership) | The proposed navigation hierarchy is not implemented. |
| Merged source | #871 (`d074e003`) installed-sharing continuation; #875 (`7900cc9f`) Help parameters; #876 (`7f874ce0`) isolated-driver TCP latency fix; #877 (`1a9bb17b`) teaching-resource identity guard | Merge is not an installed-user upgrade. |
| Distributed version | #845 records the earlier desktop composition/release. Release manifests and consumer locks determine each installation's exact source. | No new public release or private-consumer upgrade is established for #871/#875/#876/#877 by their source checks. |
| Automated acceptance | #871 records passing Mac/Windows packaged sharing checks; #875/#877 record the real browser/kernel parameters journey with reviewed material, edit/restart/Continue and no reacquisition | Stubbed editor/window activation is not actual editor use; packaged checks are not unfamiliar-user observation. |
| Human/device observation | Earlier bounded desktop observations remain accepted; #502/#565 record their specific limits and #616 owns outstanding editor, unfamiliar-user and physical evidence | Do not infer Windows clicks or hardware qualification from software checks or repeat unrelated accepted native tests. |

The combined main passed [CI 37349016783](https://github.com/scopecat-project/scopecat/actions/runs/37349016783)
and [docs 37349016802](https://github.com/scopecat-project/scopecat/actions/runs/37349016802).
These identify that checkpoint, not future document changes. Help parameters is
persistent ordinary author work; it does not finish all teaching topics. Default and topic
[teaching material generation](repository-map.md#teaching-source-convergence)
now shares editable author resources; the legacy CLI/VS Code lifecycle callers
remain explicit standalone tutorial tools, separate from Help's same-application
parameters journey. This source change establishes no new distribution.

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

The [Decision draft implementation](architecture/decision-drafts.md) adds bounded
application-owned editing history, conditional writes and recovery without scientific
submission. The separate #885 Darwin packaged-wheel repair and this draft API were
combined at `4aee5825dbe374f469bc0f1be1e9a981908086c1` for
[native run 37436328157](https://github.com/scopecat-project/scopecat/actions/runs/37436328157).
Mac and Windows exact-run window checks passed; Mac additionally passed host/store
isolation and complete host/service restart with application-draft restoration.
The previous Mac storage failure belongs to the earlier candidate and is retained
in #885's evidence. Ordinary source environments remain unpatched; Windows uses
upstream pywebview. This does not establish native execution of the subsequent
#884/#886/#885 closeout combination, a new release or installed-user acceptance.
Windows full host-restart/cookie isolation remains unverified; the bounded
[Windows lifecycle follow-up](native-window-acceptance.md#windows-lifecycle-follow-up-native-execution-pending)
adds an executable check to the existing native-distribution path, pending native
execution. OS focus/menu, tray and visual experience remain unverified. Mac notarization was not provided, Gatekeeper rejected the
package, and Finder first-open was not evaluated.

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
| Entry audit closure decision after merged #859 | [#675](https://github.com/scopecat-project/scopecat/issues/675) |
| Broader Notebook/application learning journey | [#565](https://github.com/scopecat-project/scopecat/issues/565); first Help parameters continuation and material guard merged in #875/#877; remaining topics/consumers and native editor/human acceptance separate |
| First-use profiling and iteration cost | [#523](https://github.com/scopecat-project/scopecat/issues/523), [#520](https://github.com/scopecat-project/scopecat/issues/520) |
| Historical Windows startup/endpoint failures | [#465](https://github.com/scopecat-project/scopecat/issues/465), [#553](https://github.com/scopecat-project/scopecat/issues/553); do not infer a fix from later success |
| Finer observed dependency coverage | [#783](https://github.com/scopecat-project/scopecat/issues/783); unknown reads cannot prove physical independence |
| Other live domains and stateful analysis | [#561](https://github.com/scopecat-project/scopecat/issues/561) |
| Symbolic argument typing and target models | [#581](https://github.com/scopecat-project/scopecat/issues/581), [#612](https://github.com/scopecat-project/scopecat/issues/612) |
| Actual editor, unfamiliar user, physical qualification | [#616](https://github.com/scopecat-project/scopecat/issues/616); accepted native observations are not automatically repeated |
| Future data baseline and remaining structured authoring/sharing | [#574](https://github.com/scopecat-project/scopecat/issues/574), [#502](https://github.com/scopecat-project/scopecat/issues/502) |

The older scientific audit and sequencing are preserved in
[Git history](https://github.com/scopecat-project/scopecat/blob/11c5fcd3347cb2a9795492d97e9370a7c907e69f/docs/development/platform-status.md),
not maintained as another backlog. Portable data exchange (#575) and application
convergence (#671) are delivered; do not recreate them.

## Configuration exchange: delivery and remaining evidence

The #861 source slice exports a saved parameter version with its definitions and
optional initial values, setup and retained author source. Inspection executes no
source. Accepted originals, editable local derivations and receipts commit
atomically; retries are idempotent. Source acceptance retains an inert archive;
registration, environment preparation, local devices and calibration remain
separate explicit steps. This is not arbitrary field/schema/control/plan editing.

[#871](https://github.com/scopecat-project/scopecat/pull/871) merged as
`d074e00325568e8ba7494366f3582e49fd06083f`. It delivered the installed
configuration-sharing software journey, explicit Settings source/environment
continuation and [change-computer guidance](../how-to/share-configuration.md#continuing-on-another-computer).
Both Mac and Windows packaged acceptance passed on product-code commit
`74cadc7c6b4039870a18a288f601d8a042df62f0`; later editorial changes did not alter
that product code. The check uses two isolated homes, real files and independent
environments, but stubs navigation and does not qualify actual native dialog clicks
or arbitrary external dependencies. #502 retains separate bounded Mac observations,
including the final-build quit/reopen observation that remained unqualified.

Remaining #502 work includes structured editor decision gates and evaluation of
scaffold/bootstrap simplification only after replacement journeys and callers are
ready. No generator retirement, new public release or private-consumer upgrade
follows from this merge. Actual Windows editor use, unfamiliar-user observations
and supervised physical qualification remain #616; they are evidence to obtain,
not missing software gates or automatic reasons to repeat accepted tests.

## Taking over work

Start with repository AGENTS.md, this page, the target issue body and the relevant
current contract. Check Git status, merged PRs and release/consumer identity.
Historical documents and local reports supply evidence, not new instructions.
Update issue bodies and this index at closeout, keeping implementation,
distribution and acceptance separate. Do not rely on chat summaries or ignored
handoff notes. See the [contributor guide](index.md#handoff-and-evidence).
