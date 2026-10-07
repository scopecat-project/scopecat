---
default: minor
---

Keep calibration tasks on their selected source revision through completion.

Checks, repairs and finalization use the same retained source after project edits
or client reconnection. Invalid calls are rejected before execution, and failed
finalization remains visible until explicitly retried.
