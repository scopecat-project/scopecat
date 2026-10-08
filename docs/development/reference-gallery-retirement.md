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
| `10_direct_control.py`, `33_multichannel_dc_bias.py` | Shared device ownership, physical routes, multi-device bias control | Keep focused real-worker coverage. Add future device-topic sandboxes using current APIs rather than wrapping the old gallery. |
| `23_q0_ramsey.py`, `26_parallel_multiplexed_ramsey.py`, `27_channel_timing_candidate.py`, `32_quantum_program_inspection.py`, `36_q0_fixed_if_lo_sweep.py` | Quantum execution, multiplexing, candidate lineage, layered preview and signed IF/LO semantics | Retired with duplicate gallery tests and unused probability-result experiment wrappers. Focused runner tests and shared acceptance retain execution evidence, as detailed below. |
| `20_flux_spectroscopy.py` | Complex VNA traces, flux fit, exact candidate and review provenance | Retired presentation script. Dedicated spectroscopy worker and focused scientific tests retain acquisition, numerical fits and source identity; see the mapping below. |
| `24`–`25` | Host/quantum composition and point-local routing | Extract minimal compiler/runner inputs and keep a bounded full-device journey. Review duplicated recipes and hard-coded configuration assumptions instead of preserving their signatures. |
| `29_channel_unavailable.py` | Independent multiplexed-channel availability, entity provenance and HTTP traces | Retired duplicate presentation/acquisition. The existing shared acceptance worker result now owns the assertions; mapping below. |
| `31_topology_scaled_ramsey.py` | Topology-selected entities retain their identity through compilation and results | Retired with its dedicated experiment/result wrapper. Core topology selection and the focused runner test retain selection and metadata coverage; the assertion mapping and corrected numerical row correspondence are below. |
| `28_channel_conflict_diagnostic.py` | Logical pulse-overlap diagnostics | Retired with its daemon/gallery test and dedicated `conflicting_drive` / `conflicting_drive_program` wrappers. Existing quantum scheduling and authoring tests own the diagnostic, as detailed below. |
| `34_xy_lo_sweep.py`, `35_awg_output_monitor.py`, `50_ragged_scope_capture.py` and their workflow modules | Shared owners, signed IF, entityless claims and variable-length acquisition | Retired the scripts, experiments, result wrappers and XY facade. Current contract owners and deliberately withdrawn fixture assertions are mapped below. |
| `30_drag_calibration.py` | DRAG acquisition, fit and uncertainty display, exact candidate lineage and independent verification | Retired. `test_typed_candidates.py` retains the real-device simulation and analysis with independent parameters/setup and no default mutation. Global publication/restore is no longer a required author journey. |
| `workflows/drag_beta_*` | Independent verification, ownership of edited cells, conflict detection, durable publication/recovery | Acquisition, fit and scientific scoring remain focused integration dependencies. Old freshness, verify-only procedure, semantic-merge publisher and automatic-publication registry are retired. `drag_branch_calibration` and real-daemon tests cover target-complete joint remeasurement, retained rejection, exact branch publication and restart/lost-response recovery. |
| `drag_beta_calibration_procedure`, `DragBetaProcedureIntent`, active-generation request key | Single-target fit, verify, explicitly publish and use accepted gate values | Retired the default-publishing procedure and later verify-only cohort members. The real DRAG test publishes to an explicit branch and executes the standard-gate fixture with the exact accepted revision. |
| `quantum_compilation`, `targets/list_mode`, `provider`, `virtual_lab` | Deterministic device/compiler integration | Retain only dependencies of named scientific/device tests; extract generic framework capabilities where justified. Compute-only teaching does not replace device evidence. |
| Shared acceptance and `snapshot_roundtrip.py` | Real HTTP payloads and exact current-format recovery | Already use independent parameters/setup and an empty combined registry. Keep this evidence as legacy bootstrap consumers are removed. |

Rows not marked retired are an inventory, not a claim that replacement coverage is
complete. Test existence alone does not establish that an assertion is valid
under the target design. Record replacement evidence or why an assertion
expresses an obsolete requirement before removing it.

### Retired generic cases: retained evidence

- Quantum `tests/test_pulses.py::test_parallel_intervals_cannot_overlap_on_one_logical_signal`
  checks `pulse_signal_overlap`, the exact non-q0 logical drive signal, both
  conflicting instruction identities and the structured offending instruction.
  Existing `test_program_authoring.py::test_gate_and_pulse_can_bind_in_parallel_before_final_signal_check`
  keeps the independent authoring boundary: parallel gate and direct pulse bind
  successfully, then lowering/materialization preserves the signal and both
  event identities in the scheduling rejection. Neither needs a daemon or lab
  configuration. The deleted gallery checked only the code and a hard-coded q0
  substring; its two wrappers had no other repository consumers.
  This is logical signal validation before physical placement, not evidence of
  AWG/digitizer conflicts or runtime exclusion. Shared physical claims, compiled
  multiplexing and real-worker device journeys remain with their existing owners.
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

## Topology gallery retirement: assertion mapping

The `31_topology_scaled_ramsey.py` presentation, its gallery test and the
`topology_scaled_ramsey` / `TopologyScaledRamseyDataset` experiment/result wrappers
are retired. Repository consumer inspection found only the deleted notebook using
those wrappers. The lower-level `topology_scaled_ramsey_program` remains in
`workflows/ramsey.py`: `virtual_lab/quantum_responses.py` still dispatches its
Ramsey response by program identity. Similar names do not imply identical lifetimes.

| Former assertion | Current evidence or withdrawal |
| --- | --- |
| Connected selection yields `q1`, `q0`, `q2` | Core `compiler/test_topology_selection.py::test_topology_selection_resolves_a_stable_connected_region` owns deterministic anchored ordering, connection-kind filtering and stability after topology expansion. `test_topology_selection_reports_an_unsatisfied_connected_count` owns rejection when too few connected entities exist. |
| Selected entities appear in the compiled and acquired result | Reference `unit/test_quantum_runner.py::test_topology_selection_retains_entities_through_compilation_and_results` uses the existing minimal set-readout input with a topology selection. It checks bound product axes, each compiled acquisition's entity scope, the placement's selected entity set and the executed dataset's entity index and shape. The companion row-correspondence test also checks distinct per-entity values and shot availability, as described below. |
| Three points/records and shape `3 × 3 × 64` | Three delay values and 64 shots were gallery fixture choices. The replacement checks one point and seven shots against the selected entity count. Existing runner batch-invariance coverage and core scan/dataset tests own scan and dimension behavior; the old delay vector is not a protocol requirement. |
| `iq_shots` and exact `shared/topology-scaled-ramsey/...` dimension strings | The minimal runner input checks its result alias and typed entity dimension. The retired wrapper's generated path strings are fixture identities, not a required public namespace. |
| Draw output contains `parallel_each $targets` | Withdrawn presentation-only text check. The replacement compiles and executes the existing `parallel_each` input; it does not promise the old draw wording. |
| Run status is `completed` | Checked by the same focused in-process runner execution. Existing bare-instrument runner and device-runtime tests retain device integration evidence. This is not a new daemon/worker or hardware acceptance claim. |

### Row correspondence corrected during migration

The stronger mapping check exposed a pre-existing gap at base `d20ed757`:
`map_quantum_target_results` retained canonical target acquisition order
(`q0`, `q1`, `q2`), while the product and dataset entity axis retained topology
selection order (`q1`, `q0`, `q2`). Reference result realization emits rows in
mapping order. The former gallery checked labels and shape only, so it did not
establish numerical row-to-entity correspondence.

Quantum `program_results.py` now associates acquisitions by their declared qubit
owner and orders them by the product's entity axis. The final mapping is rebuilt
through the existing builder, including its contract fingerprint. Explicit sealing
rejects duplicate/missing owners and rows supplied in the wrong entity order.
Pulse acquisition canonicalization, device row order, SDK and storage are unchanged.

`test_quantum_runner.py::test_topology_result_rows_match_the_product_entity_order`
is a passing regression, with no xfail. It checks a non-sorted topology selection
and feeds differently valued, differently available shots for each entity through
actual list-mode correlation and realization. Reversed raw address order still
produces the values and availability belonging to each product entity. Quantum
`test_result_contracts.py` independently covers an explicit non-sorted qubit set,
two points and two products, explicit mapping equivalence, fingerprint rebuilding,
and rejection of incorrect ordering, missing addresses and repeated/foreign owners.
Existing scalar result and local-dimension tests remain regression coverage.

This corrects future executions through this mapping path. No historical scientific
data was inspected or rewritten, and the tests do not establish which previously
recorded runs, if any, were affected. Identical synthetic responses can hide a swap;
the numerical regression deliberately uses distinct values and missing-shot patterns.

The topology slice left the `29` unavailable-channel scenario (retired separately
below), parallel raw-IQ shared
acceptance, worker chain, provider/setup inventory and retained scientific data
unchanged. No performance improvement is claimed. The broader retirement in #773
remains open.

## Unavailable-channel gallery retirement: assertion mapping

`29_channel_unavailable.py` and its gallery test are retired. They acquired the
same `parallel_raw_ramsey` input already executed by shared acceptance. The
existing generator now calls `_check_independent_readout` on the exact source run
referenced by its candidate execution. This reads the actual daemon/worker result
and HTTP trace without another acquisition or another daemon. Checks live outside
the device package so moving test assertions does not alter provider source identity.

| Former assertion | Current evidence or withdrawal |
| --- | --- |
| Completed run, two records, two entities and 64 shots | The generator checks the completed source and full result shape from the existing worker acquisition. Two delays and 64 shots remain this shared input's choices, not a new compatibility promise. |
| q0 available at both delays; q1 missing only at the second delay | The generator selects each entity by identity and checks every point's availability and exact `missing` reason. No whole-channel fallback or compute-only substitute. |
| Source product belongs to each entity; acquisition policy is independent | The generator checks the entity/product correspondence and `independent` policy on the acquired variable. |
| HTTP trace contains three usable series and one q1 failure | The generator requests the same bounded two-entity trace and checks all series/failure labels. |
| Exact `shared/parallel-two-qubit-ramsey/shot` dimension string and summary dictionary | Withdrawn presentation identities. Typed entity-axis discovery, result shape and actual selection remain; this path string is not a protocol. |
| Independent selection, reordered entities and partially missing shots | Existing core `measurements/test_dataset.py::test_entity_dimensions_support_labeled_selection_and_partial_availability` and `test_entity_alignment_reindexes_values_provenance_and_evidence`; reference `unit/test_list_mode_results.py` and `test_quantum_runner.py::test_topology_result_rows_match_the_product_entity_order` retain lower-level combinations with distinct numerical values. |
| Shared physical multiplexing | Existing `test_quantum_runner.py::test_parallel_qubit_set_compiles_to_one_entity_axis_result_group` retains shared I/Q outputs, one digitizer input and acquisition ownership. The acceptance source still executes the real simulated device worker chain. |

`parallel_raw_ramsey`, its result wrapper, program, response model and provider
remain unchanged. In addition to acceptance, `launch.py` uses the input for
candidate/launch scenarios. That is the bounded current dependency, not a promise
to maintain the gallery API indefinitely. No new fixture or compatibility layer is
needed for this slice. Examples 20/24–25, other compiler/device inventory, #773 and
#615 remain unfinished. Signal/ordinary-analysis benchmarks and their inputs,
Help/Notebook design and retained user data are untouched.

### Verification boundary

At base `9aaf0173e0802c3cb1bc1c845e9c4634365ebc38`, the local Linux
Python 3.14.7 run of
`uv run --locked pytest -n 0 examples/reference_lab/tests/test_acceptance.py examples/reference_lab/tests/test_gallery_notebooks.py --durations=10`
reported 9 passed and 1 failed in 49.77 seconds. The deleted test's call took
3.08 seconds. The failure predates this change: the isolated acceptance generator
reports a stale committed fixture. Recursive comparison found nine identity leaves
changed: `manual_state.binding.config_source_hash` and
`reviewed.config_source.setup.{content_hash,revision_id}` under each of
`controls_scalar`, `controls_scan` and `launch_preview`. Expected setup hash
`ad37a47f…` was locally `ef8522ab…`; source hash `ec37f58d…` was `91044357…`.
Three complex scalar components also differed within the existing 1e-12 tolerance.
No other values, keys, types or lengths changed. The shared fixture is not rewritten
by this retirement. This baseline failure remains a separate verification limit,
not a passing acceptance claim.

After retirement, the same file selection reports 8 passed and the same single
fixture failure in 44.69 seconds; acceptance call time is 22.82 seconds versus
23.00 seconds before. These single local samples (with focused contract checks
running alongside the after sample) do not establish a stable wall-time speedup.
They establish removal of one duplicate acquisition. The 79 tests in the dataset,
list-mode result and quantum runner files pass. No native-window, installed-package,
Windows or physical-hardware qualification is claimed.

## Waveform gallery retirement: contract owners

The three workflow modules had no independent author or scientific consumers.
The XY facade was used only by its own sweep and tests; the monitor definition
was used by gallery/compilation tests; ragged capture was used only by its gallery
and replacement test. Tests calling an old interface do not make it a maintained
contract. The scripts, prebuilt invocations, experiment/result wrappers and
`XYDriveGroup` are removed, rather than moving those APIs into a test directory.

| Current requirement | Evidence owner | Withdrawn gallery assumptions |
| --- | --- | --- |
| Signed IF produces the correct physical I/Q buffers | Quantum `tests/test_waveforms.py::test_signed_if_preserves_i_and_reverses_q_in_rendered_buffers` runs `plan_sampled_waveforms` and `Float64ReferenceRenderer` with positive and negative IF. Reference `unit/test_quantum_runner.py::test_fixed_if_lo_sweep_bounds_real_time_batches_with_host_effects` retains actual host/target LO execution and carrier results. | The separate hand-written XY sine renderer, facade API, q0/q1 selection, forty samples and three particular LO values are not compatibility requirements. |
| Logical demands resolve to physical state owners | Core `execution/test_resource_effect_regressions.py` checks equal-state coalescing and conflicting-state rejection; `planning/test_routing.py` checks route scope and physical endpoints. | Exact facade port names, capability counts and hard-coded reference I/Q channel order. |
| Entityless direct control and quantum execution share a physical AWG claim | Reference `unit/test_quantum_runner.py::test_entityless_host_and_quantum_target_claim_the_same_physical_awg` uses one local member-capability request and the existing quantum compiler fixture. | The sixteen-sample monitor experiment, temporary-cable narrative, metadata strings and complete scope setup are not needed to prove this relationship. This compilation check does not itself prove runtime exclusion. |
| Worker transport preserves driver operations, payloads and acquired values | Server `test_instrument_worker.py::test_spawned_worker_executes_closed_driver_requests` uses the dedicated worker project and checks actual subprocess execution. Existing quantum runner and device-runtime tests retain target/device integration. | No new full reference application clone, provider-private emission observer or single-PID topology requirement. |
| Ragged acquisition survives worker/storage/restart and supports point-local slices | Server `test_instrument_worker.py::test_ragged_point_cloud_run_survives_daemon_and_worker_boundaries` checks worker identity, restart, shapes, numerical values and acquisition evidence. Core `measurements/test_dataset.py::test_ragged_sample_selection_applies_independently_per_point_and_group` owns slicing. | The 4/7/10-point scope experiment and eight-sample repeating waveform are simulator examples, not a separate scientific protocol or required ragged API. |
| Reopening retained work does not reacquire | The existing starter lifecycle journey listed above and server worker restart coverage retain this contract. | Repeating metadata, input identity and reconnect assertions in three gallery-derived daemons. |

There is no identified uncovered contract requiring the temporary AWG-to-scope
scenario as another full-system journey. Its exact arm/play/fetch recipe and
repetition semantics are retired, not claimed to have been migrated. Numerical
test vectors remain useful when chosen for a current contract; historical vector
values and old result wrappers are not themselves obligations.

### Scope inventory retirement boundary

The scope-specific inventory is retired with its already-withdrawn recipes:
`bench-scope`, its monitor binding, scope interfaces/driver, arm/capture state,
resampling, the unused armed-waveform scope mirror and the AWG
`captured_by_scope` receipt. Provider catalog registration,
binding-based description and connection made this inventory dynamically reachable;
it was not unreachable code. Repository consumers no longer arm/fetch this scope.
This does not establish the absence of external callers: retirement follows this
gallery's integration-fixture policy, not a compatibility promise for its simulator.
These members are deleted rather than moved into a replacement test fixture.

The retained contract is AWG/digitizer/trigger participation and sequencing,
quantum `capture_queue` delivery, physical shared claims, renderer I/Q values and
batch semantics. Driver operations retain their non-scope receipts. Scientific
source identity and current-format backup/restore remain required. Existing quantum
runner/device-runtime and snapshot journeys own these checks; no scope substitute
or new compatibility facade is required.

Removing a default device and binding changes the setup structure as well as source
and implementation identities. Regenerated acceptance must be compared recursively:
explain removed inventory and every changed value or structure, and distinguish
derived identity changes from scientific results. Do not assume a hash-only diff.

The shared provider, remaining bench interfaces/codecs and quantum target remain in use by
`test_quantum_runner.py`, `unit/test_list_mode_device_runtime.py`, acceptance and
the remaining device inputs. Scope retirement does not redesign the remaining inventory
or declare every simulator member necessary. It adds no facade, testkit API,
compatibility layer or replacement application. Broader device/compiler extraction
remains in #773.

### Acceptance changes for scope retirement

The default recipe removes exactly the `bench-scope` device and
`bench-scope-monitor` capability. All remaining recipe values and structure are
unchanged. This deliberately changes the complete setup/configuration identity;
source deletion also changes the captured provider implementation identity.

The official isolated generator's fixture differs at 22 leaves:

- `candidate_proposal.items[0].proposal.base_config_content_hash`: the candidate
  retains the configuration with the reduced device inventory.
- Each of `controls_scalar`, `controls_scan` and `launch_preview` changes seven
  leaves: `manual_state.binding.config_source_hash`,
  `preflight.stages[0].config_content_hash`, `reviewed.binding.config_content_hash`,
  `reviewed.binding.setup_content_hash`, `reviewed.config_source.content_hash`, and
  `reviewed.config_source.setup.{content_hash,revision_id}`. These retain the exact
  configuration, executable setup, resolved device/implementation evidence and
  derived reviewed source identity.

Recursive comparison finds no other changed leaves, keys, types or array lengths:
scientific values, units, schemas, acquisition evidence and proposal values remain
identical. The fixture does not embed the full device inventory; its unchanged
shape does not mean the setup structure was unchanged. These checks establish
source-level software behavior, not installed-package, private-consumer or hardware
qualification. They add no historical-format reader or migration.

## Spectroscopy gallery retirement: retained device science

`20_flux_spectroscopy.py` had one repository consumer: the script-execution test
in `tests/test_flux_spectroscopy.py`. The presentation and summary dictionary are
retired. Its experiment, result schema and analysis remain dependencies of the
named scientific tests; `multichannel_bias.py` also imports its `Q0` fixture input.
No provider package bytes or inventory are removed in this slice. This is not a
claim that the shared acceptance capture executes spectroscopy.

The existing daemon test now calls the workflow directly through the shared
equipment-only `independent_lab_daemon`, with explicit independent parameters and
setup. It neither copies gallery scripts nor adds a daemon fixture or tutorial API.
The complete eleven-bias acquisition remains bounded, retaining the fit's scientific
input vector rather than shortening it merely to reduce test time.

| Former assertion or behavior | Retained evidence |
| --- | --- |
| Completed run, eleven preview points and measurement records | `test_flux_spectroscopy_worker_retains_science_and_exact_inputs` keeps the real HTTP/worker acquisition and count checks. Every record additionally checks VNA frequency/S21 and mixing-chamber acquisition identities, and complex trace shape, dtype and units. |
| Analysis identity/revision and fit-report filename | The same worker test checks these and the report's fitted-point count. `test_flux_spectroscopy_runs_fits_saves_and_proposes` retains detailed schema, traces, fit outputs and model assertions. |
| Candidate configuration identity | The worker test retains it and checks candidate resonance/linewidth equal the published fit exactly, with a 5.06 GHz resonance, 1 MHz linewidth and zero-bias sweet spot within the existing scientific tolerances. |
| Fit review accepted | The worker test retains acceptance and the RMSE bound, and checks the exact source run, analysis and fit dataset output of the review. |
| Equipment-only setup and no implicit default publication | The worker test asserts exact run parameter/setup references, unchanged saved parameters/setup and an empty combined registry. |
| Bias cleanup and robust complex fitting | The existing focused tests retain output-disabled success/failure behavior and notch recovery with a known delay and an injected outlier. These are in-process tests, not new worker failure evidence. |

The full isolated acceptance generator changes no identity, key, type, length or
other stable value. Its only three raw differences are the real/imaginary components
of `coherent_scalar.items[0].observables.iq_mean.value` and the imaginary component
at item 2, all within the existing `1e-12` scalar-IQ tolerance. The committed
scientific representatives remain unchanged; no hash update or comparison-rule
change is needed.

The host/quantum composition and point-local routing scripts `24`–`25` remain.
Compiled buffers, signed IF/LO, shared physical claims, multiplexed readout and
ragged worker/restart coverage keep their existing owners listed above. No real
hardware, installed delivery, historical data or performance qualification is
claimed; #773 remains open.

## Configuration-authority retirement evidence

Global activation, stale-global-default and restoration-of-default assertions are
retired. `test_parameter_branches.py` and the verified branch publication journey
own CAS, replay, atomic rollback and current-format recovery. Registry storage
still checks exact reads, duplicate identity, pagination and borrowed transaction
ownership; its injected write failure now targets the immutable revision insert.

The deleted combined-context persistence tests are not an editor to preserve.
`test_branch_parameter_editor.py` covers structural edits, copying, rebase/fork,
unknown values and conflicts on independent branches. Core
`tests/config/test_parameter_structure.py` covers imported/unknown evidence,
renamed source-cell addresses and preservation of untouched row provenance.
Retained input/export tests continue checking exact identities and missing or
altered scientific evidence. Candidate table updates retain typed row materialization,
and drifted source snapshots are rejected before an immutable evidence entry is saved.
The old global-default rerun fixture now explicitly selects the saved evidence entry.

The unused GUI `ConfigEntryInspector` and its default/restore actions are removed.
`parameters.bind(...)` no longer manufactures an intermediate combined entry;
independent preparation retains the exact parameter/setup choice directly.
No frozen environment, compatibility reader or historical-file rewrite is added.

## Development order

The [calibration composition contract](calibration-composition.md) separates pure
parameter merging from scientific acceptance and atomic publication. Its shared
parameter-only merge core, retained multi-source candidates and explicit joint
verification, durable target-complete orchestration and branch finalization are
implemented. The old reference cohort workflow is retired. Its peer-insensitive
freshness and implicit subset selection are withdrawn; redesigned applicability
and freshness remain work. The generic legacy cohort backend is also retired;
do not treat its removal as a pending prerequisite for current task development.

1. Remove gallery recommendations from learning routes. Keep the former tutorial
   URL as a retirement notice. Stop adding or mechanically migrating old examples.
2. Remove redundant presentation scripts and fixture-shape assertions. Extract
   remaining generic behavior into existing starter/teaching/core tests.
3. Candidate verification and branch publication are implemented, including
   parallel/sequential composition, joint verification, generation conflicts and
   lost-response recovery. Keep these scientific invariants when removing old
   consumers; do not rebuild them from the old DRAG procedure interface.
4. The `calibration`, `joint-calibration` and `task-calibration` sandboxes now own
   the generic teaching journeys. The focused DRAG fixture still owns actual
   simulated device acquisition and accepted-gate execution. Remove other
   replaced consumers and unused dependencies as their coverage is accounted for.
   Finer dependency coverage remains in #783. Bounded check-first repair is
   implemented in #853; continuous scheduling is outside that delivered scope.
5. Remove legacy bootstrap and combined-config APIs once valid behaviors have
   new owners. An obsolete gallery consumer is a retirement task, not a reason
   to retain a compatibility layer.

Keep each change complete enough to review: requirement, replacement or explicit
retirement rationale, affected consumers and removal of unused dependencies.
Do not move the whole tree to another folder and keep an active second
implementation. No old-data migration or historical-environment rewrite is part
of this cleanup.
