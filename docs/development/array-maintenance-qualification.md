# Six-target calibration qualification

This developer journey exercises the existing task, procedure, candidate and
parameter-branch APIs through a real local daemon and worker processes. It uses
a disposable teaching project with an explicitly declared software scenario.
It does not contact hardware or modify an existing laboratory project.

Run from the repository with the locked development environment:

```sh
uv run --locked pytest packages/lab-tools/tests/test_array_maintenance.py -q
```

The author implementation is in
`packages/lab-tools/tests/fixtures/array_maintenance.py`; the test copies it into
the disposable project's author package and registers its two procedures.
The task-calibration lesson supplies project infrastructure, not the array's
parameter table or calibration policy.

## Model and retained evidence

Six channels, q0–q5, share three logical readout groups. A group's health check
is a prerequisite for its two fitting stages. All fits use the same captured
parameter revision and executable setup. Each fit retains a measurement,
analysis, check result and candidate; none edits the daily branch directly.

Finalization combines all six candidates, remeasures every target against that
aggregate and publishes only after complete verification. A circular neighbor
term is zero at the initial revision but nonzero after composition: the final
measurements therefore exercise interaction between candidate edits instead of
merely repeating six independent acceptance decisions.

Each case stops the daemon after readout-a and q0 have passed, then restarts it
before automatic advancement. The test checks retained execution identities and
the original q0 run snapshot, so recovery cannot silently reacquire that fit.
This is recovery between completed stages, not an abrupt mid-acquisition crash.

| Case | Expected retained outcome | Daily branch |
| --- | --- | --- |
| Healthy | Nine checks pass; six-target aggregate verification passes | Publishes all six fitted offsets |
| Local drift | Fits pass; final verification rejects q4 and retains its decision | Unchanged |
| Readout outage | readout-b fails; q2/q3 stay blocked; four unaffected fits pass; no finalization | Unchanged |
| Concurrent edit | Verification passes against the captured candidate; publication rejects the stale destination generation | Operator's edit remains |

The project starts without combined configuration registry entries. Parameters
are saved independently, the destination branch is captured explicitly, and
setup authority remains unchanged throughout each case.

## Boundaries and next work

Readout groups represent task prerequisites here. The outage is a compute
failure, not a simulated physical lease conflict. Existing device fixtures own
routing and device-resource behavior. These tests establish neither concurrent
hardware safety nor one batched multi-target acquisition.

The policy explicitly requests all six final measurements. It does not discover
coupling, capture resolved parameter-read dependencies, reuse unaffected evidence
across changed contexts or perform automatic selective repair. Those contracts
remain subsequent implementation work. There is also no continuous maintenance
scheduler or human Windows qualification in this journey.

The analysis reads its measurements before calling `context.result(...)`, which
captures their provenance. An earlier result builder does not acquire inputs
read later; keep that ordering explicit when extending the verifier.
