---
default: patch
---

Reduce waiting for experiments that exchange many small driver messages.

Local driver communication now sends small commands and responses promptly,
reducing acquisition and cleanup delays while retaining isolated driver processes.
