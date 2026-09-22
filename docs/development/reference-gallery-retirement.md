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
| `02_session_lifetime.py`, `21_scan_shapes.py`, `40_measurement_workbench.py` | Reattach without acquisition, scan semantics, retained data projections | Compare with starter/teaching and core coverage, move missing behavior to small fixtures, then remove scripts and duplicate tests. Do not port their application setup. |
| `05_sample_workflow.py` | Exact sample revision and analysis provenance | Preserve identity/history assertions in target/sample integration tests; reassess old working-point assumptions separately. |
| `10_direct_control.py`, `33_multichannel_dc_bias.py`, `35_awg_output_monitor.py`, `50_ragged_scope_capture.py` | Shared device ownership, physical routes, entityless diagnostics, ragged acquisition | Keep focused real-worker coverage. Add future device-topic sandboxes using current APIs rather than wrapping the old gallery. |
| `20_flux_spectroscopy.py`, `22`–`26`, `28`–`29`, `31`–`32`, `34`, `36` | Compiled buffers, channel conflicts, signed IF/LO semantics, multiplexed readout, topology and inspection | Extract minimal compiler/runner inputs and keep a bounded full-device journey. Review duplicated recipes and hard-coded configuration assumptions instead of preserving their signatures. |
| `27_channel_timing_candidate.py`, `30_drag_calibration.py`, `workflows/drag_beta_*` | Exact candidate lineage, independent verification, ownership of edited cells, conflict detection, durable publication/recovery | Specify these behaviors against independent parameter branches. Retire assertions requiring publication/restoration of a global active config; existing workflow structure and working-point APIs are not acceptance criteria. |
| `quantum_compilation`, `targets/list_mode`, `provider`, `virtual_lab` | Deterministic device/compiler integration | Retain only dependencies of named scientific/device tests; extract generic framework capabilities where justified. Compute-only teaching does not replace device evidence. |
| Shared acceptance and `snapshot_roundtrip.py` | Real HTTP payloads and exact current-format recovery | Already use independent parameters/setup and an empty combined registry. Keep this evidence as legacy bootstrap consumers are removed. |

The middle rows are an inventory, not a claim that replacement coverage is
complete. Test existence alone does not establish that an assertion is valid
under the target design. Record replacement evidence or why an assertion
expresses an obsolete requirement before removing it.

## Development order

1. Remove gallery recommendations from learning routes. Keep the former tutorial
   URL as a retirement notice. Stop adding or mechanically migrating old examples.
2. Remove redundant presentation scripts and fixture-shape assertions. Extract
   remaining generic behavior into existing starter/teaching/core tests.
3. Define candidate verification/publication on independent parameter branches:
   retain exact source and verification evidence, require an explicit publication
   decision, and reject conflicting head/cell changes. Derive requirements from
   these behaviors, not from the old DRAG procedure implementation.
4. Build a focused calibration fixture and, when the API is usable, a new
   calibration-topic sandbox. Remove replaced DRAG/default-config workflows and
   their unused dependencies in the same functional change.
5. Remove legacy bootstrap and combined-config APIs once valid behaviors have
   new owners. An obsolete gallery consumer is a retirement task, not a reason
   to retain a compatibility layer.

Keep each change complete enough to review: requirement, replacement or explicit
retirement rationale, affected consumers and removal of unused dependencies.
Do not move the whole tree to another folder and keep an active second
implementation. No old-data migration or historical-environment rewrite is part
of this cleanup.
