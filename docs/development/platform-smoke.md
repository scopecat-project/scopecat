# Thin platform smoke

Ubuntu `core` remains the ordinary fast + integration suite. Windows and macOS
run six explicit nodeids from `scripts/platform_smoke.py`, serially, with the
locked `platform-smoke` dependency group. The group omits the default development,
Notebook and delivery dependency groups; it does not build a native application.

The common selection covers SDK binary transport/cancellation/owned exit, a real
instrument worker request over TCP, selected-interpreter ownership across reopen,
shutdown of a blocked worker, and application FileLock exclusion between processes
plus actual state-file replacement. macOS adds the real pywebview Cocoa delegate
ABI. Windows adds real wheel builds, relocatable Python packaging, product
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

## Initial measurement

PR [#879](https://github.com/scopecat-project/scopecat/pull/879), initial source
`05a009e2c1871d6644553a2faaff40e00e462f15`, tested merge commit
`b5490428d4235089b01bf722225b211df99c6942` in
[CI 37360452148](https://github.com/scopecat-project/scopecat/actions/runs/37360452148).
Both setup-uv logs explicitly reported a cache miss. CPython was 3.14.7.

| Runner image | Cache | Whole job | pytest | Passed / skipped / failed |
| --- | --- | --- | --- | --- |
| macos-26-arm64, 20260907.0351.1 | Cold | 22 s | 6.52 s | 6 / 0 / 0 |
| windows-2025-vs2026, 20260925.250.1 | Cold | 76 s | 46.27 s | 6 / 0 / 0 |

The Windows preparation test took 37.98 s. The first CI's unrelated formatting
check found a missing blank line after the new Cocoa import; this was corrected
in `9ef19bdac20c5d418d5d26f9701a5802d49e8454`, which also adds executable tests of
the smoke's skip rejection. Do not count that initial whole workflow as passing.

The repeated run on source `9ef19bdac20c5d418d5d26f9701a5802d49e8454`, merge SHA
`f7281bd29e034529beced920376fb04765ed6de1`, confirmed cache restores on both OSes
in [CI 37360708478](https://github.com/scopecat-project/scopecat/actions/runs/37360708478):

| Runner image (same versions as above) | Cache | Whole job | pytest | Passed / skipped / failed |
| --- | --- | --- | --- | --- |
| macos-26-arm64 | Warm | 46 s | 14.95 s | 6 / 0 / 0 |
| windows-2025-vs2026 | Warm | 68 s | 38.99 s | 6 / 0 / 0 |

Windows environment preparation took 27.92 s. Warm-cache Mac was slower than the
cold sample; runner variation and cache-transfer cost are included, not hidden.
These are observed samples, not latency guarantees or a broad flakiness study.
Both cold and warm jobs finished under 80 seconds with no failure/skip and no
system/path workarounds. This supports including `platform-smoke` in the existing
`CI gate` dependencies for this bounded selection. The aggregate requires success
from both platform matrix jobs. No repository required-check/ruleset setting is
changed. Future failures must be investigated, not retried into a passing result
or silently excluded; broader native/offline acceptance remains independent.
