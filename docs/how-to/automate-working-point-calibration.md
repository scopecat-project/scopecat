# Automate calibration for one working point

Automatic parameter publication needs an explicit saved working point. Create it
with the [parameter workspace](manage-configuration.md), including the actual
sample revision and any declared batch. The lab application must register the
calibration definitions, their procedures and the publication policy it intends
to run.

Start a worker from that code workspace:

```console
scopecat automation work . --working-point chip-a-parked
```

`chip-a-parked` identifies an existing saved context and its stable workspace.
Each planning cycle follows that workspace's current head, then freezes the exact
version for the admitted cohort. A successful calibration publishes a new version
there. The next cycle evaluates that new version. It does not change the shared
lab default or another workspace's parameter head.

Add `--once` to perform one bounded worker cycle. A cycle can admit or execute work
without finishing the entire calibration: publication waits for all required
member evidence. The resident form continues subsequent cycles. Stopping the
worker does not delete admitted procedures or retained results; the usual durable
procedure recovery rules apply.

For explicit Python orchestration, select the same scope on the evaluator:

```python
version = lab.config.workspace(context="chip-a-parked", latest=True).version
evaluator = lab.calibrations.evaluator(working_point=version)
decision = evaluator.cycle()
```

`cycle()` evaluates freshness and admits due cohorts; it does not replace the
procedure worker or finalizer. The command above composes those components for
resident operation. Omitting the working-point selection permits catalog-scoped
checks that record procedure success without publishing parameters. It never
creates a sample or guesses where calibration results should be written.

## Independent work and conflict handling

Two workspaces have independent calibration histories even when they have the same
sample and working-point display names. A workspace retains its history as its
head advances. Definitions select logical members such as `q0`; the evaluator adds
the selected workspace owner and exact sample/workpoint/batch scope before looking
up prior success or building an intent.

The selection controls admission; procedure execution and finalization still service
the daemon queue under their registered capabilities and policies. It is not a
worker access-control boundary.

An A worker and a B worker can use the same daemon. Their parameter publication
checks are independent. Shared instruments and explicit fan-out capacity limits
still constrain concurrent work. Separate workers do not grant additional device
access or bypass state recovery.

If someone edits A while its calibration is running, the old cohort cannot
overwrite A's new head. Pending publication records that replacement and a later
cycle can evaluate the new state. B's unrelated head remains usable. A changed
executable setup also prevents stale work; changing only global default parameter
values does not invalidate fixed working-point inputs.

The baseline and verification runs retain their exact sample revision and batch.
Updating the sample catalog during a cohort does not silently retarget its later
stages. Candidate parameters may differ from the baseline, as required for
verification, while the publication proof still checks their common origin.

Publication retains the verified proposals, decisions, merged result and member
successes together with the new context and operation receipt. It is an atomic
working-point update. After an uncertain response, the finalizer reconciles the
same operation instead of creating a new publication intent.

This workflow supports bounded cohorts within one working point. Dependencies on
another workspace, another batch or apparatus observations require a separate
applicability contract. Independently verified member results also do not establish
joint device-wide scientific verification merely because their parameter changes
can be merged. See [durable automation](../development/architecture/automation.md)
for the complete proof and replay contract.
