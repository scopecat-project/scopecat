# Download and install

Scopecat is experimental. [v0.1.0](https://github.com/scopecat-project/scopecat/releases/tag/v0.1.0)
is the first numbered release. Use its assets and matching release notes; source
changes after that release do not update its installers.

## Choose your installer

| Computer | Installer | Signing status |
| --- | --- | --- |
| Apple Silicon Mac (arm64) | `Scopecat.dmg` | Ad-hoc signed; no Developer ID or Apple notarization |
| Windows x64 | `Scopecat-Setup.exe` | Unsigned |

Recorded [native checks](../development/native-window-acceptance.md) cover
macOS 26.6.2 arm64 and Windows Server 2025 x64 with WebView2 153.0.4234.48 on
specific earlier candidates. They do not qualify current main or establish a
minimum OS version. Other OS versions and Intel Mac, Windows ARM and Linux
installers remain unqualified; use the candidate's release notes for its tested scope.

The available [historical desktop download](https://github.com/scopecat-project/scopecat/releases/tag/desktop-e11acefb96ae7bf2163646bd15f50c77e2a1d8d3)
predates current tutorial and recovery features and is not the first numbered
release. Choose the numbered release above or an explicitly qualified newer
candidate for the journey below. Framework
wheels and GitHub source archives are not installers; maintainers can consult
[artifact identity](../development/public-preview.md#artifact-identity-and-consumer-selection).

## What you need

- Python and application dependencies are included. Opening saved data and
  device-free practice need no source checkout, system Python or laboratory SDK.
- Windows needs **WebView2 Runtime**, which Scopecat's installer does not install.
  If missing, use Microsoft's [Evergreen Runtime installer](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution).
- For Notebook editing, install **VS Code** with **Python** and **Jupyter** extensions.
  Select the author interpreter shown by Scopecat, not Python inside the app.
- Downloads and extra dependencies need network access or a prepared offline
  delivery. App startup uses the bundled runtime; laboratory dependencies and
  SDKs are prepared separately.

## Install and try

1. On Mac, open the DMG and drag **Scopecat** to **Applications**, then open it.
   On Windows, run the installer and open **Scopecat** from the Start menu.
2. Follow [first steps](index.md): start the device-free peak practice in Help,
   or prepare an editable tutorial and its Python. **Continue** returns to the
   same tutorial folder and saved edits.
3. Follow [first experiment](quickstart.md) in Help's Parameters and scans Notebook
   to prepare and run one synthetic measurement, inspect it, quit and reopen the
   same saved result. Displaying the
   prepared preview is optional. Reopening does not submit another measurement.

For laboratory code, follow your lab's setup instructions and register its
[author folder](../how-to/maintain-application.md#author-folders), then try simulation
before connecting devices.

## If a step fails

If the OS blocks opening, keep the exact message and ask the maintainer or IT for
help. Do not disable security protections or remove quarantine. Startup failures
retain retry/quit controls; keep your code and data when retrying. Environment
preparation and update recovery are covered by [application maintenance](../how-to/maintain-application.md).
A run wait timeout means wait on the same job, not submit again.

Before updating an older build, [back up retained records](../how-to/backup-and-restore.md)
and check the [data compatibility policy](../development/data-compatibility.md).
Framework contributors use the separate [source development entry](../development/public-preview.md#daily-source-desktop).
