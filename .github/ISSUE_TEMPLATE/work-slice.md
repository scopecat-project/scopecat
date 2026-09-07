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
it exercises. Record the isolated project state directory. Retain existing CI,
generated checks and import-linter as integration gates.

## Non-goals

List excluded behavior and paths. No additional approval hierarchy is implied.

## PR completion

Carry the sections above into the PR body, then record observed behavior, checks,
remaining limitations and the closing issue. Explain source changes and regenerated
outputs together so a self-review can assess the final contract.
