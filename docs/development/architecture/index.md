# Architecture

These documents explain the present implementation and clearly marked future
directions to Scopecat contributors:

- [Experiment execution semantics](execution.md) covers authoring ownership,
  specialization, domain lowering, effects, completion, and evidence.
- [Experiment workbench and session contexts](experiment-contexts.md) defines the
  selected product direction, proposed numbering/configuration boundaries and
  staged acceptance; these contracts are not yet implemented.
- [Local application host](application-host.md) covers the unified teaching entry,
  managed operations and the boundaries retained for future deployment.
- [Configuration ownership](configuration-ownership.md) separates fixed scientific
  selection fences from default activation and records scoped publication work.
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
