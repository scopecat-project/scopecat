# Dependency security review

Renovate owns dependency fix PRs, including fixes sourced from GitHub Dependabot
alerts. Ordinary updates keep the three-day minimum release age. Automerge is off
by default; only patch/minor updates to the following explicit dependencies opt in:

- npm: `react`, `react-dom`, `@tanstack/react-query`, `lucide-react`, `@base-ui/react`,
  `@types/node`, `oxlint`, `oxlint-tsgolint`, `oxfmt`, `vitest`, `jsdom`,
  `@testing-library/jest-dom`, and `@testing-library/react`.
- pep621 / PyPI: `ruff`, retaining its existing `update-lockfile` range strategy.

React and React DOM update together. The type-aware Oxlint configuration uses its
explicit `oxlint-tsgolint` peer, so that same-tool package is included and grouped
with Oxlint. This does not enroll other Oxc packages, Vite, TypeScript, Node, or
React types. Eligible updates first clear inherited groups, then use only these
two explicit coordinated groups, avoiding mixed eligible/ineligible groups from
presets. Global `separateMinorPatch` remains the default `false`; the existing
`pyenv` split remains `true`.

This policy accepts internal-app iteration, including `ruff` and `oxfmt` 0.x minor
updates, only when existing lint, formatting and tests pass unchanged. Do not
modify source or rules automatically to make an update pass, or add fix scripts.
Major updates, unlisted dependencies (including measurement/data core and device
SDKs/drivers), Python/runtime/native packaging and release tooling, GitHub Actions,
pins and lockfile maintenance remain manual. Broad patch and stable non-major
automerge presets stay absent.

Security PRs remain manual even for an allowlisted package. Renovate turns
`vulnerabilityAlerts` into a forced override applied during config merging, so
`automerge: false` overrides ordinary package rules. Security fixes retain their
cooldown/scheduling exceptions and request the lowest fixed version. A faster
proposal is not permission to merge or proof of compatibility.

All merges use the existing protected `CI gate` and `ALLGREEN` squash merge queue.
Do not bypass the queue or weaken tests to merge dependency changes. The required
`CI gate` aggregates Python core tests, quality checks, Windows/macOS platform
smokes, UI checks and documentation. It does not prove every device, native release
or author journey works, nor does it test Renovate policy resolution. The advisory
security audit below is not a required vulnerability-free gate. This policy adds
no required status check and changes no repository settings.

## Official lockfile audit

`Dependency security` uses Google's official OSV-Scanner reusable workflows,
v2.7.0 pinned to release commit `b1d267aea5211fa648d9d442fa64b90dbdcbdd36`.
The annotated release tag was resolved through GitHub and both workflow files
were checked at that immutable ref. Upstream owns the scanner, reporter, Docker
images and result-upload steps; this repository does not maintain a scanner
wrapper, package-exclusion generator or custom report/diff implementation.

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
No current finding came from those eight packages. They remain visible and require
source-aware triage if matched; there are no speculative package overrides or
advisory suppressions. Matching alone does not prove runtime exploitability.

## Why keep a PR audit alongside Dependabot alerts?

Dependabot alerts rely on the dependency graph's actual inventory, not simply on
support for a package manager's version-update feature. GitHub's
[2026-10-20 announcement](https://github.blog/changelog/2026-10-20-dependabot-now-supports-npm-12-pnpm-11-and-12-and-bun-1-4/)
explicitly says pnpm two-document lockfiles are not yet detected by the graph and
will not generate alerts/security updates. This repository uses that exact format.
After the administrator enabled the graph/alerts, the SBOM generated at
`2026-10-23T08:01:34Z` was compared with the actual locked package/version pairs:

| Registry inventory | Exact versions in GitHub SBOM | Exact versions in OSV |
| --- | ---: | ---: |
| Python uv registry packages | 178 / 178 | 178 / 178 |
| pnpm manager document | 15 / 15 | 15 / 15 |
| pnpm UI document | 0 / 287 | 287 / 287 |

Python main-branch lock coverage is therefore verified, despite `uv.lock` not
being listed in the current documentation table. The initial SBOM 404 and empty
GraphQL manifest list were observations before setup, not a parser diagnosis.
After setup GraphQL lists 21 manifests but reports zero dependency counts for all
of them; those counts contradict the SBOM and must not be used as coverage proof.

Keep Dependabot alerts for the default branch. This workflow checks the versions
proposed in a PR before merge, and additionally fills the demonstrated pnpm UI
gap. It does not create a second fix-PR owner or recurring scan. Reassess the pnpm
check when GitHub supports both documents and an SBOM set comparison confirms it;
retain Python scanning only while its pre-merge evidence remains useful. Newly
disclosed issues on an unchanged unsupported lockfile require a manual audit
until graph coverage is fixed (or a separately reviewed monitoring policy).

## Verified initial inventory and triage

Scanned baseline `ea73ec4ca6a10673b39ab625ed4bef4d7f67503c` on 2026-10-23.
Independent parsing of the locks and comparison with OSV's `--all-packages` output
verified every package/version pair, not only direct manifest dependencies:

| Input | Lock inventory | OSV extraction |
| --- | --- | --- |
| Python | 178 PyPI records, 8 local versioned packages, 1 virtual root | 186 extracted: 178 registry + 8 local identities; virtual root omitted |
| pnpm package-manager document | 15 package/version pairs | All 15 |
| pnpm UI document | 287 package/version pairs | All 287 |

The Python registry is `https://pypi.org/simple`; pnpm resolutions use integrity
hashes with no private tarball/registry overrides. The eight local versioned Python
identities are additional to the 178 registry records, not additional third-party
packages. The current official workflow leaves all of them visible for triage.
The initial baseline's 302 npm identities and 41 advisory groups are recorded below;
use a fresh manual full scan when reviewing newer lock revisions.

| Locked package | Baseline advisory groups | Initial assessment / next review |
| --- | ---: | --- |
| certifi 2026.7.22 | 1 (two alias records) | Transitive via httpx/httpcore/requests. Compromised CA advisory; review a targeted lock fix to 2026.10.19. |
| uv 0.13.1 | 1 (two alias records) | Used by scopecat-app and CI's version-file. Bundled CA advisory; review 0.13.4 with the existing build/runtime qualification. |
| Pillow 12.3.0 | 39 | Desktop extra plus matplotlib/pystray paths. Image-parser advisories require reachability triage; all matched ranges indicate 13.0.0, so assess major-version compatibility separately. |
| npm packages | 0 | No known advisory returned for the 302 locked versions; this is not a permanent assurance. |

Representative Pillow matches are
[GHSA-22hg-r758-rrw9](https://osv.dev/vulnerability/GHSA-22hg-r758-rrw9)
(`[7.2.0, 13.0.0)`),
[GHSA-v9pv-5gc8-w2m6](https://osv.dev/vulnerability/GHSA-v9pv-5gc8-w2m6)
(`[11.0.0, 13.0.0)`), and
[GHSA-78g4-xqwv-q3g8](https://osv.dev/vulnerability/GHSA-78g4-xqwv-q3g8)
(`[11.1.0, 13.0.0)`, bundled libopenjp2 in wheels). All 39 affected records identify
PyPI/pillow and explicitly list 12.3.0; there is no observed local-package/name
collision for these hits. They are advisory matches pending platform, wheel and
input-path applicability triage, not confirmed exploitable product vulnerabilities.

There are 43 raw advisory records / 41 alias-deduplicated groups across three
Python packages. certifi and uv each report maximum severity 9.1; Pillow reaches
9.8. The three candidate fix versions exist on official PyPI and were not yanked
when checked. No dependency was upgraded in this configuration change.

Prioritize the two CA trust findings and desktop image handling for separate,
reviewed fixes. Before introducing a required security gate, triage existing
findings with explicit owners and expiry dates; then consider blocking only new
high/critical findings introduced by dependency-changing PRs. Do not add blanket
ignores or force unrelated PRs to resolve this whole baseline.

## Repository settings and remaining verification

The administrator has enabled Dependency graph and Dependabot alerts and left
Dependabot security updates off. The now-readable SBOM confirms graph population.
Earlier, the alerts endpoint explicitly returned **Dependabot alerts are disabled**;
after setup it returns HTTP 403 **Resource not accessible by integration** instead.
The current audit integration cannot list alerts; that does not establish whether
the separately installed Renovate app can read them. The OSV workflow uses the
approved security-events write permission solely for its code-scanning reports.

The administrator's latest settings screenshots show both auto-triage presets
(low-impact development-dependency dismissal and malware-alert dismissal) disabled.
Baseline review should still include previously dismissed/auto-dismissed alerts,
not only open alerts. The latest permissions screenshot explicitly includes
**Read access to Dependabot alerts**, but the application identity is cropped:
if it is the Renovate app page, no additional permission is needed. Confirm the
app identity rather than requesting broader permissions.

Permission evidence is not successful ingestion evidence. Check Renovate's next
run for alert retrieval and affected-lockfile mapping. Keep **Dependabot security
updates** off so Renovate remains the single automated fix-PR owner. The separate
audit integration's 403 does not disprove Renovate's permission. No repository setting or application permission was changed; only the workflow
report permission was approved and added.

Automatic dependency submission is not needed to obtain the verified Python
inventory. Do not enable another submission workflow or grant write access merely
to work around the pnpm gap in this batch. Renovate's experimental direct-only
OSV alerts are not used as the audit or as a second fix-PR source.

References: [OSV supported lockfiles](https://google.github.io/osv-scanner/supported-languages-and-lockfiles/),
[OSV usage](https://google.github.io/osv-scanner/usage/),
[official GitHub workflows](https://google.github.io/osv-scanner/github-action/),
[Renovate vulnerability alerts](https://docs.renovatebot.com/configuration-options/#vulnerabilityalerts).
