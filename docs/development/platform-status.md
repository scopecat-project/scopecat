# Platform status and remaining work

This index links current contracts and unresolved work. Issues describe remaining
conditions; PRs retain implementation and validation evidence. Release manifests
and consumer locks identify distributed versions. A source merge alone does not
establish an installed upgrade or human/device acceptance.

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
| Editing recovery | Application-owned Decision and parameter drafts, conflicts and explicit adoption | See [Decision drafts](architecture/decision-drafts.md), [parameter drafts](architecture/parameter-drafts.md) and [native acceptance](native-window-acceptance.md) for their distinct coverage. |
| Configuration sharing | Inert inspection, atomic editable derivations and installed continuation | [Data exchange](architecture/data-exchange.md); broader structured authoring remains #502. |
| Teaching | Same-application manual-peak practice and persistent Help parameters continuation | Other topic integration and actual editor/unfamiliar-user evidence remain #565/#616. |

## Remaining work owners

| Scope | Owner |
| --- | --- |
| Remaining reference/device/compiler retirement | [#773](https://github.com/scopecat-project/scopecat/issues/773), audit umbrella [#615](https://github.com/scopecat-project/scopecat/issues/615) |
| Broader Notebook/application learning journey | [#565](https://github.com/scopecat-project/scopecat/issues/565); first Help parameters continuation and material guard merged in #875/#877; remaining topics/consumers and native editor/human acceptance separate |
| First-use profiling and iteration cost | [#523](https://github.com/scopecat-project/scopecat/issues/523), [#520](https://github.com/scopecat-project/scopecat/issues/520) |
| Historical Windows startup/endpoint failures | [#465](https://github.com/scopecat-project/scopecat/issues/465), [#553](https://github.com/scopecat-project/scopecat/issues/553); do not infer a fix from later success |
| Finer observed dependency coverage | [#783](https://github.com/scopecat-project/scopecat/issues/783); unknown reads cannot prove physical independence |
| Other live domains and stateful analysis | [#561](https://github.com/scopecat-project/scopecat/issues/561) |
| Symbolic argument typing and target models | [#581](https://github.com/scopecat-project/scopecat/issues/581), [#612](https://github.com/scopecat-project/scopecat/issues/612) |
| Actual editor, unfamiliar user, physical qualification | [#616](https://github.com/scopecat-project/scopecat/issues/616); accepted native observations are not automatically repeated |
| Future data baseline and remaining structured authoring/sharing | [#574](https://github.com/scopecat-project/scopecat/issues/574), [#502](https://github.com/scopecat-project/scopecat/issues/502) |

## Evidence and distribution

- Desktop composition: [#829](https://github.com/scopecat-project/scopecat/pull/829),
  [#837](https://github.com/scopecat-project/scopecat/pull/837),
  [#844](https://github.com/scopecat-project/scopecat/pull/844) and
  [#845](https://github.com/scopecat-project/scopecat/pull/845).
- Configuration retirement, ordinary analysis, live groups, declared dependency
  comparison and bounded repair: #849–#853, linked from their contracts and issues.
- Entry audit and Help correction: [#675](https://github.com/scopecat-project/scopecat/issues/675)
  and [#859](https://github.com/scopecat-project/scopecat/pull/859).
- Configuration sharing: [#861](https://github.com/scopecat-project/scopecat/pull/861)
  and [#871](https://github.com/scopecat-project/scopecat/pull/871).
- Teaching source and material identity: #875/#877/#880; remaining coverage in #565.
- Native window/storage repair: [acceptance record](native-window-acceptance.md),
  including failed candidates and checks still pending. Source tests do not replace it.

Current product direction is in the [application contract](architecture/public-application.md).
Release procedures and artifact identities are in [public previews](public-preview.md).
Private consumer versions are recorded by that repository's manifest and lock.
The [previous status snapshot](https://github.com/scopecat-project/scopecat/blob/53a74eaae7d2195fa4430eda7d737506d13da9fd/docs/development/platform-status.md)
retains historical source/CI checkpoints without making them another current backlog.
