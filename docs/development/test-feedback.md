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
for CI. These are file-level cost boundaries, not promises that every fast test
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

Compare phase totals separately from wall time: parallel worker times add up,
and shared-fixture setup is charged to the first test that owns it. Do not
interpret a timing sample as a benchmark or a successful run as hardware evidence.

## CI contract

Every PR, merge-group and explicit workflow dispatch runs all Python tests on
Linux and Windows: one core job and two journey shards per platform. It also
runs both browser E2E shards, static checks, benchmark smoke, documentation and
installed-wheel checks. The gate rejects failed or unexpectedly skipped shards.
This first step changes execution topology, not PR coverage.

UI checks/build produce the browser distribution and pilot wheels. Browser
shards and installed-wheel checks depend only on that build, so installation
verification no longer waits for the entire browser journey suite. Each browser
runner keeps one worker; Python jobs cap at two workers. More independent runners
reduce wall time but add setup/runner-minute cost; inspect both before increasing
shard counts. Browser JSON reports retain timings and any retry information.

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
