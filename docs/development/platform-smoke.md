# Thin platform smoke

Ubuntu `core` remains the ordinary fast + integration suite. Windows and macOS
run six explicit nodeids from `scripts/platform_smoke.py`, serially, with the
locked `platform-smoke` dependency group. The group omits the default development,
Notebook and delivery dependency groups; it does not build a native application.

The common selection covers SDK binary transport/cancellation/owned exit, a real
instrument worker request over TCP, selected-interpreter ownership across reopen,
shutdown of a blocked worker, and application FileLock exclusion between processes
plus actual state-file replacement. macOS adds the real pywebview Cocoa delegate
ABI. Windows adds real wheel builds, minimal author resources, uv-managed Python, product
`prepare_execution_environment`, real dependency installation/capture and a fresh
interpreter importing pyarrow. The tiny delivery fixture supplies framework wheels
and manifest metadata; it does not stub installation, capture or the interpreter.
This is environment-preparation coverage, not full offline/native qualification.

Keep normal OS settings and pytest temporary directories. In particular, do not
shorten the Windows test path, enable long paths, or replace the pyarrow import
with an installation mock to make the smoke pass. The ordinary `journey` tier owns
the real preparation test; the small file/lock test is `integration`. Existing
Ubuntu core selection and the manual acceptance profiles retain their meanings.

The smoke refuses unsupported platforms, pytest errors, incomplete collection,
any skipped/xfail case, or any failed case. Missing Cocoa dependencies fail via a
real import. Tests of the runner itself launch real passing/skipping/xfailing/
failing pytest probes. Synchronization uses process completion, markers and
bounded waits rather than elapsed-time performance assertions.

All CI entry points check out `inputs.ref || github.sha`, and the smoke verifies
that exact value against Git HEAD before testing. Pull requests test their merge
commit; merge groups test their synthetic group commit; reusable callers supply
an exact commit. There is no path filter: docs-only PRs and merge groups also run
the same maintained selection, so no path-classifier skip can claim coverage.

Each platform retains JUnit and JSON with exact checkout SHA, runner/image,
Python/OS, nodeids, passed/failed/skipped counts and elapsed test time. Actions
step timestamps include setup, dependency installation, artifact upload and cache
save; they are the basis for whole-job budgets. A cache miss is recorded as cold,
and only a confirmed cache restore counts as warm. The 10-minute timeout is a
safety limit, not an expected duration. Native distribution remains a separate
manual profile; `full` acceptance does not imply native coverage.

## Historical qualification

[PR #879](https://github.com/scopecat-project/scopecat/pull/879) introduced the
bounded selection. Its [fixed cold/warm measurements](https://github.com/scopecat-project/scopecat/blob/5c8cdc30d812fa4484d8fd1af3a5b5f30aca133e/docs/development/platform-smoke.md#initial-measurement)
preserve runner images, candidate identities and the initial unrelated formatting
failure. These are observed samples, not current latency guarantees or a broad
flakiness study. Warm-cache Mac was slower in that comparison; do not infer cache
benefit from the label alone. Investigate future failures rather than retrying
into success or silently excluding checks. Native/offline acceptance is independent.
