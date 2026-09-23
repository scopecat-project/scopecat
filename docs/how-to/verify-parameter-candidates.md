# Verify fitted parameter candidates

Use this workflow after [ordinary analysis](../guides/ordinary-analysis.md) has
returned a managed result. A candidate is a saved proposal scoped to its source
run. Saving it neither verifies it nor changes the lab default.

```python
fit = author.analyze_as(run.id, "my_lab.analysis:fit_rabi", RabiFit)
candidate = author.config.stage(
    fit,
    name="rabi-carrier",
    table="drive",
    key="q0",
    fields={"amplitude": "pi_amplitude"},
)
```

`fields` maps existing parameter columns to fields in the retained result. Scopecat
reopens the publication and reads its values; editing `fit.value` cannot replace
evidence. A missing or `None` result field stops staging. The proposal retains the
source run, original fit publication, source revision, arguments and exact changed
cells. Unrelated cells retain their contents. Manual and estimated edits still use
the [parameter workspace](manage-configuration.md); a local dataclass is
not a managed receipt and no ordinary edit becomes measured automatically.

Collect new data with the exact saved candidate:

```python
check_run = (
    author.prepare("verify_rabi", candidate=candidate, scans={"amplitude": amplitudes})
    .run()
    .wait()
    .result()
)
check = author.analyze_as(
    check_run.id,
    "my_lab.analysis:verify_rabi",
    Verification,
    arguments={"maximum_error": 0.02},
)
verified = candidate.verify(check)
```

The lab owns the registered verification function and scientific acceptance
policy. Its returned dataclass must declare `accepted: bool`. A numeric fit or
successful acquisition alone is insufficient. Scopecat requires a distinct,
completed run with this exact proposal and the same sample revision and workpoint.
The project decision retains both data inputs and the verification publication.
A rejected decision is saved for inspection and raises an actionable error.

For independent sample or operating-point histories,
[publish to the exact working point](publish-working-point-calibration.md) used by
the baseline with `verified.publish_to(working_point=version, name="rabi-verified")`.
This advances only that working point and retains the accepted proposal evidence.

Select the candidate for another experiment, or explicitly change the shared
default. These are separate actions:

```python
next_run = author.prepare("next_experiment", candidate=verified.select()).run()
# Only when you intend to change the shared lab default:
verified.publish_default(name="rabi-verified", note="Independent policy passed")
# Restore the previous exact default if needed:
author.config.undo()
```

Selection is local to `prepare`; it creates no mutable global selection. The
candidate can also be used before verification for exploratory runs, with its
candidate provenance intact. Candidate launches are not saved as experiment plans;
plans currently use saved parameter contexts.

Reopen a candidate with `author.config.candidate(source_run_id, "rabi-carrier")`.
Names belong to a source run, not a lab-wide latest pointer. Reopen its independent
analysis by run and publication ID and call `candidate.verify(result)` again.

## Refine a candidate in a later experiment

Capture the destination branch before acquiring the initial baseline, and use its
exact revision for that baseline. Keep this captured head until final publication:

```python
daily = author.parameters.checkout("daily").head
author.use(parameters=daily.revision)
```

After staging `first` from the baseline analysis, prepare the next calibration
with `candidate=first`. Stage `second` from that new run's managed analysis using
the same `author.config.stage(...)` API. It may refine a cell already changed by
`first`. Then retain the chain:

```python
combined = first.then(second, name="coarse-then-fine")
final_run = author.prepare("verify_rabi", candidate=combined).run().wait().result()
final_check = author.analyze_as(
    final_run.id,
    "my_lab.analysis:verify_rabi",
    Verification,
    arguments={"maximum_error": 0.02},
)
verified = combined.verify(final_check)
published = verified.publish_to_branch(daily, name="coarse-fine-accepted")
```

`then()` records work that has already run; it does not schedule experiments. Every
later source run must have consumed the immediately preceding exact candidate.
Values copied into another revision, matching numbers from an unrelated run, and
reversed stages do not qualify. Pass original proposals in order for a longer
chain: `first.then(second, third, name=...)`; nested compositions are not supported.
A chain with no net change is rejected instead of manufacturing a proposal.

Intermediate candidates do not move `daily`. The final verification includes every
contributing source run and new data using the aggregate candidate. Earlier positive
decisions are not inherited. A concurrent branch edit blocks publication without
discarding the chain; restoring its analysis records preserves the ordered sources.
`combine()` remains the separate operation for sibling proposals from one base,
where conflicting edits are rejected rather than applied in sequence.

This is the retained candidate flow, not yet automatic parameter flow between
background calibration-task stages. Saved task check contexts currently require
an exact saved parameter revision.

Preview retains the exact candidate, scientific binding and executable setup
content. A parameter-only default change does not invalidate that fixed candidate;
a changed executable setup or a relevant manual instrument mutation still requires
a fresh preview. Selecting the active default retains its activation-generation
fence. Publication to the shared default still uses verified-acceptance and global
generation fences: a stale base cannot become the default through this facade.
For independent publication, use an [exact working point](publish-working-point-calibration.md).
Inspect the named table, row and field, reopen/rebase the desired parameter workspace explicitly, and
collect independent evidence for any changed proposal. Restoring a previous
default does not renew calibration validity.
