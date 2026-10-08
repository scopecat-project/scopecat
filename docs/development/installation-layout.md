# Application installation layout

Installed software, application data, disposable build caches and editable user
code have separate owners. `InstallationPaths.current_user()` selects per-user
locations; `InstallationPaths.isolated(home)` places application-owned validation
files inside one explicit directory. Native browser-store isolation additionally
depends on the packaged backend; an isolated home alone does not prove it. See
[native acceptance safety](native-window-acceptance.md#safety-and-failure-handling)
and the [home/host boundary](architecture/draft-recovery.md#home-host-and-privacy-boundaries).
Foreground source development continues to use `lab_tools.dev` and does not create an installed application.

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
The daily `uv run --locked python -m lab_tools.dev` entry retains data outside
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
python -m lab_tools.native_package build/delivery dist/native/Scopecat.app \
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
python -m lab_tools.application --home /path/to/application-home \
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
is visible to the running application without restarting it. Managed preparation
resolves the source requirements with compatible Scopecat execution packages, not
the desktop application's complete dependency lock. Vendor SDKs can use another
interpreter through the [SDK process protocol](architecture/sdk-process.md).

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

`ApplicationRuntime.home` is the **data** home. `installation.json` records the
registered interpreter and GUI of the current package. There is no separate
software home or retained application environment inside the data directory.
There is no retained-release installer or generated `lab.py`. Native launchers
resolve resources relative to their executable and pass the data home explicitly.
Scientific records remain
under `data-home/runtime`; retained background dependency environments remain under
`data-home/environments`. Neither is cache. User `.venv` files remain in the author
folder, with independent package copies and a base interpreter under
`.scopecat-python`. Keep both directories together.

There is no automatic migration from older development homes, nor any probing of
`Scopecat Development`. Existing files are not deleted or silently rewritten.
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
global PATH `scopecat` console**. Installing the `scopecat-lab-tools` wheel in a
Python environment creates that environment's `scopecat` and `scopecat-lab`
console scripts. This packaging fact is not an implemented application-owned CLI
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

## Distribution boundary

`python -m lab_tools.toolchain DELIVERY OUTPUT` builds a new platform delivery
containing Python and uv. Run it on the target OS/architecture after the ordinary
locked delivery build. It downloads the builder's exact Python patch version into
a temporary managed directory, checks relocation, and records the Python archive,
uv executable and uv licenses in the delivery checksum inventory. It does not
modify the input delivery or register Python with the operating system.

Native packaging extracts Python and installs locked wheels at build time.
Explicit author-environment creation extracts an independently owned base Python;
it does not reference a removable application interpreter. The low-level standalone
`install.py DESTINATION` creates an offline environment for teaching/build checks
only. It does not install a desktop app or generate launchers. Toolchain construction
needs network access; installing locked wheels is offline.

`python -m lab_tools.native_package DELIVERY APP --installer INSTALLER` packages
the delivery into a relocatable native application, adding the toolchain if needed.
Build on the target platform: macOS requires the command-line developer tools and
produces an `.app` and `.dmg`; Windows requires the MSVC developer command prompt
and Inno Setup 6 and produces `Scopecat.exe` with an optional Setup executable.
These are maintainer prerequisites, not end-user prerequisites.

The native executable runs the complete bundled Python runtime and enters the
desktop workbench. It does not install application dependencies or open
a browser. Laboratory initialization scripts are not part of native startup.
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
