---
default: patch
---

Shorten CI feedback by balancing the complete core suite across two required shards.

Keep two workers per shard, all existing test tiers and assertions, platform smoke,
quality, UI, documentation and the strict CI gate. Record measured scheduling costs
and the additional runner preparation tradeoff.
