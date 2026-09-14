# Reference fixture ownership

The public project has three distinct consumers. The CLI starter is the public
entry point. Targeted reference recipes demonstrate one advanced contract. The
full reference laboratory validates integration, not beginner usability.

| Current content | Responsibility | Direction |
| --- | --- | --- |
| Server `scaffold.py`, installed pilot verifier | Minimal public author workspace | Keep runnable without reference-lab installed; generated scripts use current APIs |
| `reference_lab/notebooks/20*`, `21*`, `40*`, `50*` | Device/measurement examples | Keep individually documented; remove unrelated setup when extracting |
| `reference_lab/quantum_compilation`, `quantum_runner`, `virtual_lab` | Quantum-to-device integration | Maintainer-owned fixture; not a mandatory author dependency |
| `reference_lab/workflows/drag_beta_*` | Calibration, publication and recovery contracts | Preserve integrated evidence; do not teach these as the first acquisition |
| `reference_lab/tests/unit` | Local scientific/compiler behavior | Prefer small fixtures without a daemon |
| Managed author/restart tests | Real process and retained-source contracts | Move generic cases to starter fixtures when they do not require routing/compiler capabilities |
| Private laboratory courses | Lab-specific methods and scientific workflows | Reuse public entry semantics; avoid a second generic framework curriculum |

This is an ownership classification, not a claim that extraction is complete.
Do not move every reference test into the core tier or remove integration
coverage merely to rename directories. Each extraction must identify the
contract it still exercises and which expensive setup becomes unnecessary.

Keep a small number of full-system journeys. Prefer a minimal real daemon for
source refresh, request rejection, retained analysis and restart when hardware
mapping is irrelevant. Use the full virtual plant for shared claims, channel
routing, compiled buffers and recovery interactions. Mocking these boundaries
would remove the evidence the tests exist to provide.

No private package may become a prerequisite for public CI or the installed
starter. Shared test helpers belong in testkit only when independently reused;
reference_lab is not a production library to install on physical benches.

## Installed author package boundary

`fixtures/installed_author_lab` is a tiny wheel-only consumer fixture, not another
user workspace template. `scripts/verify_pilot_bundle.py` builds and installs it
outside the checkout in the clean pilot environment, on both CI platforms. The
same daemon exercises installed discovery, a local wrapper, original/current
analysis after refresh, retained analysis after restart, and rejection/restoration
of changed installed bytes. This extends the installed pilot instead of adding a
second full runtime job or a dependency on private laboratory code.
