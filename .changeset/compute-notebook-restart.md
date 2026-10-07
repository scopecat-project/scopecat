---
default: patch
---

Read the compute lesson's saved mean IQ after restarting the kernel.

The lesson now imports its result type in the read cell and explicitly selects
the recorded run number from history. Reading no longer depends on acquisition
variables from the previous kernel or requires collecting another run.
