---
default: patch
---

Fix reset choices and legacy replacement

Consume the desktop reset skip-backup choice once, including cancellation and
failure. Omit the unused state-root field from legacy endpoint records so the
observed older strict author client can read them; retain separate state paths
for new layouts. Resolve native replacement registration through the candidate
runtime, preserving mixed-layout rejection and failed-start retry checks.
