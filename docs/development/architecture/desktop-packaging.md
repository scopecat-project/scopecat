# Ready-to-run desktop packaging

The desktop package contains a relocatable CPython runtime with dependencies
prepared at build time. Ordinary launch uses that fixed runtime without installing
packages or writing application files. Editable author code and vendor environments
remain independent. The [host decision](desktop-product.md#technology-decision)
retains CPython, pywebview and the existing web UI.

## Maintained installation entries

| Installation / acceptance owner | Included entry and boundary |
| --- | --- |
| Native application (`native-distribution`) | `native_bootstrap` starts the packaged host; `scopecat-lab-tools` owns the environment's `scopecat` console. Application launch uses the packaged runtime. |
| Minimal installed framework (`installed-artifacts`) | `verify_installed_framework.py` installs only core/server/instruments/quantum from standard `build_preview.py` artifacts. It uses the matching independent GUI through `--static-dir`, without application tools, testkit or checkout imports. This is a dependency/packaging test, not a separate desktop product. |
| Teaching/offline verification | Existing `scopecat-lab`, generated VS Code tasks and standalone `install.py` remain; `bundle.py` retains manifest/hash checks and offline environment installation. |

The application-tools wheel owns the `scopecat` console. Server-only consumers use
the module entry; see [CLI ownership](../../reference/cli.md#entry-ownership-and-migration).

## Coverage by consumer boundary

Windows desktop is the primary user journey. The current Mac distribution remains
supported by its existing preview entry; this cleanup does not change platform
promises or reduce existing platform coverage. Internal validation carriers are
not additional desktop products.

| Boundary | Current coverage and artifact | Unique failure surface |
| --- | --- | --- |
| Source and browser | Regular CI; Linux browser shards in `full` / `local-application` / `browser` | API, UI interactions and source regressions; not installed-package evidence |
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

The standard artifact carries framework wheels and a separate GUI ZIP. The old
pilot builder and GUI-embedded server package are retired; the `bundle.json`
offline payload remains maintained. Installed checks validate commit/hash, served
GUI bytes and version without checkout imports. Repeating scientific assertions
at this boundary catches wheel discovery and same-version source-byte changes.

Native data export/import does not qualify the complete installed configuration
exchange and source-registration journey. Track that distinction in
[#502](https://github.com/scopecat-project/scopecat/issues/502).

## Teaching intent and acceptance limits

Help supports Notebook/application collaboration through shared preparation and
Continue for all seven supplied topics. It preserves editable source and retained
results. Course ordering and remaining standalone lifecycle consumers belong to
[#565](https://github.com/scopecat-project/scopecat/issues/565); actual editor use and
unfamiliar-user observations belong to
[#616](https://github.com/scopecat-project/scopecat/issues/616).

Source/browser/kernel owners are listed in
[test feedback](../test-feedback.md#teaching-and-author-entry). Installed coverage
has two additional boundaries:

- `verify_teaching_delivery.py` checks offline installation, console/editor-task
  entry, wrong-kernel rejection and headless continuation on Linux/Windows.
  Installed Help and grouped recovery pass before duplicate standalone grouped
  stages are omitted. Default-course, editing and snapshot checks retain their
  own consumers. Application-install cache reuse does not replace empty-cache
  author and recovery preparation.
- `verify_installed_help_kernels.py` installs the full toolchain at a fresh location,
  prepares author environments offline with separate empty caches, and executes
  shipped parameters/groups through Help in real kernels. Fresh kernels reject the application interpreter; after application restart they
  read exact retained results without acquisition. Run
  `python scripts/verify_installed_help_kernels.py <toolchain-payload> <fresh-dir>`.

The installed Help verifier also restores a stopped-service snapshot at a new
location with old source/environment paths unavailable. It explicitly registers
an independently backed-up author fixture with its original source ID, then
checks grouped results and new analysis. Application snapshots do not restore
external editable source or Help Continue. Only after recovery succeeds and the
daemon stops are retired generated environments and their author cache discarded;
source, scientific data, snapshots and recovered environments remain. Earlier
failure preserves diagnostic environments. Before/after sizes are retained bytes,
not peak footprint or a speedup claim. This maintainer check adds no per-PR gate.

Automated teaching checks complement platform/fault/recovery checks. They do not
establish learner comprehension or physical scientific correctness.

<span id="required-comparison"></span>

## Composition evidence

[PR #845](https://github.com/scopecat-project/scopecat/pull/845) records the
composition decision. Reuse accepted observations unless a changed interaction or
new failure requires them again. This is not a recurring host comparison.

| Journey | Maintained evidence | Separate observation |
| --- | --- | --- |
| Install, export, external analysis, import in an empty app, remove application | `verify_native_application.py` on Mac/Windows; empty PATH, empty uv cache and offline dependency preparation for the bundled example | Real file dialogs and window lifecycle accepted in PR #837 |
| Ordinary source, independent execution Python, source edit and reopen | Installed consumer evidence linked from PR #845; repeat affected consumers for dependency updates | Actual editor selection and physical-device output |
| Optional extension wheel and execution-environment replacement | Installed-adapter journey: no adapter in application Python, explicit driver activation, retained source/analysis/snapshot, source package loss does not block application | Laboratory-specific extension maintenance |
| Two distinct app builds, failed replacement/retry and retained measurement | `verify_native_replacement.py`, same current scientific-data format only | User interaction with a changed installer, if applicable |
| Development preview and exit ownership | Application lifecycle tests; isolated home, no automatic browser launch | No need to reopen the daily app for routine tests |

Unfamiliar-user comprehension and physical scientific correctness remain separate
acceptance items. Neither headless checks nor maintainer familiarity closes them.
There is no supported persistent-data baseline yet; these checks add no migration
or arbitrary old-environment support promise.

Native window readiness, backend readiness and failure reporting are separate.
Measure first-use and reopen costs separately, with OS/cache context. Distinguish
package/installed size from duplicate payload, and host/WebView/controller/backend
memory; summed RSS is not unique physical memory. Retain diagnostics, process
ownership and clean shutdown evidence on both platforms.

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

The pending runtime-registration record is a crash-recovery journal. A newly
verified package may complete it after replacement and recreate its derived receipt
from current qualification; it need not reinstall the original package or read
historical receipt formats. Scientific-data readers are unaffected. The retained
standalone installer prepares offline environments, not desktop applications.

## Implementation boundary

The [host decision](desktop-product.md#technology-decision) retains CPython,
pywebview and the existing web UI. It records the reasons and reconsideration
criteria; packaging uses that single selected runtime.

The installed package owns one fixed application runtime. Its executable starts
that runtime directly; a previous installation's selected interpreter must not
override it. Application updates replace application files after the owned
processes exit. They do not prepare or select another Python environment inside
the data directory.

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

Current native interaction evidence is linked from the
[desktop product contract](desktop-product.md#evidence) and
[native window acceptance](../native-window-acceptance.md).
