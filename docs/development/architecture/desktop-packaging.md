# Ready-to-run desktop packaging

PR 1 of the post-#828 milestone must deliver a ready-to-run public application.
Changing implementation language is not itself acceptance. This is a selection
gate within that product PR, not a separate packaging product or a completed
replacement of the existing desktop host.

The baseline is a relocatable CPython runtime with dependencies prepared at build
time. Rust/Tauri is the preferred host candidate to evaluate against the existing
UI and Python scientific components. PyInstaller is an optional fixed-component
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

The selected implementation must remove application-startup environment creation
and candidate selection, preserve the work-aware quit/background contract, and
keep user Python independent of replaceable application files. Data initialization
and explicit current-format checks are normal runtime work, not dependency setup.

## Implementation boundary

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

The packaged-CPython qualification passes on both native distribution runners.
A disposable macOS Tauri prototype also exercised explicit backend start,
hide/reopen with the same endpoint, and clean quit. These establish feasibility,
not the host decision: Windows native interaction, active-work handling, failure
recovery, packaging and full source workflows still require qualification. Keep
the prototype outside product code until one integrated host is selected; do not
introduce a second user-selectable launch mode.
