# Build a local Mac desktop for acceptance

This is a packaged acceptance path, not a one-command daily development loop.
For daily native-window + Vite + backend editing, use the
[unified source entry](public-preview.md#daily-source-desktop).

Build a local `.app` to exercise Settings, native dialogs, clipboard and your
editor with isolated development data. The application manages its own service;
author folders do not require separate daemons. This does not install into
`/Applications` or publish a release.

## Build and open

From the public checkout, use uv, Python from `.python-version`, Node from
`.node-version`, pnpm matching `apps/scopecat-ui/package.json`, and Apple's Command
Line Tools (`xcode-select -p`). VS Code interactive cells need the Python and
Jupyter extensions. Users of an already built app do not need these build tools.

```sh
uv sync --locked
SCOPECAT_TRIAL="$(mktemp -d "$HOME/scopecat-local.XXXXXX")"
SCOPECAT_TRIAL_ID="$(uuidgen | tr '[:upper:]' '[:lower:]')"
uv run --locked python -m scripts.app_build.delivery "$SCOPECAT_TRIAL/delivery"
uv run --locked python -m scripts.app_build.native_package \
  "$SCOPECAT_TRIAL/delivery" "$SCOPECAT_TRIAL/Scopecat.app" \
  --trial-home "$SCOPECAT_TRIAL/home" \
  --trial-locator "$SCOPECAT_TRIAL/home/locator" \
  --trial-id "$SCOPECAT_TRIAL_ID"
uv run --locked python -m scripts.app_build.trial_package "$SCOPECAT_TRIAL/Scopecat.app"
codesign --verify --deep --strict "$SCOPECAT_TRIAL/Scopecat.app"
```

Delivery builds the locked frontend and wheels. Native packaging includes its own
Python, the author-environment payload and the maintained Cocoa dependency repair.
Keep the `SCOPECAT_TRIAL` path; it is outside the checkout so repository cleanup
does not remove its data. Build outputs need fresh paths. Rebuild a new `.app`
when changing framework code; editing the checkout does not update an existing
package or its execution wheels.

The inspection command reads package markers and paths only. It does not prepare,
start or restart the application, and it does not replace native signature checks.
After reviewing the exact identity and paths, explicitly start the trial once:

```sh
SCOPECAT_LOCATOR_HOME="$SCOPECAT_TRIAL/home/locator" \
  "$SCOPECAT_TRIAL/Scopecat.app/Contents/MacOS/Scopecat" \
  --home "$SCOPECAT_TRIAL/home"
```

Use that same explicit command to reopen. A marked trial refuses a missing or
conflicting `--home`, duplicate path arguments, a missing/conflicting locator
environment, or a missing/modified policy. It never falls back to the normal
user location. A double-click without the required launch context is intentionally
refused. Its bundle ID and displayed application name are distinct from a release.
Closing a window may leave the app in the menu bar; use **Quit** for a full exit.
A duplicate launch for the same home activates the existing host.

The fixed home must be fresh at build time. Paths are absolute and the trial tree
must not contain symlinks or Windows junctions. Normal release builds without trial
flags retain their existing paths and startup behavior. See the
[acceptance boundaries](native-window-acceptance.md#isolated-trial-probe) before
running programmatic native checks.

Use [First experiment](../getting-started/quickstart.md) for ordinary author work.
Candidate selection, task claims and acceptance results belong in the relevant
issue or PR, not in this reusable startup guide.

## Choose the developer mode

| Goal | Entry and boundary |
| --- | --- |
| Native desktop with ordinary Settings preparation | The packaged `.app` above; package-owned Python and author-environment resources |
| Daily native desktop with HMR | `uv run --locked python -m scripts.dev`; retained worktree home, real backend and Settings resources; [daily usage](public-preview.md#daily-source-desktop) |
| Inspect or maintain an existing backend | `scopecat app --home APPLICATION_STATE --action status`; acts on that explicit application owner, without a separate daemon per author folder |

The [Vite guide](public-preview.md#daily-source-desktop) describes frontend
prerequisites and source registration. Give Vite a separate development home,
not a running native application's data directory.

For a source-native window while debugging the framework:

```sh
pnpm --dir apps/scopecat-ui install --frozen-lockfile
pnpm --dir apps/scopecat-ui run build
uv run --locked scopecat app --home "$SCOPECAT_TRIAL/source-window" --source . --action desktop
```

This shortcut uses checkout Python and the built GUI. A fresh home has no delivery
payload, so **New code folder / prepare Python** cannot provision independent
environments there. It also uses upstream pywebview without the packaged Cocoa
repair. Use the packaged entry for ordinary Settings/native behavior.

## Data paths and troubleshooting

Native `--home` is an installation root: application state is under its `/data`.
The maintenance CLI's `--home` points directly to that state directory:

```sh
SCOPECAT_LOCATOR_HOME="$SCOPECAT_TRIAL/home/locator" \
  "$SCOPECAT_TRIAL/Scopecat.app/Contents/Resources/python/bin/scopecat" \
  app --home "$SCOPECAT_TRIAL/home/data" --action status
```

For the packaged maintenance console, set the same locator environment and pass
`home/data`, not the native installation root. The same guard covers `app`,
`notebook`, `teach`, their module entries, runtime workers and locator discovery.
The direct native-bootstrap module entry and server-maintenance CLI are not
supported ways to bypass a marked package's entry.

Startup errors after validation are in
`$SCOPECAT_TRIAL/home/data/native-start.log`; desktop errors are in
`$SCOPECAT_TRIAL/home/data/desktop/desktop.log`. A policy rejection happens before
these files are opened and may only appear in the launch terminal/native alert.
Use the read-only package inspection command above for validation, not
`--check-result` (which prepares software) or the automatic release verifier
(which rejects marked trials). Trial packages cannot be wrapped as release
installers. The explicitly targeted trial-window probe has its own approval and
ownership gates; its existence is not permission to launch a user application.

Local builds use ad-hoc signing, not a trusted/notarized release. Preserve startup
errors instead of changing macOS security settings. Source checks and browser
simulations do not establish native editor, clipboard, dialog or window acceptance;
record those observations separately in their owning issue.

## Direct-control close recovery qualification

For the #1492/#1493 follow-up, start with an empty isolated data space rather
than pre-filling the operator. The trial-window probe covers the visible
connection refusal, closing that empty controller, entering an operator in the
same reopened controller, and using the actual saved-result button/desktop
bridge. Its native closing callback is a program assertion, not evidence that a
person clicked the operating system's close button.

Keep the following evidence separate and bind it to the final candidate:

- UI/service fault injection: a rejected request versus an unknown response,
  preserved request identity, lost-session result retention, failed or pending
  save/finish, cancelling only the close intent, and late responses.
- Native WebView probe: real frontend controls and bridge, close-event callback,
  repeated close/cancel, controller ownership release, and saved-result reuse.
- Platform interaction: actual window X, application Quit and Windows source
  launcher's console Ctrl+C. A JavaScript click or native API callback does not
  qualify an OS input event. If safe input tools are unavailable, report that
  boundary rather than substituting an auto-launching observer.

Use a fresh fixed-home/fixed-locator trial for each native run. Keep existing
trials and default data untouched. All fixtures must use simulation drivers;
software shutdown and released ownership do not prove physical outputs stopped.
These checks do not implement or qualify the separate emergency-exit proposal.
