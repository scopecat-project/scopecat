---
default: minor
---

Run calibration and joint-calibration lessons through managed author sessions.

The shipped notebooks submit source-bound procedures to application workers,
reconnect to the same request, and read retained checks and publication evidence.
History, bounded waiting and step outputs are available from the author session;
closing a Notebook does not stop its worker. Regression assertions stay in the
external real-kernel harness, including in-flight disconnect and history reopening.
Task-calibration admission remains a separate migration.
