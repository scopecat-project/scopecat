# Development context

This project has one maintainer and uses self-review rather than independent
review approval. Public changes require CI and self-review, then squash merging.
This workflow does not imply small deployment scale or data volume.

The implementation is pre-stable. Breaking API and internal design changes are
acceptable; update affected consumers instead of adding speculative compatibility
layers. Persistent scientific data has a separate compatibility policy: promises
begin at explicitly designated supported baselines, not every development store.
Do not treat retained experimental evidence as disposable during a code refactor.

For trusted internal code, prefer static checks, typed APIs and normal Python
conventions. Add runtime guards, fallbacks, duplicate invariant checks or exhaustive
edge-case tests at untrusted boundaries or for concrete failures. Review primarily
for intended-path correctness and clear types.
