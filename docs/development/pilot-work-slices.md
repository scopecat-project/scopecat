# Pilot work slices and acceptance fixtures

Use the [work-slice template](https://github.com/scopecat-project/scopecat/blob/main/.github/ISSUE_TEMPLATE/work-slice.md) for an
issue and carry its outcome, owner, paths, contracts, dependencies, checks and
non-goals into the PR. One primary owner takes a slice through its Python, HTTP
and UI consumers. This is coordination for the existing self-review workflow,
not a new approval hierarchy.

## Isolated project state

Each worktree has its own `.venv`, UI dependencies and generated outputs. Never
point a second daemon at another lane's project state. From the worktree root:

```sh
uv sync --locked
pilot_project=$(mktemp -d "${TMPDIR:-/tmp}/scopecat-pilot.XXXXXX")
cp -R examples/reference_lab/config examples/reference_lab/src "$pilot_project/"
cp examples/reference_lab/scopecat.toml "$pilot_project/"
uv run scopecat start "$pilot_project" --api-only
# Use sc.open_project(pilot_project).connect() from Python.
uv run scopecat stop "$pilot_project"
```

The copied project's `.scopecat` directory contains its endpoint and durable
state. Keep `SCOPECAT_DAEMON_URL` unset when discovering this project; an explicit
endpoint or that environment variable takes precedence over discovery. Keep a
state directory if investigating a failure. Delete only your own temporary
project after stopping its daemon. The fixture generator below creates and stops
its own temporary project automatically and needs no proprietary package or
physical device.

## Shared scenarios

`examples/reference_lab/src/reference_lab/acceptance.py` captures production
client responses into `examples/reference_lab/fixtures/acceptance.json`. Python
checks validate the records and rerun the real HTTP journey; UI tests import this
same JSON into existing API/rendering consumers. Response objects are not copied
by hand into a second set of mocks.

- **Read-only diagnostic:** `temperature_diagnostic` samples the existing virtual
  mixing-chamber thermometer without applying device state or editing accepted
  configuration. Its completed run retains temperature, resistance and acquisition
  evidence in the normal measurement dataset. A separate compilation review
  exercises the existing review response without admitting a run.
- **Reviewed candidate:** the existing entity-indexed Ramsey workflow produces a
  q1 channel-delay proposal, runs its candidate and accepts it through the public
  manual-review operation. The proposal page includes its durable approval.
- **Resource waiting/cancellation:** a normal direct thermometer session blocks
  the diagnostic procedure's `context.run`. Cancelling the waiting procedure
  cancels its exact admitted child; it never samples the reserved instrument.
- **Entity-indexed analysis:** the existing raw Ramsey dataset schema carries q0
  and q1 identities, source products and acquisition policy. The gallery's channel
  unavailable scenario exercises selection and retained unavailable evidence.

Only explicitly named capture IDs and wall-clock fields are normalized. Scientific
values, content hashes, entity alignment and proposal deltas remain generated
production output. Regenerate after changing their producers; inspect the diff.

## Focused commands by lane

Run from the worktree root unless the command specifies otherwise. These are
iteration commands, not replacements for the repository CI gate.

| Lane | Focused check |
|---|---|
| Integration / Python + HTTP fixtures | `uv run pytest examples/reference_lab/tests/test_acceptance.py` |
| Operator / launcher | `uv run pytest packages/scopecat-server/tests/test_launch_preview.py` |
| Execution / resource ownership | `uv run pytest packages/scopecat-server/tests/test_automation_runtime.py -k resource_wait` |
| Data / entity analysis | `uv run pytest examples/reference_lab/tests/test_gallery_notebooks.py -k entity_axis` |
| Delivery / isolated startup | `uv run pytest packages/scopecat-server/tests/test_lifecycle.py` |
| Device / connection residency | `uv run pytest packages/scopecat-server/tests/core_integration/test_connection_residency.py packages/scopecat-server/tests/test_connection_residency_worker.py` |
| UI consumers | `pnpm --dir apps/scopecat-ui test src/test/reference-lab-acceptance.test.tsx` |

## Contract producers and shared files

| Contract / source of truth | Generation or verification |
|---|---|
| Python wire models: `packages/scopecat/src/scopecat/daemon/{wire,views,reviews}.py`; HTTP routes: `packages/scopecat-server/src/scopecat_server/http/` | `pnpm --dir apps/scopecat-ui run check:api` exports OpenAPI from the real app and regenerates `src/api-schema.d.ts`; run `generate:api` to update intentionally. `.generated/ui-api.openapi.json` is an intermediate. |
| Arrow storage: `packages/scopecat/src/scopecat/measurements/recording_arrow.py` and measurement records; cross-language sample: `testing/scopecat-testkit/src/scopecat_testkit/measurement_arrow_fixture.py` | `uv run python scripts/generate_ui_measurement_arrow_fixture.py --check`; omit `--check` to regenerate the committed Arrow fixture. The wire version and codec, not that fixture, define storage. |
| Instrument declarations and `packages/scopecat-instruments/src/scopecat_instruments/package_manifest.py`; renderer: `packages/scopecat/src/scopecat/sdk/instruments/client_codegen.py` | `uv run python scripts/generate_instrument_clients.py --check`; omit `--check` to regenerate clients, projections and catalogs. |
| Reference-lab acceptance producer and its public workflows | `uv run python scripts/generate_reference_lab_acceptance.py --check`; omit `--check` to regenerate the shared JSON. |

Coordinate especially `daemon/wire.py`, `daemon/views.py`, `daemon/reviews.py`,
measurement records/Arrow codec, reference-lab `application.py`, and generated UI
`api-schema.d.ts`. Record their current owner in the work slice. If a storage
version or response meaning changes, the dependent issue is blocked on that
producer even when Git can merge the text cleanly. Read-only investigation and
local fixture experiments can proceed while waiting. Land the producer, rebase
consumers, regenerate from source and rerun focused checks; never hand-resolve
conflicts in generated files.

Before merging, run existing CI checks: Python tests, `basedpyright`, `lint-imports`,
Ruff lint/format, generated instrument/API checks, UI tests/typecheck/build and
documentation checks. `.github/workflows/ci.yml` remains authoritative, including
its Windows test lane and browser checks. No parallel test framework is introduced.
