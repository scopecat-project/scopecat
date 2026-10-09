# Test feedback and qualification

Choose checks for the behavior changed. Documentation needs relative-link checks
and a strict build; it does not need fresh scientific or installed acceptance:

```sh
uv run --locked python scripts/check_document_links.py
uv run --locked --group docs zensical build --strict
```

## Local feedback

The shared runner reads `[tool.scopecat-tests]` in `pyproject.toml`:

```sh
uv run --locked python -m scopecat_testkit.check fast
uv run --locked python -m scopecat_testkit.check integration
uv run --locked python -m scopecat_testkit.check journey
uv run --locked python -m scopecat_testkit.check full
```

| Tier | Selection |
| --- | --- |
| `fast` | Core/library and focused reference scientific/compiler tests |
| `integration` | Server tests except explicit journey files |
| `journey` | Reference workflows, fresh-process snapshots and validation-process diagnostics |
| `core` | Fast plus integration; ordinary Python CI |
| `full` | All classified files; plain `uv run --locked pytest` also runs the full suite |

Run affected files directly while editing. Pass pytest options after `--`, such
as `fast -- -n 0 -q`. Use `--list` to inspect selection and `journey --shard 1/2`
for a deterministic file shard. Reviewed cost estimates balance shards; new
classified files receive default weights. Unclassified files fail runner selection,
including `full`. Explicit fast paths override broader prefixes; classification
does not infer cost from filenames or guarantee that fast tests are process-free.
The runner pins the root workspace configuration even for nested projects.

### Diagnostics

Each invocation writes ignored `.test-results/` selection JSON (revision, tier,
shard, files and options), timing JSON (status, wall/collection time and test
phases), JUnit XML and the slowest twenty tests. Parallel phase totals add worker
time; they are not wall time or a benchmark.

CI uses `scripts.pytest_diagnostics` on Linux and Windows. Controller and worker
logs under `test-diagnostics/` record phase boundaries, daemon lifetime and thread
stacks every 120 seconds. These flushed logs survive interruption when timing JSON
or JUnit may be incomplete. Find each worker's last unmatched phase start;
console percentages do not identify its active test, and a stack dump alone does
not prove deadlock. Reproduce suspect tests serially and in parallel as needed.

```sh
SCOPECAT_TEST_DIAGNOSTICS=test-diagnostics uv run --locked python -m scopecat_testkit.check core -- -p scripts.pytest_diagnostics --maxprocesses=2
```

## Required CI and manual acceptance

[CI](https://github.com/scopecat-project/scopecat/blob/main/.github/workflows/ci.yml)
runs on every PR, merge group, main push and explicit dispatch, without path
filters. **CI gate** requires both Linux Python core shards, bounded Mac/Windows
[platform smoke](platform-smoke.md), Python static/generated checks, UI checks/build,
and strict docs/link checks. Failures, cancellation and unexpected skips fail the
gate. The two Python shards use two workers each and retain diagnostics.

The feedback target is under five minutes from the first job start to the gate,
excluding runner queues. Record the run URL, revision and slowest job when measuring
it; it is not a timeout or permission to conceal failures with retries. See
[CI feedback cost](ci-feedback-cost.md) for measurements and tradeoffs, and
[#523](https://github.com/scopecat-project/scopecat/issues/523) for first-use profiling.

[Full acceptance](https://github.com/scopecat-project/scopecat/blob/main/.github/workflows/acceptance.yml)
is manual. Its profile gate checks every selected job and expected skip:

| Profile | Use and coverage |
| --- | --- |
| `full` | Integrated qualification: Linux/Windows Python, both browser shards, benchmark, isolated installed wheels, offline delivery and shipped teaching Notebooks, plus static/UI/docs checks |
| `local-application` | Bounded installation trial: UI build, both browser shards and Linux/Windows installed/offline teaching checks; pair with successful PR CI |
| `browser` | UI artifact producer and both real-daemon browser shards |
| `service-lifecycle` | Windows/Linux service lifecycle qualification |
| `native-distribution` | Mac/Windows native packaging and installer qualification; see [desktop packaging](architecture/desktop-packaging.md) |
| `public-preview` | Publishes immutable framework preview artifacts after successful CI for that commit; see [public previews](public-preview.md) |

Fast CI does not qualify installed delivery or a release. Browser evidence does
not cover native operation or installed recovery. Source, installation, native
interaction, unfamiliar-user and physical-device evidence remain distinct; the
[packaging matrix](architecture/desktop-packaging.md#coverage-by-consumer-boundary)
explains those boundaries. Existing observations and remaining human/physical work
are tracked in [#616](https://github.com/scopecat-project/scopecat/issues/616).

### Browser artifact reuse

```sh
gh workflow run acceptance.yml --ref <branch> -f profile=browser
gh run rerun <run-id> --failed
gh run rerun --job <browser-job-id>
```

Each dispatch builds wheels and GUI for its commit. Browser consumers validate
the shared artifact's manifest commit, all file hashes and embedded GUI identity
before execution. Same-run job retries retain the original revision and reuse a
successful producer's artifact. A failed producer must rebuild; a changed commit
or expired artifact needs a fresh dispatch. Artifacts last seven days. There is
no cross-run selection or silent consumer rebuild. Record which attempts/shards
actually ran; one retried shard is not fresh evidence for the other.

## Choosing additional checks

| Changed behavior | Relevant evidence |
| --- | --- |
| Persistent identity or recovery | Exact retained requests, non-mutating rejection and affected current-format restore paths; [data policy](data-compatibility.md) |
| Source, workers or resource ownership | Real process, restart, cancellation and resource-exclusion journeys at the changed boundary |
| Wire or UI consumers | Regenerated contracts, focused payload/component checks and affected browser interaction |
| Installation or lessons | Affected installed/platform/shipped-Notebook journey |
| Documentation | Relative links and strict build |

A public dependency update also needs affected private consumer checks. Coordinate
shared devices, ports and data directories. Measure expensive journeys before
splitting them: keep representative process/source/resource/persistence chains,
and move redundant policy combinations to focused tests. The
[workflow evaluations](workflow-evaluations.md) describe user outcomes; the
[reference retirement map](reference-gallery-retirement.md) identifies scientific
and device owners. No test run alone establishes hardware correctness.

## Representative assertion owners

| Boundary | Combined evidence | Focused or broader evidence |
| --- | --- | --- |
| Task → acquisition → analysis → verified publication | `test_calibration_smoke.py`, two targets and real workers/restart | `test_array_maintenance.py` qualifies six targets/three readout groups; server admission/finalization and API procedure tests own rejection and exact identity |
| Candidate verification and branch adoption | Reference `test_typed_candidates.py`: real managed-author and DRAG acquisition, independent verification, exact branch publication, accepted-gate use and replay | Server `test_project_analysis_runtime.py` owns rejection authority, branch conflicts, rollback and current-format receipt recovery |
| Author workspace and procedure lifetime | `test_two_workspace_publication_and_execution` retains source/parameter separation and restore after author-folder loss; managed-source retry/disconnect journey retains actual worker lifetime | `test_refresh_worker_handoff.py` and revision repositories own stale-generation rejection/cleanup; core `test_author_procedures.py` owns client reconnection fences |
| Installed adapter | First-use/settings/source registration checks remain in core | `test_installed_adapter_journey.py` owns wheel isolation and restoration; core checks do not replace it |

Policy tests using seeded measurements and in-process HTTP establish retained
decision authority, not execution of the original analysis or instrument worker.
Keep that boundary when moving assertions.

## Teaching and author entry

Shipped Notebook cells are the learning source. Verifiers compare generated
notebook/author bytes to the checkout and add assertions in evidence copies,
not learner requirements. Reinstall a stale non-editable teaching package before
checking material identity.

| Owner | Retained evidence |
| --- | --- |
| `test_grouped_teaching_journey.py` | Topic/default scaffolds in real kernels; grouped raw IQ and published curves; daemon/kernel restart and history-only reads without acquisition |
| `test_teaching_mean_iq_journey.py::test_shipped_editing_lessons` | Shipped refresh/compute cells; edits affect new calls while prepared work stays pinned; syntax repair, raw IQ/means and exact reads after restart |
| `test_managed_calibration_shipped_kernel` | Calibration/joint-calibration cells; an external observer witnesses a leased worker at actual session close, completion while closed, then retained source/data/branch results after restart |
| `test_managed_task_calibration_shipped_kernel` | Task-calibration cells; worker continues after close, prepared source survives edits, retry creates no duplicate execution, accepted/rejected/stale finalizations remain distinct |
| `scripts/verify_notebook_journey.py` | Browser Help preparation/Continue, real kernels, editable material, source/scan edits and retained-result reopening |

Standalone `verify`/`verify_maintenance` reuse shipped-cell checks and keep their
independent project environment and admission boundaries. Extra failure/read checks
remain external. Grouping does not teach parameter-save or research-history steps;
minimal teaching and server sample-history tests cover those separately.
Source/kernel checks do not establish installed delivery, actual editor use or
learner comprehension. Installed teaching and recovery owners are in
[desktop packaging](architecture/desktop-packaging.md#teaching-intent-and-acceptance-limits).

### Ordinary Settings author entry

`verify_ordinary_author.py` starts from empty application data through real
`DesktopAPI` Settings operations in Chromium. It creates a folder and independent
client/execution Python, checks existing-folder and invalid-interpreter retries,
and executes the starter in its own kernel.

```sh
uv run --locked python -m lab_tools.delivery /tmp/ordinary-delivery
uv run --locked python -m lab_tools.toolchain /tmp/ordinary-delivery /tmp/ordinary-payload
uv run --locked python scripts/verify_ordinary_author.py /tmp/ordinary-evidence /tmp/ordinary-payload
```

Use fresh paths and install Playwright Chromium, or set `SCOPECAT_TEST_CHROMIUM`.
Preview and rejected syntax leave zero runs; repaired source creates one acquisition
visible in Notebook and GUI. Restart uses GUI-copied read-only code without another
acquisition. Evidence includes cells, source/payload identity and screenshots.
Native picker/window plumbing, editor activation and VS Code `__file__` injection
are substituted. [Experiment-form draft recovery](architecture/draft-recovery.md#experiment-form-boundary)
is a separate contract from this retained-receipt journey.
