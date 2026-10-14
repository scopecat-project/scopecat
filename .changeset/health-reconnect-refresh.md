---
default: patch
---

Refresh project data when the application reconnects.

Retire outstanding requests before refreshing the current page after a health recovery, so Samples can clear stale errors without waiting for the event stream or losing the selected sample.
