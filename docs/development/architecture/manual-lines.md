# Manual physical-line confirmation

The finite manual-line workflow uses stable line IDs independently of scientific
subject, target and setup identity. Ordinary acquisition parameters may vary
between explicitly prepared acquisitions. Each child retains its original
source, configuration, operation ID and run admission. Labels are not sample IDs.

用户操作见[手动换线并逐条测量](../../how-to/measure-manual-lines.md)。
以下接纳与恢复规则是当前协议；带提交编号的验收说明仅记录当时的测试范围。

## Shared boundary with drain and recovery

This slice owns only explicitly declared manual-line acquisitions. Drain and
ordinary admission remain owned by the existing lifecycle services (#1097).
The shared interface is reviewed against #1159 at
`eca0e6c78fd5d6fa9906aff1ad3117350cb2e864`: an expired manual confirmation
remains an attention blocker. Human reconfirmation/continue is an ordinary
mutation, so the user must cancel exit wait first. The existing HTTP attention
endpoint stays outside the drain allowlist. Necessary admitted child execution
may reach the freshness fence while draining; this does not grant new human
continuation. The integrated fake-runtime test checks this boundary on main
`9e53f0590bf03268b01807d965f7462a4853e4c1`, after #1159 merged.
No exit state or second execution-state store is introduced.

`LabProcedureContext.run(..., manual_line_id=...)` plans the frozen acquisition
before asking for a human wiring confirmation through the existing interpretation
step. The confirmation identifies the physical line, child step and acquisition
intent hash. The child retains that exact interpretation as an input. A different
acquisition cannot reuse the answer.

The server records its process instance with the first answer. Idempotent answer
retries return the original evidence, including after restart. This is freshness
evidence, not authentication, permission or a physical wiring detector.

Before the **first** executor lease, the server checks that confirmation in the
same transaction as lease admission. An expired confirmation on an unstarted
queued child becomes `attention_required` with reason
`manual_line_confirmation_expired`. No execution segment or unknown device effect
is invented. The parent uses its existing step-attention path.

The existing attention continuation command accepts an explicit
`manual_line_reconfirmation` only for this unstarted case. It names the original
confirmation and a stable request key, with the new human actor. The existing
`run_continuation_authorized` event retains that evidence and the current server
instance in the same transaction as attention-to-queued. Original admission,
source, configuration, line and operation identity remain unchanged. A lost
response retries the same command; reusing its key never renews freshness on a
later server instance. After another restart a new explicit confirmation is
needed. The receipt acknowledges the historical command, so clients refresh
current state rather than treating a replay receipt as permission to execute.

Runs with execution segments retain the existing external-state reconciliation
contract. Wiring confirmation cannot resolve their uncertainty. Ordinary runs
without a manual-line declaration retain existing behavior. Completed evidence
is restored without acquiring another lease.

Historical delivery note: the initial server-side confirmation and continuation
work alone did not deliver the full list UI in #1023. The current finite list,
attempt history and comparison behavior is described below. The referenced
acceptance uses fake devices; it does not qualify physical wiring or hardware.

## Finite ordered lists and retained attempts

Experiments → Reuse and recovery → Physical line scans creates a named list from
one saved measurement template. Line labels are editable; stable line IDs and
order remain unchanged. Each prepare opens the existing acquisition form, where
ordinary settings such as frequency range or power can change. Starting creates
a durable authored procedure, whose existing interpretation step asks for wiring
confirmation before its independent child run. Reading or reopening a list never
submits, dispatches or confirms anything.

The catalog uses existing content-addressed experiment-plan revisions, including
practice ownership, retention and optimistic edits; no schema migration or second
execution-state table is added. Progress is derived from authored procedure
intents and the exact child operation receipt. The retained template source and
scientific subject/target/setup must match at admission. The next unattempted
line follows saved order; remeasurement appends another independently confirmed
attempt. A nonclosed procedure or unresolved child fences the next submission in
the existing admission transaction.

All attempts remain visible with their original label, source, actual inputs and
run result. A historical unknown remains labeled uncertain even after the existing
explicit reconciliation closes it; only that existing reconciliation evidence
permits the next attempt. An expired unstarted wiring confirmation is displayed
separately from unknown effects. No last-attempt-wins selection is made. Two exact
attempts can open the existing retained-curve comparison; settings differences
are shown without interpolating grids or forcing one rectangular result array.

The catalog supports up to 1,000 ordered lines, one machine and manual switching.
It does not automate wiring, create scientific samples from labels, change
compiler/list-mode semantics, or orchestrate arbitrary scientific identities.
