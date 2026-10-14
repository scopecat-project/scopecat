# Download and install

Scopecat is an experimental desktop application, not qualified for production use.
The first numbered release has not been published; its version is not yet selected.

## Get a package

Ask the maintainer for a qualified installer matching the features you want to try,
with its source commit, platform and checksum manifest. Current documentation follows
main and does not mean an installer of main has been released.

The [historical desktop preview](https://github.com/scopecat-project/scopecat/releases/tag/desktop-e11acefb96ae7bf2163646bd15f50c77e2a1d8d3)
is a retained download from source `e11acefb9`, **not the first numbered release or
the current main application**. Its release notes describe its qualification;
later tutorial, authoring and recovery features may be absent. Do not use it to
validate the current first-experiment journey. If no matching candidate is available,
wait for the maintainer's delivery; source development is a separate contributor path.

| Computer | Historical installer | Signing status |
| --- | --- | --- |
| Apple Silicon Mac (arm64) | `Scopecat.dmg` | Ad-hoc signed; no Developer ID or Apple notarization |
| Windows x64 | `Scopecat-Setup.exe` | Unsigned |

Intel Mac, Windows ARM and Linux installers are not qualified by this delivery.
Check the platform-specific `native-release-*.json` and `bundle-*.json` on the same
release page with the maintainer; they record the source, platform and installer
SHA-256. GitHub's **Source code** archives and `preview-<SHA>` framework wheels
are not desktop installers. Actions artifacts expire and are not a durable download
entry. A future numbered release will carry `release.json` alongside its installers;
see [artifact identity](../development/public-preview.md#artifact-identity-and-consumer-selection).

## Before installation

- **System version:** a minimum supported macOS/Windows version has not yet been
  established by Scopecat acceptance. Confirm your exact OS version and CPU with the
  maintainer before trial. A successful hosted-runner test is not a minimum OS promise.
- **Windows runtime:** the native UI uses Microsoft Edge WebView2. The Scopecat
  installer does not install its Runtime. Have the maintainer or IT check it before
  first launch. Microsoft provides the [Evergreen Runtime and offline installer](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution);
  the Edge browser alone is not a substitute for that Runtime.
- **Included:** Python, Scopecat and its application dependencies. You do not need
  system Python, uv, Git, Node.js, pnpm or a laboratory SDK to open saved data or
  use the built-in device-free practice.
- **Notebook editing:** install VS Code and its Python and Jupyter extensions
  separately. Scopecat prepares a separate author Python; select the interpreter
  shown by Settings or Help. Do not select Python inside the application bundle.
- **Network:** downloading the app, VS Code/extensions and additional dependencies
  needs network access or a separately prepared offline delivery. The app and bundled
  examples use the delivered runtime without downloading application dependencies
  at startup. Extra laboratory requirements, SDKs and firmware need separate
  preparation; an offline app launch does not prove offline laboratory setup.
- **Files:** choose a writable folder for your code and keep it, its Python environment
  and saved records. Updating the application does not update your author environment.

## Install and try

1. On Mac, open the DMG and drag **Scopecat** to **Applications** before opening it.
   On Windows, run the installer and open **Scopecat** from the Start menu.
2. If the OS blocks opening, stop and record the exact message, OS version and
   download identity for the maintainer or IT to check. Do not disable security
   protections or remove quarantine. Signing limitations are described above;
   a checksum does not establish trusted publisher identity.
3. For a matching current candidate, follow [first steps](index.md): start the
   device-free peak practice in Help, or prepare an editable tutorial and its Python.
   Use **Continue** to return to the same tutorial folder and saved edits.
4. Follow [first experiment](quickstart.md) to preview, explicitly submit one
   synthetic measurement, inspect it, quit and reopen the same saved result.
   Reopening a result does not submit another measurement.

For existing laboratory code, obtain its source and compatible dependency instructions
from your lab, then use [author folders](../how-to/maintain-application.md#author-folders).
Registering code is separate from connecting devices; complete simulation first.

## If a step fails

Keep your code, receipts and application data. Startup failures retain retry/quit
controls; record the error before retrying. If another service is still working,
finish that work before retrying an update. For a failed environment preparation,
use [application maintenance](../how-to/maintain-application.md); reinstalling the
app is not an environment repair. A submission wait timeout means wait on the same
job, not submit again. See [first experiment](quickstart.md) for reopening by receipt.

Before replacing an older build, read the [data compatibility policy](../development/data-compatibility.md)
and [back up retained records](../how-to/backup-and-restore.md). No compatibility
baseline is designated for historical development stores. Update and uninstall
steps are in [application maintenance](../how-to/maintain-application.md).

Framework contributors use [source development](../development/public-preview.md#daily-source-desktop),
which has separate build prerequisites and an isolated development home.
