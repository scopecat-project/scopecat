# Publish a verified candidate to a parameter branch

A parameter branch holds a named history of scientific inputs. The sample, batch
and setup are selected separately. Publishing advances only the reviewed branch.

Capture the branch before collecting the baseline:

```python
baseline = session.parameters.checkout("chip-a/daily").head
setup = session.setup.get("bench-v1")
session.use(parameters=baseline.revision, setup=setup)
baseline_run = session.prepare(experiment).run().wait().result()
```

Stage a candidate from retained analysis, measure with it, then obtain an independent
verification using the laboratory's policy. Publish that verified candidate:

```python
published = verified.publish_to_branch(
    baseline,
    name="chip-a-calibrated-002",
    note="Accepted after independent verification",
)
session.use(parameters=published.revision, setup=setup)
```

The server checks the exact baseline, proposal, verification and branch generation.
It does not trust an edited Python result as verification evidence. An unrelated
branch or another session's selection is unchanged.

Save trial edits before collecting the baseline if you intend to publish back to
the branch. Unsaved overrides do not equal its saved baseline. If the branch moves
during calibration, review its new head; stale calibration is not merged silently.

Repeat the exact publication call, including the same baseline and destination
name, after a lost response. The accepted revision, approval and branch update are
committed together. The branch receipt retains the producing run and verification
references. Acceptance applies to the proposed cells and recorded scientific
context, not to every value in the branch or another sample.

For automated procedures, see
[joint calibration procedures](automate-parameter-calibration.md).

## Adopt from the workbench

In Runs, open a candidate's **Review adoption and records**. Select the destination
branch, compare its current values with the candidate's original and proposed
values, and choose an eligible retained independent verification. The reviewed
branch revision and generation stay fixed. Refreshing evidence does not rebase
the candidate; a moved branch blocks adoption.

**Adopt verified candidate** advances only that branch. Its working table remains
intact, including invalid raw edits; review the changed branch in Configuration
before checkpointing that table. Other branches' working tables do not block
adoption. The comparison shows acknowledged working input separately from the
values being published.

Without eligible verification, leave the retained candidate pending and follow
[the author verification workflow](verify-parameter-candidates.md). Loading older
verification records is explicit. Old-data reanalysis, rejection and verification
of a different candidate cannot authorize publication.

If the response is lost or unreadable, **Recover original adoption** repeats the
same retained request. The browser tab retains that request through reload;
publication history on the selected branch is the durable adoption record. Reopen
it here to find the accepted revision, operator, note and exact verification.
