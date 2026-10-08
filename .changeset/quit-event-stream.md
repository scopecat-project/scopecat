---
default: patch
---

Quit normally while live updates are connected.

Quitting an idle application now closes live-update connections so the background service can finish exiting. Active work still requires a decision before quitting. If shutdown needs more time, retrying waits for the same service; exit errors no longer appear as startup failures.
