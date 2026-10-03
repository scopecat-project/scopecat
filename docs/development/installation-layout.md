# Application installation layout

Installed software, application data, disposable build caches and editable user
code have separate owners. `InstallationPaths.current_user()` selects per-user
locations; `InstallationPaths.isolated(home)` keeps validation inside one explicit
directory. Foreground source development continues to use `lab_tools.dev` and
does not create an installed application.

## Development artifacts and retention

Repository-local outputs have distinct lifetimes:

| Directory | Contents and retention |
| --- | --- |
| `/build/` | Rebuildable intermediate files and delivery staging; remove after packaging |
| `/dist/native/` | One current native candidate at a stable path |
| `/dist/qualified/` | One latest accepted installer; replace only after a new candidate passes |
| `/.test-results/` | Small reports and logs; keep the latest successful run and latest failure |
| `/.scopecat-dev/` | Persistent source-development data; never part of build cleanup |

Tool-owned directories such as frontend `dist`, `.venv` and `node_modules` retain
their ordinary locations. Share uv/pnpm caches rather than creating another cache
per build. Cache pruning is separate maintenance, not a broad directory deletion.
Old `results/` directories are historical local evidence, not a supported new
output location. Existing stores, source checkouts and SDK environments require
owner review; ignoring a directory does not make its contents disposable.

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

Shared Git ignores cover generated outputs, tool environments and local runtime
data. They must not hide maintained documentation, assets or editor tasks. A
machine's retained legacy paths belong in `.git/info/exclude`; do not add a new
shared ignore for each diagnostic experiment. Document decisions in ordinary
development docs, not in `AGENTS.md` or an ever-growing generated report archive.

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
