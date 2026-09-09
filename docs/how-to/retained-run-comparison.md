# Compare retained runs without acquisition

Use **Compare retained runs** in Analyses, run detail or procedure progress.
Choose two completed retained runs, inspect their compatible coordinate and
observable, select point positions independently and run a laboratory-owned
Python model. This operation never dispatches a procedure or starts acquisition.

The reference lab registers `signal-quadratic` through the maintained adapter in
`reference_lab/comparison.py`; its editable model lives in
`reference_lab/workflows/authored/comparison.py`.
Acquire two hardware-free `frequency_amplitude` runs over several frequencies,
then compare them. The quadratic model exposes an adjustable carrier offset. It
is an analytic teaching example, not physical calibration evidence. Experiment
authors can edit the authored fit/helper and its model version, then select
**Refresh author code**. Registering or replacing the project-level
`LabApplication(comparison_provider=...)` callback is a maintainer task, using the
public types in `scopecat.application.comparison`.

The optional `scopecat.analysis.comparison.comparison_inputs` helper checks one
real scalar coordinate and observable per point. It rejects arrays, unavailable
or nonfinite values, incompatible units, stale hashes and unfinished inputs.
Different grids remain separate: no interpolation or cross-run join is performed.
This helper uses existing Dataset reads, not storage pushdown, and supports up to
10,000 points per retained run. It is not a process-memory budget.

The reference fit traces the original immutable datasets and exact ordered
selection positions as explicit bindings. Its publications retain both run IDs
and measurement hashes, model ID/version, parameters, coefficients, selected data
and layered data/model figures. A fit is a normal primary-run analysis revision.
The primary run must actually be an input; secondary measurements are checked
against their true owner and content hash, and secondary runs must be complete.
Project analyses still cannot publish parameter proposals. Nothing copies an
analysis into another owner to bypass those checks.

Reopen old and new results in **Primary run analysis history**. Reading a result
does not execute a model. Providers publish via
`scopecat.analysis.comparison.save_comparison(analysis, request)`, which adds the
public typed `comparison-request` fact to the existing atomic analysis save.
Source actions validate the exact run, analysis and publication hash before
loading project code. They retain the original model, parameters and selections;
editing the current form cannot change what an old candidate or review means.
Configure `[authors].refresh_roots` to include the editable fit/helper module.
**Refresh author code** validates and retains the source revision before another
inspection. Inspection returns its actual `AuthorRevisionRef`; fitting requires
that reference, which is saved in the request fact. Reopening a source action
loads its retained revision before importing the application, even after active
code changes. The existing revision manifest checks the required external Python
and package environment; it does not archive installed dependencies. Comparison
execution without a retained author revision is rejected.

**Create explicit candidate** publishes a separate analysis referencing the exact
saved fit and using the primary run's frozen configuration as its proposal base.
It does not activate a configuration or reopen a closed procedure. **Record
candidate rejection** saves an independent typed fact with actor, reason,
proposal IDs and the candidate analysis ID/publication hash. This is neither
procedure approval nor global deletion of the candidate. Configuration
acceptance, default activation and calibration validity remain separate acts.

**Import suggested inputs into Launch** reads the saved next-input fact and
returns an existing typed `LaunchRequest` inside a `ComparisonHandoff`, including
the source run, analysis and publication hash. Only an explicit action imports
it. The draft retains that structured reference across console navigation and
requires fresh preview in the selected configuration. A suggestion without an
explicit parameter context clears the previous draft's sample and working point;
the console identifies the current default as awaiting review, and users can
explicitly choose a sample/context before preview. A suggestion with an exact
context must first use the existing Configuration resolver and selector; no
context or override is silently dropped. No configuration is declared valid merely
because it belonged to the source run. An uncertain previous
submission remains separately recoverable. Source provenance is session state:
the destination run does **not** yet retain a durable analysis-origin reference.
Saved-plan integration owns that future bridge. Reopen the saved source analysis
to recover suggestions after closing the console.
