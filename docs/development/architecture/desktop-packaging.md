# Ready-to-run desktop packaging

The desktop package contains a relocatable CPython runtime with dependencies
prepared at build time. Ordinary launch uses that fixed runtime without installing
packages or writing application files. Editable author code and vendor environments
remain independent. Native assembly consumes a full delivery as an intermediate,
then ships a format-2 author resource manifest with only `scopecat` and
`scopecat-server` wheels, GUI, constraints, a pinned Python recipe and uv tools.
It does not retain the Python archive or third-party wheelhouse. The generic
format-1 offline delivery and its installer remain supported. Author preparation
uses independent uv-managed or explicit Python and downloads third-party packages;
application launch remains offline. The recipe is probed from the actual packaged
interpreter and must match the delivery target. Selecting an author base checks
version and ownership; resolution, installation and worker capture validate
dependencies separately. This is not a blanket native ABI guarantee. Execution
cache identity includes the base binary, shared library (when present), version
and ABI, so replacing an interpreter at the same path creates a fresh candidate.
The source host rebuilds missing/corrupt native wheel caches on startup and repairs
its extracted dependency cache from the verified wheel. The [host decision](desktop-product.md#technology-decision)
retains CPython, pywebview and the existing web UI.

## Exact build runtime

`.python-version` is the single exact CPython patch/variant baseline for CI and
packaging. Build entry points reject host patch or variant drift. The uv version
is selected by `uv.lock`; no minor or free-threaded fallback is permitted. SDK
support and author recovery/default policies are unchanged.

Delivery preparation obtains a clean uv-managed runtime before building wheels
or preparing dependencies. Explicit `--python` paths and disabled implicit Python
downloads bind those steps to that same relocated base. Its pristine archive is
created before dependencies are installed; no development venv is copied. Native
assembly reuses that archive and installs the locked wheels offline. The retained
`app_build.toolchain` CLI copies a prepared delivery; old wheel-only deliveries
must be rebuilt before native packaging, rather than selecting another runtime late.

`toolchain/runtime.json` records the exact uv catalog URL/key, distribution BUILD,
platform/architecture/variant, uv version and SHA-256 of the portable archive.
The same metadata survives in the native manifest's `build_tools.portable_python`.
The archive hash identifies the retained relocated runtime, not the upstream
compressed download. uv checks downloads using its pinned catalog. A locked cache beneath the uv cache
directory retains the pristine archive and file hashes, keyed by tool bytes, lock,
version and target; every reuse verifies hashes and probes a fresh extraction.
Custom catalog/mirror overrides are rejected during acquisition.
Supply failures report the requested identity before GUI/dependency preparation.
Updates require Windows native installer/relocation/import acceptance and existing
Mac packaging/libpython checks; author import smoke alone is insufficient.

## Maintained installation entries

| Installation / acceptance owner | Included entry and boundary |
| --- | --- |
| Native application (`native-distribution`) | `native_bootstrap` starts the packaged host; `scopecat-app` owns the environment's `scopecat` console. Application launch uses the packaged runtime. |
| Minimal installed framework (`installed-artifacts`) | `verify_installed_framework.py` installs only core/server/instruments/quantum from standard `build_preview.py` artifacts. It uses the matching independent GUI through `--static-dir`, without application tools, testkit or checkout imports. This is a dependency/packaging test, not a separate desktop product. |
| Installed teaching verification | Help and CLI-created ordinary author source run through the selected installed runtime and verified format-2 resources; author preparation remains online. The separate format-1 `install.py` regression retains manifest/hash checks and offline environment installation. |

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
| Minimal framework installation | Linux/Windows `installed-artifacts`; four-wheel subset of standard preview plus GUI ZIP | No checkout imports, Node, reference-lab, testkit or scopecat-app; CLI/scaffold, real GUI bytes/assets/version, installed/local author discovery, source-byte identity and durable restart |
| Installed teaching consumer | Linux/Windows `installed-artifacts` in `full`, `local-application` or manual `teaching`; installed runtime and format-2 resources are archived and relocated | Windows uses the existing native builder; Linux uses an explicitly marked homologous runtime fixture, not a native application. Product import origins, retained Notebooks, ordinary CLI author preparation, application reopen and recovery are checked after the original runtime path is made unavailable. |
| Legacy offline installer | Separate step in the same installed-artifacts job; complete format-1 materials remain build inputs | `install.py` offline regression is retained independently of the installed-runtime teaching journey. |
| Desktop package | Separate Mac/Windows `native-distribution` profile | Native entry, relocation, empty PATH, unchanged application files, independent author Python and platform installer behavior |
| Public artifact consumer | Immutable `preview.json`, wheels and GUI; downstream consumer pins | Source commit and package/hash identity; a downstream pin is not evidence for a newer framework candidate |
| Release assembly | `release-publish` builds standard artifacts once, reuses them for platform delivery/native packaging | Matching release identity and qualified installer inventory; not invoked by ordinary CI |
| Human and hardware observations | Separate outstanding acceptance | Actual editor use, unfamiliar users and physical devices; automated synthetic journeys do not close these gates |

`full` and `native-distribution` are mutually exclusive profiles. The shared
standard artifact is built once within `full` / `local-application`; browser and
installed checks consume its GUI, and platform delivery reuses its wheels.
The manual `teaching` profile selects only the UI artifact producer and the two
installed-artifacts jobs; it does not enable continuous full acceptance.
The archived installed runtime is an internal validation carrier, not an additional
desktop product commitment. Format 1 remains the build intermediate and separate
offline-installer regression input. Calling course code a fixture describes its
current maintenance form; it does not retire the teaching requirement. Narrowing installed teaching
coverage requires an explicit consumer replacement and supported-platform decision.

The standard artifact carries framework wheels and a separate GUI ZIP. The old
pilot builder and GUI-embedded server package are retired; the `bundle.json`
offline payload remains maintained. Installed checks validate commit/hash, served
GUI bytes and version without checkout imports. Repeating scientific assertions
at this boundary catches wheel discovery and same-version source-byte changes.

Native data export/import does not qualify the complete installed configuration
exchange and source-registration journey. Track that distinction in
[#502](https://github.com/scopecat-project/scopecat/issues/502).

## Release asset boundary

Future release installers use `Scopecat-VERSION-PLATFORM-ARCH.dmg` / `.exe`,
with `macos` / `windows` and normalized `arm64` / `x86_64` from the qualified
target. Assembly rejects names that disagree with that target. This naming does
not add a new supported platform or architecture.

`release_assets.py assemble` checks the complete build inputs. `export --output`
creates a fresh publication directory containing two installers, seven wheels,
`preview.json`, `scopecat-ui.zip` and `release.json` (12 assets). The four native
qualification documents are embedded verbatim in `release.json`; verification
rechecks their source, target, resource inventory and checksums after export.
Separate qualification files remain in Actions evidence. `release.json` retains
the format-1 `preview` and `files` contract; `files` lists only downloadable assets.
The added `installers` index exposes target and checksum without opening evidence.

The preview manifest, UI archive and seven-wheel contract remain required by
existing pinned artifact consumers and installed-framework qualification. Native
applications only retain the two core/server author wheels, but that projection
is not the complete downstream build/test contract. Removing the other assets
requires a consumer migration first.

All seven first-party wheels released together use `release.toml` as their
version authority ([#1030](https://github.com/scopecat-project/scopecat/issues/1030)).
Formal versions are clean PEP 440: `0.1.0`, `0.1.0a2`, `0.1.0b1` or `0.1.0rc4`.
Development builds use `<release>.dev<commit timestamp>+g<full commit>`; rebuilding
the same commit keeps its version. Build numbers and full commit/content hashes
remain in manifests rather than the formal wheel version. UI versions are unchanged.
The builder stamps source metadata before building; first-party runtime dependencies
and extras select the exact same batch. Third-party constraints and downstream
adapter versions retain their own policies.

The rollback from historical 0.2 package bases to the application's 0.1 series is
intentional. Install into a fresh environment or explicitly select the new exact
versions/hashed recipe; an unqualified upgrade cannot express a downgrade. Author
environments remain content-addressed and recipe installs retain exact wheel hashes.
Separate releases may introduce independent versions later. This does not publish
to PyPI or modify historical releases (including v0.1.0 assets and tag).

## Teaching intent and acceptance limits

Help supports Notebook/application collaboration through shared preparation and
Continue for the supplied topics. It preserves editable source and retained
results. Course ordering and learning design belong to
[#565](https://github.com/scopecat-project/scopecat/issues/565); actual editor use and
unfamiliar-user observations belong to
[#616](https://github.com/scopecat-project/scopecat/issues/616).

Source/browser/kernel owners are listed in
[test feedback](../test-feedback.md#teaching-and-author-entry). Installed coverage
has two additional boundaries:

- `verify_teaching_delivery.py APP EVIDENCE --application-layout` checks an existing
  `resources/python` runtime and `resources/payload` format-2 resources. Alternatively,
  pass the payload with `--runtime-python PYTHON`. The verifier invokes the installed
  Help checks below, then uses the interpreter recorded in that same Help receipt
  to run `scopecat_app.public_cli` and prepare ordinary editable author source.
  It does not reinstall the application or generate separate service/editor tasks.
  Practice, author and recovery preparation may access the network; format 2 is
  not an offline dependency cache. A bare format-1 input retains the legacy installer
  adapter, whose offline installation evidence is recorded separately.
- `verify_installed_help_kernels.py` prepares independent author Python environments
  from minimal format-2 resources and executes `parameters` and `groups` by default.
  These checks retain restart, repair, wrong-kernel rejection and snapshot recovery;
  after application restart they read exact retained results without acquisition.
  Run `python scripts/verify_installed_help_kernels.py PAYLOAD EVIDENCE --runtime-python PYTHON`
  for the installed-runtime path. The target interpreter runs the checks with `-I`,
  verifies format 2 and records the import locations of core/server/app/tutorials.
  Receipts identify the actual interpreter, payload format/hash and runtime/source
  metadata; they do not claim this already-installed runtime was installed offline
  by the verifier. The format-1 `build_id` is optional compatibility metadata.
  Omitting `--runtime-python` retains the legacy format-1 installation adapter.
  Add `--all-topics` to also execute the remaining generator-declared topics in
  real kernels. The default teaching invocation does not pass that flag; its
  success is not evidence that every topic ran. Inspect the receipt's
  `installed_help_topics` for the actual selection.

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

Generated author client environments request the verified `scopecat` wheel,
`ipykernel` and any declared workspace dependencies. Shared dependencies are
constrained by `requirements.lock`; third-party packages normally come from online
indexes or an existing cache. The client baseline does not add server, desktop or
teaching packages. Execution preparation separately requests both verified
`scopecat` and `scopecat-server` wheels plus declared experiment dependencies,
using the same shared constraints. Both paths install from a generated hashed lock;
format 2 does not supply their complete offline dependency closure. Explicit
offline preparation requires the needed packages to be available locally.

Author revisions record import dependencies separately from execution dependencies:
notebook refresh checks the former; backend recovery still checks the latter.
Explicit author dependencies remain required on both sides. Workspaces without
an explicit dependency declaration retain their full captured environment contract.

Current native interaction evidence is linked from the
[desktop product contract](desktop-product.md#evidence) and
[native window acceptance](../native-window-acceptance.md).
