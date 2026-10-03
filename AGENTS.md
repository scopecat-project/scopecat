# Development context

This project has one maintainer and uses self-review rather than independent
review approval. Public changes require CI and self-review, then squash merging.
This workflow does not imply small deployment scale or data volume.

The implementation is pre-stable. Breaking API and internal design changes are
acceptable; update affected consumers instead of adding speculative compatibility
layers. Persistent scientific data has a separate compatibility policy: promises
begin at explicitly designated supported baselines, not every development store.
No persistent-data baseline is designated yet. Schemas 68–74 and their migration
exercises are retired development formats; schema 82 is not a supported baseline.
Do not add compatibility readers, old-codec fallbacks or migration edges for
prebaseline formats. Keep current-format backup/restore and scientific invariants tested.
Do not delete or silently rewrite historical files during a code refactor; owners
may retain old environments for their own archival reading. Designate future
compatibility only after target/setup/calibration/application models converge.
See `docs/development/data-compatibility.md`.

For trusted internal code, prefer static checks, typed APIs and normal Python
conventions. Add runtime guards, fallbacks, duplicate invariant checks or exhaustive
edge-case tests at untrusted boundaries or for concrete failures. Review primarily
for intended-path correctness and clear types.

For handoff, start at docs/development/platform-status.md and the target issue's
current body; check Git and release/consumer identities rather than inferring
completion from chat summaries or historical plans. At closeout update stale
issue bodies, separating implementation, distribution and acceptance. Ignored
reports and temporary paths must not be required project instructions or the
only portable evidence for an unresolved failure. See docs/development/index.md.
