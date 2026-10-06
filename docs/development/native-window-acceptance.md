# Exact-run native window acceptance

This check is prepared for #882; macOS and Windows execution is still pending.
Do not infer a native pass from Linux checks, browser acceptance or platform-smoke.

The existing `Full acceptance` workflow's manual `native-distribution` profile
builds both platform packages. `verify_native_application.py` now invokes
`verify_native_windows.py` after preparing its isolated author environment and
retained run. No new trigger or production test interface is introduced. Probe
contract/failure tests belong to integration, not the fast PR suite.

The probe copies the candidate package and runs its native executable with an
acceptance bootstrap. It uses the real packaged UI, `DesktopWindows`, shared
`DesktopSession`/`ApplicationRuntime`, JS bridge and service. A real SDK scan pauses
after its first ingestion. The main page's native opening button creates an exact
run window; changing the main selection must leave the second window unchanged.
Reading real records must preserve the run set, held ingestion count and snapshot.
Closing the second native WebView must leave the same service and run active;
releasing the scan must complete that run without creating another run.

## Isolation and failure handling

The candidate is unchanged. Application data, author files, acquisition gates and
reports use the outer verifier's disposable acceptance directory. The probe strips
inherited endpoint/Python overrides. Windows WebView storage is explicitly under
that directory. The copied executable and Mac Info.plist preserve native host
identity and teardown, unlike running the bundled Python directly.

Current pywebview Cocoa uses and clears its default website store even with
`private_mode=True`; `storage_path` does not isolate it. Before launching the copied
host, the probe changes only that copy's Cocoa backend to attach a real
`nonPersistentDataStore` before constructing each WKWebView. Source-shape guards
fail closed if the dependency changes, and both actual native stores must report
nonpersistent. The original backend is not edited. This test-specific store choice
means **production browser persistence is not qualified** by this probe.

Waits and the native host have deadlines. Timeout cleanup uses the existing owned
process-tree helper. A PID plus creation-time receipt permits client cleanup after
the native host crashes; detached service cleanup uses the isolated application's
existing ownership checks. Cleanup or host-exit failure makes acceptance fail,
even if earlier UI assertions passed. Logs and JSON survive disposable cleanup.

## Running the final candidate

After ordinary CI and review, use GitHub **Actions → Full acceptance → Run
workflow**. Select the final candidate branch under **Use workflow from**, choose
**profile: native-distribution**, and run once. Before interpreting results,
confirm the run's head SHA equals the approved draft PR head. Do not select
`public-preview`, which publishes release assets. This profile builds and retains
installer artifacts but does not create a release, tag or deployment.

For each of the Mac and Windows jobs, inspect the `native-<platform>` artifact's
`native-windows/result.json`, `identity.json`, `host.log`, `acquisition.log` and
`cleanup.log`. Match workflow SHA, bundle sources/hash and probe hash; require a
passed result including host exit/cleanup and all window assertions. A missing
report, skipped platform or earlier build failure is not a native pass.

The evidence is native component/bridge acceptance with programmatic DOM actions.
It does not establish OS focus, actual menu clicks, visual presentation, tray
behavior, complete production startup or persistent browser storage. Those
boundaries do not prevent this automated check from running. No real devices,
private consumer repositories or retained user data are part of the fixture.
