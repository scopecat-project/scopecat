# Reference fixture ownership

The public project has three distinct consumers. The public tutorial sandbox is the ordinary author learning entry; the CLI
starter is the minimal virtual-instrument application. The old reference gallery
is retired as teaching material, including advanced author examples. Its remaining
source temporarily supports integration tests. See the
[behavior and retirement inventory](reference-gallery-retirement.md).

| Current content | Responsibility | Direction |
| --- | --- | --- |
| Server `scaffold.py`, installed pilot verifier | Minimal public author workspace | Keep runnable without reference-lab installed; generated scripts use current APIs |
| `reference_lab/notebooks` | Legacy integration inputs | Extract valid behavior, retire redundant scripts; write new lessons in topic sandboxes |
| `reference_lab/quantum_compilation`, `quantum_runner`, `virtual_lab` | Quantum-to-device integration | Maintainer-owned fixture; not a mandatory author dependency |
| `reference_lab/workflows/drag_beta_*` | Calibration, publication and recovery contracts | Preserve integrated evidence; do not teach these as the first acquisition |
| `reference_lab/tests/unit` | Local scientific/compiler behavior | Prefer small fixtures without a daemon |
| Managed author/restart tests | Real process and retained-source contracts | Move generic cases to starter fixtures when they do not require routing/compiler capabilities |
| `packages/lab-teaching`, `packages/lab-tools` | Runnable generic tutorials and installation/sandbox lifecycle | Public installed CI executes shipped Notebooks; private consumes these packages |
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

The shared acceptance capture and snapshot roundtrip use
`reference_lab/fixtures/equipment_bootstrap.py` in their disposable projects.
This fixture starts equipment without publishing parameter defaults. Capture
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

The legacy gallery still starts with transitional parameter defaults. Reassess
its calibration/default-publication assertions against the new design; retain
needed behaviors in focused tests and retire the old consumers. There is no
requirement to port every script before removing bootstrap defaults. The
acceptance fixture is not a second user mode.

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
user workspace template. `scripts/verify_pilot_bundle.py` builds and installs it
outside the checkout in the clean pilot environment, on both CI platforms. The
same daemon exercises installed discovery, a local wrapper, original/current
analysis after refresh, retained analysis after restart, and rejection/restoration
of changed installed bytes. This extends the installed pilot instead of adding a
second full runtime job or a dependency on private laboratory code.

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

The shared `reference_lab_daemon` is opt-in. Gallery tests request it directly
or through `reference_lab_notebooks`; pure compiler/scientific unit tests do not
start a service. Journeys that own a cloned workspace use their own lifecycle
fixture rather than starting an unrelated reference daemon as well. Keep process
ownership explicit when adding tests.
