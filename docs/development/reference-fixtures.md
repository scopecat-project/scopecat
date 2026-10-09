# Reference fixture ownership

The public project has three distinct consumers. The public tutorial sandbox is the ordinary author learning entry; the CLI
starter is the minimal virtual-instrument application. The old reference gallery
is retired as teaching material, including advanced author examples. Its remaining
source temporarily supports integration tests. See the
[behavior and retirement inventory](reference-gallery-retirement.md).

| Current content | Responsibility | Direction |
| --- | --- | --- |
| Server `scaffold.py`, installed framework verifier | Minimal public author workspace | Keep runnable without reference-lab installed; generated scripts use current APIs |
| `reference_lab/tests/test_device_sessions.py` | Direct sessions and calibrated multichannel DC integration | Retains worker behavior after the final gallery scripts retired; new lessons belong in topic sandboxes |
| `reference_lab/quantum_compilation`, `quantum_runner`, `virtual_lab` | Quantum-to-device integration | Maintainer-owned fixture; not a mandatory author dependency |
| `reference_lab/workflows/drag_beta_*` | Calibration, publication and recovery contracts | Preserve integrated evidence; do not teach these as the first acquisition |
| `reference_lab/tests/unit` | Local scientific/compiler behavior | Prefer small fixtures without a daemon |
| Managed author/restart tests | Real process and retained-source contracts | Move generic cases to starter fixtures when they do not require routing/compiler capabilities |
| `packages/lab-teaching`, `packages/lab-tools` | Runnable generic tutorials and installation/sandbox lifecycle | Installed checks execute the starter/reopen Notebooks plus separate test scenarios; full desktop teaching remains #565 |
| Private laboratory courses | Lab-specific methods and scientific workflows | Keep real methods and deployment policy; remove duplicated generic tooling |

This is an ownership classification, not a claim that extraction is complete.
Do not move every reference test into the core tier or remove integration
coverage merely to rename directories. Each extraction must identify the
contract it still exercises and which expensive setup becomes unnecessary.

Keep a small number of full-system journeys. Prefer a minimal real daemon for
source refresh, request rejection, retained analysis and restart when hardware
mapping is irrelevant. Use the full virtual plant for shared claims, channel
routing, compiled buffers and recovery interactions. Mocking these boundaries
would remove the evidence the tests exist to provide.

The reference application's standard bootstrap now starts equipment without
publishing parameter defaults or importing the parameter-table declarations.
The temporary `equipment_bootstrap.py` fixture and manifest-rewriting paths are
retired; shared acceptance capture, snapshot roundtrip and author/device tests use
the same standard manifest in disposable projects. Capture
saves an independent parameter revision and selects its exact setup for previews
and runs; recovery compares both owners as well as retained scientific results.
The combined registry remains empty. Candidate acquisition is not approval:
the shared response contains an unapproved proposal, with approval decoding tested
separately in the UI. Calibration publication and restoration remain covered by
the dedicated DRAG integration workflows.

The experiment-plan journey (`tests/test_experiment_plans.py`) now also starts
with equipment only. It explicitly selects independent parameters/setup and
keeps the combined registry empty while testing comparison handoff, saved-plan
copy/replay, exact sample revisions, structural inputs and candidate child scope.
Branch edits replace the former global-default activation/restore exercise.
The old working-point-specific assertion is replaced by exact independent
parameter/setup selection; it is not a reason to keep working points forever.
The comparison provider uses `comparison_selection(run.snapshot)` to retain
source inputs instead of silently requiring a new global default.

`independent_lab_daemon` now shares that equipment-only process among the plan,
comparison, everyday-author, session and registered-target journeys. It copies no legacy
notebooks and does not set `SCOPECAT_DAEMON_URL`; consumers receive the endpoint
explicitly. `independent_parameters` saves a fresh named revision for each test
that needs one. Target selection pins those parameters and the setup alongside
the target reference; retained plans still execute after catalog/session changes.
Everyday-author tests intentionally supply complete low-level snapshots, but no
longer require an unrelated default configuration just to inspect unchanged state.
These consumers assert that the combined registry stays empty.

Session isolation now uses two distinct parameter revisions with one shared setup.
It retains failed-selection atomicity, frozen preparation, explicit scientific
overrides, per-collection numeric lookup and saved-plan destination/actor inheritance.
Parameter editors do not own sample selection; a supplied editor preserves the
session's subject instead of importing a working point's bundled sample.

Batch and parameter-context journeys also use this equipment-only fixture.
The same saved values may be selected in different cooldowns without asserting
calibration validity. Frozen plans and candidate provenance still retain their
original batch; candidate execution cannot relabel that evidence. This replaces
the old requirement to copy a working point before selecting a new batch.
Parameter-context tests retain numerical response changes, override/replay,
forged-source rejection and immutable history using independent revisions.
Launch replay keeps exact sample/setup/parameter inputs after branch edits.
`AuthorExperiment.prepare/run` accepts `ParameterResolution` explicitly in its
typed API, matching the underlying runner and preserving exact provenance.
Their duplicate unknown-parameter test is covered by the tutorial journey below;
parameter ownership itself no longer implies a sample or working-point scope.

Managed notebook recovery also uses independent parameter branches and explicit
setup selection. It tests frozen unsaved edits, stale-editor conflicts, reopening
the receipt in a fresh Python process and recovering one admission after a lost
response. Reopening distinguishes the current branch head from the exact saved
base of the run's overrides; no working-point latest/original lookup is needed.
Exploration/reanalysis tests use the same equipment-only daemon while intentionally
retaining their explicit low-level snapshots and descriptive context labels.
Those labels carry provenance, not parameter ownership or validity. Both journeys
keep the combined registry empty.

The copied-author launch suite owns a separate equipment-only daemon because it
edits source files and observes live worker batches. Explicit parameter/setup
selection now covers HTTP and Python launch, modified Ramsey timing, typed/editable
requests, required-input diagnostics, source refresh, saved plans, bounded inspection
and reconnecting to an ongoing preview. Overrides retain independent parameter
provenance through the worker boundary. Changing a daily branch does not invalidate
an exact reviewed launch; idempotent submission and new execution both retain its
original inputs. This replaces the former global-default invalidation assertion.

The analysis-recovery journey shares the independent daemon instead of starting
another default-configured process. Its procedure receives an explicit resolved
snapshot. Tests still verify one original acquisition, no reacquisition during
recovery, unchanged failed-procedure history, exact recovery provenance and
idempotency/conflict checks. Run counts compare against the existing store rather
than assuming the service belongs to only one test.

The ordinary-author launcher suite starts two equipment-only daemons to retain
foreign-endpoint isolation. Catalog, preflight, control edits and HTTP dispatch
use explicit parameter revisions. Saving another setup does not mutate an exact
reviewed launch; original-admission replay remains idempotent. The old timing
candidate launcher is removed because its delay was never consumed by compilation
or execution. DRAG tests own scientific candidate review and recovery, while
server scientific-admission tests own distinct-stage plan configurations and
unchanged sample subjects.

Retained device/scientific fixtures start with equipment only and explicitly save
parameter revisions when needed; direct instrument control needs none.
`initial_parameters()` and `bootstrap_config()` remain fixture-data builders for
saved inputs or complete low-level execution snapshots. Selecting inputs does not
publish global defaults or establish scientific acceptance.

No private package may become a prerequisite for public CI or the installed
starter. Shared test helpers belong in testkit only when independently reused;
reference_lab is not a production library to install on physical benches.

## Installed adapter qualification

The `scopecat-testkit` wheel supports the shared authoring, instrument and
workflow configuration helpers and `connection_residency` qualification contract.
They use the self-contained `config_fixtures.simple_scan_config()` preset, returning
a fresh synthetic snapshot on each call. No checkout paths or copied fixture
directories are required. Install the `server` extra for the execution helpers.

The repository's simple-scan JSON remains a configuration-document fixture;
a focused test keeps its content aligned with the preset. The wheel test runs
the existing residency contract from the built artifact outside the checkout.
`scopecat_testkit.paths` and the repository test-selection CLI are workspace-only
utilities, not installed adapter APIs.

## Driver payload identity

Worker decoding supplies `DriverPayload.content_hash` alongside `schema_id` and
the decoded `value`. The hash identifies the exact received codec bytes or
attachment bundle. Drivers can attach it to readback metadata without encoding
the decoded object again; codecs need not round-trip to identical bytes.
Aliases of one payload ID share one decode and content identity.

This is content correlation, not execution identity or proof of acquisition.
The host still owns execution keys, reservation and durable invocation records.
Adapters should prepare a command payload once and reuse it for the recorded
intent and actual submission.

## Installed author package boundary

`fixtures/installed_author_lab` is a tiny wheel-only consumer fixture, not another
user workspace template. `scripts/verify_installed_framework.py` builds and installs it
outside the checkout in the minimal framework environment, on both CI platforms. The
same daemon exercises installed discovery, a local wrapper, original/current
analysis after refresh, retained analysis after restart, and rejection/restoration
of changed installed bytes. This extends the installed framework journey instead of adding a
second full runtime job or a dependency on private laboratory code.

## Teaching capability is not fixture retirement

The reference lab's retired teaching role is distinct from generic teaching in
`lab-teaching`. Seven topic Notebooks and their editable source remain. Their
current use as maintained fixtures does not cancel the Notebook/application
learning journey. Help's single manual-peak practice is only a bounded part of
that goal. The manager topology is retired; the full learning experience has not
been absorbed into the desktop. Track that gap in
[#565](https://github.com/scopecat-project/scopecat/issues/565), with the actual
[execution and acceptance limits](architecture/desktop-packaging.md#teaching-intent-and-acceptance-limits).

## Retirement sequence

The generic private teaching packages, sandbox manager, offline builder and
verification now live in public. The author refresh, complex mean and grouped
restart journeys move with their owner into `packages/lab-tools/tests`.
Private code is not imported by public tests or deliveries.

The reference lab's teaching/workspace-template role is retired. Preserve valid
channel-routing, compiled-buffer, shared-claim and recovery behaviors in a bounded
set of tests. Old notebook interfaces, fixed inventory shapes and global-default
publication assertions are not automatically requirements. For each removal,
identify replacement coverage or explain why the old assertion is obsolete;
compute-only tutorials alone do not replace physical device evidence.

The unknown-parameter declaration/freeze/structural-history journey now uses a
compute-only tutorial daemon (`packages/lab-tools/tests/test_unknown_parameter_authoring.py`).
Its former reference-lab test and probe module are removed. The same assertions
cover unknown consumption, frozen requests and retained scientific snapshots without
the four-qubit device/quantum setup. The remaining legacy structure-context test is
also retired: this tutorial journey now adds an unknown column to an existing
table and checks both unconsumed-column execution and consumed-column rejection.
Working-point structure-origin metadata is no longer an author workflow contract.

## Explicit process fixtures

Device and scientific tests opt into `independent_lab_daemon`; pure
compiler/scientific unit tests do not start a service. The gallery-specific daemon
and notebook-copy fixtures are retired. Journeys that own a cloned workspace use
their own lifecycle fixtures, including the separate launch/author fixtures with
the name `reference_lab_daemon`. The shared `ReferenceLabDaemon` data holder still
serves the DRAG candidate fixture. Keep process ownership explicit when adding tests.

The retained-data UI journeys `run-comparison.e2e.ts` and
`copy-read-only-code.e2e.ts` use their own device-free author workspace under
`apps/scopecat-ui/e2e/fixtures/retained-signal`. One signal parameter replaces the
four-qubit configuration. Real workers retain comparison/source refresh, candidate
rejection and input handoff; a fresh kernel reopens exact old publications without
importing author code or acquiring again. The former `gallery_inputs` helper has
no remaining consumers and is removed. Physical scientific journeys keep their
separate reference fixtures.

Generic browser authoring, parameter editing, sample-map handoff, saved plans,
independent workbench contexts and lost-submission recovery use
`apps/scopecat-ui/e2e/fixtures/author-workspace`. Its two signal objects and empty
device registry exercise exact parameter/setup/source identities without loading
quantum compilation. Source edits, frozen in-flight work, conflicts and restart
still cross real workers and storage. `manual-launch.e2e.ts` and the remaining
`procedure-operator.e2e.ts` cases retain device ownership, device-revision
invalidation and diagnostic admission/restart. DRAG integration tests retain
scientific candidate verification; the legacy timing launcher is retired.

## Shared generated acceptance

`reference_lab/acceptance.py` produces the shared
`examples/reference_lab/fixtures/acceptance.json` from production client responses.
Python checks rerun the HTTP journey; UI tests consume the same records.
Regenerate with `uv run python scripts/generate_reference_lab_acceptance.py`,
or add `--check` to compare against the committed fixture.

Capture uses equipment-only initialization and explicitly saved parameters/setup.
It covers diagnostic acquisition, complex scalar IQ, candidate proposals,
resource wait/cancel and entity-indexed results. Candidate acquisition is not
approval. Scientific values and hashes remain generated output; only the capture's
explicit volatile identity fields are normalized. The scalar IQ comparison uses
`1e-12` absolute/relative tolerance for platform reduction roundoff; other fields
remain exact. Review generated differences when changing producers.

### Setup identity audit (October 2026)

The fixed acceptance setup became stale in
[#898](https://github.com/scopecat-project/scopecat/pull/898)
(`2138c5fd`), then changed again in
[#902](https://github.com/scopecat-project/scopecat/pull/902)
(`73f405ab`). Both retired source recipes inside `reference_lab`; neither refreshed
the shared fixture. The original generator's `--check` passes at #898's parent
`f34ad868` and fails at #898. #902's provider package bytes are unchanged through
the audited main `21129395` (including #923/#924).

This is expected source provenance propagation. On fresh bootstrap,
`backend_artifact_hash` hashes the provider's regular package and helpers using
sorted relative paths and exact file bytes, excluding Python bytecode. It includes
the retired workflow files even though this acceptance slice does not call them.
The artifact hash enters each `DeviceConnection`, its revision reference, and
`SetupDeviceResolution`; `resolved_setup_hash` then determines both setup content
hash and `resolved:<hash>` revision ID. The manual preview binding hashes the
complete reviewed configuration source. No identity codec or product contract
changed in this repair.

| Source | Provider artifact hash | Resolved setup hash | Configuration source hash |
| --- | --- | --- | --- |
| Before #898 | `81425f26…` | `ad37a47f…` | `ec37f58d…` |
| #898 | `811ea2c7…` | `be9679ae…` | `945a029d…` |
| #902 through `21129395` | `e8a76549…` | `ef8522ab…` | `91044357…` |

Two fresh captures at `21129395`, one under a separate temporary root with
`PYTHONHASHSEED=917`, produced identical comparison differences. Historical checks
ran from separate worktrees and locked environments on Python 3.14.7/Linux.
The fresh-bootstrap artifact path hashes package bytes, not absolute paths,
timestamps, package-install order or dependency versions. This does not assert
that deliberately edited source bytes or retained driver-source environments
have the same identity.

The complete generated JSON audit found exactly nine identity leaves: each of
`controls_scalar`, `controls_scan` and `launch_preview` changed
`manual_state.binding.config_source_hash` and
`reviewed.config_source.setup.{content_hash,revision_id}`. Three additional scalar
IQ components differed only within the existing `1e-12` roundoff tolerance;
all other values, keys, types and lengths matched. The correction takes the nine
identities from the existing generator and retains the already accepted IQ
representatives. It adds a negative comparison test for every affected identity
leaf; scientific values, source/provenance and stable fields keep their existing
exact comparisons outside that narrow IQ tolerance.

The real Python/HTTP generator, including #923's independent readout assertions,
and the remaining gallery suite provide the replacement evidence. This repair
adds no fixture protocol, acquisition, native qualification or hardware claim,
and does not close the remaining retirement or performance work in #773/#520.

## Exploratory author fixture

| Owner | Responsibility |
| --- | --- |
| Experiment author | Scientific helpers, experiment inputs/scans and retained-data analysis |
| Laboratory maintainer | Shared operations, parameter schemas and fixture composition |
| Compiler/driver maintainer | Hardware mapping, acquisition modes, SDK and connection/fault semantics |

Scientific choices (frequency, amplitude, timing, point/shot shape and retained
products) remain visible to the author. Worker leases, compiler IR and SDK buffers
are supporting diagnostics for the same frozen plan and data.

`reference_lab/workflows/exploratory_signal.py` and `reference_lab/exploration.py`
provide four analytic runs: two synthetic samples with local `q0`, each in parked
and shifted contexts. Known carrier values are fixture inputs, not discovered
calibration. `tests/test_exploration.py` checks retained identities, independent
analysis over the same data, missing-input rejection and reconnection. Context
labels describe provenance; they do not resolve parameters or prove validity.
Source refresh and transitive source retention are separately covered by the
[author revision journey](../how-to/refresh-author-code.md).

This fixture provides no instrument or physical evidence. Unknown-sample tests
must expose a missing response instead of silently recentering on the fixture's
known answer. Current user-facing outcomes are described in
[workflow evaluations](workflow-evaluations.md).

## Historical planning records

The retired [pilot roadmap](https://github.com/scopecat-project/scopecat/blob/53a74eaae7d2195fa4430eda7d737506d13da9fd/docs/development/lab-pilot-roadmap.md),
[work-slice plan](https://github.com/scopecat-project/scopecat/blob/53a74eaae7d2195fa4430eda7d737506d13da9fd/docs/development/pilot-work-slices.md) and
[calibration integration record](https://github.com/scopecat-project/scopecat/blob/53a74eaae7d2195fa4430eda7d737506d13da9fd/docs/development/calibration-branch-closeout.md)
remain in Git history. Current fixture ownership is described above; current
commands are in the [contributor guide](index.md).
