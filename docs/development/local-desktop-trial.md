# Run a local Mac desktop

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
uv run --locked python -m lab_tools.delivery "$SCOPECAT_TRIAL/delivery"
uv run --locked python -m lab_tools.native_package "$SCOPECAT_TRIAL/delivery" "$SCOPECAT_TRIAL/Scopecat.app"
open -n "$SCOPECAT_TRIAL/Scopecat.app" --args --home "$SCOPECAT_TRIAL/home"
```

Delivery builds the locked frontend and wheels. Native packaging includes its own
Python, the author-environment payload and the maintained Cocoa dependency repair.
Keep the `SCOPECAT_TRIAL` path; it is outside the checkout so repository cleanup
does not remove its data. Build outputs need fresh paths. Rebuild a new `.app`
when changing framework code; editing the checkout does not update an existing
package or its execution wheels.

To reopen, set `SCOPECAT_TRIAL` to the retained path and repeat only the `open`
command. Keep `--home`: omitting it selects the normal user data location.
Closing a window may leave the app in the menu bar; use **Quit** for a full exit.
A duplicate launch for the same home activates the existing host.

Use [First experiment](../getting-started/quickstart.md) for ordinary author work.
Candidate selection, task claims and acceptance results belong in the relevant
issue or PR, not in this reusable startup guide.

## Choose the developer mode

| Goal | Entry and boundary |
| --- | --- |
| Native desktop with ordinary Settings preparation | The packaged `.app` above; package-owned Python and author-environment resources |
| Browser frontend hot reload | `uv run --locked python -m lab_tools.dev --source . --home PATH`; checkout Python, one owned backend and Vite; Ctrl-C stops both; no native DesktopAPI |
| Inspect or maintain an existing backend | `scopecat app --home APPLICATION_STATE --action status`; acts on that explicit application owner, without a separate daemon per author folder |

The [Vite guide](public-preview.md#browser-frontend-debugging) describes frontend
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
uv run --locked scopecat app --home "$SCOPECAT_TRIAL/home/data" --action status
```

Startup errors are in `$SCOPECAT_TRIAL/home/data/native-start.log`; desktop errors
are in `$SCOPECAT_TRIAL/home/data/desktop/desktop.log`. To check package preparation
without opening a window:

```sh
"$SCOPECAT_TRIAL/Scopecat.app/Contents/MacOS/Scopecat" --home "$SCOPECAT_TRIAL/home" --check-result "$SCOPECAT_TRIAL/startup.json"
cat "$SCOPECAT_TRIAL/startup.json"
```

The reported Python should be inside this `.app`, and state should be the same
`home/data` shown in Settings. This prepares software, not author source or an
experiment. Prefer the desktop's retry/Quit controls. The CLI's `--action stop`
stops the one explicitly selected backend when needed for maintenance.

Local builds use ad-hoc signing, not a trusted/notarized release. Preserve startup
errors instead of changing macOS security settings. Source checks and browser
simulations do not establish native editor, clipboard, dialog or window acceptance;
record those observations separately in their owning issue.
