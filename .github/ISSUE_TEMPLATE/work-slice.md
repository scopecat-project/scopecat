---
name: Pilot work slice
about: One owner, one observable outcome, explicit contract dependencies
---

## User outcome

Who can do what after this lands? Give one concrete acceptance scenario.

## Primary owner and owned paths

Name one owner responsible for implementation, focused checks and self-review.
List the narrow source paths this slice changes; name consumers updated together.

## Contract inputs and outputs

List existing Python/wire/storage inputs and produced outputs. Identify generated
outputs and their source/generation command. Record shared files and their current
owner before concurrent edits.

## Dependencies

Link producer issues/PRs and state the exact contract needed. Producers land before
dependent consumers. A semantic wire, storage or generated-client conflict is a
contract dependency: coordinate source changes, rebase and regenerate. Never
manually merge generated output or invent a second upstream contract.

## Focused checks

Give one hardware-free command for the owned lane and the shared fixture scenario
it exercises. Record the isolated project state directory and worktree branch.
The fast `CI gate` remains required; generated checks and import-linter stay active.
List any affected process/browser/installed checks beyond that gate, plus the
milestone issue responsible for full acceptance. State which data, frozen-request
and resource invariants this slice protects. See `docs/development/test-feedback.md`.
Deferred acceptance is not a successful qualification result.

## Non-goals

List excluded behavior and paths. No additional approval hierarchy is implied.

## PR completion

Carry the sections above into the PR body, then record observed behavior, checks,
remaining limitations, the closing issue and CI run URL/revision. Record the
fast-gate duration excluding runner queue time and any deferred full acceptance.
Explain source changes and regenerated outputs together so a self-review can assess the final contract.
