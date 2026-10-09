# Retire the reference gallery by behavior

The gallery's presentation scripts are retired. Its remaining device, compiler
and scientific inputs support integration tests while their ownership is reviewed
in [#773](https://github.com/scopecat-project/scopecat/issues/773), under
[#615](https://github.com/scopecat-project/scopecat/issues/615). This does not make
the reference application a supported author template.

The lab originally probed user needs through runnable experiments. Retiring its
implementation does not retire those needs. [Workflow evaluations](workflow-evaluations.md)
retain representative user goals and gaps; this page maps their current evidence.
Help tutorials own teaching, core tests own mechanism rules, and a few combined
journeys retain scientific integration and product feedback. The whole old
implementation can eventually retire without creating another gallery API.

## Current evidence owners

Paths in this table are relative to `examples/reference_lab/tests/` unless stated
otherwise. These are current owners, not a commitment to keep every fixture.

| Goal or semantic boundary | Current evidence and limits |
| --- | --- |
| Direct multi-device operation | `test_device_sessions.py` checks typed DC/thermometer/VNA sessions, ownership release, calibrated routes across two devices, settled readback and parked/off outputs. Server instrument tests own connection reuse, retries and contention. Worker-failure cleanup is not established by success-only checks. |
| Bias scan, complex spectrum, temperature and fit/review | `test_flux_spectroscopy.py` retains real daemon/worker acquisition, complex S21 shape/units, temperature provenance, fit tolerances and exact review/candidate inputs. Focused tests check complex-notch recovery with delay/outlier and disabled bias after failure. |
| Ramsey, raw IQ, signed IF/LO and compilation | `test_quantum_composition.py`, `unit/test_quantum_runner.py` and `unit/test_list_mode_*` retain host ordering, point-local routes, physical buffers, multiplexed acquisition, shared claims, bounded chunks and numerical entity correspondence. Shared acceptance checks independent-channel availability and HTTP traces. |
| DRAG candidate → independent verification → adoption | `test_typed_candidates.py` retains simulated device acquisition, numerical fit/report, exact candidate lineage, explicit branch publication and accepted-gate execution. Verification does not mutate defaults or setup. |
| Joint calibration and recovery | The same candidate tests retain target-complete joint remeasurement, rejection, stale destinations, restart and lost-response replay. Server branch/analysis tests own atomic publication, conflicts and current-format recovery. |
| Live/offline grouped analysis | `test_grouped_analysis.py` and `test_live_group_traces.py` retain grouped analysis, live traces and reopening. Core dataset tests own generic grouping, availability and selection. |
| Compare results → next experiment | `test_comparison.py` and `test_experiment_plans.py` retain exact source inputs, candidate rejection, saved plans and handoff. Pure compute/UI presentation can use smaller fixtures. |
| Author edits and reopening | `test_author_refresh.py`, `test_typed_author_refresh.py` and managed-author tests retain admitted source and independent contexts across edits/restart. Shipped Help lessons own the learning experience. |

These eight groups describe the remaining review surface, not eight promises to
preserve every old scenario. Keep stable user goals and scientific/data semantics
separate from replaceable implementations. Fixed q0–q3 labels, inventory counts,
large bootstrap, old wrappers and exact display strings are fixture choices.
Do not reduce every scientific/device case to a minimal compute fixture.

## Mechanism rules already have focused owners

- Core planning/program tests own route completeness, scan composition, repeat
  order and snake traversal. Core dataset tests own labeled selection, grid/Xarray
  identity, ragged slices and availability; server `core_integration/test_run_handle.py`
  owns durable Arrow pagination.
- Quantum pulse/authoring tests own logical signal overlap and instruction
  identity through lowering. Logical rejection does not prove physical runtime
  exclusion. Quantum waveform tests own signed-IF I/Q rendering; core resource
  tests own state coalescing and conflicts.
- Server `test_instrument_worker.py` owns spawned-driver transport and ragged
  acquisition through storage/restart. Its focused input replaces the old scope
  recipe; the simulator's exact AWG-to-scope arm/play/fetch recipe is withdrawn.
- Core topology selection and quantum result-contract tests own selection and
  mapping rules. Reference runner tests check physical placement and distinct
  per-entity values/availability, not merely labels and shape.
- Server sample tests own exact sample revisions, run binding and scoped analysis.
  Starter lifecycle tests own closure, reattachment and reading without acquisition.
  Branch/parameter-structure tests own edits, conflicts, unknown values and retained
  source-cell provenance. Global-default activation/restoration is retired.

The scope device, monitor binding and scope-only interfaces are removed. Retained
AWG/digitizer/trigger participation, capture queues and non-scope receipts still
have consumers. `pump-source` remains dynamically reachable through its catalog
and route; its scientific need is an inventory question, not proof of dead code.
The unused single-output `play(waveform)` protocol and its separate sampled-waveform
codec are also retired. AWGs execute multi-channel programs through load/arm and
the shared trigger; output reset remains a state-invalidation input. Existing
payload, list-mode device-runtime, quantum-runner and worker-composition tests own
buffer transport, trigger ordering, ambiguous-load failure, shared claims and
reset recovery. Removing the old receipt-only branch does not replace these
physical/compiler boundaries with compute-only evidence.

The legacy `channel-timing` launcher and its `channel_delay` parameter are
retired. The operator supplied that proposal; neither compilation nor execution
consumed the value, so a completed candidate run did not establish timing
calibration. DRAG tests retain measured candidates, independent verification,
branch publication and joint recovery. Core scientific-admission tests own
multi-stage plan subject/configuration rules. Shared acceptance still inspects
real Ramsey planned settings and displays a trial DRAG-parameter proposal;
execution alone is not scientific approval. No timing-calibration capability is
claimed by that display fixture.

The current source map is in the [reference lab README](https://github.com/scopecat-project/scopecat/blob/main/examples/reference_lab/README.md).

## Scientific and data limits

Synthetic plants know part of the expected answer. They establish software and
numerical behavior, not physical scientific correctness or independence between
targets. The Ramsey composition response is bias-independent; spectroscopy has a
separate flux model. Unknown dependency observations cannot justify selective
calibration reuse; [#783](https://github.com/scopecat-project/scopecat/issues/783)
owns finer applicability work.

The topology migration exposed a numerical row/entity ordering defect that the
old shape-only gallery check missed. The correction orders acquisitions by the
product entity axis and rejects missing, duplicate or misordered owners. Distinct
values and missing-shot patterns now exercise that mapping. Historical scientific
data was neither inspected nor rewritten, so the affected historical runs, if any,
are unknown. [PR #902](https://github.com/scopecat-project/scopecat/pull/902) retains
the investigation and validation.

Removing definitions or inventory can change provider bytes, device revisions,
setup structure and reviewed source identity. When a change affects identities or
scientific results retained in a current acceptance fixture, use that fixture's
existing validation to check the affected evidence. Explain derived identity
changes separately from scientific results; do not skip hashes or dismiss real
scientific changes as identity churn. Preserve the existing narrow scalar-IQ
tolerance and exact parameter/setup/source evidence. Deletion alone does not
require rebuilding every fixture or creating a new evidence system.
Representative deletion and validation records
are [#929](https://github.com/scopecat-project/scopecat/pull/929) and
[#931](https://github.com/scopecat-project/scopecat/pull/931).

## Remaining work

Retire duplicate consumers and unused dependencies after checking their actual
callers and current requirements. Move generic cases to existing core, starter or
Help owners; retain bounded real-worker combinations where device/scientific
semantics matter. An obsolete interface need not be ported or copied into another
fixture. A current requirement needs an evidence owner or an explicit unresolved
gap, not a permanent record of every historical assertion.

The reference cohort, implicit subset/freshness policy and global-default
publication are already retired. Current branch composition, joint verification
and durable finalization are implemented; see [calibration composition](calibration-composition.md).
Teaching design and remaining standalone lesson consumers belong to
[#565](https://github.com/scopecat-project/scopecat/issues/565); analysis-author growth
to [#561](https://github.com/scopecat-project/scopecat/issues/561); actual editor,
unfamiliar-user and physical observations to
[#616](https://github.com/scopecat-project/scopecat/issues/616).

Retirement concerns tracked fixtures and source. It does not delete scientific
data or user environments, add historical readers, or change the
[data compatibility policy](data-compatibility.md).
