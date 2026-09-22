# Retire the reference gallery by behavior

The public tutorial sandboxes and starter replace the reference gallery's
teaching role, including its former advanced-author-example role. The remaining
reference source is a temporary integration fixture, not an application whose
interfaces must be ported to every new framework design. Git retains retired
source; installed historical environments and scientific data are untouched.

## What is still useful

| Legacy input | Behavior worth retaining | Destination and exit condition |
| --- | --- | --- |
| `00_lab_tour.py` | Instrument views and parameter inspection | Retired with its fixed inventory/row-count test. Server `test_instruments.py::test_instrument_views_expose_only_safe_configuration_summaries` covers inventory views; starter and parameter teaching cover author inspection. The reference fixture's exact device list is not a product invariant. |
| `02_session_lifetime.py`, `21_scan_shapes.py`, `40_measurement_workbench.py` | Reattach without acquisition, scan semantics, retained data projections | Retired with duplicate gallery tests. Session closure/reattachment now uses the existing starter restart journey; scan and dataset behaviors have focused core coverage listed below. |
| `05_sample_workflow.py` | Exact sample revision and analysis provenance | Retired. Dedicated sample binding/restart and sample-analysis isolation tests cover the behavior with equipment-only initialization and an empty parameter registry. |
| `22_channel_map.py` | Routing and shared physical channels | Retired fixed-map presentation/test. Generic route completeness tests, compiled multiplexing constraints and real device journeys remain; exact four-qubit endpoint strings are fixture data, not a product contract. |
| `10_direct_control.py`, `33_multichannel_dc_bias.py`, `35_awg_output_monitor.py`, `50_ragged_scope_capture.py` | Shared device ownership, physical routes, entityless diagnostics, ragged acquisition | Keep focused real-worker coverage. Add future device-topic sandboxes using current APIs rather than wrapping the old gallery. |
| `23_q0_ramsey.py`, `26_parallel_multiplexed_ramsey.py`, `27_channel_timing_candidate.py`, `32_quantum_program_inspection.py`, `36_q0_fixed_if_lo_sweep.py` | Quantum execution, multiplexing, candidate lineage, layered preview and signed IF/LO semantics | Retired with duplicate gallery tests and unused probability-result experiment wrappers. Focused runner tests and shared acceptance retain execution evidence, as detailed below. |
| `20_flux_spectroscopy.py`, `24`–`25`, `28`–`29`, `31`, `34` | Compiled buffers, channel conflicts, signed IF/LO semantics, multiplexed readout and topology | Extract minimal compiler/runner inputs and keep a bounded full-device journey. Review duplicated recipes and hard-coded configuration assumptions instead of preserving their signatures. |
| `30_drag_calibration.py` | DRAG acquisition, fit and uncertainty display, exact candidate lineage and independent verification | Retired. `test_typed_candidates.py` retains the real-device simulation and analysis with independent parameters/setup and no default mutation. Global publication/restore is no longer a required author journey. |
| `workflows/drag_beta_*` | Independent verification, ownership of edited cells, conflict detection, durable publication/recovery | Retained integration dependencies, not recommended author code. Single-candidate branch publication now exists; rebuild a minimal calibration fixture before replacing legacy cohort/automatic publication consumers. |
| `drag_beta_calibration_procedure`, `DragBetaProcedureIntent`, active-generation request key | Single-target fit, verify, explicitly publish and use accepted gate values | Retired the unused default-publishing procedure, registration, intent and mock acceptance graph. The real DRAG test now publishes to an explicit branch and executes the standard-gate fixture with the exact accepted revision. Verify-only cohort members and composition policies remain. |
| `quantum_compilation`, `targets/list_mode`, `provider`, `virtual_lab` | Deterministic device/compiler integration | Retain only dependencies of named scientific/device tests; extract generic framework capabilities where justified. Compute-only teaching does not replace device evidence. |
| Shared acceptance and `snapshot_roundtrip.py` | Real HTTP payloads and exact current-format recovery | Already use independent parameters/setup and an empty combined registry. Keep this evidence as legacy bootstrap consumers are removed. |

Rows not marked retired are an inventory, not a claim that replacement coverage is
complete. Test existence alone does not establish that an assertion is valid
under the target design. Record replacement evidence or why an assertion
expresses an obsolete requirement before removing it.

### Retired generic cases: retained evidence

- Reference `tests/unit/test_quantum_runner.py` retains actual bare-instrument
  execution, compiled multiplexing constraints, batch-invariant IQ results and
  selected-point preview across authored/logical/scheduled/physical layers with
  no acquisition. Its fixed-IF host-effect test also executes the sweep and
  checks three measured carriers (4.79, 4.80, 4.81 GHz) at -50 MHz IF.
  Shared `acceptance.py` runs the parallel raw-IQ experiment and its timing
  candidate, checks the exact analysis proposal source and leaves approval and
  parameter/setup selection unchanged. This replaces the timing gallery's
  provenance summary. The unused `q0_ramsey`, `parallel_two_qubit_ramsey` and
  `ParallelRamseyDataset` wrappers are removed; reusable programs and the
  `RamseyDataset` used by editable author code remain.
- `packages/scopecat-server/tests/test_samples_runtime.py::test_sample_revision_and_run_binding_survive_restart`
  covers sample creation/revision, bound run identity and restart. Server
  `test_project_analysis_runtime.py::test_sample_analysis_is_scoped_to_runs_bound_to_that_sample`
  covers sample-owned publication, rejects unrelated/reference-role inputs and
  preserves the analysis owner after restart. Both initialize equipment explicitly
  without publishing a global parameter default. Their explicit run snapshots
  still use small combined test inputs; this is not a claim that all sample
  fixtures have migrated to independent parameter APIs.
- `packages/scopecat/tests/planning/test_routing.py` checks complete route
  selection. Reference unit
  `test_quantum_runner.py::test_parallel_qubit_set_compiles_to_one_entity_axis_result_group`
  checks actual shared I/Q and acquisition constraints in the compiled footprint.
  Retained multichannel-DC and parallel-Ramsey journeys exercise physical values
  and multiplexed acquisition, beyond the retired mapping display.

- `packages/scopecat-server/tests/test_lifecycle.py::test_cli_daemon_first_use_loop_uses_dynamic_port_and_cleans_record`
  retains a snapshot and records, rejects reads through closed run/dataset
  handles, and reattaches after restart with identical evidence and no new run.
  It uses independent parameters and adds no daemon startup. Core
  `test_remote_lab.py` also checks connection ownership and no HTTP after close.
  The old assertion that every run must carry `ConfigRegistryRunConfigSource`
  is obsolete; exact source identity is retained without requiring that kind.
- `packages/scopecat/tests/program/test_point_plan_policy.py` checks repeat
  expansion and canonical point order; `test_point_plan_invocations.py` checks
  invocation composition. `planning/test_point_order.py` checks snake traversal,
  and `planning/test_system.py::test_planning_executes_repeated_grid_in_snake_order`
  checks its execution. The server's ragged point-cloud worker test retains
  actual acquisition across daemon/worker boundaries. The deleted gallery scan
  test checked only point counts and layout names, not DRAG or VNA science.
- `packages/scopecat/tests/measurements/test_dataset.py` covers unit-aware
  selection, availability, grouping and grid/Xarray identity. Server
  `core_integration/test_run_handle.py::test_run_projects_paged_measurements_into_one_arrow_reader`
  checks durable Arrow pagination and schema. These directly cover the deleted
  workbench's summary counts without acquiring a resonator scan first.

## Development order

The [calibration composition contract](calibration-composition.md) separates pure
parameter merging from scientific acceptance and atomic publication. Its shared
parameter-only merge core is implemented; retained multi-source candidates and
joint verification remain prerequisites for replacing the old cohort workflow.

1. Remove gallery recommendations from learning routes. Keep the former tutorial
   URL as a retirement notice. Stop adding or mechanically migrating old examples.
2. Remove redundant presentation scripts and fixture-shape assertions. Extract
   remaining generic behavior into existing starter/teaching/core tests.
3. Define candidate verification/publication on independent parameter branches:
   retain exact source and verification evidence, require an explicit publication
   decision, and reject conflicting head/cell changes. Derive requirements from
   these behaviors, not from the old DRAG procedure implementation.
   [Issue #778](https://github.com/scopecat-project/scopecat/issues/778) tracks
   atomic single-candidate publication and its retained verification evidence.
4. Build a focused calibration fixture and, when the API is usable, a new
   calibration-topic sandbox. Remove replaced DRAG/default-config workflows and
   their unused dependencies in the same functional change.
   Single-target acquisition through branch publication and accepted-gate
   execution now has a focused real-daemon test. The former global-default
   procedure is retired. Multi-target composition, durable automation and
   applicability still require their own replacement design; they are not
   implicitly satisfied by this single-candidate path.
5. Remove legacy bootstrap and combined-config APIs once valid behaviors have
   new owners. An obsolete gallery consumer is a retirement task, not a reason
   to retain a compatibility layer.

Keep each change complete enough to review: requirement, replacement or explicit
retirement rationale, affected consumers and removal of unused dependencies.
Do not move the whole tree to another folder and keep an active second
implementation. No old-data migration or historical-environment rewrite is part
of this cleanup.
