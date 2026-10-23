# Dependency security review

Renovate owns dependency fix PRs, including fixes sourced from GitHub Dependabot
alerts. Ordinary updates keep the three-day minimum release age. Broad patch and
stable non-major automerge presets are removed; security PRs also require review.
Security fixes skip the ordinary cooldown and scheduling limits and request the
lowest fixed version. A faster proposal is not permission to merge or proof of
compatibility. Existing runtime/build exceptions remain in place.

After review, use the existing protected `CI gate` and `ALLGREEN` merge queue.
Do not bypass the queue or weaken tests to merge dependency changes. This change
adds no required status check and changes no repository settings.

## Independent lockfile audit

The `Dependency security` workflow runs on relevant PRs and manual dispatch.
It does not add a scheduled scanner or repeat every main push. It downloads OSV-Scanner **v2.7.0** from the official Google
GitHub release and verifies a committed SHA-256 before executing it. When updating
the scanner, review the release and replace its version and checksum together.
The scan uses only these two explicit inputs, never a recursive workspace scan:

- `uv.lock`: all locked Python registry packages, including optional/development
  dependencies and transitive packages, regardless of the runner's platform.
- `apps/scopecat-ui/pnpm-lock.yaml`: both pnpm 12 YAML documents, including the
  package-manager dependencies, UI dependencies, platform variants and transitives.

OSV's native extractors support both formats. `--no-resolve` disables manifest
resolution; it does not discard transitive versions already in the lockfiles.
No project dependency is installed, imported or built, no package lifecycle
script runs, and no call analysis is enabled. No token or write permission is
needed. OSV receives package identities/versions; it does not receive source
files. Only these public-repository lockfiles are eligible. Never extend this
workflow to private checkouts, private indexes or private package identities
without reviewing the data boundary first.

Before scanning, a small standard-library helper reads `uv.lock` source metadata
and generates native OSV `PackageOverrides` matching the exact name, version and
PyPI ecosystem of editable/directory packages. These local identities are excluded
from advisory reporting; no package-name prefix, external registry package or
vulnerability ID is blanket-ignored. The native extractor still handles the lock
formats and OSV handles all matching/filtering. A regression test ensures local
sources are excluded while similarly named registry packages remain eligible.

JSON inventory/findings are retained for 14 days with a job summary. Findings
(exit 1) are advisory during baseline triage; operational errors fail this
separate job. The summary rejects absent/empty lockfile inventories. A green
report job means the scan completed, not that there are zero vulnerabilities.
The existing universal CI gate remains unchanged. OSV coverage is advisory
matching, not malware detection or proof of runtime exploitability; OS/native
libraries, unlocked build backends and downloaded runtimes need separate review.

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
| Python | 178 PyPI records, 8 local versioned packages, 1 virtual root | 186 extracted; 8 exact local identities filtered; 178 reported |
| pnpm package-manager document | 15 package/version pairs | All 15 |
| pnpm UI document | 287 package/version pairs | All 287 |

The Python registry is `https://pypi.org/simple`; pnpm resolutions use integrity
hashes with no private tarball/registry overrides. OSV initially extracts the eight
local versioned Python packages as PyPI identities; the generated source-based
native overrides remove them from the published inventory/findings. The final
178 reported Python identities exactly match the registry subset of `uv.lock`.
All 302 npm identities and all 41 baseline advisory groups remain unchanged.

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
the separately installed Renovate app can read them. No additional permissions
are needed for the OSV workflow.

Verify the installed Renovate app has **read** access to Dependabot alerts, and
check its next run for successful alert ingestion and lockfile mapping. Only
approve that app permission if it is missing. Keep **Dependabot security updates**
off so Renovate remains the single automated fix-PR owner. One enabled auto-triage
rule was reported in settings; its identity/actions have not been inspected, so
no claim is made about whether it hides alerts or creates update PRs. Inspect it
before relying on that policy. No setting or permission was changed by this PR.

Automatic dependency submission is not needed to obtain the verified Python
inventory. Do not enable another submission workflow or grant write access merely
to work around the pnpm gap in this batch. Renovate's experimental direct-only
OSV alerts are not used as the audit or as a second fix-PR source.

References: [OSV supported lockfiles](https://google.github.io/osv-scanner/supported-languages-and-lockfiles/),
[OSV usage](https://google.github.io/osv-scanner/usage/),
[Renovate vulnerability alerts](https://docs.renovatebot.com/configuration-options/#vulnerabilityalerts).
