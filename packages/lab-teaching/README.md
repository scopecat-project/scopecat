# Scopecat synthetic tutorials

Public, device-free tutorial resources: parameters, scans, complex computation,
source refresh, retained analysis, grouped reads and verified branch calibration.
The calibration topic includes pause/resume, retained scientific rejection and
check-only evidence that leaves the parameter branch unchanged.
The joint-calibration topic demonstrates a coupled failure after individually
successful checks, complete target coverage and recovery after composition.
Every sandbox topic includes
a complete runnable Notebook and editable author source.

Startup declares only the synthetic executable setup. Parameter definitions and
values live in the exercise's independent branch, created by `open_parameters`.
Saving, running, refreshing and reopening the tutorials require no global
parameter default or working-point fixture.

The declared synthetic sine response and its diagnostic illustrate framework
behavior. They do not implement a laboratory's physical calibration or acceptance
policy. There is no dependency on private packages, device SDKs or reference_lab.

See [tutorial sandboxes](../../docs/tutorials/teaching-sandboxes.md).
