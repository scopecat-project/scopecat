# Try the current desktop on your Mac

Use this when testing the ordinary application, Settings, file dialogs, clipboard
and VS Code from a reviewed source candidate. Build a local `.app` once, then open
it normally with an isolated test home. You do not start a daemon for each author
folder. The application starts and stops its own service.

This is a local development build, not a release or an installation into
`/Applications`. The native app and its author-environment resources come from the
same checkout. `lab_tools.dev --source .` is instead a browser/Vite debugger: it
has no native Settings bridge and is not the entry for this exercise.

## Select the candidate and prepare once

For the ordinary-entry candidate, start in an existing public repository clone:

```sh
git fetch origin test/ordinary-settings-author-flow
git worktree add --detach ../scopecat-author-trial origin/test/ordinary-settings-author-flow
cd ../scopecat-author-trial
git rev-parse HEAD
```

Use another unused worktree path if that one already exists. Record the full SHA;
fetching a newer branch later does not change this detached checkout. After this
PR merges, select the reviewed main commit instead. Keep existing working edits
in their original checkout.

Prerequisites are uv, Python from `.python-version`, Node from `.node-version`,
pnpm matching `apps/scopecat-ui/package.json` (currently 12.9.1), and Apple's
Command Line Tools (`xcode-select -p` must find them). VS Code needs its Python
and Jupyter extensions. These are developer build prerequisites; ordinary users
of an already built application do not need Node, pnpm or this checkout.

Run these commands from the selected public root:

```sh
uv sync --locked
SCOPECAT_TRIAL="$(mktemp -d "$HOME/scopecat-author-trial.XXXXXX")"
uv run --locked python -m lab_tools.delivery "$SCOPECAT_TRIAL/delivery"
uv run --locked python -m lab_tools.native_package "$SCOPECAT_TRIAL/delivery" "$SCOPECAT_TRIAL/Scopecat.app"
```

Keep the printed/selected `SCOPECAT_TRIAL` path. It is outside the checkout so
repository cleanup does not delete your trial data. The first command restores
locked Python dependencies; delivery installs locked frontend dependencies and
builds the GUI and wheels. Native packaging adds its own Python, author-environment
payload and the maintained Cocoa dependency repair. No DMG, installer, release,
private laboratory environment or physical device is needed.

Outputs must be fresh paths. If a build fails, preserve its error and directory;
use a new trial directory for a corrected attempt. Do not overwrite a running app
or copy arbitrary wheels into its checked payload. Builds download dependencies
and Python; actual duration depends on the local cache and network.

## Open the actual desktop

First check that the package can prepare its isolated application home without
opening a window:

```sh
"$SCOPECAT_TRIAL/Scopecat.app/Contents/MacOS/Scopecat" --home "$SCOPECAT_TRIAL/home" --check-result "$SCOPECAT_TRIAL/startup.json"
cat "$SCOPECAT_TRIAL/startup.json"
open -n "$SCOPECAT_TRIAL/Scopecat.app" --args --home "$SCOPECAT_TRIAL/home"
```

The result's Python should be inside this `.app`, and state should be
`$SCOPECAT_TRIAL/home/data`. This prepares software only, not author source or an
experiment. In the window, **Settings → Software and data** identifies this same
application home. Stop and retain the error if the package cannot open; changing
macOS security settings is not part of this handoff. The development package uses
local ad-hoc signing, not a trusted/notarized public release.

For subsequent launches, set `SCOPECAT_TRIAL` to the retained path and repeat only
the `open -n ... --args --home ...` command. Keep the explicit `--home`: opening the
`.app` without it selects the normal user data location instead. Closing a window
may leave the app in the menu bar; use its **Quit** action before testing a full
restart. A duplicate launch for the same home activates its existing host.

For a newer candidate, build a fresh `.app` from that exact checkout. Do not infer
that editing the checkout updated an already packaged app or its execution wheels.
This exercise does not require reinstalling or replacing your ordinary Scopecat.

## Small local Codex handoff

The local Codex can claim this work when the user chooses; this guide does not
create a remote task or connect to a Mac. Start with the candidate SHA, this guide
and [First experiment](../getting-started/quickstart.md). Record macOS/architecture,
app path, full source SHA and `startup.json`. Keep a short record of actual pauses,
errors and prompts; distinguish user reading time from software waiting.

1. In the actual desktop, use **Settings → Author code → New code folder** and
   the native folder picker. Choose the trial directory and a new folder name.
   Confirm the ready path and Python shown. Do not prepare the folder through a
   script first; empty Settings entry is the behavior under observation.
2. Open that folder in VS Code, select its displayed `.venv` Python, and run
   `notebooks/02_edit_scan.py` as Python interactive cells. Run Imports, Connect
   and Preview individually. Confirm output appears with no run yet. Run Submit
   once, then Result and analysis and Reopen. Check `[0.5, 1.0, 0.5]` in **Runs**.
   Make one real source edit (`return scale / ...` → `return 2 * scale / ...`),
   Preview, and explicitly Submit once more; check `[1, 2, 1]`.
3. On that exact saved run, click **Copy read-only code** and paste into a fresh
   VS Code kernel after the starter's Imports/Connect cells. Connect provides
   `session` and `author` as names for the same connection, so the copied snippet
   works directly. Execute the pasted read cell, not Run All, then
   inspect `list(run.measurements()["result"].require_values())`. Match the run ID
   and values. Record whether native
   clipboard works or the manual-copy fallback is needed.
4. Use **Open result in new window** for that run and verify the same ID. Exercise
   the run's **Export Scopecat file…** dialog once, saving under the trial directory;
   cancel once first if useful. Record focus/activation or path-selection friction.
   This is not another window/storage/cookie qualification matrix.
5. Save source, keep the printed receipt path, close the author connection, Quit
   Scopecat and restart the Python kernel. Reopen the same isolated application.
   Run Imports/Connect, select the existing receipt and run Reopen/Close. Check
   the exact old run without Submit or another analysis. Return a brief report
   with observations, screenshots where useful, exact IDs and remaining blockers.

Automated Linux coverage already exercises real preparation, syntax-error repair,
preview/explicit acquisition, exact GUI results and restart through independent
kernels. The purpose here is the actual Mac/editor/clipboard/picker/window seam,
not repeating all accepted journeys. Experimental form drafts are a known separate
[recovery boundary](ordinary-author-entry.md#separate-experiment-form-observation);
do not interpret their current restart loss as parameter-draft regression or
implement a new persistence system during this local observation. No real data,
physical devices, release publication or full native acceptance matrix is needed.

## Troubleshooting and other developer modes

For this native trial, startup errors are in
`$SCOPECAT_TRIAL/home/data/native-start.log`; desktop errors are in
`$SCOPECAT_TRIAL/home/data/desktop/desktop.log`. The read-only CLI status command is:

```sh
uv run --locked scopecat app --home "$SCOPECAT_TRIAL/home/data" --action status
```

Native `--home` is an installation root; `scopecat app --home` is the application
state directory, hence the extra `/data` above. Prefer the desktop's retry/Quit
controls. `scopecat app --action stop` targets that one explicit state directory
when deliberately maintaining its backend; it is not ordinary first-use setup.

For rapid Python/UI framework debugging with a source-native window, the existing
short entry is:

```sh
pnpm --dir apps/scopecat-ui install --frozen-lockfile
pnpm --dir apps/scopecat-ui run build
uv run --locked scopecat app --home "$SCOPECAT_TRIAL/source-window" --source . --action desktop
```

That uses checkout Python and the built GUI. A fresh source-window home has no
registered delivery payload: **New code folder / prepare Python** cannot provision
independent environments there. Source Python also uses upstream pywebview, without
the packaged Cocoa repair. Use the locally packaged `.app` above for the ordinary
Settings/native handoff; do not silently substitute this shortcut for it.

For browser hot reload, use the separate
[Vite entry](public-preview.md#browser-frontend-debugging). It starts one owned
backend and Vite, and Ctrl-C stops both. Do not point it at this native trial home
or start a second backend over the native application's data.

## What was actually checked remotely

On Linux at implementation commit `051f68bc52bd044ac3465d58f03c5d0bc45cd0e9`,
the development delivery/toolchain and Settings/kernel/GUI verifier passed from
empty data. `lab_tools.dev --source . --home <fresh-path>` also actually started
Vite and its owned backend: frontend HTTP and proxied `/api/v1/health` both returned
200; Ctrl-C stopped them and left no application installation selection. The
application CLI status and native-package help entry ran successfully.

The Mac package/launcher instructions were checked against `native_package`,
`native_bootstrap`, `InstallationPaths`, `desktop.run` and `ApplicationRuntime`.
**No Mac build, native window, editor, clipboard or dialog was executed remotely.**
Those remain the local observations above; passing platform smoke is not evidence
that a human completed this handoff.
