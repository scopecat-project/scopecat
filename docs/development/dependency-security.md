# Dependency security review

Renovate owns dependency fix PRs, including fixes sourced from GitHub Dependabot
alerts. See [renovate.json](../../renovate.json) for the automerge allowlist and
update exceptions; keep the executable configuration as the source of truth.

Eligible updates clear inherited groups so presets cannot combine them with
unapproved dependencies. Oxlint includes its `oxlint-tsgolint` peer because the UI
uses type-aware linting. Internal-tool updates may include 0.x minors, but existing
checks must pass without automated source or lint/format-rule fixes.

Security fixes skip ordinary cooldown/scheduling limits but still require review:
Renovate applies `vulnerabilityAlerts.automerge: false` as a forced override of
package rules. A faster proposal is not proof of compatibility.

Ordinary Python patch updates wait fourteen days; uv retains the global three-day
minimum release age. Python minor upgrades remain separate reviews. Waiting does
not prove supply compatibility: Linux, macOS and Windows CI query the locked uv
catalog for the exact ordinary CPython version using the same selector as portable
builds, without downloading an interpreter. Native distribution acceptance still
validates actual installation, relocation and application behavior.

An urgent security update may receive an explicitly reviewed cooldown exception
when the affected component is actually used here. Record the affected environments
and the evidence; a security label alone never authorizes merging. Supply checks,
build validation and merge protections still apply. Updating the application
interpreter does not repair pip or certificate stores in existing author virtual
environments; assess those environments separately without silently changing their
recorded identities.

Use the protected `CI gate` and `ALLGREEN` squash merge queue. Do not bypass the
queue or weaken checks to merge an update; assess device and release compatibility
separately when those paths are affected.

## Official lockfile audit

The [workflow](../../.github/workflows/dependency-security.yml) owns the pinned
upstream revision, scan arguments, triggers, permissions and artifact settings.
It uses Google's official OSV-Scanner reusable workflows; upstream owns the
scanner, reporter, images and result upload. No local scanner wrapper or custom
report/diff implementation is maintained.

Both paths scan exactly `uv.lock` and `apps/scopecat-ui/pnpm-lock.yaml` with
`--no-resolve --all-packages`. OSV reads all locked direct/transitive, development,
optional and platform-specific versions, including both pnpm YAML documents.
No project dependencies are installed, imported or built. No call analysis or
recursive repository scan is requested. Only these public lockfiles are eligible;
review the data boundary before adding private package identities or repositories.

- On relevant PRs targeting main, the upstream PR workflow scans the target branch
  and then the proposed merge ref, and reports newly introduced findings.
- Manual dispatch runs the upstream full scan on the selected ref's exact SHA,
  so existing findings remain inspectable. There is no scheduled or main-push scan.

Both use `fail-on-vuln: false`: findings are advisory, not part of `CI gate`.
Upstream rejects failed scans with missing output rather than treating them as
clean comparisons.
A passing differential result does not mean the baseline is vulnerability-free.
The official PR workflow retains old/new JSON and SARIF artifacts for five days;
the full workflow retains SARIF. Review run output and Security → Code scanning.

The workflow uses `contents: read`, `actions: read` and the explicitly approved
`security-events: write` for SARIF reports. No application permissions change.
SARIF upload is enabled for same-repository PRs and manual baselines. Fork and
Dependabot PRs disable upload while retaining upstream artifacts/annotations;
normal GitHub approval/token restrictions still apply. No `pull_request_target`,
extra secrets or fork-token write setting is introduced. Fork execution has not
been claimed as tested merely because a same-repository PR passes.

This advisory workflow does not run on `merge_group`. At this pinned release the
upstream PR workflow checks out `$GITHUB_BASE_REF`, which GitHub only supplies for
PR events, and exposes no base-ref override. Do not assume its documented
merge-group trigger produces the same comparison. Existing required CI continues
running on merge groups unchanged. Adding a required security gate needs separate
policy and upstream merge-group qualification.

OSV can extract local Python workspace packages as same-named PyPI identities.
They remain visible and require
source-aware triage if matched; there are no speculative package overrides or
advisory suppressions. Matching alone does not prove runtime exploitability.

## Why keep a PR audit alongside Dependabot alerts?

Dependabot alerts rely on the dependency graph's actual inventory, not simply on
support for a package manager's version-update feature. GitHub's
[2026-10-20 announcement](https://github.blog/changelog/2026-10-20-dependabot-now-supports-npm-12-pnpm-11-and-12-and-bun-1-4/)
explicitly says pnpm two-document lockfiles are not yet detected by the graph and
will not generate alerts/security updates. This repository uses that exact format.
The [initial coverage comparison](https://github.com/scopecat-project/scopecat/blob/5c8cdc30d812fa4484d8fd1af3a5b5f30aca133e/docs/development/dependency-security.md#why-keep-a-pr-audit-alongside-dependabot-alerts)
compared exact locked package/version pairs with the SBOM and OSV output. It
found the pnpm UI document missing from the graph. Those dated counts and setup
observations describe that snapshot, not the current lockfile inventory.

Keep Dependabot alerts for the default branch. This workflow checks the versions
proposed in a PR before merge, and additionally fills the demonstrated pnpm UI
gap. It does not create a second fix-PR owner or recurring scan. Reassess the pnpm
check when GitHub supports both documents and an SBOM set comparison confirms it;
retain Python scanning only while its pre-merge evidence remains useful. Newly
disclosed issues on an unchanged unsupported lockfile require a manual audit
until graph coverage is fixed (or a separately reviewed monitoring policy).

## Reviewing findings

Use the current lock revision and a fresh full scan when reviewing existing
findings. The [initial inventory and triage](https://github.com/scopecat-project/scopecat/blob/5c8cdc30d812fa4484d8fd1af3a5b5f30aca133e/docs/development/dependency-security.md#verified-initial-inventory-and-triage)
retain the original dated advisory matches and candidate fixes; they are not a
current vulnerability list or evidence that those fixes remain outstanding.
Match registry identity, version, platform, wheel and reachable input paths before
claiming exploitability. Do not add blanket ignores or force unrelated PRs to
resolve a historical baseline.

Check actual Renovate runs for alert retrieval and affected-lockfile mapping;
permission screenshots alone do not establish successful ingestion. Include
previously dismissed alerts in baseline review. Keep Renovate as the single
automated fix-PR owner. A different integration's access failure does not establish
Renovate's access. Reassess graph coverage by comparing package/version sets, not
manifest counts or a successful request alone.

### 有告警却没有 Renovate PR

`uv.lock` 中仅作为传递依赖出现的包，可能落在 Renovate 的上游告警到更新映射缺口中；
先核对实际运行日志、依赖路径与锁定制品，不把“没有 PR”解释成没有风险或权限不足。
确认修复版本后，用 `uv lock --upgrade-package <name>==<version>` 定向刷新，
复核 diff 只包含预期包，验证锁一致性、实际 wheel 准备和离线交付，再走正常审查与合并队列。
不为此新增直接依赖、扩大普通更新范围或改变 uv 单包 `update-lockfile` 策略；
修复后继续由现有 Renovate 配置负责常规更新。API 无法读取告警时，只报告已验证的代码修复，
不声称告警已关闭。

证书依赖须分别检查：notebook 的独立 `certifi`、`delivery-build` 的
`pip._vendor.certifi`，以及 PBS 自带 pip 的内置副本。独立 certifi 更新不会替换 pip 的副本。
交付准备通过隔离的锁定 build pip 执行联网 `pip wheel`；其默认 TLS 信任同时使用系统证书和
内置 certifi（见 [pip HTTPS 说明](https://pip.pypa.io/en/stable/topics/https-certificates/)），
也要核对显式 CA 配置。交付安装则由 uv 使用 `--offline --no-index --require-hashes`
读取已验证的本地 wheels，不调用 PBS pip 联网安装。版本匹配本身不证明可利用性，
仓库锁更新也不代表既有作者环境或系统证书库已更新。

Before introducing a required security gate, triage findings with owners and
expiry dates and qualify merge-group behavior. Scanner success is not device or
release compatibility evidence.

References: [OSV supported lockfiles](https://google.github.io/osv-scanner/supported-languages-and-lockfiles/),
[OSV usage](https://google.github.io/osv-scanner/usage/),
[official GitHub workflows](https://google.github.io/osv-scanner/github-action/),
[Renovate vulnerability alerts](https://docs.renovatebot.com/configuration-options/#vulnerabilityalerts).
