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
- **Complex scalar grid:** `coherent_ramsey` reduces acquired IQ shots to one
  native complex mean at each delay/phase point. The same generated records check
  real/imaginary storage and remote Arrow values, then exercise magnitude, phase,
  real and imaginary UI views without an aggregate axis or stored projections.
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
production output. The fixture check compares only the coherent scalar IQ mean's
real/imaginary components with absolute and relative tolerance `1e-12` in `ratio`,
accounting for native reduction roundoff across platforms. Generation never
rounds these values; record identity and remote Arrow comparisons within each run
remain exact. All other fixture fields remain exact. Regenerate after changing
their producers; inspect the diff.

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

The frequency/amplitude control slice captures `controls_scalar` and
`controls_scan` previews from one declaration and checks a real six-point
analytic run. It preserves normalized units, fixed/scanned source mode and
configuration/derived provenance. The shared UI fixture exercises source and
unit edits through preview and submit payloads. The HTTP launch integration
separately compares durable single-point fixed and explicit-scan requests to
Python invocation edits and verifies their measured values agree. No analytic
floating-point output is added to the strict golden preview fixture.

## Exploratory work: roles and executable foundation

A maintained calibration finishing successfully does not qualify unknown-sample
initialization, changing working points or ordinary Python editing. Assess those
journeys separately, with the three ownership levels below.

| Owner | Normal edits | Boundary requiring a different owner |
|---|---|---|
| Ordinary experiment author | A small experiment, scientific helpers, scan/input values and completed-data analysis | A new meaning for a shared operation or a shared parameter contract |
| Shared laboratory capability maintainer | Reusable operations, parameter schemas, fixture setup and project composition | New compiler lowering, device protocol or execution guarantee |
| Compiler/driver maintainer | Hardware mapping, native programs, acquisition modes, SDK and connection/fault semantics | Scientific acceptance still belongs to the experiment owner |

Frequency, amplitude, time, phase, reference frame, point/shot shape and retained
products are scientific choices: keep them visible. Worker leases, compiler IR
and SDK buffers are maintenance diagnostics, available on demand. Both views
must explain the same frozen plan and data, not execute separate implementations.

### One editable experiment, four retained runs

`src/reference_lab/workflows/exploratory_signal.py` is the author-owned file
(relative to `examples/reference_lab`). It contains a short ordinary Python
experiment, a resonance helper and a thresholded mean analysis. Editing its scan,
input default, helper or analysis does not require changes to catalog, service,
cohort, compiler or driver files. The [ordinary author path](../how-to/write-an-experiment.md) builds on this
fixture with initial module discovery, GUI control editing/submission and a Python
adapter. The separate [author revision journey](../how-to/refresh-author-code.md)
validates complete helper/analysis snapshots, atomic refresh and old admission
recovery. This four-run fixture alone does not qualify those guarantees.

`src/reference_lab/exploration.py` belongs to the fixture maintainer. It reuses
the existing reference application, parameter table, sample registry and run
store. `seed_exploration(lab)` creates two synthetic samples with the same local
`q0` identity, each in `parked` and `shifted` contexts, and returns four durable
run IDs. The carriers are 4.8, 4.9, 5.0 and 5.1 GHz respectively. The experiment
is a deterministic analytic model with a 50 MHz half-width; these values are
fixture inputs, not inferred calibration results or hidden device ground truth.
It performs no instrument operation and provides no physical qualification.

Create the isolated project as described above, then from a Python process
connected to that project:

```python
import scopecat as sc
from reference_lab.exploration import seed_exploration
from reference_lab.workflows.exploratory_signal import exploratory_mean

pilot_project = "/path/to/your/scopecat-pilot.copy"
with sc.open_project(pilot_project).connect() as lab:
    run_ids = seed_exploration(lab)  # once per fresh fixture project
    retained = lab.get_run(run_ids[0])
    whole = retained.analyze(exploratory_mean(minimum=0))
    selected = retained.analyze(exploratory_mean(minimum=0.5))
```

Here `pilot_project` is the path created in the shell example; supply that path
in Python rather than the shell variable's name. Keep the IDs or find them in the
normal run list after reconnecting. There is no committed database, new JSON
wire fixture or second example app. Callers own a fresh project and must not seed
another lane's state. The focused check is:

```sh
uv run pytest -q -n 0 examples/reference_lab/tests/test_exploration.py
```

It checks four distinct configuration hashes and sample/context bindings,
expected peak positions, two published analyses over identical retained inputs,
and exact original data/request/sample/config identity after sample revision
and client reconnection. All configurations are explicit per-run trial snapshots;
the accepted project default remains unchanged. A `context_id` records provenance
only: it does not select or merge parameters automatically.

`exploration_config(None)` deliberately removes q0's carrier. Its preview must
reject the missing value rather than reuse another sample or working point's
value. This baseline does not supply a missing-value editor or a parameter
resolver. Retain the actual diagnostic when evaluating the later context/schema
work; do not weaken validation or substitute zero to make a journey pass.

### Task goals and observations

These are goals for a participant who knows basic Python, not click scripts.
Record what the participant could accomplish, what they had to understand and
where a maintainer intervened. A proposed mechanism is not an observed success.

| Journey | Goal and observable success | Foundation versus later work |
|---|---|---|
| Edit and refresh | Change scan density, then helper response and analysis; run the revised experiment and still interpret the old result | The revision-aware Python/GUI path snapshots configured source roots, validates refresh in a fresh process and pins admitted work; direct imports retain single-process semantics. |
| Preserve a plan | Leave an unfinished scan, inspect an old run, return without losing edits; save two alternatives and reopen them | This fixture supplies an editable invocation. Route-local preservation is #436; durable saved plans are #437. |
| Select parameters | Move between A/B and parked/shifted, identify each value's source, try a local override, and encounter a missing carrier without inheriting a misleading default | Four explicit snapshots and the missing case exist. Parameter-context resolution and structural edits are #438/#439. |
| Reanalyze and continue | Compare original and selected means from retained data, explain exclusions, then use the result to choose the next scan | The two Python publications exist without reacquisition. The connected selection/comparison/next-run experience is #440. |

Unknown-sample work should include finding a useful scan window when the initial
window misses the response. The fixture is sufficient to expose workflow friction;
it does not prove that an automatic search converges or that a scientific fit is
valid. Test authors must not silently recenter scans using the fixture's carrier
and describe that as successful exploration.

### Identity invariants and parallel ownership

- A completed run keeps its exact run ID, configuration content hash, frozen
  sample revision/hash/role/context, admitted inputs and point plan. Switching
  current sample/configuration must not rewrite historical interpretation.
- New analysis publishes a new result over named retained input identities; it
  neither mutates raw measurements nor acquires again. Retained history remains
  readable without rebuilding the current experiment return tree.
- An experiment ID is a name, not a complete code revision. Existing request
  records and compute diagnostics must not be described as a transitive source
  archive or replay guarantee. Configured author revisions separately retain the
  full declared local source roots and pin the exact helper/analysis implementation.
  Their recovery contract requires matching maintained source and external
  environment. Pre-revision runs retain their original weaker provenance; old
  stores require their pinned reader across schema boundaries.
- Keep at most three development worktrees active. Each fixture lane owns its
  source, state directory and generated outputs. Serialize daemon/worker/browser,
  full-suite and benchmark tests; claim the slot only when ready to run and release
  it before editing a failure. Pure/static checks can proceed independently.
- Subsequent slices reuse this experiment and its records. Coordinate edits to
  `exploratory_signal.py` and `exploration.py` with the current owner; do not create
  a replacement registry, parameter store or gallery. Changes to shared contracts
  land before consumers even when their text does not conflict.
