# Application installation layout

Installed software, application data, disposable build caches and editable user
code have separate owners. `InstallationPaths.current_user()` selects per-user
locations; `InstallationPaths.isolated(home)` places application-owned validation
files inside one explicit directory. Native browser-store isolation additionally
depends on the packaged backend; an isolated home alone does not prove it. See
[native acceptance safety](native-window-acceptance.md#safety-and-failure-handling)
and the [home/host boundary](architecture/draft-recovery.md#home-host-and-privacy-boundaries).
Foreground source development continues to use `scripts.dev` and does not create an installed application.

## Development artifacts and retention

Repository-local outputs have distinct lifetimes:

| Directory | Contents and retention |
| --- | --- |
| `/build/` | Rebuildable intermediate files and delivery staging; remove after packaging |
| `/dist/native/` | One current native candidate at a stable path |
| `/dist/qualified/` | Local copy of one accepted installer; publish before relying on it for distribution |
| `/.test-results/` | Small reports and logs; keep the latest successful run and latest failure |
| User `Scopecat-Development` directory | Retained worktree-specific development homes and verified resource cache; outside the checkout |

Tool-owned directories such as frontend `dist`, `.venv` and `node_modules` retain
their ordinary locations. Share uv/pnpm caches rather than creating another cache
per build. Cache pruning is separate maintenance, not a broad directory deletion.
Old `results/` directories are not a supported output location. New development
must not depend on untracked inputs. Retained scientific data, site settings and
vendor SDK installations belong outside the checkout, at explicit locations.
The daily `uv run --locked python -m scripts.dev` entry retains data outside
the checkout in a worktree-specific development home, separate from installed
application data. Use `--home /absolute/path/outside/checkout` for another trial.
Keep authored source in its own tracked directory. Classify existing files
by their contents and use: a disposable environment can be rebuilt, while retained
scientific data, SDKs, settings and authored source are not build outputs.

Use the same application path, name and bundle identity for repeated native
checks. Stop that application before replacing its package. Keep test data in an
explicit isolated home, and use Computer Use only for native interactions that
automated checks cannot establish. Stable identity aids discovery but does not
guarantee reuse of every OS or Computer Use permission after rebuilding.

For example, build a delivery into `build/delivery`, then run on macOS:

```sh
python -m scripts.app_build.native_package build/delivery dist/native/Scopecat.app \
  --installer dist/native/Scopecat.dmg
```

Use `dist/native/Scopecat` and
`dist/native/Scopecat-Setup.exe` on Windows. Existing outputs are rejected rather
than silently overwritten or expanded into another timestamped directory.

`verify_native_application.py APP REPORTS [INSTALLER]` now copies the input app
into disposable work: it never renames the caller's candidate. Both it and
`verify_macos_download.py INSTALLER REPORTS` remove temporary app/environment
copies on success or failure, retaining their result JSON and available logs.
Use `--keep-work` only when diagnosing a failure, and remove the printed workspace
afterwards. The reports directory must be fresh; remove the previous small report
after review. CI artifacts have their own bounded retention.

`verify_native_replacement.py PREVIOUS CURRENT REPORTS` follows the same policy:
replacement copies and the generated author environment are disposable; startup
and replacement reports and available logs survive failure. Both input packages
remain untouched. `--keep-work` retains the isolated replacement workspace.

Shared Git ignores cover disposable outputs, rebuildable tool environments and
temporary runtime bindings. They must not hide maintained source, documentation,
assets or editor tasks. Retained inputs should have explicit locations rather
than being hidden by `.git/info/exclude`. Reuse the fixed
output locations; after stopping their processes, remove obsolete outputs before
rebuilding. Incompatible development stores are explicitly reset, not migrated or
silently replaced by a new directory. Diagnostic `--keep-work` directories must
be removed after their useful evidence is published to the owning issue.

## Installed application directories

For an already registered source folder, maintainers can select an existing
execution interpreter without installing packages or restarting the application:

```sh
python -m scopecat_app.application --home /path/to/application-home \
  --action select-source-environment --workspace /path/to/source \
  --python /path/to/execution-environment/bin/python
```

On Windows, pass the environment's `Scripts/python.exe`. The interpreter must
already provide compatible Scopecat execution dependencies and source requirements.
Validation precedes saving the binding; failure preserves the prior selection.
This changes background execution for subsequent preparation, not the interpreter
selected in VS Code. Existing retained tasks keep their environment binding.
Settings exposes the same operation. Adding a folder requires an explicit execution
Python; preparing dependencies from `pyproject.toml` is a separate action. Registration
is visible to the running application without restarting it.
托管准备解析作者目录的 `pyproject.toml` 与 Scopecat 执行依赖，并以交付包完整的
`requirements.lock` 约束共享依赖版本，与本地 Notebook 环境采用同一交付基线。
这不会把整套桌面依赖安装进作者环境；可以增加声明的新包，但不能静默升级锁内共享依赖，
冲突会报错并保留原环境选择。Vendor SDK 可通过
[SDK process protocol](architecture/sdk-process.md) 使用独立解释器。

| Purpose | macOS | Windows |
| --- | --- | --- |
| Native app | User-selected Applications folder | User Programs `Scopecat` |
| Entry | `Scopecat.app` | User Start Menu `Scopecat.lnk` |
| Application Python and dependencies | Inside `.app` | Inside installed program directory |
| Data and settings | `~/Library/Application Support/Scopecat` | Local AppData `Scopecat` |
| Build cache | `~/Library/Caches/Scopecat` | Local AppData `Scopecat/Cache` |
| Initial author folder | `~/Scopecat/experiments` | `~/Scopecat/experiments` |

Windows setup uses Inno Setup's per-user program and Start Menu locations; the
data/cache locations use
platformdirs. These follow the separation in
[Apple's file system guidance](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/FileSystemOverview/FileSystemOverview.html)
and [Windows Known Folders](https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid).

An isolated validation uses a separate application package and `data`, `cache`,
and `experiments` under its explicit root. It must not publish a daily Start Menu or
Applications entry. The installer is headless in either mode.

`ApplicationRuntime.home` is the application data home, outside the installed
package. There is no separate software home or retained application installation
inside it. Native launchers resolve package resources relative to their executable
and pass the home explicitly.

New homes use these ownership boundaries:

| Location under the home | Contents and retention |
| --- | --- |
| `scopecat.application.json` | Explicit instance layout; no author manifest or generated `lab.py` |
| `data/` | Scientific SQLite store, immutable objects, original author-job receipts and practice-owned files; never cache |
| `state/` | Installation registration, author folders and retained interpreter references, deployment identity, daemon endpoint/logs and worker coordination; **not** a disposable directory |
| `environments/` | Execution dependencies and their independent Python; retain while saved work can reference them |
| `cache/` | Source/driver materializations reproducible from retained immutable bundles; regenerate only after stopping work |
| `authors/` | Default Help-created editable user code, including local client environments; user-owned files |
| `desktop/`, `learning/` and root lock/journal files | Native window coordination, recovery choices and Help continuation; stable host anchors, retained independently of scientific reset |

User `.venv` directories remain with their author folder. Their base Python is
managed by uv or explicitly supplied by the user; retain it while environments
reference it. Existing `.scopecat-python` bases remain untouched. Developer delivery/build caches
continue to use their explicit cache locations; worktrees have independent homes.

Recognized existing desktop homes retain `runtime/.scopecat`, their deployment
identity, registrations and environment references in place. Their scientific store
is inspected at that location, including unsupported-format recovery with verified
external backup or an explicit per-operation skip. Reset removes only the confirmed
store files and initializes at the same location. No implicit move, generic historical
migration, multiple-space manager or probing of `Scopecat Development` is added.

The native app is relocatable; moving an author's virtual environment independently
of its base Python is not supported.

## Application, command and data identities

Keep four identities separate when describing installation:

| Identity | Meaning | Does not determine |
| --- | --- | --- |
| Distribution/release | Versioned native payload, wheels and manifest source hashes | Which local data space is in use or whether a consumer upgraded |
| Installed copy | A concrete native package at a local path, with bundled Python and GUI | Ownership of data merely by containing executable code |
| Running owner | Processes coordinated for an explicit application home, with lifecycle and locks | A new data space for each window, Notebook kernel or Python environment |
| Data space | Persistent store identity and scientific history under the selected data home | The package location, author folder or current process ID |

The current native installers provide the desktop application entry, **not a
global PATH `scopecat` console**. Installing the `scopecat-app` wheel in a
Python environment creates that environment's `scopecat` console script. This packaging fact is not an implemented application-owned CLI
contract and does not require installing the whole application in every author
venv. The generated author client environment starts with `scopecat` and
`ipykernel`. Execution environments separately include framework/server requirements
and declared experiment dependencies; optional SDK processes retain their own
qualified requirements.

### Confirmed installation target; CLI delivery remains unimplemented

Ordinary users install the native desktop application. The application owns its
runtime and persistent data; multiple author venvs contain SDK/client and experiment
or device dependencies, not another complete GUI application. Deleting an author
venv must not delete experimental data. This ownership boundary does not remove the
separately qualified execution/runtime dependencies described above.

The confirmed direction is an optional CLI/launcher distributed by the desktop
application, controlling or connecting to that same application and its explicit
data space. It must not silently create another data service. This application-owned
CLI is **not implemented**: native installation still adds no global PATH console.
Command discovery, installation selection, version negotiation and platform
packaging details remain follow-up design work, not a new product-direction choice.

A complete pip-installed GUI, an independently distributed CLI client, and
portable/headless delivery are not current second primary channels. Reconsider
them only for an explicit requirement; retained tools and acceptance payloads do
not establish such a commitment. Compiling Python extensions into a desktop host
or installing a host package into a venv does not determine data ownership.
No launcher, PATH integration or packaging change is delivered by this documentation
work. See the canonical
[entry map](architecture/public-application.md#user-journeys-and-entry-ownership).

## Runtime assets and author-environment resources

Two resource formats serve different purposes. Format 1 is the complete locked
build/offline material set, including the dependency wheelhouse and installer;
delivery preparation also retains the portable Python archive and uv. Format 2 is
a smaller GUI and author-preparation resource set: GUI assets, the core/server wheels,
dependency constraints, `author.json`, a checksum manifest and optional uv tools.
It does not contain the complete application runtime or an offline dependency
wheelhouse, and is not a complete application upgrade. Native application updates
still replace the installed application after work has stopped.

The existing application installation record stores `static_dir` independently of
optional `delivery_root` and `delivery_manifest_sha256`. These reference the
existing `bundle.json` identity; they do not define another installation format.
Native startup supplies its current payload explicitly. An ordinary installed
Python environment obtains the resource root from its own
`scopecat-lab-delivery.json` receipt, using the selected interpreter's prefix.
Source-only operation without a receipt does not guess a bundle from the GUI's
parent directory.

Installer receipts contain absolute resource paths. Relative paths, missing moved
payloads and a manifest differing from the receipt fail explicitly; no neighboring
bundle is searched. Native relocation registers the new explicit payload on its
next normal startup. It retains the existing stopped/idle ownership requirements.
GUI/runtime qualification does not hash the dependency wheelhouse. Explicit author
creation or rebuilding checks the recorded manifest and full payload before moving
an old environment. Reusing an existing author `.venv` needs no delivery resources.
Older development application records without resources can still serve data;
reopen the native application or explicitly update its installation to register
resources before preparing new environments. No scientific data migration is added.

## Author resource build inputs

`scripts.app_build.author_resources.build` accepts either a legacy delivery path
or an `AuthorInputs` value. The latter is an in-process build contract, not another
installer or distribution format. Its producer supplies:

- A material root with `gui/index.html` and all GUI assets, exactly one
  `scopecat` wheel and one `scopecat_server` wheel under `wheels/`, and
  `requirements.lock` for the author's shared dependency constraints.
- An expected SHA-256 `files` map covering exactly those materials, plus the
  optional host `toolchain/uv` (Windows: `uv.exe`) and optional uv licenses
  (`LICENSE-APACHE`, `LICENSE-MIT`). The toolchain directory may be absent.
- The full `target` identity (OS, CPU, Python minor, implementation and
  free-threaded ABI), nonempty `sources` identity from the producer, and optional
  `runtime` metadata (empty by default). Source identities are retained verbatim;
  hashes detect mismatched bytes, not publisher authenticity or source provenance.
- Optional `AuthorRelease` context with explicit `public_version`,
  `release_version`, `build_number`, `build_id` and `build_tools` fields. Development
  inputs need none of these. Native inputs require all of them; portable Python
  provenance stays under `build_tools`, separate from runtime product identities.

The builder checks target compatibility, unique required wheels, symlinks and the
producer's expected hashes before writing. It compares copied bytes with those
same hashes before recording the generated `author.json`, then verifies the
format 2 output. Producer files outside this selected material set are not copied
or needed: no `bundle.json`, `dependencies.lock`, `build.lock`, `install.py`,
Python archive or third-party wheelhouse is required for `AuthorInputs`.
The producer owns preparation, source identity and expected hashes; the builder
must not derive expected hashes from potentially changed materials at consumption.
The recipe interpreter is independently probed against the declared target.

For existing path callers, the compatibility adapter still verifies the complete
format 1 delivery before selecting these materials, and retains its metadata
(including empty source metadata accepted by the legacy verifier).
Development resource preparation now produces `AuthorInputs` directly: it exports
locked constraints, builds the two core/server wheels and supplies the Vite handoff
GUI and source-byte identity. Mac/Windows host pywebview material is prepared and
verified in a separate cache, outside the author payload. See
[daily source development](public-preview.md#daily-source-desktop) for cache repair,
restart behavior and the independently owned author environments.

The installed teaching journey consumes an explicitly selected installed runtime
and verified format 2 resources after archive relocation, rather than invoking
`install.py` as part of that journey. Its Windows application and Linux runtime
fixture are still constructed from full format 1 materials. Native packaging also
retains that complete build input, but selects explicit `AuthorInputs` from the
same verified staged delivery. Its build ID binds sources, target and the full
file inventory; Python provenance must match the hashed runtime record and
archive, and the author recipe must match the installed interpreter. Core/server
wheels are copied from this delivery, not rebuilt. Windows installer versions and
release assembly consume the resulting format 2 identity and wheel hashes.
The legacy path adapter and installer remain supported; neither the runtime
archive nor the third-party wheelhouse producer changes in this projection step. See
[teaching acceptance limits](architecture/desktop-packaging.md#teaching-intent-and-acceptance-limits)
for the selected commands and evidence boundaries. Neither consumer migration
retires format 1 or turns format 2 into a complete application upgrade.

## Distribution boundary

The locked delivery build prepares a clean uv-managed Python at the repository's
exact pinned patch/variant, checks relocation, and records the pristine Python
archive, uv executable and uv licenses in the delivery checksum inventory.
Build on the target OS/architecture. The retained
`python -m scripts.app_build.toolchain DELIVERY OUTPUT` command verifies and copies
that already-prepared delivery; it does not acquire a different Python at packaging
time. A delivery missing its prepared archive must be rebuilt. Neither path
registers Python with the operating system. See the
[exact build runtime](architecture/desktop-packaging.md#exact-build-runtime) contract.

Native packaging extracts Python and installs locked wheels at build time.
Author-environment preparation follows `scopecat_app.author_python`: it accepts an
explicit interpreter (`--python` or `UV_PYTHON`), or finds a separately uv-managed
Python using the workspace's `.python-version` when present, otherwise the
`author.json` recipe's version (falling back to the running interpreter's version
if no recipe exists).
The version fallback selects a version request, not the application executable.
When the required managed Python is missing, online preparation may download it;
offline preparation fails explicitly. The resolver checks version compatibility
and rejects the application's internal interpreter, including virtual environments
based on it. It does not extract the application's bundled Python for authors or
silently substitute another Python version after a failed download.

The low-level standalone `install.py DESTINATION` remains a separate offline
installation path for complete format 1 materials and legacy regression checks.
It does not install a desktop app or generate launchers. Toolchain construction
needs network access; installing the locked format 1 wheels is offline. Format 2
alone does not promise offline author dependency preparation.

`python -m scripts.app_build.native_package DELIVERY APP --installer INSTALLER` packages
the delivery and its prepared toolchain into a relocatable native application.
Build on the target platform: macOS requires the command-line developer tools and
produces an `.app` and `.dmg`; Windows requires the MSVC developer command prompt
and Inno Setup 6 and produces `Scopecat.exe` with an optional Setup executable.
These are maintainer prerequisites, not end-user prerequisites.

The native executable runs the complete bundled Python runtime and enters the
desktop workbench. It does not install application dependencies or open
a browser. Laboratory initialization scripts are not part of native startup.
On Windows, `Scopecat.exe` hosts Python in its own process, with the same explicit
AppUserModelID as the installed shortcut. GUI windows therefore belong to the
application executable; backend and author subprocesses still use their selected
ordinary Python interpreters. The Windows platform smoke job compiles and checks
this process identity, interpreter selection, argument forwarding and exit status.
Create or register ordinary source explicitly after opening the application.
Updates replace the native application after work has stopped. Startup registers
the installed runtime; it never selects a separately prepared candidate.

Native startup failures are written to `data-home/native-start.log` and presented
through a native error dialog. `--home ROOT --check-result FILE` instead performs
headless setup for acceptance. `scripts/verify_native_application.py APP REPORTS`
checks relocation, empty PATH, repeat startup, unchanged app contents, retained
author Python after the app is moved away, and service start/stop before removal. The
`native-distribution` acceptance profile builds and runs this on macOS and Windows.

Mac previews use ad-hoc signing of each embedded Mach-O file followed by the
outer application seal. This requires no certificate or paid account, but does
not establish Gatekeeper trust. Apple Developer ID signing and notarization are
not configured; Windows previews remain unsigned. Private consumers use the public
installer and register ordinary laboratory source with an explicit execution
Python. They do not rebuild the application or inject startup initialization.

Do not add a second application manager to solve packaging. Native setup and updates
must use the same application/data ownership, independent author environments and
explicit stop behavior. Uninstalling software must not erase scientific data or
author projects. Windows window behavior remains a target-platform acceptance item.

## Confirmed in-place data reset

The native recovery action owns only the standard SQLiteProjectStore database,
sidecars and object directory. It never deletes the entire home or `.scopecat`.
Existing application/deployment/daemon and worker fences remain held while taking
an optional raw backup and deleting the store. An external archive uses a strict
file inventory, raw DB/WAL capture under SQLite writer reservation, a manifest,
checksums and publication before deletion; unresolved rollback journals are refused.
These fences coordinate Scopecat owners, not concurrent manual filesystem edits.

`desktop/reset-backup.json` remembers a selected external directory only. Skip
backup is not persisted. `data-reset.json` is an interrupted-operation marker,
not a history registry: it fences normal startup, names any verified archive and
separates deletion from initialization. Remaining old bytes must be covered by
that backup before a resumed deletion. The marker is removed after initialization.
Backup failure never authorizes deletion. Partial deletion is reported truthfully.
No `spaces/`, selection pointer, automatic migration or backup garbage collector
is introduced. Installations and source directories stay at their original paths.
