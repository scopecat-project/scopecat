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
