---
default: patch
---

# Retain pending direct results independently of device control

Keep successful unsaved direct measurements available for save or explicit discard
through command replay eviction and control-session expiry while the daemon lives.
Reserve bounded pending-result capacity before collection and release it only after
confirmed save/discard. Serialize concurrent dispositions and preserve original
result identities through failed, delayed, and repeated UI requests.
Rediscover pending results after a window reload without reconnecting the device.
Keep Python-owned collection results outside the UI queue; backend restart still
ends unsaved retention and requires an explicit local-forget decision.
