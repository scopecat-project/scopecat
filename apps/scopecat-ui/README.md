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

The server manages project processes for explicitly dispatched procedures, with
at most two live workers. A worker runs the normal durable `resume` operation
until closure, attention, or interpretation input, then exits. Waiting for review
consumes no process. The manager observes submitted review input and wakes ready
procedures; HTTP disconnects do not cancel execution. Durable leases remain the
authority across processes.

Manager membership is retained in `.scopecat/console-procedures.json`. On daemon
restart, only previously managed, ready procedures are eligible to resume; other
CLI procedures are not automatically adopted. An observed nonzero worker exit
pauses automatic dispatch until an explicit Resume execution request. Attention
and closed procedures leave the manager. This is process management, not a
hardware recovery or procedure cancellation protocol. Daemon shutdown does not
forcibly kill hardware workers.

Process output goes to `.scopecat/console-worker.log`. A failed spawn retains the
procedure ID and reports `dispatch_error`. The progress view offers Resume
execution and links to child runs and Decisions; its procedure ID remains in the
URL. Configuration acceptance stays in the declared procedure and review policy.
The generic GUI does not accept calibration parameters itself. Existing projects
without a provider show an empty state.

Decision review renders retained run, sample and project analysis publications
inline, including curves, facts and proposed parameter differences. Table changes
are expanded to changed fields; quantity representation changes remain visible.
Simple scalar response schemas use form fields, while complex structures retain
the JSON editor. The same revision/hash checks and recorded reviewer apply to both.
The GUI does not replace the procedure's verification or acceptance policy.

Launch forms also support arrays of string enums as multi-select fields. Membership
changes invalidate previews; procedure progress links to retained analysis results.

The progress view can cancel a ready or review-waiting procedure. It records the
actor and reason and submits the observed revision; a concurrent worker start or
review response causes a conflict rather than cancelling a changed execution.
Completed steps and evidence remain available, and late review input is rejected.
Python callers use `lab.procedures.get(id).cancel(actor=..., reason=...)`.
Executing and attention-required procedures cannot use this idle cancellation
operation. Cancelling a child acquisition remains a separate run operation; it
does not imply cancellation of its parent procedure or immediate hardware stop.
