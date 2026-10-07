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
files in a classified directory are included with a default weight; stale weights
affect balance, not coverage. Files outside classified paths fail selection,
including `full` selection, until assigned a tier. Lab/application tests use
file-level classification so a new runtime journey cannot silently become fast.
Library directories retain their fast classification and server directories their
integration classification; authors must still explicitly promote expensive
server workflows to journey. Classification does not infer cost from filenames.

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

## Coverage and CI

The [historical timing sample](https://github.com/scopecat-project/scopecat/blob/53a74eaae7d2195fa4430eda7d737506d13da9fd/docs/development/test-feedback.md#september-24-calibration-qualification-split)
records the original tier split; current optimization work belongs to #520.

| Invariant | Ordinary PR coverage | Full qualification |
| --- | --- | --- |
| Real task → acquisition → analysis → candidate → verified publication | `test_calibration_smoke.py`, two targets, real workers and restart | `test_array_maintenance.py`, six targets and three readout groups |
| Negative verification cannot publish; exact candidate references and unknown outcomes | `packages/scopecat/tests/api/test_procedures.py`; server `test_calibration_task_finalization.py` | Array drift/rejection scenario |
| Task restart, current-format restore, failed prerequisites and admission | Server `test_calibration_check_admission.py` and `test_calibration_task_finalization.py` | Array restart and readout-failure scenarios |
| Concurrent edits cannot be overwritten | Server parameter-branch tests and API procedure conflict tests | Array branch-conflict scenario |
| Installed adapter identity, refresh and isolated restoration | Core keeps first-use, settings, author workspace and registration checks; these do not replace wheel isolation | `test_installed_adapter_journey.py` retains the complete installed-package chain |

### Required gate

[CI](https://github.com/scopecat-project/scopecat/blob/main/.github/workflows/ci.yml)
is the required PR gate. Performance improvements are tracked in
[#520](https://github.com/scopecat-project/scopecat/issues/520).
Every PR, merge-group, main push and explicit CI dispatch runs:

- Linux Python `core` (all fast and integration files), with two pytest workers,
  diagnostics and the pandas adapter check;
- Bounded macOS/Windows [platform smoke](platform-smoke.md);
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

## Choosing checks

Run affected tests directly or select the relevant tier during development.
Choose additional coverage by the behavior that changed:

| Changed behavior | Relevant evidence |
| --- | --- |
| Persistent identity or recovery | Exact retained requests, non-mutating rejection and affected current-format backup/restore paths; see [data policy](data-compatibility.md) |
| Source loading, workers or resource ownership | Process, restart, cancellation or resource-exclusion journeys exercising that boundary |
| Wire or UI consumers | Regenerated contracts, focused payload/component checks and affected browser interaction |
| Installation or tutorial execution | Affected installed/platform/Notebook journey |
| Documentation | Relative links and strict documentation build |

A public dependency update needs the affected private consumer checks; broader
qualification is useful when it covers a changed integration boundary. Shared
ports, devices or data directories require coordination, while isolated checks
can run independently. Full acceptance qualifies an integrated release candidate;
use the manual profile on the intended revision and record its actual coverage.
A green fast gate does not stand in for unexecuted acceptance.

Measure expensive journeys before decomposing them. Keep representative real
process/source/resource/persistence/restart chains; move redundant input/policy
combinations to focused tests. Preserve the observable assertion or explain why
it is obsolete. Timing observations are measurements, not permission to relax
correctness checks or conceal failures with retries. See the
[author performance baseline](author-performance.md) for prepare-boundary costs.

## Candidate policy assertion placement

`test_typed_candidates_retain_cells_and_independent_policy` retains the real
managed-author chain: acquire, fit, stage named cells, prepare the exact candidate,
acquire independent verification data and run its policy. It starts with equipment
only and selects a parameter branch. Advancing that branch leaves a prepared
candidate unchanged; verification changes neither the branch nor the lab default. Unknown
fit fields, receipt authority, candidate identity and unrelated-cell preservation
remain checked there.

`test_drag_candidate_publishes_to_branch_and_runs_accepted_gate` retains actual
DRAG acquisition, fit/figure/report, candidate acquisition and cross-run decision
with independent parameter/setup inputs. It then publishes the verified candidate
to the captured branch and executes the standard-gate fixture with that exact
parameter revision and setup. The global registry remains empty. This replaces
the retired DRAG gallery and unused single-target default-publishing procedure;
no global-default publication or restore is required.
The managed-author journey also publishes explicitly to an independent branch
and retries the same request. `test_project_analysis_runtime.py` covers rejected
decisions, branch-head/base conflicts, transaction rollback and current-format
backup/restore of publication receipts. Legacy publication fences remain there
until their consumers retire.

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

## Thin Windows and macOS smoke

See [platform smoke selection and measurements](platform-smoke.md) for the bounded
platform checks, exact-SHA/no-skip contract and recorded runner/cache evidence.

### Grouping lesson evidence

`test_grouped_teaching_journey.py` executes the generated `groups.ipynb` in a
real ipykernel, using both the groups-topic scaffold and the default scaffold
used by the standalone verifier. It checks notebook and author-resource bytes
against this checkout before execution; reinstall `scopecat-lab-teaching` when
that non-editable package is stale. The learner's cells are unchanged.

Appended checks retain 42 points, two 21-point groups and their published curves;
the final amplitude exercise produces a separate 63-point, three-group run.
After both daemon and kernel restart, the original run/publication, raw IQ and
analysis values/curves must agree without acquisition. The same reopen checks
feed `verify_maintenance`, whose additional-analysis cell is also exercised.
Bookmarks and raw-array copies are verifier evidence, not learner requirements.

This replaces the parallel grouping notebook in `verify_groups`. Its extra
parameter-save and research-directory exercises are not part of the shipped
grouping lesson: parameter persistence remains covered by
`test_minimal_teaching_journey.py`, and research membership/history/restart by
`test_research_history_associations_and_bench_survive_restart` in
`test_samples_runtime.py`. The grouping journey no longer claims those as
learner steps. Standalone `verify` still uses the independently prepared project
environment and its existing admission failures; source-kernel coverage does not
establish fresh installed-delivery success, actual editor interaction, Help
integration of this topic, unfamiliar-user acceptance, or physical-device results.

### Editing lesson evidence

`test_shipped_editing_lessons` in `test_teaching_mean_iq_journey.py` executes
`refresh.ipynb` and `compute.ipynb` with real ipykernels, using both topic scaffolds
and the default verifier scaffold. Generated notebooks and related author resources
are compared byte for byte with this checkout before execution; reinstall the
non-editable teaching package after material changes.

The verifier inserts source edits at the learner's editing stops. Refresh uses
`sc.notebook()` and the original imported alias without explicit refresh or
reimport: old requests/preparations retain seed 200, new calls use the edited seed,
new modules are discovered, live updates can pause/resume, and a syntax error is
rejected before repair. Compute retains raw 7×64 IQ and matching seven scalar means.
After daemon and kernel restart, only the shipped connection/history/read cells
and an explicit recorded-number selection run; repeated reads preserve run count.
The bookmark and raw array are external evidence, not added learner requirements.

The former parallel editing notebook's deliberate compute failure and incompatible
scalar read remain external checks in `verify_editing`; repair runs in the same
session, and failed reads preserve the original shots. The existing source-level
mean-IQ journey also retains conversion of one experiment from shots to means.
Standalone `verify` and `verify_maintenance` consume these shipped-cell checks while
keeping the groups chain. These source-kernel checks do not establish fresh wheel
installation, Help integration, native editor interaction, unfamiliar-user learning
or physical-device acceptance.

### Managed calibration lesson evidence

`test_managed_calibration_shipped_kernel` executes the shipped calibration and
joint-calibration notebooks in real ipykernels against one application data root
and a separately registered author folder. It checks generated notebook/source
bytes first. The learner cells keep `sc.notebook()` and use managed submissions.
An external observer wraps the actual close call: it requires a leased procedure
and running worker, closes the Notebook session, then observes completion through
an independent read-only connection while the original session remains closed.
This is distinct from the later application/kernel restart and history-only reopen,
which must retain raw measurements, source/intent identity and branch results
without new acquisitions. No source fingerprint or import check is bypassed.

Scientific rejection, missing-target coverage, history completeness and publication
assertions live outside the learner cells. The internal source-lifecycle regression
retains the exact fit checkpoint and replay checks through `should_yield`; it does
not introduce an ordinary-author pause or confirmation API. Task-calibration
retains its existing separate journey pending its source-bound admission migration.
These are source/software journeys, not installed-delivery, native editor, device
or unfamiliar-user acceptance.
