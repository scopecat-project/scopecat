# Changelog

User-visible changes are collected with Knope change files. Historical SHA
previews retain their original identities.
## 0.1.0 (2026-10-14)

### Features

#### Try Scopecat's first experimental numbered release.

Scopecat 0.1.0 (build 1) brings experiment preparation, execution, saved results,
parameter editing and Python authoring into one desktop application. It remains
experimental: this version does not establish a stable API or data compatibility
baseline, and simulation checks do not qualify laboratory hardware.

#### Prepare experiments and recover your work

Browse experiments, edit inputs and start a measurement with automatic preparation
and checks; Preview is available when you want to inspect them first. Unfinished
inputs and competing window edits remain recoverable after reopening. If a
submission's response is lost, recovery checks the original request instead of
starting another acquisition. Background procedures and calibration tasks retain
their source and inputs when an author client disconnects; continuing, cancelling
and adopting parameters remain explicit actions.

#### Find results and continue analysis

Follow live measurements, browse multidimensional slices and open a result in a
separate window. Runs, collections and saved analyses retain exact identities;
page navigation preserves the selected result and editable experiment inputs.
Read-only Notebook snippets reopen saved records without acquiring or analyzing
again. Run applicable author-declared analyses, repeat eligible saved analyses
with their original settings, and trace parameter proposals back to their source
results. Reanalysis does not acquire measurements or adopt parameters.

#### Edit parameters beside the sample map

Edit an object's referenced parameters or the full working table in the same
recoverable draft, including unfinished values. Explicitly use the resolved target,
setup and working inputs for the next experiment, or save a named checkpoint.
Submitted experiments keep their captured inputs. Compare and review parameter
candidates before adopting them, with retained source and adoption evidence.

#### Learn and author in the same application

Help offers editable Notebook courses for basic experiments, parameters and scans,
grouped analysis, computation and advanced calibration. Continue reopens the same
folder and saved results without overwriting edits or repeating measurements.
Register your own author folder in Settings, or create lesson source with
`scopecat init DIRECTORY --topic TOPIC`.

The installed application includes its own runtime and can start offline without
system Python. Author and worker environments use separate compatible Python,
managed by uv or explicitly supplied by the user, and respect workspace Python
and dependency declarations. Their preparation normally downloads Python and
third-party dependencies: the installer is not a complete offline author kit.
Failed or cancelled preparation preserves the previous usable environment and
retains diagnostics. Scopecat does not change system PATH or global packages.
Notebook editing requires a separate editor, such as VS Code with Python and
Jupyter extensions, using the author interpreter shown by Scopecat.

#### Keep data and recovery under your control

Application data is separate from editable author code. Settings can back up an
idle application; current-format backup and restore preserve retained records and
source identities. Restart and quit require a decision when background work is
active. Incompatible data can be reset explicitly from the startup window, with
an external backup by default; a failed backup leaves the original data in place.

This release uses development data schema 115. Earlier prebaseline data formats
and old run identifiers are not read or automatically migrated. Keep older data,
source, environments and their matching application version separately, and use
a new data space for this version. Backups are not a cross-version migration
service, and old-format backups are not promised to restore here. Existing
scientific records are not retrospectively corrected or rewritten.

#### Experimental installation limits

Installer targets are Apple Silicon Mac (arm64) and Windows x64. Mac bundles use
ad-hoc signing without Developer ID or Apple notarization; Windows installers are
unsigned. Do not disable OS security protections to open them. Windows requires
Microsoft WebView2 Runtime, which the installer does not install. Author downloads,
editor extensions and laboratory SDKs have separate prerequisites.

Earlier native checks cover specific macOS and Windows CI environments, not a
minimum OS version or every clean computer. Intel Mac, Windows ARM and Linux
installers remain unqualified. Windows startup health-check timeouts
([#465](https://github.com/scopecat-project/scopecat/issues/465)) and loss of the
service endpoint after startup
([#553](https://github.com/scopecat-project/scopecat/issues/553)) remain unresolved
risks; successful later runs do not establish a fix. Preserve failure diagnostics.
See the [installation guide](https://scopecat-project.github.io/scopecat/getting-started/install/)
for prerequisites and recovery guidance.
