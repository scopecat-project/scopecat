---
default: patch
---

Keep machine-specific paths out of shared source and fix Windows dependency preparation.

Source sharing now leaves out Scopecat’s local data-path settings. Preparing
execution dependencies on Windows now avoids failures caused by overly long
paths. Long source-folder and Python paths remain readable in Settings.
