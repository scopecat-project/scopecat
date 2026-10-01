# Architecture

These documents explain the present implementation and clearly marked future
directions to Scopecat contributors:

- [One application, independent execution contexts](public-application.md) is the
  canonical product target: direct workbench entry, shared resource authority,
  same-service practice scopes and retirement of the manager/legacy source path.
- [Device and driver management](device-management.md) defines the target device
  registry, connection revisions, setup references and shared physical access;
  it distinguishes these from the current setup-copy editor and project backend.
- [Experiment execution semantics](execution.md) covers authoring ownership,
  specialization, domain lowering, effects, completion, and evidence.
- [Experiment workbench and session contexts](experiment-contexts.md) defines the
  selected product direction, implemented ownership boundaries and staged
  acceptance. Use the [current platform status](../platform-status.md) for remaining work.
- [Local application host](application-host.md) describes the packaged application
  runtime registration, process ownership and native window lifecycle.
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

- [Quantum compiler and device adapter boundary](quantum-adapters.md)
