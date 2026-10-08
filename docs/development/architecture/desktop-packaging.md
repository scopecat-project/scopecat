# Ready-to-run desktop packaging

PR #829 delivered ready-to-run installation; PR #837 completed the bounded
multi-window, file and lifecycle product gate on Mac and Windows. The
[desktop product decision](desktop-product.md#technology-decision) retains the
Python/WebView host. PR #844 separated author execution and vendor environments.
PR #845 qualified these capabilities together and closed obsolete delivery paths;
it does not reopen host selection or expand into a full GUI redesign.

The package contains a relocatable CPython runtime with dependencies prepared at
build time. Editable author code and vendor environments remain independent.

## Maintained installation entries

| Installation / acceptance owner | Included entry and boundary |
| --- | --- |
| Native application (`native-distribution`) | `native_bootstrap` starts the packaged host; `scopecat-lab-tools` owns the environment's `scopecat` console. No host/runtime process redesign accompanies CLI ownership. |
| Minimal installed framework (`installed-artifacts`) | `verify_installed_framework.py` installs only core/server/instruments/quantum from standard `build_preview.py` artifacts. It uses the matching independent GUI through `--static-dir`, without application tools, testkit or checkout imports. This is a dependency/packaging test, not a separate desktop product. |
| Teaching/offline verification | Existing `scopecat-lab`, generated VS Code tasks and standalone `install.py` remain; `bundle.py` retains manifest/hash checks and offline environment installation. |

CLI ownership has moved from the server wheel to the application-tools wheel.
A server-only consumer must replace `scopecat COMMAND` with the module entry;
complete application consumers retain their console commands. Installing only
server dependencies does not install windows, teaching tools or their commands.
See the [CLI migration](../../reference/cli.md#entry-ownership-and-migration).

## Coverage by consumer boundary

Windows desktop is the primary user journey. The current Mac distribution remains
supported by its existing preview entry; this cleanup does not change platform
promises or reduce existing platform coverage. Internal validation carriers are
not additional desktop products.

| Boundary | Current coverage and artifact | Unique failure surface |
| --- | --- | --- |
| Source and browser | Regular CI; Linux browser shards in `full` / `local-application` | API, UI interactions and source regressions; not installed-package evidence |
| Minimal framework installation | Linux/Windows `installed-artifacts`; four-wheel subset of standard preview plus GUI ZIP | No checkout imports, Node, reference-lab, testkit or lab-tools; CLI/scaffold, real GUI bytes/assets/version, installed/local author discovery, source-byte identity and durable restart |
| Offline installer and teaching consumers | Same Linux/Windows job; platform delivery assembled with `--public-artifacts` | Archived `bundle.json`/standalone `install.py`, empty-cache offline install, installed public console, retained notebook and generated editor-task callers, application reopen |
| Desktop package | Separate Mac/Windows `native-distribution` profile | Native entry, relocation, empty PATH, unchanged application files, independent author Python and platform installer behavior |
| Public artifact consumer | Immutable `preview.json`, wheels and GUI; downstream consumer pins | Source commit and package/hash identity; a downstream pin is not evidence for a newer framework candidate |
| Release assembly | `release-publish` builds standard artifacts once, reuses them for platform delivery/native packaging | Matching release identity and qualified installer inventory; not invoked by ordinary CI |
| Human and hardware observations | Separate outstanding acceptance | Actual editor use, unfamiliar users and physical devices; automated synthetic journeys do not close these gates |

`full` and `native-distribution` are mutually exclusive profiles. The shared
standard artifact is built once within `full` / `local-application`; browser and
installed checks consume its GUI, and platform delivery reuses its wheels.
The offline archive is an internal validation carrier, not an additional desktop
product commitment. Calling course code a fixture describes its current maintenance
form; it does not retire the teaching requirement. Existing generated teaching callers are retained;
retiring them or narrowing their platform matrix requires an explicit consumer
replacement and supported-platform decision.

The old pilot builder, its four-package `manifest.json` and GUI-embedded server
wheel/sdist are retired. Standard server wheels remain unchanged: GUI is a separate
ZIP. Acceptance replaces the old embedded-resource assertions with commit/hash,
served index and asset byte equality, and `build-info.json` version checks. The
minimal environment still excludes the other three wheels in the seven-package
standard artifact. Do not confuse this retired pilot format with the maintained
`bundle.json` offline payload.

Matching scientific assertions at source and installed boundaries are intentional:
the latter catches wheel discovery and same-version byte changes. Similarly,
standalone offline installation and native offline startup fail at different
boundaries. Neither native data export/import nor these checks qualifies the full
installed configuration-exchange/source-registration/environment journey.

## Teaching intent and acceptance limits

The teaching goal is Notebook and application collaboration, not management of
per-course backend services. Removing that manager did not complete or cancel the
learning journey. Help's manual-peak practice is a delivered bounded capability;
it does not replace the seven authored topics or their editable source. Follow
[the existing teaching issue #565](https://github.com/scopecat-project/scopecat/issues/565)
for remaining course design and fixture-consumer work, separately from
human/device observations in #616.

Help parameters now provides the representative desktop → Notebook → editable
source → application results → restart/Continue journey (#875/#877). Default and
topic generation share editable resources (#880). All seven topics now share
Help preparation/continuation (#913); #918 preserves course selection and links
Settings to the exact author folder. Remaining standalone lifecycle consumers
and teaching design are tracked in #565.

Current automated evidence has distinct boundaries:

- `lab_tools.verify` executes shipped start/reopen Notebooks in separate kernels,
  adding checks to evidence copies. Editing and grouping now execute shipped
  Notebooks with separate evidence checks (#903/#904), including real kernels
  and retained-result reopening after restart.
- `test_calibration_teaching_journey.py` runs shipped binding/cells through IPython
  and restarts the service. #870 fixed the implicit import refresh that rejected
  retained intent classes; calibration/joint-calibration also passed real ipykernel
  execution. That resolved defect is documented in the PR, not a current blocker.
- `verify_teaching_delivery.py` checks offline installation, console/editor-task
  entry, wrong-kernel rejection and headless continuation.
- `verify_notebook_journey.py` uses real browser/kernel execution and verifies
  generated material identity, edits, restart and Continue without reacquisition.
  External editor/window activation is substituted; actual native editor use and
  unfamiliar-user comprehension remain separate observations in #616.
- `verify_installed_help_kernels.py` installs an existing full toolchain payload
  into a fresh location and runs shipped parameters/groups cells through Help's
  same-application preparation path. Installation and author preparation start
  with separate empty caches and offline resolution; neither provisioning nor
  kernels are substituted. Fresh kernels reject the application interpreter,
  then read exact retained results after application restart without acquisition.
  Run `python scripts/verify_installed_help_kernels.py <toolchain-payload> <fresh-dir>`.
  This is a targeted maintainer check, not an additional per-PR platform gate.
  It preserves the old delivery/snapshot fixtures: browser/native interaction,
  legacy editor tasks, default-course analysis, compute/refresh and snapshot
  relocation assertions are outside this bounded proof.

Teaching checks complement fault/recovery/platform checks. They do not establish
that a person can understand the material or operate real devices.

## Composition evidence

These are the criteria used for the recorded host decision, not a requirement to
benchmark another host in every release.

PR #845 used the following composition boundary. Reuse the earlier accepted native
window observations; rerun them only for a changed interaction or a new failure.

| Journey | Maintained evidence | Separate observation |
| --- | --- | --- |
| Install, export, external analysis, import in an empty app, remove application | `verify_native_application.py` on Mac/Windows; empty PATH, empty uv cache and offline dependency preparation for the bundled example | Real file dialogs and window lifecycle accepted in PR #837 |
| Ordinary source, different SDK Python, manual decisions, source edit, kill/reopen and practice cleanup | PR #844 installed private consumer evidence; repeat affected consumers when the public pin changes | Actual VS Code selection and real vendor SDK/output |
| Optional extension wheel and execution-environment replacement | Installed-adapter journey: no adapter in application Python, explicit driver activation, retained source/analysis/snapshot, source package loss does not block application | Laboratory-specific extension maintenance |
| Two distinct app builds, failed replacement/retry and retained measurement | `verify_native_replacement.py`, same current scientific-data format only | User interaction with a changed installer, if applicable |
| Development preview and exit ownership | Application lifecycle tests; isolated home, no automatic browser launch | No need to reopen the daily app for routine tests |

Unfamiliar-user comprehension and physical scientific correctness remain separate
acceptance items. Neither headless checks nor maintainer familiarity closes them.
There is no supported persistent-data baseline yet; these checks add no migration
or arbitrary old-environment support promise.

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
Qualified artifacts include `native-release.json` and `bundle.json` alongside the
installer. They bind its SHA-256 to source, target, runtime identity and inventory;
archive them together before Actions retention expires. Native acceptance also
exports a real measurement, analyzes it in the independent client with the
application stopped, and imports/saves it in a fresh application without author
code or execution environments. `data-journey.json` records that composition;
it does not stand in for human file-dialog or editor observations.
These are development previews. macOS uses ad-hoc signing, without Developer ID
or notarization; Windows installers are unsigned.

Mac packaging signs embedded Mach-O files individually, inside out, before
sealing the outer application. It refreshes the embedded delivery checksum for
the signed toolchain executable before sealing; the input delivery is unchanged.
The package and relocated running application must pass signature checks,
including Python extensions outside standard nested-code directories.
`scripts/verify_macos_download.py INSTALLER REPORTS` mounts the shipped DMG
read-only, checks its actual application, applies quarantine to the disposable
copy, records Gatekeeper's assessment, and proves that editing a sealed resource
fails signature verification. The fresh reports directory retains `report.json`;
disposable app copies are removed even after failure unless `--keep-work` is set.
CI retains `macos-download/report.json`.
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
offline dependency isolation, qualify user environments or replace native
installation acceptance. A ready backend alone is not a finished desktop application.

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

The [host decision](desktop-product.md#technology-decision) retains CPython,
pywebview and the existing web UI. It records the reasons and reconsideration
criteria; packaging uses that single selected runtime.

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
environment; ordinary application launch may not. Vendor runtime isolation uses
the [SDK process protocol](sdk-process.md).

Generated user environments install only `scopecat`, `ipykernel` and their
dependency closure from the verified offline wheels, constrained to the delivery's
versions. They do not install the server, desktop or teaching packages. Author
revisions record import dependencies separately from execution dependencies:
notebook refresh checks the former; backend recovery still checks the latter.
Explicit author dependencies remain required on both sides. Workspaces without
an explicit dependency declaration retain their full captured environment contract.

## Historical packaging observations

The [original packaging record](https://github.com/scopecat-project/scopecat/blob/53a74eaae7d2195fa4430eda7d737506d13da9fd/docs/development/architecture/desktop-packaging.md#native-interaction-evidence-2026-10-01)
retains the native host/tray/exit investigations, failures and dated observations.
Current interaction evidence is linked from the [desktop product contract](desktop-product.md#evidence)
and [native window acceptance](../native-window-acceptance.md).
