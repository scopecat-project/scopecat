# Scopecat project console

Local-only React client for the Scopecat daemon. The production build is
designed to be served by the daemon so all API calls stay on relative
`/api/v1/*` paths.

```sh
pnpm install
```

For frontend development, run an API-only daemon and Vite in separate
terminals:

```sh
uv run --project ../.. scopecat serve <project> --api-only --port 8765
pnpm run dev
```

Vite proxies `/api` to `http://127.0.0.1:8765`. Set
`SCOPECAT_DAEMON_ORIGIN` to use a different local daemon address.

`pnpm run build` writes only to this application's ignored `dist/` directory.
From the repository root, use that bundle for a source-checkout preview with
`scopecat start <project> --static-dir apps/scopecat-ui/dist`.
The repository-level `scripts/build_server_distribution.py` assembles the
server in a temporary directory and verifies that its wheel and source
distribution contain the same bundle.

`pnpm-workspace.yaml` applies the same 3-day minimum release age as Renovate
to direct and transitive dependency resolution.

## Development checks

```sh
pnpm run format:check
pnpm run lint
pnpm run typecheck
pnpm run test
```

Oxfmt owns layout and Oxlint owns correctness checks. Generated API types and
package-manager output are excluded; their producing tools remain authoritative.

## API contract

`src/api-schema.d.ts` is generated from the UI-used subset of the daemon's
OpenAPI contract. Run
`pnpm run generate:api` after changing a UI-used transport model; CI runs
`pnpm run check:api` so the generated contract cannot drift. Keep endpoint calls
and presentation mapping beside their owning feature or data domain, keep shared
transport behavior in `src/api-client.ts`, keep stable application aliases in
`src/api-contract.ts`, and treat the generated daemon contract as authoritative.

## Browser end-to-end test

```sh
pnpm run test:e2e:install
pnpm run test:e2e
```

The test first builds the current UI into `dist/` and passes that directory
explicitly to the daemon. It then
creates a temporary starter project, starts its daemon on a dynamic port,
executes the generated first notebook, and drives the daemon-served GUI through
a parameter default and an exact-entry restore through the activation ledger. It
also saves a notebook analysis candidate,
accepts it in the GUI, follows its provenance back to the producing run, and
restores the previous default. The fixture removes the daemon and project after
success or an assertion failure. If identity-safe daemon shutdown itself fails,
it retains the project and reports the daemon log for manual cleanup.

## Project calibration launch

The Calibrations page discovers entries from the optional
`LabApplication.launch_provider` callback. A callback receives a connected
`LabClient` and a validated `LaunchRequest` (`list`, `preview` or `submit`). `list` returns
`calibrations` entries with `id`, `title`, `description`, and a JSON Schema
`request` object. The initial form supports scalar string, number, integer and
boolean properties, required fields, defaults and string enums. Projects should
advertise only inputs supported by this form. `preview` receives the entry ID and
its input values; the provider validates the project request and compiles against
configuration. Its returned JSON becomes the expandable preview detail.

Callbacks run in a separate project process using the daemon interpreter, with
a 60-second timeout. `list` and `preview` only read and compile. An entry may set
`can_submit: true`; its `submit` callback validates the preview configuration
hash/generation and calls `lab.procedures.submit`, returning `procedure_id`.
Callbacks must not acquire data or activate configuration inside this bounded
request. This is a trusted project-code contract, not a sandbox.

The server separately wakes a project process for the admitted procedure. Its
normal `resume` operation uses the existing durable procedure and lease rules;
HTTP disconnects do not cancel it. The process waits across interpretation input,
then continues until closure or attention. Multiple wakeups are deduplicated
locally, while the durable lease remains authoritative across processes. Process
output goes to `.scopecat/console-worker.log`. A failed spawn does not erase the
submission: the response retains the procedure ID and reports `dispatch_error`.
The progress view offers Resume execution and links to child runs and Decisions.
Its procedure ID is retained in the URL for reopening the page.

Workers exit on daemon connection failure. After a daemon restart or process
failure, reopen progress and request resume for a runnable procedure; this does
not bypass attention or retry a failed hardware step. Configuration acceptance
remains part of the declared procedure and its review policy. The generic GUI
does not accept calibration parameters itself. Existing projects without a
provider show an empty state.
