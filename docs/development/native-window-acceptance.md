# Exact-run native window acceptance

The #888 Windows cookie repair passed its bounded **Mac and Windows native
checks** on 2026-10-06. Each result below is bound to its tested candidate;
Linux checks, browser acceptance and platform-smoke are not native passes.

## 独立短 smoke

`Native smoke` 工作流在 GitHub-hosted Windows/macOS 上运行，也可手动触发。
PR 仅在工作流列出的 desktop、native host、关键导航、退出协议、探针或
Python runtime/build 输入变化时运行；不增加定时任务或全慢验收 gate。

短流程仍构建平台 delivery 和 native application，但不构建 installer，不运行
科学 journey 或 reset。隔离的原生准备完成后，调用
`verify_native_windows.py APP HOME --short`，检查健康连接、决策/运行导航和
正常关闭预检。它复用完整探针的原生 host、backend、退出和外层清理，先保存
业务结果再记录退出结果；`always` artifact 保留 identity、JSON 和诊断日志。
仅允许 disposable hosted runner，不在个人桌面运行。

此路径会替换测试副本的 bootstrap，不是用户首次打开、OS 菜单/托盘或安装资格。
完整安装、升级/恢复、reset 和发行候选仍使用 `native-distribution`；短流程通过
不能替代这些高风险边界。成本以各步骤和 job 时间观察，不能把单次 hosted 样本
当作固定预算；失败时保留首次结果，先归因再决定是否重试。

## Accepted Windows repair

[PR #888](https://github.com/scopecat-project/scopecat/pull/888) records the
Mac and Windows repair qualification at `9537010eeeda8dae2b3a317c6389d03828483985`.
The [fixed historical record](https://github.com/scopecat-project/scopecat/blob/5c8cdc30d812fa4484d8fd1af3a5b5f30aca133e/docs/development/native-window-acceptance.md#accepted-windows-repair)
preserves candidate identities, report hashes, runner images and earlier results.
These results do not qualify a later main, schema upgrade, injected races, all
Windows versions or full human interaction. Mac signature/tamper checks passed;
notarization was absent, Gatekeeper rejected with code 3 and Finder first-open
was not evaluated.

## Earlier accepted combined candidate

The [earlier combined-candidate record](https://github.com/scopecat-project/scopecat/blob/5c8cdc30d812fa4484d8fd1af3a5b5f30aca133e/docs/development/native-window-acceptance.md#earlier-accepted-combined-candidate)
retains the #885/#886 qualification and its exact artifact identities. That
Windows candidate did not run the complete host-restart/cookie-isolation check.
Do not transfer its pass to later combinations or to untested desktop behavior.

## Windows lifecycle probe

`verify_windows_storage.py` extends the existing opted-in `--native-windows`
path on Windows, after the exact-run probe has saved a real Decision draft and
exited. It runs three original packaged native launchers against the actual
WebView2 backend selected by the Windows delivery. The acceptance bootstrap
drives the original launchers; it never patches the loaded backend. The approved
production wheel repair below is separate from this harness. No alternate profile
or new workflow trigger is introduced.

- A and B overlap at one fixed loopback origin, using the same copied executable.
  Both must start without the other's marker or cookie. B writes and clears its
  cookie; A must retain its marker and cookie through both operations. Checks
  compare document cookies, the native cookie API and real HTTP requests.
- Actual WebView2 profiles must be private, and their observed user-data folders
  must differ across hosts. The exact packaged version and loaded backend hashes are
  required and recorded. Windows `DeleteAllCookies` must leave B's localStorage marker intact;
  Cocoa's all-website-data clear semantics are not imposed on Windows.
- After both hosts exit successfully, the wrapper stops the application service,
  verifies the old PID/creation-time identity is gone, then starts it again. C
  first checks absence of A/B browser state at the unchanged test origin, then
  loads the exact procedure in the real UI and restores its reviewer. The full
  application-owned draft receipt must equal the earlier real-form edit before
  and after restart. Browser data is never used as a recovery fallback.
- After those independent recovery checks, C returns to the fixed origin, seeds
  a fresh cookie/marker and opens a second native window in the same host. It
  records both windows' actual stores, native cookies, document cookies and HTTP
  echoes before requiring the original window to retain its cookie and marker.
  Closing the peer must also preserve the original values. Peer values are
  observations, not a new cross-window sharing policy. A cookie loss fails C and
  the aggregate; earlier A/B and C recovery observations remain in the reports.

Inspect `windows-storage/result.json`, `draft-fixture.json`, `A.json`, `B.json`,
`C.json` and their logs alongside `native-windows/identity.json`. Stage reports,
nonzero exits, deadlines and cleanup failures are required evidence, not optional
warnings. The existing always-run workflow upload collects the entire directory,
including error reports in the same filenames. Early host/import errors produce
stage JSON and logs; setup errors produce aggregate JSON. Explicit stage-exit,
stage-pass, owned-host/service-cleanup and aggregate status lines reach the job
log. Lightweight tests check actual retained A/B/C reports/logs against the upload
paths, including failure cases, and reject a missing directory. These Linux tests
validate failure handling and evidence retention, not native Windows behavior. The existing eight window checks and independent marker
assertions remain unchanged.

The same-host assertion remains strict. It never reseeds cookies after opening
its peer or changes the expectation to match deletion. Mac assertions and
application-owned draft persistence remain unchanged.

### Confirmed Windows failure and approved repair

The [failure and repair record](https://github.com/scopecat-project/scopecat/blob/5c8cdc30d812fa4484d8fd1af3a5b5f30aca133e/docs/development/native-window-acceptance.md#confirmed-windows-failure-and-approved-repair)
contains the reproduced Windows cookie deletion defect, approved production
repair and incomplete-report attempts. An incomplete report is not a pass;
use the exact repair candidate above for the bounded qualification.

## Historical failed candidate

The [failed-candidate record](https://github.com/scopecat-project/scopecat/blob/5c8cdc30d812fa4484d8fd1af3a5b5f30aca133e/docs/development/native-window-acceptance.md#historical-failed-candidate)
remains available separately from successful runs. Later success does not
reinterpret an earlier failed or missing check as successful.

## Probe contract

The existing manual `Full acceptance` workflow's `native-distribution` profile
builds both platform packages and explicitly passes `--native-windows` to
`verify_native_application.py`. Default invocation retains the existing headless
checks and does not open these WebViews. Probe contract/failure tests belong to
integration, not the fast PR suite.

The probe copies the candidate package and replaces only its bootstrap. It uses
the real packaged UI and the candidate's production pywebview backend/store,
`DesktopWindows`, shared `DesktopSession`/`ApplicationRuntime`, JS bridge and
service. A real SDK scan pauses after its first ingestion. The main page's opening
button creates an exact-run window; changing the main selection must leave that
window unchanged. Reading records must preserve the run set, held ingestion count
and snapshot. Closing the second native WebView must leave the same service and
run active; releasing the scan must complete that run without another run.

A real ProcedureWorker creates one waiting interpretation through the service,
without acquiring another run. The real Decisions form saves a reviewer draft.
The probe waits for the real edit to reach the application-owned draft API, then
unmounts the form before creating the second window. It compares the full durable
draft receipt and a separate localStorage marker after creation, restoration,
reads and closure;
returning to Decisions must restore the reviewer. The second window's storage is
also recorded, without imposing a new sharing policy. Both injected JS bridges
must direct title calls to their own Python Window object; this is bridge ownership
evidence, not a claim about OS title rendering.

## Safety and failure handling

**Run the WebView probe only on the disposable GitHub-hosted macOS/Windows VMs
created by the manual job. Never run it on a user's Mac, a retained desktop or a
self-hosted runner.** Upstream pywebview 6.2.1 Cocoa uses and clears its default
website store when each private window is created. The candidate's bounded
production dependency patches are qualified only for the exact candidates recorded
above. Neither the application's isolated `--home` nor pywebview `storage_path`
isolates that native store. This may affect
unrelated retained WebKit data under the same host identity. No local override is
supported, and the probe never replaces the candidate backend or store.

Before any opted-in native execution, the verifier requires `GITHUB_ACTIONS=true`,
`RUNNER_ENVIRONMENT=github-hosted`, a matching macOS/Windows `RUNNER_OS`, and a
resolved acceptance directory strictly below absolute `RUNNER_TEMP`. The direct
wrapper and embedded probe repeat that guard. These are practical accidental-run
checks, not attestation: do not manufacture those variables to run locally.
The workflow's hosted VM is the browser-data isolation boundary.

Application data, author files, gates and reports use disposable acceptance
files. The candidate is unchanged; the copied executable and Mac Info.plist keep
native host identity and teardown. The probe removes inherited endpoint/Python
overrides. Its backend/default-store behavior matches production: if window
creation clears the draft, the check must fail and retain that evidence. The approved
production dependency repair must remain bound to its recorded platform evidence.

Waits and the host have deadlines. Timeout cleanup uses the existing owned
process-tree helper. A PID plus creation-time receipt permits acquisition-client
cleanup after host crashes; detached service cleanup uses the isolated runtime's
ownership checks. Host-exit or cleanup failure makes acceptance fail even if UI
assertions passed. Logs and JSON survive disposable cleanup.

## Cocoa production dependency acceptance

The approved [host-lifetime nonpersistent store repair](architecture/draft-recovery.md#approved-native-store-repair)
is delivered as a locked Darwin wheel, not a probe monkeypatch. The manual Mac
job also runs `verify_cocoa_storage.py` against that packaged dependency. It uses
three instances of the original native launcher and bundle identity, a single
loopback test server/origin, and real WebKit/NSHTTP cookie sentinels. A/B overlap
to prove sharing within A and isolation between hosts; C starts after A exits to
prove same-origin ephemerality. Cookie interface and actual HTTP request checks
must agree with the constructed WebView's nonpersistent store. The existing
independent localStorage marker assertions remain unchanged.

Inspect `cocoa-storage/result.json`, `A.json`, `B.json`, `C.json` and their logs.
A missing stage, host failure or cleanup error is not a pass. The reports include
probe/bundle hashes and each host checks the exact installed dependency version
and Cocoa source hash. This check now requires PR #886's draft API. After both A/B
hosts exit, it stops and restarts their application service; C separately verifies
same-test-origin browser ephemerality and exact-procedure draft restoration in the
real UI/API. The test origin stays fixed; the application service port may change.
This runs before the original acceptance workspace is removed, and the wrapper
retains `draft-fixture.json` plus every stage report/log. A combined candidate uses
fresh schema-111 data; it never upgrades an old development fixture.

## Running a candidate requiring native qualification

After ordinary CI and review, use GitHub **Actions → Full acceptance → Run
workflow** for a change requiring native requalification. Select the reviewed
candidate ref under **Use workflow from**, choose **profile: native-distribution**,
and confirm the run's head SHA equals the exact reviewed SHA. The earlier
#885/#886 result above required their combined API and probe; neither an older
branch nor a later main inherits that result. Do not choose `candidate-artifacts`,
which publishes release assets. The native profile builds and retains installer
artifacts but does not create a release, tag or deployment.

For each platform, inspect the `native-<platform>` artifact's
`native-windows/result.json`, `identity.json`, `host.log`, `acquisition.log` and
`cleanup.log`. Match workflow SHA, bundle sources/hash and probe hash. Require a
passed result, host exit/cleanup, storage observations and all window assertions.
Missing reports, a skipped platform or an earlier build failure are not passes.
A storage failure is evidence about the unchanged production path; do not retry
with an alternate store and call it qualification.

This is native component/bridge acceptance using programmatic DOM actions. It
does not establish OS focus, actual menu clicks, visual presentation, tray,
complete production startup, or durable browser persistence. The draft check covers
window creation within the live process. The separate Cocoa and Windows lifecycle
checks cover browser ephemerality and application-draft recovery across complete
host/service restart at their respective accepted SHAs above.
No real devices, private consumer repositories or retained user data are fixtures.

## In-place reset recovery probe

`verify_native_reset.py` now targets confirmed deletion and initialization at the
same path, preserving source and installation files. Its programmatic DOM action
explicitly selects skip-backup; default backup, picker cancellation, archive
verification and failures are covered in focused host/storage tests. Native folder
picker interaction for this reset flow remains unqualified. The probe replaces the
copied bootstrap, calls production desktop code and operates real confirmation
buttons; HTTP navigation/running status is not workbench rendering qualification.

The prior run 37475673494 qualified the superseded preserved-space design at
`1a2e9a7f6583a3e47f530443660ccbca187d5636`; it is not evidence for this destructive
reset design. Its preceding Windows bridge startup failure remains unexplained.
The current rewrite requires a separately scoped acceptance decision, not an
automatic repeat of the full native distribution matrix.

Native probe reports retain child Python output, host logs and cleanup results.
The window/reset sequence proceeds only after host and background cleanup are
confirmed, records unexecuted reset reasons, and remains failed if either probe
fails. No later successful result overwrites an earlier failure.
