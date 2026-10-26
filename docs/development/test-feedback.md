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

## Static checks

Use the commands in `.github/workflows/ci.yml` as the CI entry points. Python
rules, type-check roots and import contracts live in root/member `pyproject.toml`;
UI scripts and rules live in `apps/scopecat-ui/package.json`, `.oxlintrc.json`,
`.oxfmtrc.json` and `tsconfig*.json`. Lockfiles and interpreter/package-manager
version files are authoritative; use locked dependency installation.

When changing coverage, inspect effective nested configuration: Ruff `src`
classifies imports rather than selecting files, and member overrides can replace
root fields. Basedpyright execution environments use first-match order; excluded
files may still be followed through imports. Generated declarations, notebooks
and workspace templates need their generation/journey checks as well as applicable
static checks.

Keep brief reasons beside necessary dynamic-framework, private-test or generated-
workspace exceptions. Probe candidate rules before changing policy; fix real
boundaries rather than adding casts or relocating ignores. Record one-time audit
counts, timings, validation and unresolved findings in the issue/PR, not here.

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
[the #914 experiment](https://github.com/scopecat-project/scopecat/pull/914) and its
[fixed measurements](https://github.com/scopecat-project/scopecat/blob/5c8cdc30d812fa4484d8fd1af3a5b5f30aca133e/docs/development/ci-feedback-cost.md) for the historical
latency/runner-cost tradeoff; these are not current performance guarantees. See
[#523](https://github.com/scopecat-project/scopecat/issues/523) for first-use profiling.

[Full acceptance](https://github.com/scopecat-project/scopecat/blob/main/.github/workflows/acceptance.yml)
is manual. Its profile gate checks every selected job and expected skip:

| Profile | Use and coverage |
| --- | --- |
| `full` | Integrated qualification: Linux/Windows Python, both browser shards, benchmark, isolated installed wheels, relocated runtime teaching and separate legacy offline-installer regression, plus static/UI/docs checks |
| `local-application` | Bounded installation trial: UI build, both browser shards and Linux/Windows installed-artifacts checks; pair with successful PR CI |
| `teaching` | UI artifact producer and Linux/Windows installed-artifacts only: independent framework validation, separate legacy installer regression, and relocated Windows application/Linux runtime fixture with format-2 Help/kernel and recovery checks; pair with successful PR CI |
| `browser` | UI artifact producer and both real-daemon browser shards |
| `service-lifecycle` | Windows/Linux service lifecycle and reset-backup qualification |
| `native-distribution` | Mac/Windows native packaging and installer qualification; see [desktop packaging](architecture/desktop-packaging.md) |
| `public-preview` | Publishes immutable framework preview artifacts after successful CI for that commit; see [public previews](public-preview.md) |

Fast CI does not qualify installed delivery or a release. Browser evidence does
not cover native operation or installed recovery. Source, installation, native
interaction, unfamiliar-user and physical-device evidence remain distinct; the
[packaging matrix](architecture/desktop-packaging.md#coverage-by-consumer-boundary)
explains those boundaries. Existing observations and remaining human/physical work
are tracked in [#616](https://github.com/scopecat-project/scopecat/issues/616).

### Notebook course locator preflight

After building the current GUI, serve `apps/scopecat-ui/dist` on loopback (or use
an existing current GUI endpoint) and run:

```sh
uv run --locked python -m http.server 5173 --bind 127.0.0.1 --directory apps/scopecat-ui/dist
# In another shell; SCOPECAT_TEST_CHROMIUM can select an installed Chromium.
uv run --locked python -m testing.app_support.notebook_course http://127.0.0.1:5173
```

This short browser check uses the same Python locator as
`verify_notebook_journey.py` against the real Help component: the `课程` combobox,
selection by lesson value, URL update and selection after reload. It requires no
payload, native bridge or Notebook environment. The installed verifier also runs
this preflight before preparing a course. To exercise only the representative
parameters path, use `verify_notebook_journey.py WORK PAYLOAD GUI --parameters-only`;
the default still includes the existing groups continuation. Neither command
qualifies native windows or editor activation. `verify_ordinary_author.py` starts
in Settings and currently has no Help course selector; it is a separate author
workflow, not another course-control contract.

### Browser artifact reuse

```sh
gh workflow run acceptance.yml --ref <branch> -f profile=browser
# Optional narrow qualification; defaults still run both complete journeys.
gh workflow run acceptance.yml --ref <branch> -f profile=browser \
  -f browser_shard=2 -f 'browser_files=application-loop|parameter-working-inputs'
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

### 定向慢验收与失败归因

日常改动先按上表定位受影响的行为：改动真实进程/恢复边界、UI 交互、教学入口或安装
消费者时，在本地运行对应文件/用例；需要托管产物或目标平台证据时，选上面的验收
profile，浏览器可进一步限定 shard/files。入口、生成器或共享夹具变更须覆盖实际消费者，
不能只让启动成功就停止，也不能以单测通过代替受影响的用户路径。
纯文档不需重跑慢验收。相关改动触发定向运行，不把全量慢测加入 PR gate，也不默认
增设定时运行；运行范围、通过结果及未覆盖项写进 PR。

发行候选确定后，提前按交付边界完成集成资格化，不等发布前最后一步才检查积累的漂移。
以候选 SHA 的 `full` 和实际交付所需的安装/原生证据为依据，具体仍遵循
[发行流程](public-preview.md)及[打包矩阵](architecture/desktop-packaging.md#coverage-by-consumer-boundary)。
更换代码、依赖或产物后重新核对受影响证据；旧 SHA 的绿色结果、单个浏览器分片或被跳过的
profile 不能算作新候选的完整资格化。缺少的组明确标为未知，不为填表无条件重跑全矩阵。

失败时记录能定位该失败的 SHA、运行链接及关键日志；涉及产物或平台差异时再补相应身份/环境信息。
再按最小相关用例复现；修复后走完原先被阻断的路径。

| 分类 | 判据与处理 |
| --- | --- |
| 产品缺陷 | 当前有效用户路径违反行为/数据契约；修产品并保留回归，不能改科学语义让测试通过 |
| 夹具漂移 | 旧命令、选择器、生成内容或 API 假设与当前契约不符；复用实际入口/工具，必要时补低成本 producer-consumer 检查，不另写第二套实现 |
| 环境问题 | 有证据指向依赖、runner、权限或进程回收；记录环境差异并修正环境，不吞清理错误或盲加 timeout |
| 未归因 | 只有超时、取消或重试后通过；保留未解决结论和后续诊断，不把绿色重跑当根因修复 |

同 SHA 的已成功任务可按上面的产物复用规则保留；重试须有具体诊断假设，记录实际尝试，
同一诊断假设未带来新证据时，停止重复重试并转向定位。仅在修复证据覆盖原失败路径后关闭对应问题。
已有未归因验收失败继续跟踪 [#1058](https://github.com/scopecat-project/scopecat/issues/1058)，
运行节奏与防漂移证据归 [#1071](https://github.com/scopecat-project/scopecat/issues/1071)。

比较开销时分别记录首个实际 job 开始到 gate 结束的墙钟、实际 job 时长之和及测试步骤
耗时，标注队列等待、尝试次数和选择范围；runner 秒不是计费分钟，也不等于测试耗时。
历史数字、run 链接和本地样本放在 issue/PR，不变成永久时限承诺；当前未运行的组仍未知。

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

Extra failure/read checks remain outside learner cells. Grouping does not teach
parameter-save or research-history steps;
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
uv run --locked python -m scripts.app_build.delivery /tmp/ordinary-delivery
uv run --locked python -m scripts.app_build.toolchain /tmp/ordinary-delivery /tmp/ordinary-payload
uv run --locked python scripts/verify_ordinary_author.py /tmp/ordinary-evidence /tmp/ordinary-payload
```

Use fresh paths and install Playwright Chromium, or set `SCOPECAT_TEST_CHROMIUM`.
Preview and rejected syntax leave zero runs; repaired source creates one acquisition
visible in Notebook and GUI. Restart first uses GUI-copied read-only code without another
acquisition, then explicitly prepares and runs again from a saved two-run comparison.
The same author folder retains source edits, saved parameter choices and exact
decision provenance through application and kernel shutdown. Evidence includes cells, source/payload identity and screenshots.
Native picker/window plumbing, editor activation and VS Code `__file__` injection
are substituted. [Experiment-form draft recovery](architecture/draft-recovery.md#experiment-form-boundary)
is a separate contract from this retained-receipt journey.
