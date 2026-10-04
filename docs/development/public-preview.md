# Public previews and source development

Public publishes the desktop application and framework Python artifacts. A
laboratory supplies ordinary source or extension wheels, its dependency lock and
site settings; it does not rebuild the desktop. Consumers need no public checkout,
Git submodule, Node.js or pnpm. Application and author environments update separately.

## Numbered releases

Everyday merges run CI and produce evidence; they do not publish a release.
The public application has one user-facing version in `release.toml`, independent
of the internal Python and UI package versions. `0.0.0` / build `0` is an
unreleased bootstrap marker and cannot be published. The first release version
will be chosen separately after the tooling is merged. Preparing a release uses
Knope's suggested change-file bump by default; an explicit version override is
available when the intended release needs one.

Add a small user-facing Markdown change file under `.changeset/` for a feature,
fix or breaking change. Run `knope document-change`, or write:

```markdown
---
default: patch
---

Explain what the user can now do or what was corrected.
```

Use `minor` for a feature and `major` for a breaking change. Internal-only work
need not add a change file. Commit messages have no required convention:
`[changes].ignore_conventional_commits = true` prevents Knope from reading them.
Knope's default 0.x semantics map ordinary feature/fix changes to patch and
breaking changes to minor. Review the suggested version and notes in the release
PR; a suggested bump does not itself authorize publication.

The prepare workflow installs the pinned `KNOPE_VERSION` through the official
`knope-dev/action`, pinned to a full commit SHA. Knope updates only the public
version and `CHANGELOG.md`. Its
[versioned files](https://knope.tech/reference/config-file/packages/) can synchronize
several representations of one version; they are deliberately not used to force
all internal packages to share a version. The
[PrepareRelease step](https://knope.tech/reference/config-file/steps/prepare-release/)
processes change files locally and does not publish or commit by itself.
Renovate tracks the official action's pinned SHA/version and the CLI's annotated
`KNOPE_VERSION` separately. The existing `githubActionsVersions` preset recognizes
the CLI's `knope/v...` release tags. Updates to either require review rather than
automerge; exercise preparation and native PR/release dry-runs without publishing
before accepting an upgrade. All workflow references to the CLI must stay aligned.

Knope retains change files during prereleases so the eventual stable release can
include the full set; it removes them when preparing the stable version.

### Prepare, review, then publish

1. Commit user-visible change files with ordinary work. From a clean checkout,
   install the `KNOPE_VERSION` recorded in the release workflows and run
   `uv run --locked python scripts/release.py prepare --build-number NUMBER`.
   Add `VERSION` after `prepare` to override the suggested version.
   This edits/stages version, build number, changelog and consumed change files.
   It never pushes, tags or publishes. Inspect the diff before committing.
2. Alternatively dispatch **Prepare release** on main with a strictly increasing
   native build number and, optionally, a version override. It prepares a dedicated branch,
   explicitly runs CI (including docs) in the same workflow, then opens a normal
   release PR using Knope's native `CreatePullRequest` and changelog template.
   The PR is ready for review; creating or merging it does not publish a release.
   The default token cannot be assumed to trigger another workflow.
   Failed validation leaves a reviewable branch without a PR; inspect and resolve
   it before retrying with a fresh branch identity. This automation needs the
   repository's existing permission to create PRs; it does not create credentials
   or change repository permissions. If PR creation is disabled, use the local
   preparation path and the normal maintainer PR workflow.
3. Review the exact version, build number and user-visible notes; merge through
   the normal CI/self-review process. No tag or release is created by that merge.
4. Run **Full acceptance → full** on the integrated commit, then dispatch
   **Publish release** with its full SHA. It verifies
   main ancestry and successful full acceptance for that exact commit, runs CI
   once in the same workflow (including the docs build), builds framework and
   native artifacts from that commit, then assembles and verifies the entire
   release. A prior CI run or Pages deployment is not an additional prerequisite.
   Only the final job can write a release. After checks reject an existing tag or
   release, Knope's native `Release` step creates the tag and release using its
   version, changelog, prerelease handling and configured assets. Knope uploads
   assets through a draft and publishes after successful uploads.
   If publishing fails after draft creation, stop and inspect that draft; never
   overwrite assets or reuse the version automatically. There is no automatic
   repair or post-upload download verification: the integrity guarantee is the
   complete local asset verification before handing those files to Knope.

Each candidate, including each alpha/beta/RC, uses a new public version and a
strictly increasing build number (1–9999; the shared single-field Mac build limit). Versions support `X.Y.Z`,
`X.Y.Z-alpha.N`, `X.Y.Z-beta.N` and `X.Y.Z-rc.N` with positive N. Public metadata
suffixes are not used; full commit and artifact hashes carry provenance.
Application tags use `v<version>`. Version history is selected with `v[0-9]*`
then strictly validated; future independently released packages may use
`<pkg>-v<version>`, but no second release line is implemented. Historical
`preview-<SHA>` and `desktop-<SHA>` tags/releases remain unchanged.

### Artifact identity and consumer selection

A numbered GitHub release `vVERSION` retains the framework wheels, UI archive,
Mac DMG, Windows installer, per-platform bundle/qualification manifests,
`preview.json` and `release.json`. The latter binds public version, build number,
full commit and every asset's SHA-256. Missing or unexpected files, wrong commits,
versions, platforms or hashes stop assembly/publication. Python wheel METADATA
is checked against the package inventory, not just its filename.

Internal wheel versions retain each package's base version and add a unique
`.dev<TIMESTAMP>+scopecat.<PUBLIC-PEP440>.g<SHA12>` identity. Public `alpha.1`,
`beta.1` and `rc.1` map to PEP 440 `a1`, `b1`, `rc1`; these wheel versions are
exact consumer pins, not independently published PyPI stable versions. The UI
build uses a SemVer prerelease derived from its own base plus the public version
and commit. Mac short version is the public numeric triplet; its build is the
explicit build number. Windows file version is the numeric triplet plus build.
The full public prerelease identity remains in the manifests.

The private consumer can select `--release VERSION` and optionally require
`--commit FULL_SHA`; it verifies release/preview identity and hashes before
updating wheel URLs and the dependency lock. Existing SHA preview selection
remains for retained historical artifacts. Choosing a new release and accepting
it on laboratory hardware are separate actions; no consumer pin changes merely
because a release exists.

For local builds and offline verification:

```sh
uv run --locked python scripts/build_preview.py build/preview --ref FULL_SHA --release
uv run --locked python scripts/release_assets.py assemble build/release --commit FULL_SHA
uv run --locked python scripts/release_assets.py verify build/release --commit FULL_SHA
```

Assembly requires both qualified native platforms, their manifests and all public
artifacts in one flat directory; the publish workflow shows the exact layout.
Tests use synthetic files to exercise rejection paths without network writes;
that is not evidence of a built or published native release.

**Full acceptance → native-distribution** retains qualified native Actions
artifacts for 14 days. These diagnostics are not durable releases. The ordinary
non-release archive build remains available without `--release`, with commit-based
development stamps. Never replace assets of any historical or numbered release.

### Existing framework-only preview delivery

**Full acceptance → public-preview** remains an explicit, durable framework-only
publishing path. After the exact commit passes CI, dispatch that profile to build
and publish its wheels, UI and `preview.json` as `preview-<full SHA>`. It does not
require native installers and is not automatically run on merge. Existing SHA
releases and pins stay readable and immutable; new SHA previews remain available.
A consumer can continue selecting `tools/select_public.py FULL_SHA` in private.

The numbered release workflow is an additional recommended path when delivering
the complete application and framework together. Private `select_public.py
--release VERSION` resolves that path; both modes preserve the same pin schema,
exact wheel URLs and lock hashes. Private `dev.py`, public `lab_tools.preview`,
and delivery `--preview` read the selected URL/hash without assuming a tag format.
`build_preview.py` and delivery `--public-artifacts` share build/validation code;
there is no second package versioning platform.

Whether framework-only delivery should also acquire numeric versions, or whether
framework and native release schedules should be unified, remains a future policy
decision. Introducing Knope does not remove the existing delivery capability or
add native prerequisites to a framework-only preview.

### Qualification boundaries

The native app contains its own Python and dependencies. Author dependencies and
vendor SDK/firmware are separate deliverables and must be qualified on the target
platform for offline use. See [installation and maintenance](../how-to/maintain-application.md),
[Mac first open](../how-to/mac-preview.md), and
[packaging qualification](architecture/desktop-packaging.md).
Windows remains unsigned and Mac ad-hoc signed, without trusted signing or
notarization. The application is experimental and is not intended for production
use. A plain numeric version does not change that status. An alpha/beta/RC suffix
marks a version as a prerelease; Knope uses that version identity for the GitHub
prerelease flag. Production suitability is a separate documented claim.

No persistent-data compatibility baseline is designated. A release number is
not a data schema version or a promise to read prior development stores. Follow
the [data compatibility policy](data-compatibility.md); current-format recovery
and rejection of unsupported formats remain distinct from future compatibility.

## Run without installation

From the public root:

Install Python 3.14+, uv, Node.js and pnpm first. The launcher installs the locked
frontend dependencies on explicit development startup.

```sh
uv run --group delivery python -m lab_tools.dev --source .
```

This starts a backend and Vite in the foreground, prints URLs and never opens a
browser. Ctrl-C stops both. Disposable data and generated example source stay in
`.scopecat-dev` between runs; checkout cleanup removes them. Use `--home` with an
explicit directory outside the checkout for work you intend to retain, and put
maintained author code in Git. It does not create a release, desktop entry or installation
selection. Use `--workspace` to register laboratory author source. Registration
does not select or activate its driver factory. Use **Update from source** in
**Devices and drivers** when you want to use that implementation; an unavailable
vendor environment does not prevent starting the development application.
Device connection remains explicit. Source registration bindings created by the launcher are
removed after successful shutdown; an existing binding is retained.

Consumers can run against the pinned preview GUI with `--preview`, or explicitly
overlay a public checkout's editable packages for framework work. Overlay all
public packages together and preserve the consumer lock. A checkout may live
anywhere; a sibling directory has no special meaning. Framework changes require
restart; author source refresh and idle driver source updates use the normal APIs.
