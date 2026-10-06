# Exact-run native window acceptance

The combined #885/#886 candidate passed its specified **Mac and Windows native
checks** on 2026-10-06. Qualification is bound to the exact candidate below.
Linux checks, browser acceptance and platform-smoke are not native passes.

## Accepted combined candidate

[Manual run 37436328157](https://github.com/scopecat-project/scopecat/actions/runs/37436328157)
used `native-distribution` at `4aee5825dbe374f469bc0f1be1e9a981908086c1`, combining
#885 `0614b0402450751d28b65b7cffba913b4d8a4cbf` and
#886 `722e195d48c398c5d65385fdf610beb260f8395c`. Both native jobs succeeded;
independent review read the raw reports and matched commit, bundle and probe hashes.
This does not claim native execution of a later main or the three-PR #884/#886/#885
closeout combination. Documentation-only updates do not alter the tested code.

- Mac 26.6.2 arm64, image `20260907.0351.1`, and Windows Server 2025 amd64,
  image `20260925.250.1`: eight exact-run window checks and host exit/cleanup passed.
  Full application draft views and the independent marker remained equal across
  creation, restoration, reads and secondary close.
- Mac A/B/C storage checks passed: same-host sharing, overlapping same-origin host
  isolation, actual cookie interfaces/HTTP requests and retained default/NSHTTP
  sentinels. After host exit and service restart, C had no old marker/cookie at the
  fixed test origin `http://127.0.0.1:49685`, while the exact procedure form and
  complete application draft recovered. Service PID changed `17495` to `17863`
  and port `49687` to `49702`.
- The Darwin bundle contains `pywebview-6.2.1+scopecat.1`, wheel SHA-256
  `69e66d40be74245e21569a8f83e98dd1b4013cd703233da19f78942bd0f976a9`.
  All three hosts verified loaded Cocoa SHA-256
  `2fe47f99c36beb0bc5d156b3006321ff94c0f101d4fffc1ff49a5d2bad5abd39`.
  Ordinary source environments and Windows retain upstream pywebview.
- [Mac reports](https://github.com/scopecat-project/scopecat/actions/runs/37436328157/artifacts/11399659221),
  ZIP SHA-256 `8d1df6b36b37f14388394a40200951e9e97caa347440b55ade75edd298249921`;
  [Windows reports](https://github.com/scopecat-project/scopecat/actions/runs/37436328157/artifacts/11399981013),
  ZIP SHA-256 `cba90569644107eda3644bd35366ee75f0bab99055d1818893b43bd1c22b293c`.

Windows did not run a corresponding complete host-restart/cookie-isolation check.
OS focus, menu clicks, tray, native dialogs, visual presentation and complete user
launch remain unevaluated. Mac signature/tamper checks passed, but notarization was
not provided, Gatekeeper rejected the package (return code 3), and Finder first-open
was not evaluated. Installer artifacts were retained without a release/tag/deploy.

## Windows lifecycle follow-up (native execution pending)

`verify_windows_storage.py` extends the existing opted-in `--native-windows`
path on Windows, after the exact-run probe has saved a real Decision draft and
exited. It runs three original packaged native launchers against the unchanged
upstream WebView2 backend. Only their acceptance bootstrap is replaced. No
production patch, alternate profile or new workflow trigger is introduced.

- A and B overlap at one fixed loopback origin, using the same copied executable.
  Both must start without the other's marker or cookie. B writes and clears its
  cookie; A must retain its marker and cookie through both operations. Checks
  compare document cookies, the native cookie API and real HTTP requests.
- Actual WebView2 profiles must be private, and their observed user-data folders
  must differ across hosts. The upstream version and loaded backend hashes are
  recorded. Windows `DeleteAllCookies` must leave B's localStorage marker intact;
  Cocoa's all-website-data clear semantics are not imposed on Windows.
- After both hosts exit successfully, the wrapper stops the application service,
  verifies the old PID/creation-time identity is gone, then starts it again. C
  first checks absence of A/B browser state at the unchanged test origin, then
  loads the exact procedure in the real UI and restores its reviewer. The full
  application-owned draft receipt must equal the earlier real-form edit before
  and after restart. Browser data is never used as a recovery fallback.

Inspect `windows-storage/result.json`, `draft-fixture.json`, `A.json`, `B.json`,
`C.json` and their logs alongside `native-windows/identity.json`. Stage reports,
nonzero exits, deadlines and cleanup failures are required evidence, not optional
warnings. All eight files survive disposable workspace cleanup. Linux tests only
validate failure handling and embedded-script syntax; they cannot qualify native
Windows behavior. The existing eight window checks and independent marker
assertions remain unchanged.

The scope is host isolation/restart, not a new assertion that Windows cookies
survive creation of another window within one host. Upstream 6.2.1
[WebView2 initialization](https://github.com/r0x0r/pywebview/blob/6.2.1/webview/platforms/edgechromium.py)
calls `DeleteAllCookies` for each private WebView; the
[WinForms backend](https://github.com/r0x0r/pywebview/blob/6.2.1/webview/platforms/winforms.py)
uses a host-level temporary directory. This is a source-level risk requiring
separate native evidence before proposing any production dependency repair.
It is not reported as a demonstrated fault or a passed cookie-preservation check.

For this follow-up, dispatch **Full acceptance**, ref
`codex/windows-native-recovery`, profile **native-distribution**, and match the
run's head SHA to the reviewed PR head. No native run is yet recorded for this
follow-up. The accepted `4aee5825` / run `37436328157` remains unchanged and does
not qualify these new checks. Do not use `public-preview` or modify triggers to
obtain dispatch access.

## Historical failed candidate

[Manual run 37424157181, attempt 1](https://github.com/scopecat-project/scopecat/actions/runs/37424157181)
used `native-distribution` at exact commit
`de57155099f8afc1bd50b20b8b48c345fb0cc07f`. Both artifact identity reports match
that source and probe SHA-256
`bf31d4c0b58a6e171442113eb463c0bc66bc04a7ddab24534624efbe4e67f7d4`.
Later documentation changes do not extend this runtime qualification to new code.

| Platform | Evidence and result |
| --- | --- |
| Windows Server 2025, image `windows-2025-vs2026` `20260925.250.1` | [Job 112139903864](https://github.com/scopecat-project/scopecat/actions/runs/37424157181/job/112139903864) passed all eight native checks and host exit/cleanup. [Artifact 11394926096](https://github.com/scopecat-project/scopecat/actions/runs/37424157181/artifacts/11394926096), ZIP SHA-256 `c9b7197cc3758a0d88e4b85abaadec0cfc7a4270ba28357d891376cb19834715`. |
| macOS 26.6.2 arm64, image `macos-26-arm64` `20260907.0351.1` | [Job 112139904049](https://github.com/scopecat-project/scopecat/actions/runs/37424157181/job/112139904049) created and rendered the exact-run secondary, then failed `after_secondary_creation`: both the saved real draft and independent marker became null. [Artifact 11394134805](https://github.com/scopecat-project/scopecat/actions/runs/37424157181/artifacts/11394134805), ZIP SHA-256 `d59e113607f8375efcdcb3605b8074c2802b9f9d3f83ece852ecfca4c72e1ad4`. |

Mac reached only the first named check (real decision edit saved); restoration,
bidirectional bridge ownership, main-selection independence, read invariants,
secondary close and original-run completion checks were not reached. Owned
acquisition cleanup was recorded and there was no `cleanup_error`; the combined
host-exit/cleanup failure reflects the original nonzero host exit, not proof that
cleanup itself failed. Earlier headless/Cocoa teardown checks do not erase this
native failure. No release, tag or deployment was created.

The [approved recovery/store design](architecture/draft-recovery.md) is a follow-up
contract, not a fix in this candidate. Keep the independent storage assertion.

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
production dependency patch is qualified only for the combined candidate above. Neither the application's isolated
`--home` nor pywebview `storage_path` isolates that native store. This may affect
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

## Running the final candidate

After ordinary CI and review, use GitHub **Actions → Full acceptance → Run
workflow**. Select the reviewed acceptance-only integration branch
`codex/native-draft-acceptance` under **Use workflow from**, choose
**profile: native-distribution**, and run once for the final combined candidate.
Confirm the run's head SHA equals the exact integration SHA in the handoff/#885
body. #885's uncombined branch lacks #886's required API and is not this candidate. Do not choose
`public-preview`, which publishes release assets. The native profile builds and
retains installer artifacts but does not create a release, tag or deployment.

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
window creation within the live process; the separate Mac Cocoa check covers
browser ephemerality and application-draft recovery across complete host/service
restart. That restart result is not extended to Windows.
No real devices, private consumer repositories or retained user data are fixtures.
