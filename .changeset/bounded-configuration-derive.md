---
default: patch
---

Reject oversized configuration copy requests before parsing them.

Configuration derivation now limits the complete HTTP request to 32 MiB while
preserving the existing 16 MiB document allowance. Oversized requests return 413
without saving an original, editable copy, setup or receipt, including when the
request omits or misstates its length.
