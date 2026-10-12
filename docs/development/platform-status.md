# Platform capability boundaries

本页说明已实现能力及其边界。具体任务和验收条件见 GitHub issues；实现与验证证据
保留在对应 PR。发布清单和消费者锁标识实际分发版本，源码合并不等于安装升级或真人/设备验收。

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
| Recovery | Current-format backup/restore and non-mutating rejection of unsupported formats | 尚未指定持久数据支持基线，见 [#574](https://github.com/scopecat-project/scopecat/issues/574) |
| Editing recovery | Application-owned Decision, parameter and experiment drafts, conflicts and explicit adoption; exact original-submission recovery | See [experiment recovery](architecture/experiment-drafts.md), [Decision drafts](architecture/decision-drafts.md), [parameter drafts](architecture/parameter-drafts.md) and [native acceptance](native-window-acceptance.md) for their distinct coverage. |
| Configuration sharing | Inert inspection, atomic editable derivations and installed continuation | [Data exchange](architecture/data-exchange.md); 结构化作者方向见 [#502](https://github.com/scopecat-project/scopecat/issues/502)。 |
| Teaching | Same-application manual-peak practice and persistent Help continuation for the supplied Notebook topics | 普通作者入口与学习路线已交付；学习者观察见 [#565](https://github.com/scopecat-project/scopecat/issues/565)，实际编辑器及用户验收见 [#616](https://github.com/scopecat-project/scopecat/issues/616)。 |

## Evidence and distribution

版本以发布清单和消费者锁为准；源码合并、安装态软件检查、原生观察和真人/物理
验收是不同证据。当前操作与报告入口见[原生验收](native-window-acceptance.md)、
[测试反馈](test-feedback.md)和[发布流程](public-preview.md)。

具体任务见 [GitHub issues](https://github.com/scopecat-project/scopecat/issues)；
验收入口为 [#616](https://github.com/scopecat-project/scopecat/issues/616)，
首次使用性能为 [#523](https://github.com/scopecat-project/scopecat/issues/523)，
历史 Windows 故障分别见 [#465](https://github.com/scopecat-project/scopecat/issues/465) 与
[#553](https://github.com/scopecat-project/scopecat/issues/553)。
已关闭的 [#520](https://github.com/scopecat-project/scopecat/issues/520) 保留历史测量和
未决证据；关闭状态本身不证明问题已修复或持续达到五分钟 CI 目标。

Current product direction is in the [application contract](architecture/public-application.md).
Release procedures and artifact identities are in [public previews](public-preview.md).
Private consumer versions are recorded by that repository's manifest and lock.
The [previous status snapshot](https://github.com/scopecat-project/scopecat/blob/53a74eaae7d2195fa4430eda7d737506d13da9fd/docs/development/platform-status.md)
retains historical source/CI checkpoints without making them another current backlog.
