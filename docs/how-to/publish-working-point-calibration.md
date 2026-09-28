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
