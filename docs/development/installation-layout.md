# Application installation layout

Installed software, application data, disposable build caches and editable user
code have separate owners. `InstallationPaths.current_user()` selects per-user
locations; `InstallationPaths.isolated(home)` keeps validation inside one explicit
directory. Foreground source development continues to use `lab_tools.dev` and
does not create an installed application.

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
registered interpreter and GUI. The `software_home` field remains bookkeeping
pending model cleanup; native startup does not create a software directory.
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
a browser. A trusted `--initializer SCRIPT` may scaffold laboratory starter code;
it is checksummed with the delivery, runs on first setup and retries if interrupted.
Updates replace the native application after work has stopped. Startup registers
the installed runtime; it never selects a separately prepared candidate.

Native startup failures are written to `data-home/native-start.log` and presented
through a native error dialog. `--home ROOT --check-result FILE` instead performs
headless setup for acceptance. `scripts/verify_native_application.py APP HOME`
checks relocation, empty PATH, repeat startup, unchanged app contents, retained
author Python after the app is moved away, and service start/stop before removal. The
`native-distribution` acceptance profile builds and runs this on macOS and Windows.

Mac previews use ad-hoc signing of each embedded Mach-O file followed by the
outer application seal. This requires no certificate or paid account, but does
not establish Gatekeeper trust. Apple Developer ID signing and notarization are
not configured; Windows previews remain unsigned. The native private installer/preview consumer must be switched and
qualified before this replaces its existing maintainer installation command.

Do not add a second application manager to solve packaging. Native setup and updates
must use the same application/data ownership, independent author environments and
explicit stop behavior. Uninstalling software must not erase scientific data or
author projects. Windows window behavior remains a target-platform acceptance item.
