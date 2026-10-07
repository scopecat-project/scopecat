---
default: patch
---

Keep grouped quantum results aligned with their selected entities.

Quantum acquisitions now map each row and its availability to the matching entity,
including topology selections and explicitly ordered qubit sets. Custom mappings
that repeat, omit or misorder entity acquisitions are rejected. This corrects new
executions; it does not inspect or rewrite previously recorded scientific data.
