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

Preview captures the current lab generation even for an immutable candidate.
A default change after preview requires a fresh `prepare(...)`; the old prepared
launch cannot bypass review. Publication uses the existing verified-acceptance
and generation fences. A stale
base cannot become the default through this facade. Inspect the named table,
row and field, reopen/rebase the desired parameter workspace explicitly, and
collect independent evidence for any changed proposal. Restoring a previous
default does not renew calibration validity.
