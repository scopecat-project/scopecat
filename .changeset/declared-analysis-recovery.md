---
default: patch
---
Explain unavailable run analyses and reload failed declarations explicitly.

Saved results remain readable when the original author environment or declaration
cannot load. Reloading only reads the saved source, while run changes and repeated
submits cannot accidentally start another analysis or reuse another run's form.
