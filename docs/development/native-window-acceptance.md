# Exact-run native window acceptance

The real native check for #882 ran on 2026-10-06: **Windows passed; macOS failed**.
Linux checks, browser acceptance and platform-smoke are not native passes.

## Recorded candidate

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
the real packaged UI, unmodified pywebview backend and production default store,
`DesktopWindows`, shared `DesktopSession`/`ApplicationRuntime`, JS bridge and
service. A real SDK scan pauses after its first ingestion. The main page's opening
button creates an exact-run window; changing the main selection must leave that
window unchanged. Reading records must preserve the run set, held ingestion count
and snapshot. Closing the second native WebView must leave the same service and
run active; releasing the scan must complete that run without another run.

A real ProcedureWorker creates one waiting interpretation through the service,
without acquiring another run. The real Decisions form saves a reviewer draft.
The probe then unmounts the form before creating the second window, so its save
effect cannot repair or hide store clearing. It checks the original draft and an
independent localStorage marker after creation, restoration, reads and closure;
returning to Decisions must restore the reviewer. The second window's storage is
also recorded, without imposing a new sharing policy. Both injected JS bridges
must direct title calls to their own Python Window object; this is bridge ownership
evidence, not a claim about OS title rendering.

## Safety and failure handling

**Run the WebView probe only on the disposable GitHub-hosted macOS/Windows VMs
created by the manual job. Never run it on a user's Mac, a retained desktop or a
self-hosted runner.** Current pywebview Cocoa uses and clears its default website
store when each private window is created. Neither the application's isolated
`--home` nor pywebview `storage_path` isolates that native store. This may affect
unrelated retained WebKit data under the same host identity. No local override is
supported, and no store/backend monkeypatch is used to avoid discovering a bug.

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
creation clears the draft, the check must fail and retain that evidence. A future
product fix requires the platform evidence and separate semantic review.

Waits and the host have deadlines. Timeout cleanup uses the existing owned
process-tree helper. A PID plus creation-time receipt permits acquisition-client
cleanup after host crashes; detached service cleanup uses the isolated runtime's
ownership checks. Host-exit or cleanup failure makes acceptance fail even if UI
assertions passed. Logs and JSON survive disposable cleanup.

## Running the final candidate

After ordinary CI and review, use GitHub **Actions → Full acceptance → Run
workflow**. Select `codex/native-window-acceptance` under **Use workflow from**,
choose **profile: native-distribution**, and run once for the final candidate.
Confirm the run's head SHA equals the reviewed draft PR head. Do not choose
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
complete production startup, or cross-restart browser persistence. The draft
check does cover storage survival across window creation within the live process.
No real devices, private consumer repositories or retained user data are fixtures.
