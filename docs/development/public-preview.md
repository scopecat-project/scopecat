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

在 `.changeset/` 中记录用户可见的功能、修复或不兼容变化，默认使用中文；SDK、Notebook、命令和配置名保留自然写法。可运行 `knope document-change`，也可直接编写文件。简单变化用一句完整的话即可，生成后为列表项：

```markdown
---
default: patch
---

重新打开应用后保留选中的运行结果。
```

复杂条目以一个完整的 `# 短标题` 开头，空一行后说明用户影响和必要操作；不要默认写成长篇、多标题文章：

```markdown
---
default: minor
---

# 重开 Notebook 后继续查看保存的结果

重新连接同一数据空间后，可用保存的运行 ID 查看原结果，无需重新采集。
请保留原始 ID，并选择 Scopecat 显示的作者解释器。
```

仓库固定的 Knope 0.23.0 会将标题和正文整理为发布说明，默认沿用其生成层级，不手工重复添加版本或分类标题。标题应短而完整，保持在一个物理行；普通段落的硬换行可能使首行成为不完整标题。上述样例经该版本及当前 oxfmt 实测：`#` 标题保持单行，详细条目生成标题与正文，简单句子生成列表项。写法也可参考 [Knope 的 change 示例](https://knope.tech/reference/concepts/change/)。

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

### First numbered delivery readiness

The user entry is [Download and install](../getting-started/install.md). Before
presenting a first numbered installer there, review the following with the candidate:

- Choose the public version and native build number explicitly; `0.0.0` / build `0`
  is still only bootstrap state. Existing release tooling is not evidence of an
  actual numbered release or a successful prepare/publish workflow run.
- State the candidate's tested OS versions and architectures using
  [native acceptance](native-window-acceptance.md) evidence. The first experimental
  delivery may limit its claim to those systems; it need not establish a full
  minimum-version matrix. Packaging defaults alone do not establish support.
- Qualify Windows WebView2 presence and the missing-Runtime path on a clean target.
  The installer copies the application and shortcut; it neither bundles nor installs
  the Evergreen Runtime. Agree on the runtime prerequisite with the delivery owner
  before promising first-launch or offline installation on a new computer.
- Verify the exact candidate's short journey: download, install, prepare a tutorial
  and author folder, simulate once, quit, reopen and continue. Include failed
  preparation/startup recovery and preserve source and receipts. Link target-OS
  evidence; backend tests cannot replace native first-open or editor observations.
- Record signing/notarization limitations, external editor/network requirements and
  data compatibility in the release notes. Only replace the historical-download
  notice after a real release exists and its assets and identity have been checked.

Choose the version separately and describe the actual signing limitations; trusted
signing is not an added prerequisite for this experimental delivery. This checklist
does not run Prepare, Publish or native acceptance.

### Prepare, review, then publish

发布前，以最后实际分发版本与最终候选的差异为准，合并重复条目，移除已撤回或被后续实现取代的描述，并复核不兼容变化和必要操作。changefile 是可编辑的发布输入，不是逐条保存历史 PR 的日志。首版只描述最终用户能力、限制和升级注意事项，不要求用户经过未发布的中间迁移。

Use the workflow-pinned Knope version to inspect the real generated notes with
`knope --dry-run prepare-release`; it reports proposed changes without writing
files or publishing. Check complete headings, body structure, duplication and
migration guidance. Review the generated changelog again in the release PR;
automatic version selection does not replace this editorial pass.

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
   the normal CI process. No tag or release is created by that merge.
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

### Daily source desktop

Install Python 3.14+, uv, Node.js and pnpm, then run from the public checkout:

```sh
uv run --locked python -m lab_tools.dev
```

This opens the existing native pywebview desktop with Vite HMR and the real
Python backend. On macOS, the command first prepares a cached **Scopecat Dev**
`.app` with the existing application icon and a stable worktree-specific bundle
identity. It requires Xcode command line tools (`xcode-select --install`). The
small host links the current Python in-process and preserves its environment,
stdin, signals and exit status; it does not copy Python, dependencies, source or
GUI into the `.app`. The printed host path can be used to identify it in native
tools. It is a local development host, not a distributable installation.

Windows similarly prepares a cached **Scopecat Dev.exe**, with a stable
worktree-specific AppUserModelID distinct from the installed application. Install
Visual Studio C++ Build Tools with the Windows SDK (Desktop development with C++);
the entry discovers the installed compiler or uses an active Native Tools prompt.
The window belongs to this executable rather than Python. The host loads the
current Python in-process, keeps the editable virtual environment and uses its
ordinary Python executable for backend subprocesses. It inherits the development
terminal for restart commands and Ctrl+C; the waiting launcher forwards its exit
status. No application package or copied Python environment is required.

Host caches change only for checkout/interpreter/ABI/libpython, icon or host
construction inputs. Ordinary GUI and application-source edits do not rebuild
the host. After changing the host's own inputs, safely quit the existing owner
before relaunching. `--browser` bypasses the native host for internal inspection.

First startup prepares locked, verified wheels and an independent
Python payload for ordinary Settings author-environment preparation. Later starts
reuse content-addressed resources. UI edits use HMR and do not rebuild wheels or
an app. Source Python/package edits print a restart reminder: enter `r` to restart
an idle backend, or `w` to wait until it becomes idle. Restart refuses active or
unknown work; it never force-stops it. Changes to native host Python need a full
safe quit and relaunch. Existing author `.venv` directories and retained execution
environments are never silently updated to follow the checkout.

This resource preparation still uses the locked delivery builder and a separate
Python payload for author environments; source mode does not remove that cost.
The cache identity includes package source and dependency/build inputs, so a
changed identity can require fresh resource preparation on startup or restart.
UI-only edits do not change that payload. It does not create a DMG or Windows
installer, and developers need not package one for each source validation.
Reserve the separate packaging path for installed-delivery acceptance; a working
source desktop does not establish that a downloaded installer works.

The default home is a stable hash of the canonical worktree path under the user's
`Scopecat-Development` data directory, outside the checkout and separate from the
installed application's data. Each worktree has its own owner and dynamic ports;
a duplicate invocation reuses its owner and activates the native windows.
The application starts blank: no example author folder is created or registered.
Use Settings to create or register ordinary author source.

```sh
# A separate retained blank trial (the path must be new or owned by this checkout):
uv run --locked python -m lab_tools.dev --home /absolute/path/to/dev-trial
# Allocate a new blank home in the OS temporary directory; do not auto-delete it:
uv run --locked python -m lab_tools.dev --temporary
```

Startup reports source, home, UI/backend URLs and log locations. `q` followed by
Enter or Ctrl-C requests exit after work becomes idle; `c` cancels a pending
terminal wait. Native Quit uses the ordinary application work-aware flow. Choosing to keep work
in the background hides the window and keeps the launcher alive. If Vite exits,
the terminal reports the failure and log path; backend work continues. Use `q` or
Ctrl-C for a safe exit before relaunching.
No history is reset or automatically deleted. Retain useful temporary-home data
outside the OS temp directory before the OS cleans it.

`--browser` is an internal inspection option using the same Vite UI and real
backend. Open its printed URL manually. Native bridge functions are unavailable;
there is no alternative browser product or substitute UI. Vite receives an owned
endpoint file and follows backend port changes after safe restart.

For packaged acceptance, use [Build a local desktop](local-desktop-trial.md).
That independent entry qualifies packaging and the installed lifecycle; it is not
the everyday source editing loop. Native HMR, bridge, multi-window and Quit behavior
must also be observed on the target OS; cloud backend tests do not establish it.
