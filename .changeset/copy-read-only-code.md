---
default: patch
---

Copy read-only Notebook code for exact runs and saved analyses, with manual-copy fallback.

Runs and Project Analyses can copy a snippet that opens the selected run or exact saved publication, including older versions of the same analysis key. Run your connection cell for the same data space first, then paste into a separate cell and run only that cell. The snippet reads saved records without collecting or analyzing again; it does not reconstruct custom typed/group objects. If clipboard access is unavailable or denied, the same code appears for manual copying.
