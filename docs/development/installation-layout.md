# Application installation layout

Installed software, application data, disposable build caches and editable user
code have separate owners. `InstallationPaths.current_user()` selects per-user
locations; `InstallationPaths.isolated(home)` keeps validation inside one explicit
directory. Foreground source development continues to use `lab_tools.dev` and
does not create an installed application.

| Purpose | macOS | Windows |
| --- | --- | --- |
| Entry | `~/Applications/Scopecat.app` | User Start Menu `Scopecat.lnk` |
| Software versions | App `Contents/Resources/software` | User Programs `Scopecat` |
| Data and settings | `~/Library/Application Support/Scopecat` | Local AppData `Scopecat` |
| Build cache | `~/Library/Caches/Scopecat` | Local AppData `Scopecat/Cache` |
| Initial author folder | `~/Scopecat/experiments` | `~/Scopecat/experiments` |

Windows program and Start Menu locations use Known Folder APIs, including folder
redirection. Data/cache locations use platformdirs. These follow the separation in
[Apple's file system guidance](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/FileSystemOverview/FileSystemOverview.html)
and [Windows Known Folders](https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid).

An isolated install instead has `software`, `data`, `cache`, `experiments`, and a
platform entry under its explicit root. It must not publish a daily Start Menu or
Applications entry. The installer is headless in either mode.

`ApplicationRuntime.home` is the **data** home. `installation.json` records the
selected interpreter, GUI and software home. Releases and their retained delivery
files live in the software home; updates reuse that location. The launcher lives
beside the software and passes the data home explicitly. Scientific records remain
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

This is a packaging building block: invoking the delivery's `install.py` still
requires a bootstrap Python. It does not yet provide a native executable that starts
the bundled interpreter. Do not tell users to execute the builder as an installation
step. Native packaging must put retained interpreters outside the replaceable app
bundle before adopting this delivery in the default installer.

This is the installed layout, not yet a standalone native distribution. The current
maintainer installer still needs uv and a managed Python. The generated Mac app
must not be advertised as a relocatable drag-and-drop artifact. A complete native
release still needs a bootstrap launcher, native setup/removal,
platform CI and signature/notarization policy. Signing must cover immutable payloads;
it cannot be bolted onto an app whose contents are then edited by the old updater.

Do not add a second application manager to solve packaging. Native setup and updates
must use the same application/data ownership, independent author environments and
explicit stop behavior. Uninstalling software must not erase scientific data or
author projects. Windows window behavior remains a target-platform acceptance item.
