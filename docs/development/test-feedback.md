# Test feedback and full qualification

Use explicit tiers while editing; plain `uv run --locked pytest` still runs the
full suite. The shared runner reads `[tool.scopecat-tests]` in `pyproject.toml`:

```sh
uv run --locked python -m scopecat_testkit.check fast
uv run --locked python -m scopecat_testkit.check integration
uv run --locked python -m scopecat_testkit.check journey
uv run --locked python -m scopecat_testkit.check full
```

`fast` covers core/library tests and reference scientific/compiler unit tests.
Explicit `fast_paths` take precedence over broader journey/integration prefixes;
the reference unit directory uses this after its automatic daemon fixture was
removed. Reference workflows still use the journey tier. `integration` covers
server tests except the explicit journey files. `journey` covers reference-lab workflows, fresh-process
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

## CI contract during the architecture transition

[CI](https://github.com/scopecat-project/scopecat/blob/main/.github/workflows/ci.yml)
is the required PR gate during the redesign tracked in
[#610](https://github.com/scopecat-project/scopecat/issues/610), building on the
feedback work in [#520](https://github.com/scopecat-project/scopecat/issues/520).
Every PR, merge-group, main push and explicit CI dispatch runs:

- Linux Python `core` (all fast and integration files), with two pytest workers,
  diagnostics and the pandas adapter check;
- Python typing, import boundaries, lint, formatting and generated instruments;
- UI API generation, formatting, lint, unit tests, typing and production build;
- strict documentation/link checks.

The required check remains **CI gate**. Every listed job must succeed; failure,
cancellation and unexpected skips fail the gate. There are no path filters,
soft failures or retries that turn failures into success. New tests still follow
the existing file-tier classification; plain pytest remains the full suite.

The feedback target is **under five minutes from the first job starting until
CI gate completes, excluding time waiting for a runner**. It is a measured target,
not a five-minute timeout or a reason to cancel a slow valid test. Record the run
URL, revision and slowest job before claiming the target is met. Cold dependency
caches and runner variation may exceed it; tune the bottleneck rather than masking
failures. Timing and startup artifacts remain available on failed runs.

[Full acceptance](https://github.com/scopecat-project/scopecat/blob/main/.github/workflows/acceptance.yml)
is a separate manual workflow. It retains the full Linux/Windows Python matrix,
both browser shards, benchmark smoke, isolated wheel imports, offline installation and every shipped
teaching Notebook, alongside static/UI/docs checks. Its default `full` profile requires every job to succeed at the
**Acceptance gate (full)**. These checks were moved, not deleted or silently
reported as passing by the fast gate. Successful fast CI does not qualify a release,
Windows operation or installed/offline delivery.

The explicit `local-application` profile reuses the UI build, both browser shards,
and installed Windows/Linux pilot and offline Notebook checks. It skips the broad
Python matrix, benchmark and duplicate static checks; those expected skips are
checked by **Acceptance gate (local-application)**, while every selected job must
succeed. Use it with successful fast PR CI for a bounded experimental installation
trial. Record the exact scope and unresolved qualification in #616; it does not
establish the full architecture milestone, a supported data baseline or release
readiness. Platform artifacts contain the offline bundle, executed notebooks,
acceptance report and retained lifecycle logs, including on failure.

Run the `full` profile at architecture milestones and before publishing a release. Select the intended branch/ref
in Actions and record the resulting exact revision and run URL in the parent issue.
The manual workflow executes that checkout, without changing a private repository's
Actions settings. A later code change needs corresponding validation; an older
successful run does not qualify the new revision.

| Change or milestone | Required evidence beyond the common gate |
| --- | --- |
| Storage/identity | Focused current-format, rejection-without-mutation and frozen-request checks; actual current-format backup/restore journey before the milestone closes. No prebaseline migration gate; see [data policy](data-compatibility.md). |
| Worker/code loading/resource ownership | Relevant process, restart, cancellation and resource-exclusion journeys on the PR's revision |
| Wire/UI consumer | Regenerate from the producer; focused component/payload checks and the affected browser journey |
| Installation/tutorial changes | Affected installed/offline checks on the changed platform; Windows/Linux installed profile before a bounded experimental trial; full acceptance before release |
| Architecture milestone or release | Full acceptance on the integrated revision; link results and unresolved limitations |

Scientific data identity, immutable requests, admission idempotency, batch
applicability, resource exclusion and failure cleanup remain contracts throughout
this transition. UI text, navigation and old deployment assumptions may be replaced
with the new design, but name the replacement evidence in the issue/PR. Do not
delete old integration scenarios merely because they are outside the common gate.
The parent issue tracks when to reconsider this temporary gate after integration.

## Choosing work during development

Start with affected tests and the appropriate tier, finish implementation and
self-review, then submit the candidate to the fast PR gate. Record the focused
checks and any deferred milestone qualification in the PR. Do not run a full
matrix after every intermediate edit. Re-run relevant broader checks after a
material change to storage, source isolation, process ownership or execution,
not merely because a documentation line changed.

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
acquire independent verification data and run its policy. It starts with equipment
only and selects a parameter branch. Advancing that branch leaves a prepared
candidate unchanged; verification changes neither the branch nor the lab default. Unknown
fit fields, receipt authority, candidate identity and unrelated-cell preservation
remain checked there.

`test_drag_candidate_retains_science_without_default_publication` retains actual
DRAG acquisition, fit/figure/report, candidate acquisition and cross-run decision
with independent parameter/setup inputs. It replaces the retired DRAG gallery's
scientific assertions without requiring global-default publication and restore.
Legacy publication fences remain in `test_project_analysis_runtime.py`; these
are not evidence that verified publication to parameter branches exists.

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
