# Architecture

These documents explain the present implementation and clearly marked future
directions to Scopecat contributors:

- [Experiment execution semantics](execution.md) covers authoring ownership,
  specialization, domain lowering, effects, completion, and evidence.
- [Lab daemon](daemon.md) covers durable run ownership, instrument workers,
  cancellation, API events, configuration, and storage.
- [Durable procedure automation](automation.md) covers replayable multi-run
  procedures and the path from one calibration to device-scale orchestration.
- [Structured experiment authoring](structured-authoring.md) records the proposed
  long-term GUI/Python ownership, request, history and sharing direction. It is not
  a shipped editor contract.
- [Scalability benchmarks](../scalability.md) records representative workload
  profiles, target envelopes, and current boundaries.

Architecture is an implementation choice rather than a product requirement.
Change it decisively when demonstrated workflows reveal a clearer design, while
preserving user-facing concepts that still deliver value.
