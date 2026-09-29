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
| Retained software versions | Application data `software` | Application data `software` |
| Data and settings | `~/Library/Application Support/Scopecat` | Local AppData `Scopecat` |
| Build cache | `~/Library/Caches/Scopecat` | Local AppData `Scopecat/Cache` |
| Initial author folder | `~/Scopecat/experiments` | `~/Scopecat/experiments` |

Windows setup uses Inno Setup's per-user program and Start Menu locations; the
maintainer entry helper uses Known Folder APIs. Data/cache locations use
platformdirs. These follow the separation in
[Apple's file system guidance](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/FileSystemOverview/FileSystemOverview.html)
and [Windows Known Folders](https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid).

An isolated install instead has `software`, `data`, `cache`, `experiments`, and a
platform entry under its explicit root. It must not publish a daily Start Menu or
Applications entry. The installer is headless in either mode.

`ApplicationRuntime.home` is the **data** home. `installation.json` records the
selected interpreter, GUI and software home. Releases and their retained delivery
files live in the software home; updates reuse that location. Native launchers
resolve resources relative to their executable and pass the data home explicitly.
Scientific records remain
under `data-home/runtime`; retained background dependency environments remain under
`data-home/environments`. Neither is cache. User `.venv` files remain in the author
folder, with independent package copies.

There is no automatic migration from older development homes, nor any probing of
`Scopecat Development`. Existing files are not deleted or silently rewritten.
Changing the program location of an existing data home is rejected: installed
virtual environments contain absolute paths and cannot be relocated as a folder.

## Distribution boundary

`python -m lab_tools.toolchain DELIVERY OUTPUT` builds a new platform delivery
containing Python and uv. Run it on the target OS/architecture after the ordinary
locked delivery build. It downloads the builder's exact Python patch version into
a temporary managed directory, checks relocation, and records the Python archive,
uv executable and uv licenses in the delivery checksum inventory. It does not
modify the input delivery or register Python with the operating system.

`prepare_home` extracts this Python beside the retained release's runtime before
creating the virtual environment. Both the extracted base interpreter and runtime
must stay at their installation paths. The immutable archive stays inside the
verified bundle; generated Python bytecode stays outside its checksum inventory.
Author environments and background execution environments can consequently share
the retained base interpreter while keeping their own package directories. Runtime
maintenance uses the installed `uv` dependency, without consulting the user's PATH.
Toolchain construction needs network access; installing its locked wheels is offline.

`python -m lab_tools.native_package DELIVERY APP --installer INSTALLER` packages
the delivery into a relocatable native application, adding the toolchain if needed.
Build on the target platform: macOS requires the command-line developer tools and
produces an `.app` and `.dmg`; Windows requires the MSVC developer command prompt
and Inno Setup 6 and produces `Scopecat.exe` with an optional Setup executable.
These are maintainer prerequisites, not end-user prerequisites. The old
`install.py`/`desktop_install` entry helper remains a source-maintainer path; it
does not produce the relocatable application.

The native executable starts an embedded minimal Python, prepares the complete
runtime outside the app, then enters the existing desktop workbench. It never opens
a browser. A trusted `--initializer SCRIPT` may scaffold laboratory starter code;
it is checksummed with the delivery, runs on first setup and retries if interrupted.
Later unseen app payloads prepare an update candidate without changing the selected
runtime or stopping work. Reopening a previously seen app does not replace that
candidate. Applying it uses the workbench's existing explicit stop/update flow.

Native startup failures are written to `data-home/native-start.log` and presented
through a native error dialog. `--home ROOT --check-result FILE` instead performs
headless setup for acceptance. `scripts/verify_native_application.py APP HOME`
checks relocation, empty PATH, repeat startup, unchanged app contents, retained
author Python and service start/stop after the app is moved away. The
`native-distribution` acceptance profile builds and runs this on macOS and Windows.

These are unsigned prerelease artifacts; signing and macOS notarization are not
configured. The native private installer/preview consumer must be switched and
qualified before this replaces its existing maintainer installation command.

Do not add a second application manager to solve packaging. Native setup and updates
must use the same application/data ownership, independent author environments and
explicit stop behavior. Uninstalling software must not erase scientific data or
author projects. Windows window behavior remains a target-platform acceptance item.
