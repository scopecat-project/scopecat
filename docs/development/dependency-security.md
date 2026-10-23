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

The `Dependency security` workflow runs on relevant PRs/main updates, daily, and
on manual dispatch. It downloads OSV-Scanner **v2.7.0** from the official Google
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

JSON inventory/findings are retained for 14 days with a job summary. Findings
(exit 1) are advisory during baseline triage; operational errors fail this
separate job. The summary rejects absent/empty lockfile inventories. A green
report job means the scan completed, not that there are zero vulnerabilities.
The existing universal CI gate remains unchanged. OSV coverage is advisory
matching, not malware detection or proof of runtime exploitability; OS/native
libraries, unlocked build backends and downloaded runtimes need separate review.

## Verified initial inventory and triage

Scanned baseline `ea73ec4ca6a10673b39ab625ed4bef4d7f67503c` on 2026-10-23.
Independent parsing of the locks and comparison with OSV's `--all-packages` output
verified every package/version pair, not only direct manifest dependencies:

| Input | Lock inventory | OSV extraction |
| --- | --- | --- |
| Python | 178 PyPI records, 8 local versioned packages, 1 virtual root | 186; virtual root omitted |
| pnpm package-manager document | 15 package/version pairs | All 15 |
| pnpm UI document | 287 package/version pairs | All 287 |

The Python registry is `https://pypi.org/simple`; pnpm resolutions use integrity
hashes with no private tarball/registry overrides. OSV treats the eight local
versioned Python packages as PyPI identities: triage any future hits against their
actual local source, rather than replacing them with public registry packages.

| Locked package | Baseline advisory groups | Initial assessment / next review |
| --- | ---: | --- |
| certifi 2026.7.22 | 1 (two alias records) | Transitive via httpx/httpcore/requests. Compromised CA advisory; review a targeted lock fix to 2026.10.19. |
| uv 0.13.1 | 1 (two alias records) | Used by scopecat-app and CI's version-file. Bundled CA advisory; review 0.13.4 with the existing build/runtime qualification. |
| Pillow 12.3.0 | 39 | Desktop extra plus matplotlib/pystray paths. Image-parser advisories require reachability triage; most fixes indicate 13.0.0, so assess major-version compatibility separately. |
| npm packages | 0 | No known advisory returned for the 302 locked versions; this is not a permanent assurance. |

There are 43 raw advisory records / 41 alias-deduplicated groups across three
Python packages. certifi and uv each report maximum severity 9.1; Pillow reaches
9.8. The three candidate fix versions exist on official PyPI and were not yanked
when checked. No dependency was upgraded in this configuration change.

Prioritize the two CA trust findings and desktop image handling for separate,
reviewed fixes. Before introducing a required security gate, triage existing
findings with explicit owners and expiry dates; then consider blocking only new
high/critical findings introduced by dependency-changing PRs. Do not add blanket
ignores or force unrelated PRs to resolve this whole baseline.

## Administrator setup still needed

The alerts API explicitly returned **Dependabot alerts are disabled**. The
integration cannot read the administrative vulnerability-alert/security-update
settings (HTTP 403), and repository metadata omitted `security_and_analysis`.
Therefore dependency graph state, Dependabot security-update state and Renovate's
installed permissions have not been confirmed.

A repository administrator should inspect **Settings → Advanced Security**:

1. Ensure Dependency graph is enabled and enable Dependabot alerts.
2. Verify the installed Renovate app already has read access to Dependabot alerts;
   grant only that permission if missing, through the app's normal approval UI.
3. Keep **Dependabot security updates** disabled (disable it if currently enabled)
   so Renovate is the single automated fix-PR owner. Alerts remain enabled.
4. After the next Renovate run, verify alert ingestion and the affected lockfile
   mapping in its logs. Do not assume all transitive alerts can produce a fix PR:
   manager/lockfile mapping and available resolutions still constrain automation.

No settings or permissions were changed by this PR. The OSV workflow works
independently of these settings. Renovate's experimental direct-dependency OSV
alerts are not used as the audit or as a second PR source.

References: [OSV supported lockfiles](https://google.github.io/osv-scanner/supported-languages-and-lockfiles/),
[OSV usage](https://google.github.io/osv-scanner/usage/),
[Renovate vulnerability alerts](https://docs.renovatebot.com/configuration-options/#vulnerabilityalerts).
