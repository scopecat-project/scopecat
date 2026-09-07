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

The Experiments page discovers entries from the optional
`LabApplication.launch_provider` callback. It receives a connected `LabClient`
and validated `LaunchRequest`, and returns the matching typed `LaunchCatalog`,
`LaunchPreview` or `LaunchSubmission` from `scopecat.application.launch`.
The HTTP endpoints expose those models through generated OpenAPI types.

Catalog entries carry a stable ID/version, explicit `actions`, diagnostic or
calibration `kind`, independent `configuration_effect`, and project-owned request
and optional review schemas. Review does not imply writeback: a diagnostic may
require judgment while leaving configuration unchanged. The reference lab exposes
one thermometer diagnostic and a q1 timing candidate requiring review; accepting
that candidate as default remains a separate operation in parameter proposals.

The form renders scalar string/number/integer/boolean fields and string-enum arrays.
Other valid project schemas (including numeric enums and nullable type unions)
remain available in the catalog, but the console explicitly directs the operator
to a project-specific form/Python workflow. It does not cast arbitrary catalog JSON
into a second local entry type or implement a general schema renderer.

Preview only reads and compiles, returning its exact request hash, immutable active
configuration binding and bounded first-experiment summary. The form invalidates
preview after input, sample, actor or catalog-version changes. Submission includes
the same binding/hash and one retry key. The project resolves the immutable config
entry rather than silently switching to the latest default, and calls
`lab.procedures.submit(..., expected_config_generation=...)`. The existing admission
transaction resolves an exact retry before checking the current generation for new
work. Reusing a key with different intent conflicts; retrying an admitted request
still returns its procedure after the default changes.

Callbacks run in a separate project process using the daemon interpreter, with
a 60-second timeout. They must not acquire data or activate configuration inside
this bounded request. This is a trusted project-code contract, not a sandbox.
The durable `procedure_id` in a submission receipt leads to existing procedure
steps and their run/analysis/configuration output references. A best-effort dispatch
failure retains that ID and exposes `dispatch_error`; Dispatch existing procedure retries the
existing procedure instead of admitting another.

`LaunchWorkspace` owns catalog selection, `LaunchForm` owns its form/request,
`PreflightSummary` renders preview evidence, and `ProcedureProgress` consumes the
read-only procedure operator projection plus existing review/cancel APIs. The
projection combines the authoritative current step/child, retained history, resource
owner, and observed worker membership. History pagination never changes dispatch
permission. Retained procedures can be reopened from the server-backed history or
their URL after browser or daemon restart. Cancellation requested remains distinct
from cancellation completion; a waiting child's cancellation leaves its resource
owner running. Unknown child effects require reconciliation, and both explicit
dispatch and launch admission replay enforce the same server-side gate.

The server manages project processes for explicitly dispatched procedures, with
at most two live workers. A worker runs the normal durable `resume` operation
until closure, attention, or interpretation input, then exits. Waiting for review
consumes no process. The manager observes submitted review input and wakes ready
procedures; HTTP disconnects do not cancel execution. Durable leases remain the
authority across processes.

Manager membership is retained in `.scopecat/console-procedures.json`. On daemon
restart, only previously managed, ready procedures are eligible to resume; other
CLI procedures are not automatically adopted. An observed nonzero worker exit
pauses automatic dispatch until an explicit Dispatch existing procedure request. Attention
and closed procedures leave the manager. This is process management, not a
hardware recovery or procedure cancellation protocol. Daemon shutdown does not
forcibly kill hardware workers.

Process output goes to `.scopecat/console-worker.log`. A failed spawn retains the
procedure ID and reports `dispatch_error`. The progress view offers explicit dispatch and links to exact child runs and analysis publications; its procedure ID remains in the
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

The progress view cancels idle work immediately or requests **Stop after current
step** while a procedure is executing. Both record actor, reason and the observed
revision. A pending request retains the leased/running state and is not a claim
that hardware has stopped. After the current durable effect returns and its
output is retained, the worker closes the procedure as cancelled without starting
another step. A request arriving between steps also blocks the next step.

Python callers use `lab.procedures.get(id).cancel(actor=..., reason=...)` and inspect
`handle.snapshot.cancellation` and `closure` separately. The request is durable
across reconnects. Worker failure remains failed; uncertain cleanup remains
attention-required. Cancellation does not turn these into successful stops or
authorize retries. An unresponsive worker remains pending until its outcome can
be established; no process is killed and no elapsed-time promise is made.

Completed steps and evidence remain available, and late review input is rejected.
Immediate interruption of a child acquisition remains a separate run operation;
it does not imply cancellation of its parent procedure. This API stops at durable
step boundaries, so an already-started step (including configuration activation)
is allowed to finish. Project code should express separate effects as separate
steps rather than hide a whole experiment sequence inside one effect.
