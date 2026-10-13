---
default: patch
---

Retry an unconfirmed device save with the original request, preventing a lost response from creating a false revision conflict. Device and configuration IDs now always use secure browser randomness, including browsers without randomUUID.
