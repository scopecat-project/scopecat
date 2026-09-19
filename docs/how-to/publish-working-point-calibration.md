# Publish a verified candidate to a working point

Use a saved working point when different samples or operating points need independent
parameter histories. Publishing to one working point advances only its saved head;
it does not change the application's default configuration or another working point.

Start the baseline measurement from an exact saved version:

```python
parameters = session.config.workspace(context="chip-a-parked", latest=True)
baseline_version = parameters.version
baseline = session.prepare("rabi", working_point=baseline_version).run().wait().result()
```

Stage a candidate from retained analysis, measure with that candidate, and verify it
using an explicit laboratory policy as described in the parameter-candidate workflow.
Publish the resulting verified candidate to the baseline version:

```python
published = verified.publish_to(
    working_point=baseline_version,
    name="chip-a-parked-calibrated-002",
    note="Accepted after independent verification",
)
next_run = session.prepare("rabi", working_point=published).run()
```

The destination must be the exact working point used by the baseline measurement.
Its sample revision, working point, and batch must match, and its head must still be
that version. The server reads the retained proposal and verification; changing a
Python result object does not change the evidence.

Save trial parameter edits before collecting the baseline if you intend to publish
back to that working point. A baseline with unsaved overrides does not have the same
configuration as the saved destination. Likewise, if someone advances the destination
while calibration runs, publication fails. Reopen the current version and explicitly
repeat or rebase the work; publication never merges stale calibration silently.

The accepted revision retains its proposal and verification references. Existing
versions remain usable by exact reference. Publication does not switch an already
prepared run or an open parameter editor to a newer version.

For recoverable publication, generate and retain an operation ID before calling
`verified.publish_to(..., operation_id=operation_id)`. Repeating that exact call
returns the same saved version, including after a response is lost.

For lower-level automation, use
`session.config.publish_context(ConfigContextPublishCommand(...))`
with a stable operation ID. An identical retry returns the original receipt even
after the head advances; a different command with that operation ID is rejected.
`session.config.context_publish_operation(operation_id)` retrieves the receipt.
The receipt, approval, saved version, and head update commit together. The command
uses an exact `ConfigContextRef`, not a global configuration generation.

This API publishes a single-sample working point. It does not claim that a candidate
is valid for another sample, another batch, an apparatus object, or a complete
multi-object calibration program. `publish_default()` remains an explicit operation
for changing the application-wide default.

For a registered policy that repeatedly evaluates freshness and publishes bounded
cohorts, use [working-point automation](automate-working-point-calibration.md).
