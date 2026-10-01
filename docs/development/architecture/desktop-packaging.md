# Ready-to-run desktop packaging

PR 1 of the post-#828 milestone must deliver a ready-to-run public application.
Changing implementation language is not itself acceptance. This is a selection
gate within that product PR, not a separate packaging product or a completed
replacement of the existing desktop host.

The baseline is a relocatable CPython runtime with dependencies prepared at build
time. Rust/Tauri was the preferred alternative evaluated against the existing
UI and Python scientific components; the delivery decision is below. PyInstaller
is an optional fixed-component
packaging candidate; evaluate Nuitka only for an identified benefit. Do not compile
editable author code or vendor environments into the desktop. Do not maintain
multiple permanent distribution mechanisms after selection.

## Required comparison

- Native window readiness, backend readiness and failure reporting are distinct.
- Measure first empty-store use separately from reopening; disclose OS cache and
  environment. A few local samples do not establish production latency budgets.
- Report package size, installed size and duplicate payload separately. A smaller
  host executable does not imply the scientific dependencies disappeared.
- Separate host, WebView, controller and backend memory. Summed RSS is not unique
  physical memory; do not compare a bare Rust executable to an entire Python app.
- Include build time, dynamic source/extension loading, native dependency handling,
  diagnostics, process ownership and clean shutdown on both Mac and Windows.
- Verify no runtime package installation and no writes to installed application
  files. Explicit author/extension dependency maintenance remains separate.

## Reproducible Python baseline

Run the **Full acceptance** workflow with the **native-distribution** profile on
the intended branch. After each platform passes, its Actions artifacts include
`scopecat-preview-OS-COMMIT` with `Scopecat.dmg` or `Scopecat-Setup.exe`, retained
for 14 days. The commit in the artifact name identifies the tested source.
Failed qualifications retain diagnostics but do not publish an installer artifact.
These are development previews. macOS uses ad-hoc signing, without Developer ID
or notarization; Windows installers are unsigned.

Mac packaging signs embedded Mach-O files individually, inside out, before
sealing the outer application. It refreshes the embedded delivery checksum for
the signed toolchain executable before sealing; the input delivery is unchanged.
The package and relocated running application must pass signature checks,
including Python extensions outside standard nested-code directories.
`scripts/verify_macos_download.py INSTALLER FRESH_HOME` mounts the shipped DMG
read-only, checks its actual application, applies quarantine to the disposable
copy, records Gatekeeper's assessment, and proves that editing a sealed resource
fails signature verification. CI retains `macos-download/report.json`.
Gatekeeper rejection is expected for an untrusted ad-hoc preview and is recorded
separately from signature validity. This does not prove Finder first-open behavior;
that still requires a browser download and the [user first-open steps](../../how-to/mac-preview.md).

After constructing a native package, run from the repository environment:

```sh
uv run --locked python scripts/measure_packaged_runtime.py \
  --app /absolute/path/Scopecat.app --output /fresh/qualification-directory
```

On Windows, `--app` is the directory containing `Scopecat.exe`. The tool requires
a fresh output directory, launches only the package's interpreter with an empty
PATH, creates an empty application store and checks HTTP health/UI delivery before
stopping. It repeats with the same store and records timings/RSS in `report.json`.
It does not open a browser, initialize private code, connect devices or create a
second installation. The native acceptance matrix retains this report alongside
its existing packaging checks.

This probe uses the existing lifecycle controller and does not prove the final
host has no controller process. It does not exercise the native window, prove
offline dependency isolation, qualify user environments or replace the full PR 1
acceptance. A ready backend alone is not a finished desktop application.

To qualify replacement rather than reinstalling one artifact, provide two different
native builds with the same current scientific-data format:

```sh
uv run --locked python scripts/verify_native_replacement.py \
  /path/to/previous/Scopecat.app /path/to/current/Scopecat.app \
  /fresh/replacement-qualification
```

On Windows, pass the two directories containing `Scopecat.exe`. The probe copies
the packages into its fresh test home, creates one measurement and independent user
Python with the previous build, then replaces the application at the same path.
It exercises a missing-GUI startup failure and retry, reads the original measurement
without resubmission, and checks source identity, user Python configuration, unchanged
application files and clean shutdown. Input packages are not changed. The retained
`replacement.json` identifies both manifests. This is not a prebaseline migration
test or a native window interaction test.

The native entry now starts the package's interpreter without calling the retained
delivery installer. Explicitly created author environments retain their own base
Python outside application files. Candidate-update and capability-snapshot commands
and storage have been removed, together with the retained-release installer and
its generated launchers. The standalone offline-environment installer remains for
teaching/build verification; it cannot install a desktop application. The pending runtime
registration record remains a crash-recovery journal, not a user-selectable update.
It may be completed by a newly verified package after replacement; recovery does
not require reinstalling the package that first wrote the marker. The native entry
recreates its derived runtime receipt from current package qualification, without
reading historical receipt formats. Scientific-data readers are unaffected.
The final host must preserve the work-aware quit/background contract. Data
initialization and explicit current-format checks are normal runtime work, not
dependency setup.

## Implementation boundary

For this delivery, retain the CPython/pywebview host and the existing web UI.
Direct packaged startup and independent author Python address the demonstrated
installation coupling without a new host protocol. The disposable Tauri prototype
establishes feasibility, but has not qualified Windows window/tray interaction,
active-work shutdown or failure recovery. It therefore does not justify replacing
the functioning lifecycle implementation in this batch. Do not ship both hosts.
Reconsider Tauri for a measured native-integration limitation; Python scientific
execution remains a separate boundary whichever host is used.

The installed package owns one fixed application runtime. Its executable starts
that runtime directly; a previous installation's selected interpreter must not
override it. Application updates replace application files after the owned
processes exit. They do not prepare or select another Python environment inside
the data directory. This removes `native_bootstrap.prepare`'s `prepare_home` and
candidate-selection responsibilities, rather than merely moving them into Rust.

The desktop host owns windows, reopening, background presence and application
process lifetime. The Python backend owns scientific state, active-work reporting
and orderly experiment shutdown. Before quitting, the host asks the backend about
active work and offers the existing work-aware choices. Hiding a window preserves
the same backend; reopening must not create another one. Host/backend failures
must leave visible diagnostics and a recoverable next launch without requiring
users to choose interpreters or kill processes manually.

An author environment is a separate resource with an explicit interpreter and
dependencies. Its base interpreter must survive replacement or removal of the
desktop package. Registering ordinary source must not install it into application
Python. Explicit author dependency preparation may install packages in the author
environment; ordinary application launch may not. Vendor runtime isolation builds
on this boundary in PR 3.

Generated user environments install only `scopecat`, `ipykernel` and their
dependency closure from the verified offline wheels, constrained to the delivery's
versions. They do not install the server, desktop or teaching packages. Author
revisions record import dependencies separately from execution dependencies:
notebook refresh checks the former; backend recovery still checks the latter.
Explicit author dependencies remain required on both sides. Workspaces without
an explicit dependency declaration retain their full captured environment contract.

The packaged-CPython qualification passes on both native distribution runners.
A disposable macOS Tauri prototype also exercised explicit backend start,
hide/reopen with the same endpoint, and clean quit. These establish feasibility,
not production qualification for Tauri. Keep the prototype outside product code;
do not introduce a second user-selectable launch mode. Final qualification of the
retained host must still cover native interaction, active-work handling, recovery
and full source workflows for the completed product batch.

### Native interaction evidence (2026-10-01)

The macOS packaged host was exercised through its actual window and JavaScript
bridge using an isolated qualification copy. Only its bundle identifier/name and
bootstrap's fixed test home differed from the built application; the host and UI
implementation were unchanged. The native folder picker opened and cancelled,
then created a device-free author folder and independent Python. Registration
restarted the backend and returned to Settings with that folder selected.

A three-point synthetic experiment, with a 30-second delay per point, exercised
the active-work close dialog. Keep running in background returned successfully;
subsequent window interaction and the system Quit command remained responsive.
Cancel and Quit when work finishes worked. The client completed acquisition,
analysis and retained-result reopening, after which the host, backend and workers
exited without remaining qualification processes.

The UI inspection tool can reactivate hidden windows. This run therefore does
not independently prove hidden-window visibility or menu-bar/tray reopening.
Those interactions and Windows native interaction remain unqualified. CI's
macOS/Windows native distribution checks qualify packaging and runtime behavior,
not these UI interactions. A transient disconnection during source registration
also exposed obsolete daemon-start instructions; the UI now explains waiting for
an in-progress restart or using the application's recovery action.

### Follow-up observations (2026-10-02)

The user confirmed that macOS **Open Anyway** permits opening the downloaded
preview, and subsequently confirmed menu-bar icon visibility. These observations
do not qualify every macOS version or the full hide/reopen sequence. The Mac menu
bar now uses a dedicated monochrome Cocoa template; the application/Dock icon
remains colored. macOS window close hides; application Quit checks active work.

The Windows report of minimizing, hiding through the tray and then failing to
reopen led to window-state normalization before hiding and after showing. Actual
Windows validation of that correction is still required. The startup/recovery
pages share operation feedback and duplicate-action suppression. Workbench tests
cover slow quit, failed stop and failed cancellation of automatic quit. These are
behavioral checks, not a claim that native platform interaction is complete.

A subsequent user report exposed a Cocoa delegate ABI mismatch while installing
the Quit handler. That exception killed the startup supervisor before preparation.
The hook now preserves pywebview's existing Objective-C method signature, and
initialization failures enter the recovery page. A local isolated, hidden native
window reached the workbench, invoked the actual application-termination delegate,
and exited with its backend stopped. This checks native integration without
claiming physical menu clicks. Native distribution CI now runs the Cocoa ABI and
desktop session regressions before packaging.

Window close now hides on **both** platforms, preserving experimental work.
Explicit Quit from the tray/application menu remains the work-aware stop action.

The later missing-menu-bar report was reproduced in a packaged app: replacing
the launcher with `execve` of the embedded Python preserved the running-app name
but lost `NSBundle.mainBundle`'s bundle identifier. The status item reported
visible while its native window retained zero height after layout. The Mac host
now calls CPython's stable `Py_BytesMain` entry point in the native process and
links the bundled library through an app-relative rpath. Python subprocesses
still use the bundled interpreter. With the same image and tray code, the native
window received a real screen position and 34-point height, and the user
confirmed the icon was visible. Closing and reopening the isolated application
returned to the same backend; Cmd-Q stopped it. The distribution check now
asserts the Cocoa bundle identifier after relocation, in addition to checking
the runtime path. Object creation or `isVisible` alone is not display evidence.
