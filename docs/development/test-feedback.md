# Test feedback and full qualification

Use explicit tiers while editing; plain `uv run --locked pytest` still runs the
full suite. The shared runner reads `[tool.scopecat-tests]` in `pyproject.toml`:

```sh
uv run --locked python -m scopecat_testkit.check fast
uv run --locked python -m scopecat_testkit.check integration
uv run --locked python -m scopecat_testkit.check journey
uv run --locked python -m scopecat_testkit.check full
```

`fast` covers core/library tests. `integration` covers server tests except the
explicit journey files. `journey` covers reference-lab workflows, fresh-process
snapshots and validation-process diagnostics. `core` combines fast and integration
for CI. The runner pins the workspace pytest configuration and root even when
a tier contains only a nested project. These are file-level cost boundaries, not promises that every fast test
is pure or that every integration test starts a process.

Pass pytest options after `--`, for example `fast -- -n 0 -q`. Run affected test
files directly for focused development. Use `--list` to inspect a tier without
collecting tests, and `journey --shard 1/2` for one deterministic shard. Shards
keep files intact and use reviewed file-cost estimates to balance work. New
files are always included with a default weight; stale weights affect balance,
not coverage. New runtime journeys should be classified explicitly.

Every runner invocation writes ignored `.test-results/` artifacts:

- Selection JSON: source revision, tier, shard, selected files and pytest options.
- Timing JSON: exit status, wall time, collection-ready time (including worker
  startup under xdist), and each test's setup/call/teardown costs
  and outcomes. xdist reports are aggregated by the controller.
- JUnit XML plus the slowest twenty tests in terminal output.

CI enables `scripts.pytest_diagnostics` on both Linux and Windows. Each pytest
worker and the controller write a separate, flushed log under `test-diagnostics/`:
test setup/call/teardown boundaries and a thread-stack dump every 120 seconds.
Daemon startup and lifetime evidence is retained in the same artifact. These
files survive an interrupted pytest run; timing JSON and JUnit may not be complete.
A stack dump is diagnostic evidence, not a test deadline or proof of deadlock.
Use the last unmatched phase start in each worker log to identify stalled tests;
parallel console percentages do not identify the active test. Keep two workers
in CI, then reproduce a suspect test with `-n 0` and in parallel as needed.

To collect the same evidence locally:

```sh
SCOPECAT_TEST_DIAGNOSTICS=test-diagnostics uv run --locked python -m scopecat_testkit.check core -- -p scripts.pytest_diagnostics --maxprocesses=2
```

Compare phase totals separately from wall time: parallel worker times add up,
and shared-fixture setup is charged to the first test that owns it. Do not
interpret a timing sample as a benchmark or a successful run as hardware evidence.

## CI contract

Every PR, merge-group and explicit workflow dispatch runs all Python tests on
Linux and Windows: one core job and two journey shards per platform. It also
runs both measured, file-level browser E2E projects, static checks, benchmark smoke, documentation and
installed-wheel checks. The gate rejects failed or unexpectedly skipped shards.
This first step changes execution topology, not PR coverage.

UI checks/build produce the browser distribution and pilot wheels. Browser
shards and installed-wheel checks depend only on that build, so installation
verification no longer waits for the entire browser journey suite. Each browser
runner keeps one worker; Python jobs cap at two workers. More independent runners
reduce wall time but add setup/runner-minute cost; inspect both before increasing
shard counts. Browser JSON reports retain timings and any retry information.
The first browser project lists its files explicitly; the second includes all
remaining files so new tests are never silently omitted. Run one with
`pnpm exec playwright test --project=journey-1`; ordinary Playwright runs both.

A squash push to main builds and validates the formal revision's artifacts,
including static checks, UI unit checks, benchmark smoke, docs and installed
pilots. It does not repeat the Python journey matrix or browser E2E that gated
the PR. Direct unreviewed main pushes are outside the repository's PR workflow.
Use workflow dispatch when an explicit full main qualification is required.

## Choosing work during development

Start with affected tests and the appropriate tier, finish implementation and
self-review, then submit the final candidate to the full PR gate. Do not wait for
full CI after every intermediate edit. Re-run broader checks after a material
change to storage, source isolation, process ownership or execution, not merely
because a documentation line or final gitlink changed.

Private consumers use the same runner with their own classifications. A public
pin update normally needs affected consumer tests and a no-acquisition reopen
check. Cross-cutting runtime/storage changes warrant full private qualification.

The next optimization is to split the expensive author journeys: keep real
process, persistence and restart coverage in a few representative scenarios;
move input combinations and policy branches into focused tests. Change-to-risk
selection is deferred until that mapping is reviewed. Do not shorten normal
startup budgets, relax assertions, or use retries to hide flaky failures.

Slow journey phases are also product performance signals. Investigate repeated
prepare, source loading, analysis and reads before replacing them with seeded
fixtures. The [author performance baseline](author-performance.md) measures the
actual notebook prepare boundary, which direct scan execution does not cover.

## Candidate policy assertion placement

`test_typed_candidates_retain_cells_and_independent_policy` retains the real
managed-author chain: acquire, fit, stage named cells, prepare the exact candidate,
acquire independent verification data, run its policy, publish explicitly, reject
stale admission without side effects, and restore the previous default. Unknown
fit fields, receipt authority, candidate identity and unrelated-cell preservation
remain checked there.

`test_typed_candidate_policy_uses_retained_decision_and_workpoint` checks negative
policy branches through in-process HTTP and real SQLite publications. It seeds
completed measurement records through admission/executor services and registers an
explicit source bundle, without running an instrument or analysis worker. It
proves that editing a returned dataclass cannot override a retained rejection,
that rejection remains inspectable, and that both the ordinary client and verified
publication endpoint reject another workpoint. Source capture/execution is covered
by the real journey; this fixture does not claim to validate managed analysis
execution or source validation. No production deadlines, retry rules or CI
selection change with this split.
